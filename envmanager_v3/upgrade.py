"""V3 offline upgrade backup and bounded, continuously held instance lock.

No Registry, elevation, installer execution, migration, or V2 dependency.  A
successful backup is *not* permission to upgrade after its session is closed.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import stat
import time
import uuid
from urllib.parse import quote

from . import __version__
from .database import SCHEMA_VERSION, _inspect_schema


PROTOCOL_VERSION = 1
MAX_LEASE_SECONDS = 7200
TEMP_DIRS = frozenset({"tmp", "temp", ".tmp", ".temp", "__pycache__"})
TERMINAL_EXECUTIONS = ("committed", "compensated", "cancelled", "resolved_current")


class UpgradeBlocked(RuntimeError):
    """Fail closed: this operation does not authorize replacement or migration."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _stamp():
    return datetime.now(timezone.utc).isoformat()


def default_data_directory():
    """Resolve the *calling identity's* Windows known folder, never env/HKCU."""
    if os.name != "nt":
        raise UpgradeBlocked("DATA_DIR_REQUIRED", "Non-Windows helpers require explicit --data-dir.")
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    local_app_data = GUID.from_buffer_copy(uuid.UUID("f1b32785-6fba-4fcf-9d55-7b8e7f157091").bytes_le)
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    get_folder = shell.SHGetKnownFolderPath
    get_folder.argtypes = (ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE,
                          ctypes.POINTER(ctypes.c_void_p))
    get_folder.restype = ctypes.c_long
    ole = ctypes.WinDLL("ole32")
    ole.CoTaskMemFree.argtypes = (ctypes.c_void_p,)
    ole.CoTaskMemFree.restype = None
    value = ctypes.c_void_p()
    result = get_folder(ctypes.byref(local_app_data), 0, None, ctypes.byref(value))
    try:
        if result < 0 or not value.value:
            raise UpgradeBlocked("USER_FOLDER_UNAVAILABLE", f"SHGetKnownFolderPath failed: {result:#x}")
        return _local_path(Path(ctypes.wstring_at(value)) / "EnvManagerV3")
    finally:
        if value.value:
            ole.CoTaskMemFree(value)


def require_unelevated():
    """Read this process token; never elevate, impersonate, or infer a user."""
    if os.name != "nt":
        raise UpgradeBlocked("ORIGINAL_USER_REQUIRED", "Original-user token validation requires Windows.")
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    security.OpenProcessToken.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
    security.OpenProcessToken.restype = wintypes.BOOL
    security.GetTokenInformation.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                             wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
    security.GetTokenInformation.restype = wintypes.BOOL
    token = wintypes.HANDLE()
    if not security.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise UpgradeBlocked("ORIGINAL_USER_REQUIRED", "Cannot inspect original-user token.")
    try:
        elevated, needed = wintypes.DWORD(), wintypes.DWORD()
        if not security.GetTokenInformation(token, 20, ctypes.byref(elevated), ctypes.sizeof(elevated), ctypes.byref(needed)):
            raise UpgradeBlocked("ORIGINAL_USER_REQUIRED", "Cannot read TokenElevation.")
        if elevated.value:
            raise UpgradeBlocked("ORIGINAL_USER_REQUIRED", "Start upgrade helper as the original unelevated user; administrator fallback is forbidden.")
    finally:
        kernel.CloseHandle(token)


class _ParentProcess:
    """Keep the exact Windows process object, avoiding PID-reuse ambiguity."""
    def __init__(self, pid):
        if os.name != "nt" or pid <= 0:
            raise UpgradeBlocked("PARENT_UNAVAILABLE", "--parent-pid requires a valid Windows process.")
        import ctypes
        from ctypes import wintypes
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        self.kernel.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.handle = self.kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only
        if not self.handle:
            raise UpgradeBlocked("PARENT_UNAVAILABLE", "Cannot monitor installer process; upgrade blocked.")

    def check(self):
        state = self.kernel.WaitForSingleObject(self.handle, 0)
        if state == 0:
            raise UpgradeBlocked("PARENT_EXITED", "Installer process exited; upgrade session cancelled.")
        if state != 258:  # WAIT_TIMEOUT means the process is still alive
            raise UpgradeBlocked("PARENT_UNAVAILABLE", "Installer liveness is unknown; upgrade blocked.")

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _io_path(path):
    """Use Windows extended paths only at I/O boundaries, not in receipts."""
    path = Path(path)
    if os.name == "nt" and not str(path).startswith("\\\\?\\"):
        return Path("\\\\?\\" + os.path.abspath(path))
    return path


