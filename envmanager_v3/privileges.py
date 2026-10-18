"""Short-lived, System-only elevation. Never elevate the HTTP service.

job_root is trusted service configuration, never an HTTP-supplied path. Source
mode is not a secure production capability and is refused. Releases must install
a pinned package in an administrator-protected location. An entry point dispatches
fixed --system-helper mode to system_helper.main (owned by the app entry point).
"""

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
import secrets
from dataclasses import dataclass
from contextvars import copy_context
import queue
import threading

from .environment import V3EnvironmentError


class ElevationError(V3EnvironmentError):
    pass


MAX_REQUEST_BYTES = 16 * 1024 * 1024
REQUEST_FILE = "request.json"
RESULT_FILE = "result.json"
JOURNAL_FILE = "progress.jsonl"


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False,
                      separators=(",", ":"), sort_keys=True).encode("ascii")


def _no_reparse(path):
    """Reject existing symlink/junction components, including the configured root."""
    path = Path(path).absolute()
    for component in (path, *path.parents):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if component.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ElevationError("UNSAFE_PATH", "Helper paths cannot traverse reparse points.")


def _dll(name):
    return ctypes.WinDLL(name, use_last_error=True)


def _api(dll, name, restype, *argtypes):
    function = getattr(dll, name)
    function.restype, function.argtypes = restype, argtypes
    return function


def _win_failure(code, message):
    return ElevationError(code, message, {"winerror": ctypes.get_last_error()})


@contextmanager
def _private_security():
    """Protected inheritable DACL: caller SID, SYSTEM and Administrators only."""
    kernel, advapi = _dll("kernel32"), _dll("advapi32")
    close = _api(kernel, "CloseHandle", wintypes.BOOL, wintypes.HANDLE)
    free = _api(kernel, "LocalFree", ctypes.c_void_p, ctypes.c_void_p)
    token, sid_text, descriptor = wintypes.HANDLE(), wintypes.LPWSTR(), ctypes.c_void_p()
    try:
        process = _api(kernel, "GetCurrentProcess", wintypes.HANDLE)()
        if not _api(advapi, "OpenProcessToken", wintypes.BOOL, wintypes.HANDLE,
                    wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))(process, 8, ctypes.byref(token)):
            raise _win_failure("ACL_FAILED", "Cannot query the current user token.")
        get_info = _api(advapi, "GetTokenInformation", wintypes.BOOL, wintypes.HANDLE,
                        ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                        ctypes.POINTER(wintypes.DWORD))
        size = wintypes.DWORD()
        get_info(token, 1, None, 0, ctypes.byref(size))  # TokenUser size probe
        if not size.value:
            raise _win_failure("ACL_FAILED", "Cannot query the current user SID.")
        buffer = ctypes.create_string_buffer(size.value)
        if not get_info(token, 1, buffer, size.value, ctypes.byref(size)):
            raise _win_failure("ACL_FAILED", "Cannot read the current user SID.")
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        if not _api(advapi, "ConvertSidToStringSidW", wintypes.BOOL, ctypes.c_void_p,
                    ctypes.POINTER(wintypes.LPWSTR))(sid, ctypes.byref(sid_text)):
            raise _win_failure("ACL_FAILED", "Cannot format the current user SID.")
        sddl = f"D:P(A;OICI;FA;;;{sid_text.value})(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"
        if not _api(advapi, "ConvertStringSecurityDescriptorToSecurityDescriptorW", wintypes.BOOL,
                    wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
                    ctypes.POINTER(wintypes.DWORD))(sddl, 1, ctypes.byref(descriptor), None):
            raise _win_failure("ACL_FAILED", "Cannot create a private helper DACL.")
        class SecurityAttributes(ctypes.Structure):
            _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p),
                        ("bInheritHandle", wintypes.BOOL)]
        attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor.value, False)
        yield attributes
    finally:
        if descriptor.value:
            free(descriptor)
        if sid_text:
            free(ctypes.cast(sid_text, ctypes.c_void_p))
        if token.value:
            close(token)


