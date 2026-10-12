"""Explicit, elevated Windows acceptance; never imported by host-registry tests.

Run from an administrator Python process after a human approves UAC:
  python tools/manual_windows_acceptance.py --work-dir ABS --exe ABS --test-id GUID

The controller waits for ready_for_gui.json, then launches the installed executable
from its NON-elevated context with the supplied --config and --state-dir. The
product's own UAC flow is exercised. This tool does not launch/install/elevate.
All registry accesses are limited to preflighted GUID names in real Environment
keys. Unknown/external values are retained and reported, never blindly deleted.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from contextlib import contextmanager
from dataclasses import asdict
import json
import os
from pathlib import Path
import re
import sys
import time
import traceback
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from envmanager.config import parse_config
from envmanager.elements import CleanupSettings
from envmanager.models import ConflictError, RegistryState
from envmanager.persistence import StateRepository
from envmanager.privileges import is_administrator
from envmanager.removal import RemovalRequest, RemovalScope
from envmanager.service import EnvironmentService
from envmanager.store import REG_EXPAND_SZ, REG_SZ, WindowsStore

SUFFIXES = ("DUAL", "PARTIAL", "RAW", "EMPTY", "SPECIAL", "RESTORE", "ROLLBACK_A", "ROLLBACK_B")


def canonical_test_id(value: str) -> str:
    """Require a full GUID; canonical hex is safe in TOML and variable names."""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{32}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", value):
        raise argparse.ArgumentTypeError("--test-id must be a full GUID")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise argparse.ArgumentTypeError("--test-id must be a full GUID") from exc
    if len(value) not in (32, 36) or parsed.int == 0:
        raise argparse.ArgumentTypeError("--test-id must be a nonzero full GUID")
    return parsed.hex.upper()


def absolute_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise argparse.ArgumentTypeError("path must be absolute")
    return path.resolve()


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", required=True, type=absolute_path)
    parser.add_argument("--exe", required=True, type=absolute_path)
    parser.add_argument("--test-id", required=True, type=canonical_test_id)
    parser.add_argument("--gui-timeout", type=int, default=300,
                        help="explicit GUI wait bound, seconds (1..900, default 300)")
    args = parser.parse_args(argv)
    if not 1 <= args.gui_timeout <= 900:
        parser.error("--gui-timeout must be in 1..900")
    if not args.exe.is_file() or args.exe.suffix.lower() != ".exe":
        parser.error("--exe must name an existing installed .exe")
    if args.work_dir == Path(args.work_dir.anchor) or args.work_dir == args.exe.parent:
        parser.error("--work-dir must be a dedicated acceptance directory")
    # A reused directory could carry old readiness, state or ownership records.
    if args.work_dir.exists() and any(args.work_dir.iterdir()):
        parser.error("--work-dir must be new or empty")
    return args


def names_for(test_id):
    prefix = "ENV_MANAGER_VNEXT_QA_" + canonical_test_id(test_id) + "_"
    return {suffix: prefix + suffix for suffix in SUFFIXES}


def durable_json(path: Path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class Evidence:
    def __init__(self, directory):
        self.directory = directory
        self.phases = []

    def log(self, event, **data):
        record = {"time": time.time(), "event": event, **data}
        with (self.directory / "registry-journal.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def phase(self, name, status="passed", **data):
        result = {"name": name, "status": status, "time": time.time(), **data}
        self.phases.append(result)
        self.log("phase", **result)
        durable_json(self.directory / "phases.json", self.phases)
        print(f"{status.upper()}: {name}", flush=True)


class ScopedAcceptanceStore:
    """Small allowlist and last-confirmed state guard around the real adapter."""
    def __init__(self, backend, names, evidence, *, scopes=("User", "System")):
        self.backend = backend
        if not scopes or set(scopes) - {"User", "System"}:
            raise ValueError("invalid acceptance scopes")
        self.allowed = frozenset((scope, name) for scope in scopes for name in names.values())
        self.evidence = evidence
        self.expected = {}
        self.fail_once = None

    def target(self, scope, name):
        if (scope, name) not in self.allowed:
            raise ValueError("acceptance refuses access outside its exact test-name allowlist")
        return scope, name

    def read(self, scope, name):
        self.target(scope, name)
        return self.backend.read(scope, name)

    def preflight(self):
        states = [self.read(*target) for target in sorted(self.allowed)]
        collisions = [state.to_dict() for state in states if state.exists]
        self.evidence.log("preflight", states=[state.to_dict() for state in states])
        if collisions:
            raise ConflictError("test-variable collision; no writes permitted: " + json.dumps(collisions))
        self.expected = {(state.scope, state.name): state for state in states}

    def check_permission(self, scope):
        self.backend.check_permission(scope)

    def put(self, state):
        target = self.target(state.scope, state.name)
        current = self.read(*target)
        expected = self.expected.get(target)
        if expected is None or not current.same_content(expected):
            self.evidence.log("external_change_refused", actual=current.to_dict(),
                              expected=expected.to_dict() if expected else None)
            raise ConflictError("test target changed outside this runner; preserving it")
        self.evidence.log("mutation_intent", before=current.to_dict(), after=state.to_dict())
        try:
            if state.exists:
                self.backend.write(state)
            else:
                self.backend.delete(state.scope, state.name)
        finally:
            actual = self.read(*target)
            # Only an exact requested result becomes cleanup-owned. An unknown
            # partial/externally modified result must never become owned.
            if actual.same_content(state):
                self.expected[target] = actual
            self.evidence.log("mutation_observed", actual=actual.to_dict(),
                              verified=actual.same_content(state))
        if not actual.same_content(state):
            raise RuntimeError("raw registry write verification failed")
        if self.fail_once == target:
            self.fail_once = None
            self.evidence.log("injected_failure_after_verified_write", target=list(target))
            raise OSError("acceptance injection after real write")

    def write(self, state):
        self.put(state)

    def delete(self, scope, name):
        self.put(RegistryState(name, scope, False))

    def notify(self):
        self.backend.notify()

    def cleanup(self):
        leftovers = []
        changed = False
        for scope, name in sorted(self.allowed):
            try:
                actual = self.read(scope, name)
                expected = self.expected.get((scope, name))
                if not actual.exists:
                    continue
                if expected is None or not actual.same_content(expected):
                    leftovers.append({"state": actual.to_dict(), "reason": "external/unknown state retained"})
                    continue
                self.delete(scope, name)
                changed = True
            except Exception as exc:
                leftovers.append({"scope": scope, "name": name, "reason": str(exc)})
        if changed:
            try:
                self.notify()
            except Exception as exc:
                self.evidence.log("cleanup_notification_error", error=str(exc))
        self.evidence.log("cleanup", residuals=leftovers)
        durable_json(self.evidence.directory / "residuals.json", leftovers)
        return leftovers


def record(value, scope, *, special=False):
    fields = {"default_value": value, "scope": scope, "mode": "replace", "editable": True,
              "special": special}
    return "{ " + ", ".join(f"{key} = {json.dumps(value)}" for key, value in fields.items()) + " }"


def acceptance_config(names):
    values = {"DUAL": "A;B", "PARTIAL": "A;B", "RAW": "B", "EMPTY": "",
              "SPECIAL": "  A;B\t  ", "RESTORE": "Z", "ROLLBACK_A": "NEW_A", "ROLLBACK_B": "NEW_B"}
    lines = []
    for scope in ("User", "System"):
        for suffix, value in values.items():
            if suffix.startswith("ROLLBACK_"):
                if suffix == "ROLLBACK_A":
                    lines.append(f"[Rollback{scope}.Default]")
            else:
                lines.append(f"[{suffix.title()}{scope}.Default]")
            lines.append(names[suffix] + " = " + record(value, scope, special=suffix == "SPECIAL"))
    return "\n".join(lines) + "\n"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def registry_acceptance(store, names, evidence):
    config_text = acceptance_config(names)
    (evidence.directory / "qa.toml").write_text(config_text, encoding="utf-8")
    service = EnvironmentService(parse_config(config_text), store, StateRepository(evidence.directory / "registry-state"))

    def raw(scope, suffix, value, kind=REG_EXPAND_SZ):
        return RegistryState(names[suffix], scope, True, value, kind)

    def verify(expected):
        actual = store.read(expected.scope, expected.name)
        require(actual.same_content(expected), f"raw mismatch: expected {expected.to_dict()}, actual {actual.to_dict()}")
        evidence.log("assert_raw", state=actual.to_dict())

    def execute(name, plan, status=None):
        require(plan.can_execute, f"blocked plan: {plan.conflicts}")
        evidence.log("final_preview", phase=name, action=plan.action, warnings=list(plan.warnings),
                     changes=[{"before": c.before.to_dict(), "after": c.after.to_dict()} for c in plan.changes])
        result = service.execute(plan)
        require(result.status == status if status else result.success, f"{name}: {result}")
        evidence.phase(name, result=asdict(result))
        return result

    def remove(app):
        draft = service.prepare_remove(RemovalRequest(RemovalScope.SCHEME, frozenset((app,)), {app: "Default"}))
        require(not draft.shared_conflicts, "unexpected acceptance-only sharing conflict")
        return service.finalize_remove(draft.id)

    # Same exact variable name, independently persisted in both actual scopes.
    for scope in ("User", "System"):
        app = "Dual" + scope
        execute(f"{scope}: apply and default cleanup", service.plan(app, "Default"))
        verify(raw(scope, "DUAL", "A;B", REG_SZ))
    verify(raw("User", "DUAL", "A;B", REG_SZ))
    verify(raw("System", "DUAL", "A;B", REG_SZ))
    evidence.phase("real HKCU/HKLM same-name isolation")

    for scope in ("User", "System"):
        store.write(raw(scope, "PARTIAL", "A;C"))
        execute(f"{scope}: ordinary replace partial removal", remove("Partial" + scope))
        verify(raw(scope, "PARTIAL", "C"))

        store.write(raw(scope, "RAW", " A ;B;; C "))
        service.set_cleanup_settings(CleanupSettings(False, False, False))
        execute(f"{scope}: cleanup-off raw slice removal", remove("Raw" + scope))
        verify(raw(scope, "RAW", " A ;; C "))
        service.set_cleanup_settings(CleanupSettings())

        execute(f"{scope}: create unique empty value", service.plan("Empty" + scope, "Default"))
        verify(raw(scope, "EMPTY", "", REG_SZ))
        execute(f"{scope}: actively remove unique empty value", remove("Empty" + scope))
        verify(RegistryState(names["EMPTY"], scope, False))

        execute(f"{scope}: special exact original string", service.plan("Special" + scope, "Default"))
        verify(raw(scope, "SPECIAL", "  A;B\t  ", REG_SZ))
        execute(f"{scope}: special whole-key removal", remove("Special" + scope))
        verify(RegistryState(names["SPECIAL"], scope, False))

        original = raw(scope, "RESTORE", " A ;A;; B\t ")
        store.write(original)
        result = execute(f"{scope}: apply before history restoration", service.plan("Restore" + scope, "Default"))
        execute(f"{scope}: lossless history restoration", service.plan_restore(result.operation_id))
        verify(original)

        first, second = raw(scope, "ROLLBACK_A", " A ;A;; "), raw(scope, "ROLLBACK_B", "", REG_SZ)
        store.write(first)
        store.write(second)
        store.fail_once = (scope, names["ROLLBACK_B"])
        execute(f"{scope}: injected failure after second real write", service.plan("Rollback" + scope, "Default"), "rolled_back")
        verify(first)
        verify(second)
        store.delete(scope, first.name)
        store.delete(scope, second.name)
        store.fail_once = (scope, second.name)
        execute(f"{scope}: injected failure restores original absence", service.plan("Rollback" + scope, "Default"), "rolled_back")
        verify(RegistryState(first.name, scope, False))
        verify(RegistryState(second.name, scope, False))
    # Exercise effective cleanup of pre-existing dirty raw contents in both scopes.
    for scope in ("User", "System"):
        store.write(raw(scope, "DUAL", "A;A; B ;;\t;"))
        execute(f"{scope}: dirty value cleanup and preserved type", service.plan("Dual" + scope, "Default"))
        verify(raw(scope, "DUAL", "A;B"))


class NativeWindows:
    """Native visibility/token/process evidence; no mouse or unrelated windows."""
    def __init__(self):
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.u = ctypes.WinDLL("user32", use_last_error=True)
        self.a = ctypes.WinDLL("advapi32", use_last_error=True)
        self.g = ctypes.WinDLL("gdi32", use_last_error=True)
        self.callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        prototypes = [
            (self.k.OpenProcess, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            (self.k.CloseHandle, [wintypes.HANDLE], wintypes.BOOL),
            (self.k.QueryFullProcessImageNameW, [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            (self.k.GetProcessTimes, [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4, wintypes.BOOL),
            (self.k.WaitForSingleObject, [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            (self.a.OpenProcessToken, [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)], wintypes.BOOL),
            (self.a.GetTokenInformation, [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            (self.u.EnumWindows, [self.callback, wintypes.LPARAM], wintypes.BOOL),
            (self.u.SetThreadDpiAwarenessContext, [wintypes.HANDLE], wintypes.HANDLE),
            (self.u.GetWindowThreadProcessId, [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
            (self.u.IsWindowVisible, [wintypes.HWND], wintypes.BOOL),
            (self.u.IsIconic, [wintypes.HWND], wintypes.BOOL),
            (self.u.GetWindow, [wintypes.HWND, wintypes.UINT], wintypes.HWND),
            (self.u.GetWindowTextW, [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            (self.u.GetWindowRect, [wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            (self.u.GetDpiForWindow, [wintypes.HWND], wintypes.UINT),
            (self.u.MonitorFromWindow, [wintypes.HWND, wintypes.DWORD], wintypes.HANDLE),
            (self.u.GetMonitorInfoW, [wintypes.HANDLE, ctypes.c_void_p], wintypes.BOOL),
            (self.u.PostMessageW, [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], wintypes.BOOL),
            (self.u.GetWindowDC, [wintypes.HWND], wintypes.HDC),
            (self.u.ReleaseDC, [wintypes.HWND, wintypes.HDC], ctypes.c_int),
            (self.u.PrintWindow, [wintypes.HWND, wintypes.HDC, wintypes.UINT], wintypes.BOOL),
            (self.g.CreateCompatibleDC, [wintypes.HDC], wintypes.HDC),
            (self.g.CreateCompatibleBitmap, [wintypes.HDC, ctypes.c_int, ctypes.c_int], wintypes.HBITMAP),
            (self.g.SelectObject, [wintypes.HDC, wintypes.HGDIOBJ], wintypes.HGDIOBJ),
            (self.g.DeleteObject, [wintypes.HGDIOBJ], wintypes.BOOL),
            (self.g.DeleteDC, [wintypes.HDC], wintypes.BOOL),
            (self.g.GetDIBits, [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT], ctypes.c_int),
        ]
        for function, params, result in prototypes:
            function.argtypes, function.restype = params, result

    @contextmanager
    def physical_coordinates(self):
        """Use physical pixels for both the target RECT and GDI buffer.

        DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 is the signed handle -4.
        Restore the prior thread context even when enumeration/capture fails.
        This only changes the observer thread, never the product or its windows.
        """
        previous = self.u.SetThreadDpiAwarenessContext(wintypes.HANDLE(-4))
        if not previous:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield
        finally:
            if not self.u.SetThreadDpiAwarenessContext(previous):
                raise ctypes.WinError(ctypes.get_last_error())

    def process(self, pid):
        process = self.k.OpenProcess(0x1000 | 0x00100000, False, pid)
        if not process:
            return None
        token = wintypes.HANDLE()
        try:
            size = wintypes.DWORD(32768)
            path = ctypes.create_unicode_buffer(size.value)
            if not self.k.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(size)):
                return None
            times = [wintypes.FILETIME() for _ in range(4)]
            if not self.k.GetProcessTimes(process, *(ctypes.byref(t) for t in times)):
                return None
            start = ((times[0].dwHighDateTime << 32) + times[0].dwLowDateTime) / 10_000_000 - 11644473600
            if not self.a.OpenProcessToken(process, 0x0008, ctypes.byref(token)):
                return None
            elevated, needed = wintypes.DWORD(), wintypes.DWORD()
            if not self.a.GetTokenInformation(token, 20, ctypes.byref(elevated), ctypes.sizeof(elevated), ctypes.byref(needed)):
                return None
            return {"pid": pid, "exe": str(Path(path.value).resolve()), "process_started": start,
                    "token_elevated": bool(elevated.value)}
        finally:
            if token:
                self.k.CloseHandle(token)
            self.k.CloseHandle(process)

    def windows(self, exe, launch_after, *, include_owned=False):
        found = []
        @self.callback
        def collect(hwnd, _):
            try:
                owner = self.u.GetWindow(hwnd, 4)
                if owner and not include_owned:  # GW_OWNER: default remains independent windows.
                    return True
                pid = wintypes.DWORD()
                self.u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                data = self.process(pid.value)
                if not data or not same_executable(data["exe"], exe) or data["process_started"] < launch_after:
                    return True
                title = ctypes.create_unicode_buffer(1024)
                self.u.GetWindowTextW(hwnd, title, len(title))
                rect = wintypes.RECT()
                if not self.u.GetWindowRect(hwnd, ctypes.byref(rect)):
                    return True
                found.append({**data, "hwnd": int(hwnd), "title": title.value,
                              "owner_hwnd": int(owner) if owner else None,
                              "nativeIsWindowVisible": bool(self.u.IsWindowVisible(hwnd)),
                              "minimized": bool(self.u.IsIconic(hwnd)),
                              "coordinate_units": "physical_pixels",
                              "rect": [rect.left, rect.top, rect.right, rect.bottom]})
            except OSError:
                pass  # A process may exit during enumeration.
            return True
        with self.physical_coordinates():
            if not self.u.EnumWindows(collect, 0):
                raise ctypes.WinError(ctypes.get_last_error())
        return found

    def display_metrics(self, hwnd):
        class MonitorInfo(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("monitor", wintypes.RECT),
                        ("work", wintypes.RECT), ("flags", wintypes.DWORD)]
        with self.physical_coordinates():
            dpi = int(self.u.GetDpiForWindow(hwnd))
            require(dpi > 0, "GetDpiForWindow failed")
            monitor = self.u.MonitorFromWindow(hwnd, 2)
            info = MonitorInfo()
            info.cbSize = ctypes.sizeof(info)
            require(bool(monitor and self.u.GetMonitorInfoW(monitor, ctypes.byref(info))),
                    "GetMonitorInfo failed")
            return {"dpi": dpi, "screen_scale_percent": dpi / 96 * 100,
                    "monitor_rect_physical": [info.monitor.left, info.monitor.top,
                                              info.monitor.right, info.monitor.bottom],
                    "monitor_work_rect_physical": [info.work.left, info.work.top,
                                                   info.work.right, info.work.bottom]}

    def screenshot(self, window, path):
        with self.physical_coordinates():
            self._screenshot_physical(window, path)

    def _screenshot_physical(self, window, path):
        from PySide6.QtGui import QImage
        hwnd = window["hwnd"]
        rect = wintypes.RECT()
        require(bool(self.u.GetWindowRect(hwnd, ctypes.byref(rect))), "physical GetWindowRect failed")
        window["rect"] = [rect.left, rect.top, rect.right, rect.bottom]
        window["coordinate_units"] = "physical_pixels"
        left, top, right, bottom = window["rect"]
        width, height = right - left, bottom - top
        require(0 < width <= 10000 and 0 < height <= 10000, "invalid capture dimensions")
        dc = self.u.GetWindowDC(hwnd)
        require(bool(dc), "GetWindowDC failed")
        memory = bitmap = previous = None
        try:
            memory = self.g.CreateCompatibleDC(dc)
            bitmap = self.g.CreateCompatibleBitmap(dc, width, height)
            require(bool(memory and bitmap), "capture bitmap allocation failed")
            previous = self.g.SelectObject(memory, bitmap)
            require(bool(self.u.PrintWindow(hwnd, memory, 2)), "PrintWindow failed")
            self.g.SelectObject(memory, previous)
            previous = None
            # BITMAPINFOHEADER followed by no color table, 32-bit top-down BGRA.
            header = ctypes.create_string_buffer(40)
            import struct
            struct.pack_into("<IiiHHIIiiII", header, 0, 40, width, -height, 1, 32, 0, width * height * 4, 0, 0, 0, 0)
            pixels = ctypes.create_string_buffer(width * height * 4)
            require(self.g.GetDIBits(memory, bitmap, 0, height, pixels, header, 0) == height, "GetDIBits failed")
            data = pixels.raw
            require(any(min(data[channel::4]) != max(data[channel::4]) for channel in range(3)),
                    "capture is uniformly blank")
            # Native little-endian 0xffRRGGBB storage is GDI BGRX. QImage's
            # RGB32 ignores the unused alpha byte and needs no GUI application.
            picture = QImage(data, width, height, width * 4, QImage.Format_RGB32)
            require(not picture.isNull() and picture.save(str(path), "PNG"), "PNG capture save failed")
            window["capture_size"] = [width, height]
        finally:
            if previous and memory:
                self.g.SelectObject(memory, previous)
            if bitmap:
                self.g.DeleteObject(bitmap)
            if memory:
                self.g.DeleteDC(memory)
            self.u.ReleaseDC(hwnd, dc)

    def close(self, window, exe, launch_after):
        # Re-check identity at the final action boundary (including HWND reuse).
        current = next((item for item in self.windows(exe, launch_after)
                        if item["hwnd"] == window["hwnd"] and item["pid"] == window["pid"]
                        and item["token_elevated"]), None)
        require(current is not None, "window identity changed before WM_CLOSE")
        process = self.k.OpenProcess(0x1000 | 0x00100000, False, window["pid"])
        require(bool(process), "cannot hold GUI process for exit verification")
        try:
            require(bool(self.u.PostMessageW(window["hwnd"], 0x0010, 0, 0)), "WM_CLOSE failed")
            require(self.k.WaitForSingleObject(process, 15000) == 0, "GUI did not exit after WM_CLOSE")
        finally:
            self.k.CloseHandle(process)


def same_executable(first, second):
    return os.path.normcase(str(Path(first).resolve())) == os.path.normcase(str(Path(second).resolve()))


def gui_acceptance(args, evidence):
    # Fresh GUI state avoids registry-test rollback history/recovery dialogs.
    state_dir = evidence.directory / "gui-state"
    state_dir.mkdir()
    launch_after = time.time()
    ready = {"status": "ready_for_gui", "launch_after": launch_after,
             "exe": str(args.exe), "config": str(evidence.directory / "qa.toml"),
             "state_dir": str(state_dir), "timeout_seconds": args.gui_timeout,
             "launch_from_non_admin": True, "runner_does_not_launch": True}
    durable_json(evidence.directory / "ready_for_gui.json", ready)
    evidence.phase("ready for independent installed GUI/UAC launch", "waiting", readiness=ready)
    native = NativeWindows()
    deadline = time.monotonic() + args.gui_timeout
    seen = []
    while time.monotonic() < deadline:
        seen = native.windows(args.exe, launch_after)
        matches = [window for window in seen if window["nativeIsWindowVisible"]
                   and window["token_elevated"] and not window["minimized"]
                   and window["title"] == "环境变量方案管理器"]
        if matches:
            window = max(matches, key=lambda item: (item["rect"][2] - item["rect"][0]) *
                         (item["rect"][3] - item["rect"][1]))
            # Let the first paint complete; only this verified native window is captured.
            time.sleep(1)
            current = next((item for item in native.windows(args.exe, launch_after)
                            if item["hwnd"] == window["hwnd"] and item["pid"] == window["pid"]
                            and item["nativeIsWindowVisible"] and item["token_elevated"]), None)
            require(current is not None, "GUI visibility/token changed before capture")
            try:
                native.screenshot(current, evidence.directory / "gui.png")
                evidence.phase("installed executable visible elevated main window", window=current,
                               screenshot="gui.png")
            finally:
                native.close(current, args.exe, launch_after)
                evidence.phase("acceptance GUI closed and process exited")
            pid_file = evidence.directory / "launch_pid.json"
            if pid_file.exists():
                evidence.log("controller_launch_metadata", data=json.loads(pid_file.read_text(encoding="utf-8-sig")))
            return current
        time.sleep(0.5)
    evidence.phase("installed GUI/UAC bounded wait", "failed", last_matching_windows=seen)
    raise TimeoutError(f"no visible elevated installed main window within {args.gui_timeout}s")


def main(argv=None):
    args = arguments(argv)
    # No directory creation, configuration write or registry mutation before token check.
    if os.name != "nt" or not is_administrator():
        print("REFUSED: explicitly run this tool after approving administrator UAC; no writes performed.", file=sys.stderr)
        return 2
    args.work_dir.mkdir(parents=True, exist_ok=True)
    evidence = Evidence(args.work_dir)
    store = ScopedAcceptanceStore(WindowsStore(), names_for(args.test_id), evidence)
    error = None
    gui = None
    leftovers = []
    try:
        native = NativeWindows()
        runner = native.process(os.getpid())
        require(runner is not None and runner["token_elevated"], "real runner token is not elevated")
        evidence.phase("real administrator token", process=runner)
        store.preflight()
        evidence.phase("all test names absent in both real Environment keys")
        registry_acceptance(store, names_for(args.test_id), evidence)
        leftovers = store.cleanup()
        require(not leftovers, "test registry residuals retained; see residuals.json")
        evidence.phase("registry cleanup verified")
        gui = gui_acceptance(args, evidence)
    except BaseException as exc:
        error = {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}
        evidence.phase("acceptance stopped", "failed", error=error)
    finally:
        # Even on collision the expected map is empty, so cleanup cannot delete it.
        leftovers = store.cleanup()
        report = {"status": "passed" if error is None and not leftovers else "failed",
                  "test_id": args.test_id, "exe": str(args.exe), "work_dir": str(args.work_dir),
                  "phases": evidence.phases, "gui": gui, "error": error, "residuals": leftovers}
        durable_json(args.work_dir / "result.json", report)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