def _sync_dir(path):
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _replace(source, destination):
    if os.name == "nt":
        # Windows cannot fsync a directory with Python's os.open.  Flush file
        # contents first, then publish with a write-through rename.
        import ctypes
        from ctypes import wintypes
        move = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
        move.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD)
        move.restype = wintypes.BOOL
        if not move(str(_io_path(source)), str(_io_path(destination)), 0x1 | 0x8):
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        os.replace(source, destination)
    _sync_dir(destination.parent)


def _write_json(path, value):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with _io_path(temporary).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=True, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    _replace(temporary, path)


def _read_json(path, *, control=True):
    path = _io_path(path)
    # Control messages are small and local, not an unbounded input stream.
    if control and path.stat().st_size > 1024 * 1024:
        raise UpgradeBlocked("INVALID_CONTROL", "Control JSON exceeds 1 MiB.")
    return json.loads(path.read_text(encoding="utf-8"))


def _local_path(value):
    path = Path(value).absolute()
    if str(path).startswith("\\\\"):
        raise UpgradeBlocked("INVALID_PATH", "Upgrade paths must be local.")
    # Refuse junctions/symlinks in every existing component, before resolve()
    # could hide them. Never traverse somebody else's data through a reparse.
    for part in (path, *path.parents):
        disk_part = _io_path(part)
        if disk_part.exists() or disk_part.is_symlink():
            info = disk_part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise UpgradeBlocked("REPARSE_POINT", f"Reparse point: {part}")
    resolved = _io_path(path).resolve()
    # Keep the public path convention for receipts and containment checks.
    return Path(str(resolved).removeprefix("\\\\?\\")) if os.name == "nt" else resolved


def _outside(path, source):
    if path == source or path.is_relative_to(source) or source.is_relative_to(path):
        raise UpgradeBlocked("INVALID_PATH", "Backup/session directory must be disjoint from data directory.")


def _hash(path, check=lambda: None):
    digest = hashlib.sha256()
    with _io_path(path).open("rb") as stream:
        while block := stream.read(1024 * 1024):
            check()
            digest.update(block)
    return digest.hexdigest()


def _inventory(root, check):
    """Hash durable data, include empty directories, ignore only named transients."""
    root = _io_path(root)
    files, directories, excluded = {}, [], []

    def visit(directory):
        for path in sorted(directory.iterdir()):
            check()
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise UpgradeBlocked("REPARSE_POINT", f"Data contains a reparse point: {relative}")
            if stat.S_ISDIR(info.st_mode):
                if path.name.lower() in TEMP_DIRS:
                    excluded.append(relative + "/")
                else:
                    directories.append(relative)
                    visit(path)
            elif stat.S_ISREG(info.st_mode):
                if path.name.lower().endswith(".lock") or path.name.lower().endswith("-shm"):
                    excluded.append(relative)
                    continue
                identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                digest = _hash(path, check)
                after = path.stat()
                if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    raise UpgradeBlocked("SOURCE_CHANGED", f"Changed while hashing: {relative}")
                files[relative] = {"sha256": digest, "size": info.st_size, "identity": list(identity)}
            else:
                raise UpgradeBlocked("UNSUPPORTED_FILE", f"Not a regular data file: {relative}")

    root_info = root.stat()
    visit(root)
    return {"files": files, "directories": directories, "excluded": excluded,
            "root_identity": [root_info.st_dev, root_info.st_ino]}