def _create_private_directory(path):
    if os.name == "nt":
        with _private_security() as attributes:
            create = _api(_dll("kernel32"), "CreateDirectoryW", wintypes.BOOL,
                          wintypes.LPCWSTR, ctypes.c_void_p)
            if not create(str(path), ctypes.byref(attributes)):
                raise _win_failure("ACL_FAILED", "Cannot create the private helper directory.")
    else:  # Allows isolated core/launcher tests on non-Windows hosts.
        path.mkdir(mode=0o700)


@contextmanager
def _locked_request(path):
    """Deny write/delete sharing throughout UAC wait and helper execution."""
    if os.name != "nt":
        # Non-Windows production elevation is rejected by _launch_helper.
        with Path(path).open("rb"):
            yield
        return
    kernel = _dll("kernel32")
    create = _api(kernel, "CreateFileW", wintypes.HANDLE, wintypes.LPCWSTR,
                  wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    handle = create(str(path), 0x80000000, 0x00000001, None, 3, 0x80, None)
    if handle in (None, ctypes.c_void_p(-1).value):
        raise _win_failure("REQUEST_LOCK_FAILED", "Cannot hold the immutable request file.")
    try:
        yield
    finally:
        _api(kernel, "CloseHandle", wintypes.BOOL, wintypes.HANDLE)(handle)


def _is_admin():
    return bool(_api(_dll("shell32"), "IsUserAnAdmin", wintypes.BOOL)())


def _helper_command(pipe, parent_pid, job_id, nonce):
    from .native_ipc import validate_pipe_name, validate_binding
    validate_pipe_name(pipe)
    validate_binding(nonce, job_id)
    if type(parent_pid) is not int or parent_pid <= 0:
        raise ElevationError("INVALID_REQUEST", "Invalid invocation parent.")
    if not getattr(sys, "frozen", False):
        raise ElevationError("SYSTEM_CAPABILITY_NOT_READY", "Source Python is not a trusted production package.")
    executable = str(Path(sys.executable).resolve(strict=True))
    return [executable, "--system-helper", "--pipe", pipe, "--parent", str(parent_pid),
            "--operation", job_id, "--nonce", nonce]


def _shell_execute(command, cwd, on_process=None):
    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong),
                    ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
                    ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int),
                    ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
                    ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE),
                    ("hProcess", wintypes.HANDLE)]
    shell, kernel = _dll("shell32"), _dll("kernel32")
    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x40 | 0x100  # SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
    info.lpVerb, info.lpFile = "runas", command[0]
    info.lpParameters = subprocess.list2cmdline(command[1:])
    info.lpDirectory, info.nShow = str(cwd), 0  # SW_HIDE; service stays unelevated.
    if not _api(shell, "ShellExecuteExW", wintypes.BOOL,
                ctypes.POINTER(ShellExecuteInfo))(ctypes.byref(info)):
        error = ctypes.get_last_error()
        if error == 1223:
            raise ElevationError("UAC_CANCELLED", "System authorization was cancelled.")
        raise ElevationError("ELEVATION_FAILED", "Cannot start the System helper.", {"winerror": error})
    if not info.hProcess:
        raise ElevationError("HELPER_UNCERTAIN", "Helper launch returned no process handle.")
    try:
        payload = on_process(info.hProcess) if on_process else None
        wait = _api(kernel, "WaitForSingleObject", wintypes.DWORD, wintypes.HANDLE, wintypes.DWORD)
        if wait(info.hProcess, 30000) != 0:
            raise ElevationError("HELPER_UNCERTAIN", "Cannot confirm helper termination.")
        exit_code = wintypes.DWORD()
        if not _api(kernel, "GetExitCodeProcess", wintypes.BOOL, wintypes.HANDLE,
                    ctypes.POINTER(wintypes.DWORD))(info.hProcess, ctypes.byref(exit_code)):
            raise ElevationError("HELPER_UNCERTAIN", "Cannot read helper exit status.")
        return (exit_code.value, payload) if on_process else exit_code.value
    finally:
        _api(kernel, "CloseHandle", wintypes.BOOL, wintypes.HANDLE)(info.hProcess)


