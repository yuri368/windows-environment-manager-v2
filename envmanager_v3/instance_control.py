"""Local page control, independent of HTTP and all environment operations.

No locator (or URL-bearing file) exists. The kernel owns endpoint lifetime.
Windows uses NativePipe's local-only, first-instance pipe and PID APIs; Linux
uses an abstract AF_UNIX endpoint and SO_PEERCRED, solely for File-backend CI.
Both sides pin live processes, check owner/login session/image, and bind the
exchange to fresh challenges. In source mode the image is the Python runtime:
this is a same-user control channel, not a security boundary against that user
running arbitrary Python. It confers only page opening, never Service access.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import select
import socket
import struct
import sys
import threading
import time

from .native_ipc import NativePipe, Win32, PIPE_PREFIX, decode


class ControlError(RuntimeError):
    pass


def configuration(args):
    """Compare effective scope/options, including source/package and assets."""
    data = args.data_dir.resolve()
    environment = args.environment_file or data / "environment.json"
    values = [args.backend, os.path.normcase(str(data)),
              os.path.normcase(str(environment.resolve())) if args.backend == "file" else
              (os.path.normcase(str(args.environment_file.resolve())) if args.environment_file else None),
              os.path.normcase(str(args.assets.resolve())), args.port,
              os.path.normcase(str(Path(__file__).resolve().parent))]
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def _token(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _same_scope(actual, own):
    if any(actual[k] != own[k] for k in ("owner", "login", "image", "hash", "elevated")):
        raise ControlError("INSTANCE_PEER_REJECTED: owner, login session or program image differs.")


class _Identity:
    def __init__(self):
        self.win = Win32() if os.name == "nt" else None
        if not self.win and not sys.platform.startswith("linux"):
            raise ControlError("INSTANCE_CONTROL_UNAVAILABLE: local peer verification requires Windows or Linux.")

    @contextmanager
    def pin(self, pid):
        if self.win:
            from ctypes import wintypes as W
            with self.win.process(pid) as handle:
                value = self.win.identity(handle)
                identity = {"pid": value.pid, "created": value.created, "owner": value.sid,
                            "login": value.session, "image": os.path.normcase(value.image),
                            "hash": value.image_hash, "elevated": value.elevated}
                def alive():
                    if self.win.kcall("WaitForSingleObject", W.DWORD, [W.HANDLE, W.DWORD], handle, 0) != 258:
                        raise ControlError("INSTANCE_PEER_EXITED: bound process exited.")
                yield identity, alive
        else:
            # pidfd pins this process generation, rather than trusting a reused PID.
            handle = os.pidfd_open(pid)
            try:
                fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
                image = str(Path(f"/proc/{pid}/exe").resolve(strict=True))
                identity = {"pid": pid, "created": int(fields[19]),
                            "owner": str(Path(f"/proc/{pid}").stat().st_uid),
                            "login": int(fields[3]), "image": image,
                            "hash": hashlib.sha256(Path(image).read_bytes()).hexdigest(), "elevated": False}
                def alive():
                    if select.select([handle], [], [], 0)[0]:
                        raise ControlError("INSTANCE_PEER_EXITED: bound process exited.")
                alive()
                yield identity, alive
            finally:
                os.close(handle)


def _endpoint(data_dir, owner):
    key = hashlib.sha256(("page-control-v1\0" + owner + "\0" +
                          os.path.normcase(str(Path(data_dir).resolve()))).encode()).hexdigest()
    return PIPE_PREFIX + key if os.name == "nt" else "\0EnvManagerV3-" + key


def _connect_native(name, win):
    from ctypes import wintypes as W
    deadline = time.monotonic() + 3
    while True:
        try:
            return NativePipe.connect(name, win=win, timeout=3)
        except Exception as error:
            # Only connection acquisition may wait. Never reconnect/replay once
            # a request has been sent, even if the browser result is uncertain.
            if getattr(error, "details", {}).get("winerror") != 231 or time.monotonic() >= deadline:
                raise
            remaining = max(1, int((deadline - time.monotonic()) * 1000))
            win.check(win.kcall("WaitNamedPipeW", W.BOOL, [W.LPCWSTR, W.DWORD], name, remaining))


class _UnixConnection:
    """Same bounded framing as NativePipe; never listens on an IP socket."""
    def __init__(self, connection):
        self.connection = connection
        connection.settimeout(3)

    def peer_pid(self):
        pid, uid, _ = struct.unpack("3i", self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != os.getuid():
            raise ControlError("INSTANCE_PEER_REJECTED: wrong local UID.")
        return pid

    def send(self, value):
        data = json.dumps(value).encode()
        self.connection.sendall(struct.pack("<I", len(data)) + data)

    def _read(self, count):
        parts = bytearray()
        deadline = time.monotonic() + 3
        while len(parts) < count:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ControlError("INSTANCE_TIMEOUT: control frame timed out.")
            self.connection.settimeout(remaining)
            chunk = self.connection.recv(count - len(parts))
            if not chunk:
                raise ControlError("INSTANCE_DISCONNECTED: control peer disconnected.")
            parts.extend(chunk)
        return bytes(parts)

    def receive(self):
        size = struct.unpack("<I", self._read(4))[0]
        if not 0 < size <= 8192:
            raise ControlError("INSTANCE_PROTOCOL: invalid control frame size.")
        return decode(self._read(size))

    def close(self):
        self.connection.close()


class InstanceController:
    def __init__(self, data_dir, config, server, opener, lock):
        self.identity = _Identity()
        with self.identity.pin(os.getpid()) as (own, alive):
            alive()
            self.own = own
        self.endpoint = _endpoint(data_dir, own["owner"])
        self.config, self.server, self.opener, self.lock = config, server, opener, lock
        self.session = secrets.token_hex(32)
        self.stopping = threading.Event()
        self.listener = None
        self.thread = None

    def __enter__(self):
        if os.name == "nt":
            self.listener = NativePipe.create(self.own["owner"], win=self.identity.win,
                                              name=self.endpoint, timeout=0.2)
        else:
            self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                self.listener.bind(self.endpoint)
                self.listener.listen(4)
                self.listener.settimeout(0.2)
            except BaseException:
                self.listener.close()
                raise
        self.thread = threading.Thread(target=self._run, name="v3-page-control", daemon=True)
        self.thread.start()
        return self

    def _held(self):
        if self.stopping.is_set() or self.lock.file is None or self.lock.file.closed:
            raise ControlError("INSTANCE_STOPPING: controller no longer owns the application lock.")
        held, current = os.fstat(self.lock.file.fileno()), self.lock.path.stat()
        if (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino):
            raise ControlError("INSTANCE_LOCK_CHANGED: application lock was replaced.")

    def _exchange(self, connection):
        with self.identity.pin(connection.peer_pid()) as (peer, alive):
            _same_scope(peer, self.own)
            self._held()
            challenge = secrets.token_hex(32)
            connection.send({"protocol": 1, "server": self.own, "session": self.session, "challenge": challenge})
            request = connection.receive()
            fields = {"protocol", "operation", "config", "session", "challenge", "clientNonce", "client"}
            if (not isinstance(request, dict) or set(request) != fields or type(request["protocol"]) is not int
                    or request["protocol"] != 1 or request["operation"] not in ("open", "status")
                    or request["session"] != self.session or request["challenge"] != challenge
                    or not _token(request["clientNonce"]) or request["client"] != peer):
                raise ControlError("INSTANCE_PROTOCOL: only session-bound open/status is accepted.")
            alive()
            self._held()
            reply = {"protocol": 1, "session": self.session, "clientNonce": request["clientNonce"]}
            if request["config"] != self.config:
                reply["status"] = "INSTANCE_CONFIG_MISMATCH"
            elif request["operation"] == "status":
                reply["status"] = "running"
            else:
                # URL is generated here, never supplied by a caller/file. Reopen
                # does not call Service, replay plans, or rotate cookie/CSRF.
                try:
                    url = self.server.new_launch_url()
                    reply["status"] = "opened" if self.opener(url) else "INSTANCE_BROWSER_FAILED"
                except Exception:
                    reply["status"] = "INSTANCE_OPEN_FAILED"
            alive()
            connection.send(reply)
            # DisconnectNamedPipe discards unread bytes. A final receipt ensures
            # the client consumed the reply before this pipe accepts a new peer.
            if connection.receive() != {"received": request["clientNonce"]}:
                raise ControlError("INSTANCE_PROTOCOL: invalid response receipt.")

    def _run(self):
        while not self.stopping.is_set():
            connection = None
            try:
                if os.name == "nt":
                    self.listener.timeout = 0.2
                    self.listener.accept()
                    self.listener.timeout = 3
                    connection = self.listener
                else:
                    client, _ = self.listener.accept()
                    connection = _UnixConnection(client)
                self._exchange(connection)
            except Exception:
                # No request/exception text (which may contain secrets) in logs.
                # Failed peers get a disconnect; clients report a bounded error.
                pass
            finally:
                if os.name == "nt" and self.listener.connected:
                    from ctypes import wintypes as W
                    self.identity.win.kcall("DisconnectNamedPipe", W.BOOL, [W.HANDLE], self.listener.handle)
                    self.listener.connected = False
                elif os.name != "nt" and connection is not None:
                    connection.close()

    def __exit__(self, *_):
        self.stopping.set()
        # In-flight exchanges are bounded; finish before releasing InstanceLock.
        self.thread.join()
        self.listener.close()


def request_existing(data_dir, config, operation="open"):
    """Never create an adapter/Service, follow a URL, or retry an uncertain open."""
    if operation not in ("open", "status"):
        raise ControlError("INSTANCE_PROTOCOL: only open/status is accepted.")
    identity = _Identity()
    connection = None
    try:
        with identity.pin(os.getpid()) as (own, own_alive):
            name = _endpoint(data_dir, own["owner"])
            if os.name == "nt":
                connection = _connect_native(name, identity.win)
            else:
                client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                connection = _UnixConnection(client)
                client.connect(name)
            with identity.pin(connection.peer_pid()) as (peer, alive):
                _same_scope(peer, own)
                hello = connection.receive()
                if (not isinstance(hello, dict) or set(hello) != {"protocol", "server", "session", "challenge"}
                        or type(hello["protocol"]) is not int or hello["protocol"] != 1
                        or hello["server"] != peer or not _token(hello["session"]) or not _token(hello["challenge"])):
                    raise ControlError("INSTANCE_PROTOCOL: invalid server binding.")
                nonce = secrets.token_hex(32)
                alive()
                own_alive()
                connection.send({"protocol": 1, "operation": operation, "config": config,
                                 "session": hello["session"], "challenge": hello["challenge"],
                                 "clientNonce": nonce, "client": own})
                reply = connection.receive()
                alive()
                if (not isinstance(reply, dict) or set(reply) != {"protocol", "status", "session", "clientNonce"}
                        or type(reply["protocol"]) is not int or reply["protocol"] != 1
                        or reply["session"] != hello["session"] or reply["clientNonce"] != nonce):
                    raise ControlError("INSTANCE_PROTOCOL: reply is not bound to this request.")
                connection.send({"received": nonce})
                if reply["status"] != ("opened" if operation == "open" else "running"):
                    errors = {"INSTANCE_CONFIG_MISMATCH", "INSTANCE_BROWSER_FAILED", "INSTANCE_OPEN_FAILED"}
                    code = reply["status"] if isinstance(reply["status"], str) and reply["status"] in errors else "INSTANCE_PROTOCOL"
                    raise ControlError(code + ": existing instance did not confirm page open (no retry).")
                return reply["status"]
    except ControlError:
        raise
    except Exception as error:
        raise ControlError("INSTANCE_CONTROL_UNAVAILABLE: no verified live V3 controller; open was not confirmed (no retry).") from error
    finally:
        if connection is not None:
            connection.close()