def _copy(source, destination, check):
    with _io_path(source).open("rb") as reader, _io_path(destination).open("xb") as writer:
        while block := reader.read(1024 * 1024):
            check()
            writer.write(block)
        writer.flush()
        os.fsync(writer.fileno())


def _sqlite_uri(path, *, readonly=False):
    if os.name == "nt":
        # as_uri() treats the extended prefix as an authority. Encode the
        # native filename; SQLite decodes it before the Windows VFS opens it.
        uri = "file:" + quote(str(_io_path(path)), safe="/:")
        return uri + ("?mode=ro&" if readonly else "?") + "vfs=win32-longpath"
    return path.as_uri() + ("?mode=ro" if readonly else "")


def _sqlite_backup(shadow, destination, check, *, product_database=False):
    # Only the private shadow is opened with SQLite. This includes committed
    # WAL without creating/updating even a transient -shm in the user's tree.
    with closing(sqlite3.connect(_sqlite_uri(shadow, readonly=True), uri=True, timeout=0)) as source:
        source.set_progress_handler(lambda: (check(), 0)[1], 10000)
        version = source.execute("PRAGMA user_version").fetchone()[0]
        if product_database:
            _, has_schema = _inspect_schema(source)
            if has_schema:
                placeholders = ",".join("?" for _ in TERMINAL_EXECUTIONS)
                pending = source.execute(
                    f"SELECT id,status FROM executions WHERE status NOT IN ({placeholders})",
                    TERMINAL_EXECUTIONS).fetchall()
                if pending:
                    raise UpgradeBlocked("UNFINISHED_EXECUTION", f"Resolve executions before upgrade: {pending!r}")
        with closing(sqlite3.connect(_sqlite_uri(destination), uri=True)) as backup:
            backup.set_progress_handler(lambda: (check(), 0)[1], 10000)
            backup.execute("PRAGMA synchronous=FULL")
            source.backup(backup, pages=128, progress=lambda *_: check(), sleep=0.01)
            if backup.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise UpgradeBlocked("SQLITE_INTEGRITY", f"Invalid SQLite backup: {destination.name}")
            if backup.execute("PRAGMA user_version").fetchone()[0] != version:
                raise UpgradeBlocked("SQLITE_VERSION", "SQLite backup changed its schema version.")
    with _io_path(destination).open("r+b") as stream:
        os.fsync(stream.fileno())
    return {"user_version": version, "method": "sqlite3.Connection.backup",
            "committed_wal_included": True, "product_database": product_database}


def verify_backup(backup_path, manifest_sha256=None, *, check=lambda: None):
    """Verify the published manifest and every restore file; never restore data."""
    root = _local_path(backup_path)
    manifest_path = root / "manifest.json"
    if manifest_sha256 is not None and _hash(manifest_path, check) != manifest_sha256:
        raise UpgradeBlocked("BACKUP_CHANGED", "Backup manifest hash changed.")
    manifest = _read_json(manifest_path, control=False)
    if manifest.get("result") != "complete" or manifest.get("protocol_version") != PROTOCOL_VERSION:
        raise UpgradeBlocked("BACKUP_INCOMPLETE", "No completed backup manifest.")
    actual = _inventory(root / "data", check)
    expected = manifest["files"]
    if set(actual["files"]) != set(expected) or actual["directories"] != manifest["directories"] or actual["excluded"]:
        raise UpgradeBlocked("BACKUP_CHANGED", "Backup file/directory inventory changed.")
    for relative, info in expected.items():
        if any(actual["files"][relative][key] != info[key] for key in ("size", "sha256")):
            raise UpgradeBlocked("BACKUP_CHANGED", f"Backup content changed: {relative}")
    return manifest