@dataclass(frozen=True)
class _PipeOutcome:
    result: dict
    receipt: dict
    exit_code: int


def _verified_result(request, digest, result):
    from .system_helper import _same
    if (not isinstance(result, dict) or result.get("job_id") != request["job_id"]
            or result.get("digest") != digest or result.get("restore") is not request["restore"]
            or result.get("status") not in ("committed", "compensated", "rejected", "recovery_required")
            or not isinstance(result.get("targets"), list) or not isinstance(result.get("errors"), list)
            or len(result["targets"]) != len(request["plans"])):
        raise ElevationError("HELPER_UNCERTAIN", "Incomplete bound helper phase result.")
    for target, plan in zip(result["targets"], request["plans"]):
        if (not isinstance(target, dict) or any(target.get(k) != plan[k] for k in ("scope", "name", "before", "after"))
                or target.get("status") not in ("not_started", "unchanged", "committed", "compensated", "recovery_required")):
            raise ElevationError("HELPER_UNCERTAIN", "Helper phase differs from the frozen targets.")
        if result["status"] in ("committed", "compensated"):
            if result["status"] == "compensated" and target["status"] == "not_started":
                continue
            desired = plan["after"] if result["status"] == "committed" else plan["before"]
            if target["status"] not in (result["status"], "unchanged") or not _same(target.get("result"), desired):
                raise ElevationError("HELPER_UNCERTAIN", "Helper phase lacks exact verified readback.")
    if result["status"] == "committed" and (request["restore"] or result["errors"]):
        raise ElevationError("HELPER_UNCERTAIN", "Inconsistent helper phase success.")
    return result


def _launch_helper(request_path, digest, controller=None):
    from .native_ipc import (require_production, Win32, NativePipe, bind_parent,
                             drive_frozen_request, read_protected_receipt, verified_package, consume_review)
    from .system_helper import load_request
    require_production()  # No UAC/native APIs while System capability is not ready.
    request = load_request(request_path, digest, allow_type_change=True)
    win, nonce = Win32(), secrets.token_hex(32)
    with verified_package(win=win) as package, win.process(os.getpid()) as parent_handle:
        review = consume_review(request["plans"], package["rules_sha256"])
        parent = win.identity(parent_handle)
        if parent.image_hash != package["image_hash"]:
            raise ElevationError("PACKAGE_HASH", "Running parent image differs from the protected package.")
        if parent.elevated:
            raise ElevationError("PARENT_ELEVATED", "UI/HTTP/User writes must remain unelevated.")
        with NativePipe.create(parent.sid, win=win) as pipe:
            command = _helper_command(pipe.name, parent.pid, request["job_id"], nonce)
            def exchange(helper_handle):
                bind_parent(pipe, helper_handle, parent_handle, nonce, request["job_id"])
                compensated = False
                def verify_phase(message):
                    nonlocal compensated
                    phase = message["result"]
                    if phase.get("restore") is True:
                        compensated = True
                    _verified_result(dict(request, restore=request["restore"] or compensated), digest, phase)
                    durable = read_protected_receipt(parent.sid, request["job_id"], digest, win=win)
                    if (durable.get("event") != "phase" or durable.get("result") != phase
                            or durable.get("receiptSequence") != message["receiptSequence"]
                            or phase.get("review_digest") != hashlib.sha256(_json_bytes(review)).hexdigest()
                            or phase.get("package_digest") != package["package_digest"]):
                        raise ElevationError("HELPER_UNCERTAIN", "Phase result differs from its protected receipt.")
                terminal = drive_frozen_request(pipe, request, digest, nonce, review=review, package=package,
                                                controller=controller, verify_phase=verify_phase)
                receipt = read_protected_receipt(parent.sid, request["job_id"], digest, win=win)
                if (receipt.get("event") != "terminal" or receipt.get("digest") != digest
                        or receipt.get("receiptSequence") != terminal["receiptSequence"]
                        or terminal["result"].get("review_digest") != hashlib.sha256(_json_bytes(review)).hexdigest()
                        or terminal["result"].get("package_digest") != package["package_digest"]
                        or receipt.get("result") != terminal["result"]):
                    raise ElevationError("RECEIPT_INVALID", "Protected receipt differs from the bound pipe result.")
                return terminal["result"], receipt
            code, (result, receipt) = _shell_execute(command, Path(sys.executable).parent, exchange)
            return _PipeOutcome(result, receipt, code)


