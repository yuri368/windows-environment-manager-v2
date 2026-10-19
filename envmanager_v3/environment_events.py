"""Optional, read-only Windows environment key notifications.

No native library, event or Registry key is opened at import/construction time.
RegistryChangeListener uses a dedicated persistent thread and manual-reset
events. A signal is consumed and its one-shot registration rearmed under the
adapter's lock, including when a foreground read/preflight drains it first.

The native API reports a *key*, not a value or writer. Signals are unknown
unless consumed inside the adapter's locked, verified local write boundary,
which compares the complete scope before/after, not just the written value.
This cannot detect unobserved external ABA in the final compare/write window.
See https://learn.microsoft.com/en-us/windows/win32/api/winreg/nf-winreg-regnotifychangekeyvalue
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import threading


class NativeRegistryEvents:
    """Small injectable Win32 boundary; constructing it only loads DLLs.

    Tests replace this entire boundary or its DLL factories. All HANDLE/HKEY
    arguments and returns have explicit pointer-sized ctypes signatures.
    """

    def __init__(self):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        self._create = kernel.CreateEventW
        self._create.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL,
                                 wintypes.LPCWSTR)
        self._create.restype = wintypes.HANDLE
        self._set = kernel.SetEvent
        self._reset = kernel.ResetEvent
        self._close = kernel.CloseHandle
        for function in (self._set, self._reset, self._close):
            function.argtypes = (wintypes.HANDLE,)
            function.restype = wintypes.BOOL
        self._single = kernel.WaitForSingleObject
        self._single.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        self._single.restype = wintypes.DWORD
        self._wait = kernel.WaitForMultipleObjects
        self._wait.argtypes = (wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
                               wintypes.BOOL, wintypes.DWORD)
        self._wait.restype = wintypes.DWORD
        self._arm = advapi.RegNotifyChangeKeyValue
        self._arm.argtypes = (wintypes.HKEY, wintypes.BOOL, wintypes.DWORD,
                              wintypes.HANDLE, wintypes.BOOL)
        self._arm.restype = wintypes.LONG

    @staticmethod
    def _failed():
        raise ctypes.WinError(ctypes.get_last_error())

    def create_event(self):
        handle = self._create(None, True, False, None)
        if not handle:
            self._failed()
        return handle

    def set_event(self, handle):
        if not self._set(handle):
            self._failed()

    def reset_event(self, handle):
        if not self._reset(handle):
            self._failed()

    def close_event(self, handle):
        if not self._close(handle):
            self._failed()

    def arm(self, key, event):
        # LAST_SET covers value add/change/delete; SECURITY forces a recheck on
        # access changes. Key closure/deletion also signals the registered event.
        # Foreground reads may rearm too, so registration must outlive their
        # threads. THREAD_AGNOSTIC requires Windows 8+; older Windows fails
        # explicitly rather than silently degrading notification continuity.
        code = self._arm(int(key), False, 0x00000004 | 0x00000008 | 0x10000000,
                         event, True)
        if code:
            raise ctypes.WinError(code)  # LSTATUS, not GetLastError.

    def is_set(self, handle):
        result = self._single(handle, 0)
        if result == 0:
            return True
        if result == 258:  # WAIT_TIMEOUT
            return False
        self._failed()

    def wait(self, handles):
        array = (wintypes.HANDLE * len(handles))(*handles)
        result = self._wait(len(handles), array, False, 0xFFFFFFFF)
        if result >= len(handles):
            self._failed()
        return result


def _error(exc):
    return {"message": str(exc), "winerror": getattr(exc, "winerror", None)}


class RegistryChangeListener:
    """One-shot watches for fixed environment keys, sharing the adapter RLock.

    native protocol: create/set/reset/close_event, arm(key,event), is_set(event),
    wait(handles)->index. winreg protocol: OpenKey, KEY_NOTIFY, key.Close().
    emit(scope, reason, error=None) runs under lock and must only update adapter
    state. Optional wake() runs on the worker OUTSIDE that lock; it should merely
    schedule a Service refresh. No user callback is needed for take_events().

    Missing/denied/deleted keys are failures, never silently created or treated
    as empty. Recovery is explicit close/start, without a reconnect polling loop.
    """

    def __init__(self, registry, locations, lock, emit, *, native=None, wake=None):
        self._registry = registry
        self._locations = dict(locations)
        self._lock = lock
        self._emit = emit
        self._native = native
        self._wake = wake
        self._ready = threading.Event()
        self._stopping = threading.Event()
        self._thread = None
        self._stop_handle = None
        self._watches = {}
        self._active = set()
        self._errors = {}
        self._callback_error = None

    def start(self):
        if self._thread is not None:
            return self.status()
        self._thread = threading.Thread(target=self._run,
                                        name="V3-environment-notifications", daemon=True)
        try:
            self._thread.start()
        except Exception as exc:
            with self._lock:
                for scope in self._locations:
                    self._fail(scope, "listener_start_failed", exc)
            self._ready.set()
            return self.status()
        self._ready.wait()
        return self.status()

    def status(self):
        with self._lock:
            return {"running": bool(self._thread and self._thread.is_alive()
                                    and not self._stopping.is_set() and self._active),
                    "closed": (not bool(self._thread and self._thread.is_alive())
                               and not self._watches and self._stop_handle is None),
                    "scopes": {scope: scope in self._active and not self._stopping.is_set()
                               for scope in self._locations},
                    "errors": dict(self._errors), "callback_error": self._callback_error}

    def _fail(self, scope, reason, exc):
        self._active.discard(scope)
        self._errors[scope] = _error(exc)
        self._emit(scope, reason, _error(exc))

    def request_wake(self):
        """Caller holds adapter lock; wake callbacks even after foreground drain.

        The stop event doubles as a control wake. Only close sets _stopping;
        consuming a control wake cannot accidentally terminate the watcher.
        """
        if (self._wake is not None and self._stop_handle is not None
                and not self._stopping.is_set()
                and self._thread is not threading.current_thread()):
            try:
                self._native.set_event(self._stop_handle)
            except Exception as exc:
                self._callback_error = str(exc)

    def drain(self, *, known_scope=None):
        """Caller holds adapter lock. Never wait, reopen, or invoke user code."""
        if self._stopping.is_set():
            return False
        changed = False
        for scope in tuple(self._active):
            key, event = self._watches[scope]
            try:
                if not self._native.is_set(event):
                    continue
                # Reset BEFORE rearming, or a new signal could be erased. Never
                # re-register a still-pending wait (Microsoft documents a leak).
                self._native.reset_event(event)
                self._native.arm(key, event)
                if scope != known_scope:
                    self._emit(scope, "key_changed")
            except Exception as exc:
                self._fail(scope, "listener_interrupted", exc)
            else:
                changed = True
                continue
            changed = True
        return changed

    def _wake_service(self):
        if self._wake is not None:
            try:
                self._wake()
            except Exception as exc:
                # Delivery is advisory; queued events remain available to reads
                # and take_events even when the application's callback fails.
                with self._lock:
                    self._callback_error = str(exc)

    def _run(self):
        try:
            with self._lock:
                if self._native is None:
                    self._native = NativeRegistryEvents()
                self._stop_handle = self._native.create_event()
                for scope, (root, path) in self._locations.items():
                    key = event = None
                    try:
                        key = self._registry.OpenKey(root, path, 0,
                                                     self._registry.KEY_NOTIFY)
                        event = self._native.create_event()
                        self._watches[scope] = (key, event)
                        self._native.arm(key, event)
                        self._active.add(scope)
                        self._emit(scope, "listener_started")
                    except Exception as exc:
                        # Store partially acquired resources for worker cleanup.
                        self._watches[scope] = (key, event)
                        self._fail(scope, "listener_start_failed", exc)
            self._ready.set()
            self._wake_service()
            while not self._stopping.is_set():
                with self._lock:
                    handles = [self._stop_handle] + [self._watches[s][1]
                                                    for s in self._locations
                                                    if s in self._active]
                if len(handles) == 1:
                    break
                selected = self._native.wait(handles)
                with self._lock:
                    if self._stopping.is_set():
                        break
                    if selected == 0:
                        self._native.reset_event(self._stop_handle)
                    changed = self.drain()
                if changed or selected == 0:
                    self._wake_service()
        except Exception as exc:
            with self._lock:
                for scope in self._locations:
                    if scope not in self._errors:
                        self._fail(scope, "listener_interrupted" if self._ready.is_set()
                                   else "listener_start_failed", exc)
        finally:
            with self._lock:
                self._cleanup_resources()
            self._ready.set()
            self._wake_service()

    def _cleanup_resources(self):
        """Under lock, after native wait ends. Retain failed closes for retry."""
        for scope, (key, event) in tuple(self._watches.items()):
            if scope in self._active:
                self._emit(scope, "listener_stopped")
            self._active.discard(scope)
            # Key closure signals its event: close the key first, retaining
            # both resources if key.Close fails, rather than discarding a live
            # registration or reporting fully closed with a leaked handle.
            try:
                if key is not None:
                    key.Close()
                    key = None
                if event is not None:
                    self._native.close_event(event)
                    event = None
            except Exception as exc:
                self._fail(scope, "listener_cleanup_failed", exc)
            if key is None and event is None:
                del self._watches[scope]
            else:
                self._watches[scope] = (key, event)
        if self._stop_handle is not None:
            try:
                self._native.close_event(self._stop_handle)
            except Exception as exc:
                for scope in self._locations:
                    self._fail(scope, "listener_cleanup_failed", exc)
            else:
                self._stop_handle = None

    def close(self):
        """Signal, then join outside the adapter lock. Idempotent; no polling."""
        self._stopping.set()
        with self._lock:
            if self._stop_handle is not None and self._thread and self._thread.is_alive():
                try:
                    self._native.set_event(self._stop_handle)
                except Exception as exc:
                    for scope in tuple(self._active):
                        self._fail(scope, "listener_stop_failed", exc)
                    # Do not close HANDLEs under an outstanding native wait, or
                    # join a worker we failed to wake. A later close can retry;
                    # status.closed=False prevents replacement/leaking a thread.
                    return self.status()
        if (self._thread is not None and self._thread.ident is not None
                and self._thread is not threading.current_thread()):
            self._thread.join()
        with self._lock:
            if not self._thread or not self._thread.is_alive():
                self._cleanup_resources()
        return self.status()