class UpgradeSession:
    """Owns the existing application InstanceLock until finish/close/deadline."""

    def __init__(self, data_dir, target_version, lease_seconds, allow_missing=False, cancellation_check=None):
        if not isinstance(target_version, str) or not target_version.strip() or len(target_version) > 128:
            raise UpgradeBlocked("INVALID_VERSION", "A nonempty target version (max 128 characters) is required.")
        if not math.isfinite(lease_seconds) or not 0 < lease_seconds <= MAX_LEASE_SECONDS:
            raise UpgradeBlocked("INVALID_LEASE", f"Lease must be >0 and <= {MAX_LEASE_SECONDS} seconds.")
        self.data_dir = _local_path(data_dir)
        self.source_data_existed = _io_path(self.data_dir).exists()
        if (self.source_data_existed and not _io_path(self.data_dir).is_dir()) or (not self.source_data_existed and not allow_missing):
            raise UpgradeBlocked("NO_DATA_DIRECTORY", "Existing user data directory required; do not infer an administrator's profile.")
        self.target_version = target_version
        self.backup_id = uuid.uuid4().hex
        self.backup_path = None
        self.manifest_sha256 = None
        self.source_inventory = None
        self.deadline = time.monotonic() + lease_seconds
        self.expires_at_unix = time.time() + lease_seconds
        self._lock = None
        self._blocked = None
        self.closed = False
        self._cancellation_check = cancellation_check or (lambda: None)

    def _time_check(self):
        self._cancellation_check()
        if time.monotonic() >= self.deadline:
            raise UpgradeBlocked("LEASE_EXPIRED", "Upgrade lease expired; replacement is forbidden.")

    def _acquire(self):
        # Reuse production locking, rather than inventing a second namespace.
        # The lazy import also permits __main__ to dispatch to this module.
        from .__main__ import InstanceLock
        _local_path(self.data_dir / "instance.lock")
        lock = InstanceLock(_io_path(self.data_dir / "instance.lock"))
        try:
            lock.__enter__()
        except RuntimeError as error:
            raise UpgradeBlocked("INSTANCE_ACTIVE", str(error)) from error
        self._lock = lock

    def _lock_check(self):
        held = os.fstat(self._lock.file.fileno())
        current = _io_path(self.data_dir / "instance.lock").stat()
        if (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino):
            raise UpgradeBlocked("LOCK_REPLACED", "Application instance lock file was replaced.")

    def receipt(self, result="ready"):
        return {"protocol_version": PROTOCOL_VERSION, "result": result,
                "backup_id": self.backup_id, "backup_path": str(self.backup_path) if self.backup_path else None,
                "manifest_sha256": self.manifest_sha256, "data_dir": str(self.data_dir),
                "target_version": self.target_version, "helper_pid": os.getpid(),
                "source_data_existed": self.source_data_existed,
                "lease_expires_at_unix": self.expires_at_unix}

    def check(self):
        if self.closed:
            raise UpgradeBlocked("SESSION_CLOSED", "No upgrade lock is held by this session.")
        if self._blocked:
            raise self._blocked
        try:
            self._time_check()
            self._lock_check()
            if _inventory(self.data_dir, self._time_check) != self.source_inventory:
                raise UpgradeBlocked("SOURCE_CHANGED", "User data changed outside the application lock.")
            verify_backup(self.backup_path, self.manifest_sha256, check=self._time_check)
            self._time_check()
        except Exception as error:
            self._blocked = error if isinstance(error, UpgradeBlocked) else UpgradeBlocked("CHECK_FAILED", str(error))
            raise self._blocked
        return self.receipt()

    def finish(self, outcome="installed"):
        if outcome not in ("installed", "cancelled"):
            raise UpgradeBlocked("INVALID_OUTCOME", "finish outcome must be installed or cancelled.")
        try:
            self.check()
            receipt = self.receipt("finished")
            receipt["outcome"] = outcome
            _write_json(self.backup_path / "finish.json", {**receipt, "finished_at": _stamp()})
            return receipt
        finally:
            self.close()

    def close(self):
        if self._lock is not None:
            self._lock.__exit__(None, None, None)
            self._lock = None
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def prepare_upgrade(data_dir, target_version, *, backup_root=None, lease_seconds=1800,
                    allow_missing=False, cancellation_check=None, fault_injector=None):
    """Make a complete backup and RETURN A STILL-LOCKED UpgradeSession.

    Caller must keep the session alive across replacement, call check just
    before replacement, finish afterwards, and close on every failure.  For
    separate installer processes use CLI hold/status/finish instead. The only
    source-tree coordination write is InstanceLock's existing instance.lock.
    fault_injector(stage, session) is for deterministic I/O failure testing.
    """
    session = UpgradeSession(data_dir, target_version, lease_seconds, allow_missing, cancellation_check)
    root = _local_path(backup_root or session.data_dir.with_name(session.data_dir.name + ".upgrade-backups"))
    _outside(root, session.data_dir)
    staging = root / (".incomplete-" + session.backup_id)
    inject = fault_injector or (lambda *_: None)
    try:
        session._acquire()
        session._time_check()
        before = _inventory(session.data_dir, session._time_check)
        _io_path(root).mkdir(parents=True, exist_ok=True)
        _io_path(staging).mkdir()
        shadow, payload = staging / "shadow", staging / "data"
        _io_path(shadow).mkdir()
        _io_path(payload).mkdir()
        for relative in before["directories"]:
            _io_path(shadow / relative).mkdir(parents=True, exist_ok=True)
            _io_path(payload / relative).mkdir(parents=True, exist_ok=True)
        inject("before_copy", session)
        for relative, info in before["files"].items():
            _copy(session.data_dir / relative, shadow / relative, session._time_check)
            if _hash(shadow / relative, session._time_check) != info["sha256"]:
                raise UpgradeBlocked("SOURCE_CHANGED", f"Changed while copying: {relative}")
        inject("after_copy", session)
        databases = set()
        for relative in before["files"]:
            path = shadow / relative
            with _io_path(path).open("rb") as stream:
                magic = stream.read(16)
            if magic == b"SQLite format 3\x00" or path.suffix.lower() in (".sqlite3", ".sqlite", ".db"):
                databases.add(relative)
        if "state.sqlite3" in before["files"]:
            databases.add("state.sqlite3")
        sidecars = {name + suffix for name in databases for suffix in ("-wal", "-shm", "-journal")}
        schemas = {}
        for relative in before["files"]:
            if relative in databases:
                inject("before_sqlite_backup", session)
                schemas[relative] = _sqlite_backup(shadow / relative, payload / relative,
                                                   session._time_check, product_database=relative == "state.sqlite3")
            elif relative not in sidecars:
                _copy(shadow / relative, payload / relative, session._time_check)
        inject("after_sqlite_backup", session)
        if _inventory(session.data_dir, session._time_check) != before:
            raise UpgradeBlocked("SOURCE_CHANGED", "Data changed during backup; upgrade blocked.")
        output = _inventory(payload, session._time_check)
        manifest = {"protocol_version": PROTOCOL_VERSION, "result": "complete",
                    "backup_id": session.backup_id, "created_at": _stamp(),
                    "source_data_dir": str(session.data_dir), "source_product_version": __version__,
                    "source_data_existed": session.source_data_existed,
                    "source_version_note": "helper build version; historical installed version is not inferred",
                    "target_version": target_version, "target_schema_supported": SCHEMA_VERSION,
                    "source_inventory": before, "sqlite": schemas,
                    "files": {name: {k: v[k] for k in ("size", "sha256")} for name, v in output["files"].items()},
                    "directories": output["directories"], "excluded_transients": before["excluded"],
                    "normalized_sqlite_sidecars": sorted(sidecars & set(before["files"])),
                    "restore_note": "Stop all instances; preserve current data; restore data/ as a whole with no stale SQLite sidecars. No automatic restore."}
        inject("before_manifest", session)
        for directory in reversed([payload, *[payload / p for p in output["directories"]]]):
            _sync_dir(directory)
        _write_json(staging / "manifest.json", manifest)
        # Retain the shadow as provenance only. Restoration uses data/, never
        # shadow/: it may contain uncommitted WAL frames as well as committed.
        inject("before_publish", session)
        final = root / session.backup_id
        _replace(staging, final)
        session.backup_path = final
        session.manifest_sha256 = _hash(final / "manifest.json", session._time_check)
        session.source_inventory = before
        session.check()
        return session
    except BaseException as error:
        session.close()
        artifact = session.backup_path or staging
        if _io_path(artifact).is_dir():
            try:
                _write_json(artifact / "failure.json", _failure(error))
            except OSError:
                pass
        if isinstance(error, (KeyboardInterrupt, SystemExit, UpgradeBlocked)):
            raise
        raise UpgradeBlocked("BACKUP_FAILED", str(error)) from error