class PreparedSystemBatch:
    """One UAC, frozen review, zero-write prepare, live conditional compensation.

    Public methods accept no replacement request. finish/exit never mutate the
    environment. Context is captured on the factory thread, not inherited from
    a different HTTP thread. A lost session is terminal; it is never reconnected.
    """
    def __init__(self, plans, job_root, *, restore=False, job_id=None):
        from .system_helper import validate_request
        self.request = validate_request({"protocol": 1, "job_id": job_id or uuid.uuid4().hex,
                                         "restore": restore, "plans": plans}, allow_type_change=True)
        self.data = _json_bytes(self.request)
        self.digest = hashlib.sha256(self.data).hexdigest()
        self.root = Path(job_root).absolute()
        self.job_dir = self.root / self.request["job_id"]
        self.context = copy_context()
        self.commands, self.replies = queue.Queue(), queue.Queue()
        self.thread, self.state, self.last = None, "new", None
        self.ending, self.prepared_once = False, False

    def prepared(self):  # Internal pipe-controller callback, on the owner thread.
        if self.ending:
            return "abort"
        if self.prepared_once:
            return "commit_batch"  # Already-authorized same-plan compensation preflight.
        self.prepared_once = True
        self.replies.put({"status": "prepared", "job_id": self.request["job_id"], "digest": self.digest,
                          "restore": self.request["restore"], "targets": [], "errors": []})
        return self.commands.get(timeout=120)

    def phase(self, result):  # Protected-receipt verified before this callback.
        self.replies.put(result)
        if self.ending or not self.prepared_once:
            return "finish"
        return self.commands.get(timeout=120)

    def allow_mutation(self):
        return not self.ending and self.state not in ("unknown", "ended")

    def _run(self):
        try:
            _no_reparse(self.root)
            self.root.mkdir(parents=True, exist_ok=True)
            _create_private_directory(self.job_dir)
            path = self.job_dir / REQUEST_FILE
            with path.open("xb") as stream:
                stream.write(self.data); stream.flush(); os.fsync(stream.fileno())
            with _locked_request(path):
                outcome = _launch_helper(path, self.digest, self)
                if not isinstance(outcome, _PipeOutcome):
                    raise ElevationError("HELPER_UNCERTAIN", "Missing authenticated live helper outcome.")
                if outcome.exit_code != 0 and outcome.result.get("status") in ("committed", "compensated"):
                    raise ElevationError("HELPER_UNCERTAIN", "Live helper exited unsuccessfully.")
                self.replies.put(outcome.result)
        except BaseException as exc:
            error = exc if isinstance(exc, ElevationError) else ElevationError("HELPER_UNCERTAIN", "Live helper session failed.")
            self.replies.put(_launch_result(self.request, self.digest, self.job_dir, error))

    def _receive(self):
        try:
            result = self.replies.get(timeout=180)
        except queue.Empty:
            self.state = "unknown"
            raise ElevationError("HELPER_UNCERTAIN", "Live helper status is unknown; do not retry.")
        self.last = result
        if result.get("status") == "recovery_required" and result.get("channel_state") != "connected":
            self.state = "unknown"
        return result

    def prepare(self):
        if self.state != "new":
            raise ElevationError("IPC_REPLAY", "A System session can be prepared only once.")
        self.state = "starting"
        self.thread = threading.Thread(target=lambda: self.context.run(self._run), daemon=True,
                                       name="V3-System-session")
        self.thread.start()
        result = self._receive()
        if result["status"] == "prepared":
            self.state = "prepared"
        else:
            self.state = "unknown" if result["status"] == "recovery_required" else "ended"
        return result

    def commit(self):
        if self.state != "prepared":
            raise ElevationError("IPC_REPLAY", "Commit requires this live prepared session.")
        self.state = "committing"
        self.commands.put("commit_batch")
        result = self._receive()
        if self.state != "unknown":
            self.state = "committed" if result["status"] == "committed" else "failed"
        return result

    def conditional_compensate(self):
        if self.state != "committed":
            raise ElevationError("OWNER_UNKNOWN", "Conditional compensation requires the same live committed helper.")
        self.state = "compensating"
        self.commands.put("compensate_batch")
        result = self._receive()
        if self.state != "unknown":
            self.state = "compensated" if result["status"] == "compensated" else "failed"
        return result

    compensate = conditional_compensate

    def finish(self):
        if self.state in ("new", "ended"):
            self.state = "ended"
            return self.last
        if self.state == "unknown":
            # No command can authorize a mutation here. Pipe deadlines close the
            # helper; a queued finish is harmless if it still awaits a phase.
            self.ending = True
            self.commands.put("finish")
            return self.last
        self.ending = True
        self.commands.put("abort" if self.state == "prepared" else "finish")
        self._receive()
        if self.thread:
            self.thread.join(timeout=35)
            if self.thread.is_alive():
                self.state = "unknown"
                raise ElevationError("HELPER_UNCERTAIN", "Helper termination is unknown; receipt retained.")
        # An abort produces a rejected phase followed by a final protected
        # receipt/exit check. Never hide that final check's failure behind phase.
        while not self.replies.empty():
            self.last = self.replies.get_nowait()
        if self.last and self.last.get("launch_status") == "uncertain":
            self.state = "unknown"
            return self.last
        self.state = "ended"
        return self.last

    def __enter__(self):
        result = self.prepare()
        if result["status"] != "prepared":
            code = "UAC_CANCELLED" if result["status"] == "cancelled" else result["errors"][0]["code"]
            raise ElevationError(code, "System helper could not prepare.", result)
        return self

    def __exit__(self, *_):
        self.finish()