def _failure(error, session=None):
    return {"protocol_version": PROTOCOL_VERSION, "result": "blocked",
            "backup_id": session.backup_id if session else None,
            "backup_path": str(session.backup_path) if session and session.backup_path else None,
            "error_code": getattr(error, "code", "UPGRADE_FAILED"), "message": str(error)}


def _serve(session, control):
    """Foreground helper. Requests and responses use fresh, per-call nonces."""
    ready = {**session.receipt(), "session_dir": str(control)}
    _write_json(control / "ready.json", ready)
    print(json.dumps(ready, ensure_ascii=True), flush=True)
    next_check = 0
    try:
        while True:
            session._time_check()
            if time.monotonic() >= next_check:
                session.check()
                next_check = time.monotonic() + 1
            for request_path in sorted(control.glob("*.request.json")):
                request = _read_json(request_path)
                nonce = request_path.name.removesuffix(".request.json")
                if request.get("request_id") != nonce or request.get("backup_id") != session.backup_id:
                    raise UpgradeBlocked("INVALID_CONTROL", "Request does not match this backup session.")
                response_path = control / (nonce + ".response.json")
                if response_path.exists():
                    continue
                if request.get("action") == "status":
                    reply = session.check()
                elif request.get("action") == "finish":
                    reply = session.finish(request.get("outcome"))
                else:
                    raise UpgradeBlocked("INVALID_CONTROL", "Unknown helper action.")
                _write_json(response_path, {**reply, "request_id": nonce})
                if session.closed:
                    _write_json(control / "terminal.json", reply)
                    return 0
            time.sleep(0.05)
    except Exception as error:
        _write_json(control / "terminal.json", _failure(error, session))
        raise
    finally:
        session.close()


def request_session(session_dir, action="status", *, outcome=None, timeout=10):
    """Require a fresh answer from the still-lock-owning helper, not ready.json."""
    if action not in ("status", "finish") or not math.isfinite(timeout) or not 0 < timeout <= 60:
        raise UpgradeBlocked("INVALID_CONTROL", "Invalid request action or timeout.")
    control = _local_path(session_dir)
    ready = _read_json(control / "ready.json")
    nonce = uuid.uuid4().hex
    request = {"action": action, "outcome": outcome, "request_id": nonce, "backup_id": ready["backup_id"]}
    _write_json(control / (nonce + ".request.json"), request)
    deadline = time.monotonic() + timeout
    response_path = control / (nonce + ".response.json")
    while time.monotonic() < deadline:
        if response_path.exists():
            reply = _read_json(response_path)
            if reply.get("request_id") != nonce or reply.get("backup_id") != ready["backup_id"]:
                raise UpgradeBlocked("INVALID_CONTROL", "Mismatched helper response.")
            if reply.get("result") != ("ready" if action == "status" else "finished"):
                raise UpgradeBlocked("HELPER_BLOCKED", "Helper refused this request.")
            if action == "status" and time.time() >= reply["lease_expires_at_unix"]:
                raise UpgradeBlocked("LEASE_EXPIRED", "Helper response expired.")
            return reply
        if (control / "terminal.json").exists():
            terminal = _read_json(control / "terminal.json")
            raise UpgradeBlocked(terminal.get("error_code", "SESSION_CLOSED"), terminal.get("message", "Helper session ended."))
        time.sleep(0.05)
    raise UpgradeBlocked("HELPER_UNRESPONSIVE", "No live lock-owner acknowledgement; do not replace files.")