def prepare_system_batch(plans, job_root, *, restore=False, job_id=None):
    return PreparedSystemBatch(plans, job_root, restore=restore, job_id=job_id)


def _launch_result(request, digest, job_dir, error):
    cancelled = error.code == "UAC_CANCELLED"
    not_started = error.code in ("SYSTEM_CAPABILITY_NOT_READY", "PARENT_ELEVATED", "UNAVAILABLE", "REVIEW_REQUIRED",
                                 "INVALID_REVIEW", "REVIEW_MISMATCH", "RULES_MISMATCH")
    status = ("rejected" if not_started else "cancelled") if not request["restore"] and (not_started or cancelled) else "recovery_required"
    return {"status": status, "job_id": request["job_id"], "digest": digest,
            "restore": request["restore"], "targets": [], "errors": [error.to_dict()],
            "job_dir": str(job_dir), "launch_status": "not_started" if not_started else "cancelled" if cancelled else "uncertain",
            "channel_state": "not_started" if not_started or cancelled else "owner_unknown"}


def run_system_batch(plans, job_root, *, restore=False):
    """Execute a frozen System batch; never retry uncertain launches automatically.

    restore=True compares each original after and restores original before.
    Its UAC cancellation reports recovery_required, since the prior commit lives.
    Validation/pre-launch filesystem errors raise ElevationError. Job artifacts
    are retained for the service's durable recovery/reconciliation workflow.
    """
    from .system_helper import validate_request, _same, _unique_object, _invalid_constant
    request = validate_request({"protocol": 1, "job_id": uuid.uuid4().hex,
                                "restore": restore, "plans": plans}, allow_type_change=True)
    data = _json_bytes(request)
    if len(data) > MAX_REQUEST_BYTES:
        raise ElevationError("REQUEST_TOO_LARGE", "System batch exceeds request size limit.")
    root = Path(job_root).absolute()
    _no_reparse(root)
    job_dir = root / request["job_id"]
    digest = hashlib.sha256(data).hexdigest()
    if all(_same(plan["before"], plan["after"]) for plan in request["plans"]):
        # No permission request or helper artifact is needed for an exact no-op.
        return {"status": "compensated" if restore else "committed",
                "job_id": request["job_id"], "digest": digest, "restore": restore,
                "targets": [dict(plan, status="unchanged", result=plan["before"])
                            for plan in request["plans"]], "errors": []}
    try:
        root.mkdir(parents=True, exist_ok=True)
        _create_private_directory(job_dir)
        request_path = job_dir / REQUEST_FILE
        with request_path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        with _locked_request(request_path):
            try:
                outcome = _launch_helper(request_path, digest)
            except ElevationError as exc:
                return _launch_result(request, digest, job_dir, exc)
            except OSError as exc:
                return _launch_result(request, digest, job_dir,
                                      ElevationError("HELPER_UNCERTAIN", "Helper launch failed.",
                                                     {"errno": exc.errno}))
            try:
                # No read of user-writable result.json, even after helper exit.
                # Only the authenticated pipe + protected original receipt count.
                if not isinstance(outcome, _PipeOutcome):
                    raise ValueError("Missing authenticated pipe outcome")
                exit_code = outcome.exit_code
                receipt_data = _json_bytes(outcome.result)
                if len(receipt_data) > MAX_REQUEST_BYTES:
                    raise ValueError("Oversized helper receipt")
                result = json.loads(receipt_data, object_pairs_hook=_unique_object,
                                    parse_constant=_invalid_constant)
                if (outcome.receipt.get("event") != "terminal"
                        or outcome.receipt.get("digest") != digest
                        or outcome.receipt.get("job_id") != request["job_id"]
                        or type(outcome.receipt.get("receiptSequence")) is not int
                        or outcome.receipt["receiptSequence"] < 1
                        or outcome.receipt.get("result") != result):
                    raise ValueError("Missing protected terminal receipt")
                if (not isinstance(result, dict) or result.get("job_id") != request["job_id"]
                        or result.get("digest") != digest or result.get("restore") is not restore
                        or result.get("status") not in ("committed", "compensated", "rejected",
                                                       "recovery_required")
                        or not isinstance(result.get("targets"), list)
                        or not isinstance(result.get("errors"), list)):
                    raise ValueError("Invalid helper receipt")
                targets = result["targets"]
                if len(targets) != len(request["plans"]):
                    raise ValueError("Incomplete helper receipt")
                for target, plan in zip(targets, request["plans"]):
                    if (not isinstance(target, dict)
                            or any(target.get(key) != plan[key] for key in ("scope", "name", "before", "after"))
                            or target.get("status") not in ("not_started", "unchanged", "committed",
                                                            "compensated", "recovery_required")):
                        raise ValueError("Helper receipt differs from the frozen request")
                    if result["status"] in ("committed", "compensated"):
                        # A failure can stop before later targets are attempted;
                        # the helper compensates only its attempted writes.
                        if result["status"] == "compensated" and target["status"] == "not_started":
                            continue
                        desired = plan["after"] if result["status"] == "committed" else plan["before"]
                        if (target["status"] not in (result["status"], "unchanged")
                                or not _same(target.get("result"), desired)):
                            raise ValueError("Missing verified helper result")
                if (result["status"] == "committed" and (restore or result["errors"])):
                    raise ValueError("Inconsistent helper success")
                if exit_code != 0 and result["status"] in ("committed", "compensated"):
                    raise ValueError("Helper exited unsuccessfully")
                result["job_dir"] = str(job_dir)
                return result
            except (OSError, ValueError, TypeError, AttributeError, UnicodeError, RecursionError, ElevationError):
                return _launch_result(request, digest, job_dir,
                                      ElevationError("HELPER_UNCERTAIN", "Missing or invalid helper receipt.",
                                                     {"exit_code": getattr(outcome, "exit_code", None)}))
    except OSError as exc:
        raise ElevationError("JOB_IO", "Cannot prepare the private helper job.",
                             {"errno": exc.errno, "job_dir": str(job_dir)}) from exc