def main(argv=None):
    """Standalone PyInstaller entry: no product server or launch ticket."""
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check-user", allow_abbrev=False,
                        help="Check the current user token without creating upgrade state")
    for name in ("hold", "prepare"):
        command = commands.add_parser(name, allow_abbrev=False)
        command.add_argument("--data-dir", type=Path)
        command.add_argument("--target-version", required=True)
        command.add_argument("--backup-root", type=Path)
        command.add_argument("--session-dir", type=Path, required=True)
        command.add_argument("--lease-seconds", type=float, default=1800)
        command.add_argument("--require-unelevated", action="store_true")
        command.add_argument("--parent-pid", type=int)
        command.add_argument("--prepare-timeout", type=float, default=120)
    for name in ("status", "finish"):
        command = commands.add_parser(name, allow_abbrev=False)
        command.add_argument("--session-dir", type=Path, required=True)
        command.add_argument("--timeout", type=float, default=10)
        if name == "finish":
            command.add_argument("--outcome", choices=("installed", "cancelled"), required=True)
    args = parser.parse_args(argv)
    control = None
    session = None
    parent = None
    try:
        if args.command == "check-user":
            require_unelevated()
            print(json.dumps({"protocol_version": PROTOCOL_VERSION, "result": "passed"}), flush=True)
            return 0
        if args.command in ("hold", "prepare"):
            if args.require_unelevated:
                require_unelevated()
            if not math.isfinite(args.prepare_timeout) or not 0 < args.prepare_timeout <= MAX_LEASE_SECONDS:
                raise UpgradeBlocked("INVALID_TIMEOUT", "Prepare timeout must be >0 and <=7200 seconds.")
            if args.parent_pid is not None:
                parent = _ParentProcess(args.parent_pid)
                parent.check()
            prepare_deadline = time.monotonic() + args.prepare_timeout
            data_dir = _local_path(args.data_dir) if args.data_dir is not None else default_data_directory()
            candidate = _local_path(args.session_dir)
            _outside(candidate, data_dir)
            root = _local_path(args.backup_root or data_dir.with_name(data_dir.name + ".upgrade-backups"))
            _outside(candidate, root)
            # Exclusive new directory prevents stale-ready reuse and two helpers
            # accidentally sharing one mailbox. Parent ACL is the user's ACL.
            candidate.mkdir(mode=0o700, parents=False, exist_ok=False)
            control = candidate

            def progress(stage, current_session):
                # Diagnostic only: installers may discover preparation here,
                # but only ready + a fresh status acknowledgement can authorize.
                _write_json(control / "progress.json", {
                    "protocol_version": PROTOCOL_VERSION, "result": "preparing",
                    "stage": stage, "helper_pid": os.getpid(), "updated_at": _stamp(),
                    "backup_id": current_session.backup_id if current_session else None})

            progress("starting_backup", None)

            def cancellation_check():
                if (control / "cancel.json").exists():
                    raise UpgradeBlocked("CANCELLED", "Installer cancelled the upgrade session.")
                if parent is not None:
                    parent.check()
                if prepare_deadline is not None and time.monotonic() >= prepare_deadline:
                    raise UpgradeBlocked("PREPARE_TIMEOUT", "Backup preparation timed out; replacement forbidden.")

            with prepare_upgrade(data_dir, args.target_version, backup_root=root,
                                 lease_seconds=args.lease_seconds, allow_missing=True,
                                 cancellation_check=cancellation_check,
                                 fault_injector=progress) as session:
                prepare_deadline = None
                return _serve(session, control)
        result = request_session(args.session_dir, args.command, outcome=getattr(args, "outcome", None), timeout=args.timeout)
        print(json.dumps(result, ensure_ascii=True), flush=True)
        return 0
    except Exception as error:
        failure = _failure(error, session)
        if control is not None and control.is_dir() and not (control / "ready.json").exists():
            try:
                _write_json(control / "terminal.json", failure)
            except OSError:
                pass
        print(json.dumps(failure, ensure_ascii=True), flush=True)
        return 1
    finally:
        if parent is not None:
            parent.close()


if __name__ == "__main__":
    raise SystemExit(main())
