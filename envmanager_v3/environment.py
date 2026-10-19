"""Raw V3 environment storage (Python 3.11+, standard library only).

read/write/delete return snapshot dictionaries. list returns {"items": [...]};
check_permission and notify return dictionaries too. Notifications are explicit,
so a service can notify once after a batch. No adapter reads process.environ or
expands environment strings. Construction performs no Registry operations.

Revisions are opaque, instance-bound tokens: a value signature plus a monotonic
observation counter. Observed A -> B -> A changes invalidate a previous token;
Windows' optional start_changes listener also invalidates scope continuity on
unknown native signals and observation gaps, even if the raw value is equal.
Unobserved ABA changes cannot be detected. The service must hold its process
lock across a reviewed commit. Windows Registry has no atomic value CAS against
uncooperative external writers; expected checks run immediately before writes.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import ctypes
from ctypes import wintypes
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import threading
from typing import Any, Iterator
import uuid


SCOPES = ("User", "System")
REG_SZ = "REG_SZ"
REG_EXPAND_SZ = "REG_EXPAND_SZ"
SUPPORTED_TYPES = (REG_SZ, REG_EXPAND_SZ)
MAX_UTF16_UNITS = 32766


class V3EnvironmentError(Exception):
    """Stable, JSON-compatible adapter error; no native exceptions escape I/O."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = {} if details is None else details

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "details": self.details}


def _utf16_units(value: str) -> int:
    return len(value.encode("utf-16-le", errors="surrogatepass")) // 2


def canonical_name(name: str) -> str:
    """V2 ConfigFormat rule: upper each character only if it occupies one UTF16 unit.

    In particular, sharp-s does not expand to SS. Supplementary characters are
    retained. This function identifies targets; it never renames stored values.
    """
    if not isinstance(name, str):
        raise V3EnvironmentError("INVALID_NAME", "Environment name must be a string.")
    return "".join(upper if _utf16_units(upper := char.upper()) == 1 else char
                   for char in name)


def _target(scope: str, name: str) -> tuple[str, str]:
    if not isinstance(scope, str) or scope not in SCOPES:
        raise V3EnvironmentError("INVALID_SCOPE", "Scope must be User or System.")
    if not isinstance(name, str) or not name or "=" in name or "\0" in name:
        raise V3EnvironmentError("INVALID_NAME", "Name must be nonempty without '=' or NUL.")
    return scope, canonical_name(name)


def _scope(scope: str) -> None:
    _target(scope, "_")


def _write_input(scope: str, name: str, raw: str, value_type: str | None) -> None:
    _target(scope, name)
    if not isinstance(raw, str) or "\0" in raw:
        raise V3EnvironmentError("INVALID_RAW", "Raw value must be a string without NUL.")
    if any(0xD800 <= ord(character) <= 0xDFFF for character in raw):
        raise V3EnvironmentError("INVALID_RAW", "Raw value must contain valid Unicode characters.")
    if _utf16_units(raw) > MAX_UTF16_UNITS:
        raise V3EnvironmentError("VALUE_TOO_LONG", "Value exceeds 32766 UTF16 units.",
                                 {"limit": MAX_UTF16_UNITS, "units": _utf16_units(raw)})
    if value_type is not None and value_type not in SUPPORTED_TYPES:
        raise V3EnvironmentError("INVALID_TYPE", "Only REG_SZ and REG_EXPAND_SZ can be written.")


def _entry(name: str, raw: Any, value_type: str, *, supported: bool = True,
           native_type: int | None = None) -> dict:
    result = {"name": name, "raw": raw if supported else None,
              "type": value_type, "supported": supported}
    if not supported:
        # ascii(repr) makes binary/control/unicode content safe for JSON and UI.
        result.update(display=ascii(raw), native_type=native_type)
    return result


class _Environment:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._epoch = uuid.uuid4().hex
        self._version = 0
        self._observed: dict[tuple[str, str], tuple[str, int]] = {}

    def _snapshot(self, scope: str, name: str, entry: dict | None) -> dict:
        content = {"exists": entry is not None,
                   "name": entry["name"] if entry else None,
                   "raw": entry["raw"] if entry else None,
                   "type": entry["type"] if entry else None,
                   "supported": entry["supported"] if entry else True}
        if entry and not entry["supported"]:
            content.update(display=entry["display"], native_type=entry["native_type"])
        signature = hashlib.sha256(json.dumps(content, ensure_ascii=True,
                                              sort_keys=True).encode("ascii")).hexdigest()
        key = (scope, canonical_name(name))
        previous = self._observed.get(key)
        if previous is None or previous[0] != signature:
            self._version += 1
            previous = (signature, self._version)
            self._observed[key] = previous
        snapshot = {"scope": scope, "name": entry["name"] if entry else name,
                    "exists": entry is not None, "raw": content["raw"],
                    "type": content["type"],
                    "revision": f"{self._epoch}:{previous[1]}:{signature}",
                    "supported": content["supported"]}
        if entry and not entry["supported"]:
            snapshot.update(display=entry["display"], native_type=entry["native_type"])
        return snapshot

    def _check_expected(self, expected: dict | None, current: dict) -> None:
        if expected is None:
            return
        required = ("scope", "name", "exists", "raw", "type", "revision")
        if (not isinstance(expected, dict) or any(k not in expected for k in required)
                or not isinstance(expected["name"], str)
                or not isinstance(expected["revision"], str)
                or not isinstance(expected["exists"], bool)):
            raise V3EnvironmentError("INVALID_EXPECTED", "Expected must be a complete snapshot.")
        matches = (expected["scope"] == current["scope"]
                   and canonical_name(expected["name"]) == canonical_name(current["name"])
                   and all(expected[k] == current[k] for k in ("exists", "raw", "type", "revision")))
        if not matches:
            raise V3EnvironmentError("CONFLICT", "Environment changed since capture.",
                                     {"expected": expected, "current": current})

    @staticmethod
    def _require_supported(current: dict) -> None:
        if current["exists"] and not current["supported"]:
            raise V3EnvironmentError("UNSUPPORTED_TYPE", "Unsupported values are read-only.",
                                     {"scope": current["scope"], "name": current["name"],
                                      "type": current["type"]})

    def _write_type(self, current: dict, requested: str | None, name: str) -> str:
        self._require_supported(current)
        if current["exists"]:
            if requested is not None and requested != current["type"]:
                raise V3EnvironmentError("TYPE_MISMATCH", "Existing Registry type must be preserved.",
                                         {"type": current["type"], "requested": requested})
            return current["type"]
        return requested or (REG_EXPAND_SZ if canonical_name(name) == "PATH" else REG_SZ)


_file_locks_guard = threading.Lock()
_file_locks: dict[str, Any] = {}


class FileEnvironment(_Environment):
    """Persistent empty-by-default environment, isolated from the Registry.

    Each operation reloads the file to observe other instances. Instances sharing
    a path share a thread lock; interprocess locking belongs to the service.
    JSON format: {"format": 1, "values": {"User": [...], "System": [...]}}.
    Values store name/raw/type and preserve original spelling and text exactly.
    Unsupported numeric types may be imported as {name, raw, type: <integer>};
    these remain visible but cannot be changed or deleted through this adapter.
    """

    def __init__(self, path: str | os.PathLike):
        super().__init__()
        self.path = Path(path).absolute()
        with _file_locks_guard:
            self._lock = _file_locks.setdefault(os.path.normcase(str(self.path.resolve())),
                                                threading.RLock())

    @staticmethod
    def _empty() -> dict:
        return {"format": 1, "values": {scope: [] for scope in SCOPES}}

    def _load(self) -> dict:
        try:
            with self.path.open("r", encoding="utf-8") as stream:
                state = json.load(stream)
        except FileNotFoundError:
            return self._empty()
        except (ValueError, UnicodeError) as exc:
            raise V3EnvironmentError("INVALID_STORAGE", "Environment file is not valid JSON.",
                                     {"path": str(self.path)}) from exc
        except OSError as exc:
            self._io_error(exc, "read")
        try:
            if (not isinstance(state, dict) or type(state.get("format")) is not int
                    or state["format"] != 1 or set(state["values"]) != set(SCOPES)):
                raise ValueError("Unexpected file schema")
            for scope in SCOPES:
                rows = state["values"][scope]
                if not isinstance(rows, list):
                    raise ValueError("Values must be arrays")
                seen = set()
                for row in rows:
                    if not isinstance(row, dict) or set(row) != {"name", "raw", "type"}:
                        raise ValueError("Invalid value record")
                    key = _target(scope, row["name"])
                    if key in seen:
                        raise ValueError("Duplicate canonical target")
                    seen.add(key)
                    if row["type"] in SUPPORTED_TYPES:
                        _write_input(scope, row["name"], row["raw"], row["type"])
                    elif type(row["type"]) is not int or not 0 <= row["type"] <= 0xFFFFFFFF:
                        raise ValueError("Invalid stored type")
        except (KeyError, TypeError, ValueError, V3EnvironmentError) as exc:
            raise V3EnvironmentError("INVALID_STORAGE", "Invalid environment file schema.",
                                     {"path": str(self.path)}) from exc
        return state

    def _io_error(self, exc: OSError, operation: str) -> None:
        raise V3EnvironmentError("PERMISSION_DENIED" if isinstance(exc, PermissionError)
                                 else "STORAGE_IO", "Environment file operation failed.",
                                 {"operation": operation, "path": str(self.path),
                                  "errno": exc.errno}) from exc

    def _save(self, state: dict) -> None:
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                             prefix=f".{self.path.name}.", suffix=".tmp",
                                             dir=self.path.parent, delete=False) as stream:
                temporary = stream.name
                json.dump(state, stream, ensure_ascii=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
        except OSError as exc:
            self._io_error(exc, "write")
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
                except OSError:
                    # Preserve the original save error; an abandoned temp is not committed.
                    pass

    @staticmethod
    def _find(state: dict, scope: str, name: str) -> dict | None:
        key = canonical_name(name)
        return next((row for row in state["values"][scope]
                     if canonical_name(row["name"]) == key), None)

    @staticmethod
    def _decode(row: dict | None) -> dict | None:
        if row is None:
            return None
        supported = row["type"] in SUPPORTED_TYPES
        return _entry(row["name"], row["raw"], row["type"] if supported
                      else f"REG_TYPE_{row['type']}", supported=supported,
                      native_type=None if supported else row["type"])

    def read(self, scope: str, name: str) -> dict:
        _target(scope, name)
        with self._lock:
            return self._snapshot(scope, name, self._decode(self._find(self._load(), scope, name)))

    def list(self, scope: str | None = None) -> dict:
        if scope is not None:
            _scope(scope)
        with self._lock:
            state = self._load()
            items = [self._snapshot(s, row["name"], self._decode(row))
                     for s in (SCOPES if scope is None else (scope,))
                     for row in state["values"][s]]
            self._observe_absences(items, SCOPES if scope is None else (scope,))
            return {"items": items}

    def _observe_absences(self, items: list[dict], scopes: tuple) -> None:
        present = {(item["scope"], canonical_name(item["name"])) for item in items}
        for scope, name in tuple(self._observed):
            if scope in scopes and (scope, name) not in present:
                self._snapshot(scope, name, None)

    def write(self, scope: str, name: str, raw: str, type: str | None = None,
              *, expected: dict | None = None) -> dict:
        _write_input(scope, name, raw, type)
        with self._lock:
            state = self._load()
            row = self._find(state, scope, name)
            current = self._snapshot(scope, name, self._decode(row))
            self._check_expected(expected, current)
            value_type = self._write_type(current, type, name)
            if row is None:
                row = {"name": name, "raw": raw, "type": value_type}
                state["values"][scope].append(row)
            else:
                row.update(raw=raw, type=value_type)
            self._save(state)
            return self._snapshot(scope, name, self._decode(row))

    def delete(self, scope: str, name: str, *, expected: dict | None = None) -> dict:
        _target(scope, name)
        with self._lock:
            state = self._load()
            row = self._find(state, scope, name)
            current = self._snapshot(scope, name, self._decode(row))
            self._check_expected(expected, current)
            self._require_supported(current)
            if row is not None:
                state["values"][scope].remove(row)
                self._save(state)
            return self._snapshot(scope, name, None)

    def check_permission(self, scope: str) -> dict:
        _scope(scope)
        # Read-only advisory probe. Actual save remains the authoritative check.
        with self._lock:
            self._load()
            target = self.path if self.path.exists() else self.path.parent
            while not target.exists() and target != target.parent:
                target = target.parent
            writable = os.access(target, os.W_OK)
            return {"scope": scope, "writable": writable}

    def notify(self) -> dict:
        return {"notified": False, "reason": "isolated_file_backend"}


class WindowsEnvironment(_Environment):
    """Raw HKCU/HKLM values, with an optional read-only notification lifecycle.

    Service contract (all optional; existing read/list/write/delete unchanged):
    - start_changes(on_change=None, native=None) explicitly opens watches;
      returns changes_status(). Construction is still inert.
    - on_change() is a worker-thread wake hint OUTSIDE the adapter lock. Schedule
      a refresh; never wait for a worker or perform blocking Service work here.
    - take_events() drains native signals and returns coalesced JSON events,
      one per affected scope, with sequence/continuity/reason/error. Queuing is
      independent of delivery, so focus refresh and execute preflight can drain.
    - change_cursor()/wait_for_change(cursor, timeout=25.0) are optional adapter
      subscriptions. The opaque cursor is instance-bound; waits are capped at
      30 seconds and perform NO Registry reads/native scans. Results contain
      cursor/changed/reset/events/status. None or another boot's cursor returns
      reset=True immediately; treat both scopes as unknown. Wait outside Service
      locks. Events coalesce by scope, not a complete per-value operation log;
      unknown evidence survives take_events() and intervening known writes.
    - invalidate_continuity(scope=None, reason=...) reports sleep/resume or other
      externally detected observation gaps. It never reads/writes the Registry.
    - changes_status() exposes active scopes/failures. An inactive watch is NOT
      evidence of continuous observation; Service must disable deletion evidence
      captured while monitoring is unavailable. close() joins and frees watches.

    Scope invalidation changes all observed revisions in that scope, including
    absences/unsupported values; it never changes the adapter boot identity or
    another scope. Events are NOT occurrence audits. A local write first drains
    unknown signals, then compares the complete scope before/after under lock.
    Only a verified target change with all other values untouched consumes its
    scope's pending signal as known_write. Extra changes, rearm failures and
    other scopes remain unknown. Native notifications have no writer identity:
    this boundary cannot detect unobserved external ABA in the final CAS window.
    Service still owns complete plan audits and propagation to editing sessions.

    Pass both fake winreg and fake native events for safe notification tests.
    """

    USER_KEY = "Environment"
    SYSTEM_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"

    def __init__(self, winreg_module=None, *, notifier=None):
        super().__init__()
        self._injected_registry = winreg_module is not None
        if winreg_module is None:
            try:
                import winreg as winreg_module
            except ImportError as exc:
                raise V3EnvironmentError("UNAVAILABLE", "Windows Registry is unavailable.") from exc
        self._reg = winreg_module
        self._notifier = notifier
        self._changes = None
        self._changes_lifecycle = threading.RLock()
        self._change_sequence = 0
        self._change_events: dict[str, dict] = {}
        self._latest_change_events: dict[str, dict] = {}
        self._latest_unknown_events: dict[str, dict] = {}
        self._change_condition = threading.Condition(self._lock)

    def _change(self, scope: str, reason: str, error: dict | None = None) -> None:
        # Called under the same lock as snapshots and expected comparisons.
        for target, (signature, version) in tuple(self._observed.items()):
            if target[0] == scope:
                self._version += 1
                self._observed[target] = (signature, self._version)
        self._queue_change(scope, reason, error)

    def _queue_change(self, scope: str, reason: str, error: dict | None = None,
                      *, known_target: str | None = None) -> None:
        self._change_sequence += 1
        previous = self._change_events.get(scope)
        unknown = known_target is None or bool(previous and previous["continuity"] == "unknown")
        event = {"scope": scope, "sequence": self._change_sequence,
                 "continuity": "unknown" if unknown else "known_write",
                 "reason": previous["reason"] if known_target is not None and unknown else reason,
                 "reasons": list(dict.fromkeys((previous or {}).get("reasons", []) + [reason]))}
        if known_target is not None:
            event["targets"] = list(dict.fromkeys((previous or {}).get("targets", [])
                                                   + [canonical_name(known_target)]))
        if error is not None:
            event["error"] = error
        elif previous and "error" in previous:
            event["error"] = previous["error"]
        # Bounded by two scopes. Unknown evidence cannot be overwritten by a
        # later equal value or by a subsequent successful listener restart.
        self._change_events[scope] = event
        self._latest_change_events[scope] = event
        if unknown:
            self._latest_unknown_events[scope] = event
        self._change_condition.notify_all()
        if self._changes is not None:
            self._changes.request_wake()

    def _drain_changes(self, *, known_scope: str | None = None) -> None:
        if self._changes is not None:
            self._changes.drain(known_scope=known_scope)

    def _scope_rows(self, key, scope: str) -> dict | None:
        if self._changes is None or not self._changes.status()["scopes"][scope]:
            return None
        return {canonical_name(row["name"]): row for row in self._enumerate(key)}

    def _finish_local_change(self, key, scope: str, name: str, before_rows: dict | None,
                             desired: dict | None) -> dict:
        """Verify full-key local fence before consuming any apparent own signal.

        The listener shares our lock, so it cannot consume/relabel the local
        signal before this check. This is bounded readback, not a native CAS or
        a guarantee against invisible external ABA. Never suppress a later event.
        """
        try:
            after_rows = self._scope_rows(key, scope) if before_rows is not None else None
            entry = self._query(key, name)
        except Exception:
            if before_rows is not None:
                self._change(scope, "write_readback_failed")
                self._drain_changes()
            raise
        known = False
        if before_rows is not None and after_rows is not None:
            predicted = dict(before_rows)
            target = canonical_name(name)
            if desired is None:
                predicted.pop(target, None)
            else:
                predicted[target] = desired
            known = predicted == after_rows and entry == desired
            if not known:
                self._change(scope, "write_overlap")
        self._drain_changes(known_scope=scope if known else None)
        snapshot = self._snapshot(scope, name, entry)
        if known:
            self._queue_change(scope, "known_write", known_target=name)
        return snapshot

    def start_changes(self, *, on_change=None, native=None) -> dict:
        from .environment_events import RegistryChangeListener

        if on_change is not None and not callable(on_change):
            raise V3EnvironmentError("INVALID_CALLBACK", "on_change must be callable.")
        with self._changes_lifecycle:
            if self._changes is not None:
                return self.changes_status()  # Explicit close/start for recovery.
            if self._injected_registry and native is None:
                raise V3EnvironmentError("INVALID_EVENT_BACKEND",
                                         "An injected Registry requires injected native events.")
            with self._lock:
                self._changes = RegistryChangeListener(
                    self._reg, {scope: self._location(scope) for scope in SCOPES},
                    self._lock, self._change, native=native, wake=on_change)
            # start waits for registration; never hold the adapter lock here.
            return self._changes.start()

    def changes_status(self) -> dict:
        with self._lock:
            if self._changes is None:
                return {"running": False, "closed": True,
                        "scopes": {scope: False for scope in SCOPES},
                        "errors": {}, "callback_error": None}
            return self._changes.status()

    def take_events(self) -> list[dict]:
        with self._lock:
            self._drain_changes()
            events = deepcopy(sorted(self._change_events.values(), key=lambda event: event["sequence"]))
            self._change_events.clear()
            return events

    def change_cursor(self) -> str:
        with self._lock:
            return f"{self._epoch}:{self._change_sequence}"

    def wait_for_change(self, cursor: str | None = None, *, timeout: float = 25.0) -> dict:
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not 0 <= timeout <= 30 or not math.isfinite(timeout)):
            raise V3EnvironmentError("INVALID_EVENT_TIMEOUT", "Timeout must be between 0 and 30 seconds.")
        if cursor is None:
            epoch, sequence = None, -1
        else:
            if not isinstance(cursor, str):
                raise V3EnvironmentError("INVALID_EVENT_CURSOR", "Invalid environment event cursor.")
            try:
                epoch, number = cursor.split(":")
                if len(epoch) != 32 or any(c not in "0123456789abcdef" for c in epoch):
                    raise ValueError()
                if not number or not number.isascii() or not number.isdecimal():
                    raise ValueError()
                sequence = int(number)
            except (AttributeError, ValueError):
                raise V3EnvironmentError("INVALID_EVENT_CURSOR", "Invalid environment event cursor.") from None
        with self._change_condition:
            reset = epoch != self._epoch
            if not reset and sequence > self._change_sequence:
                raise V3EnvironmentError("INVALID_EVENT_CURSOR", "Cursor is ahead of this adapter.")
            if not reset:
                self._change_condition.wait_for(lambda: sequence != self._change_sequence, timeout)
            events = []
            for scope in SCOPES:
                latest = self._latest_change_events.get(scope)
                unknown = self._latest_unknown_events.get(scope)
                if reset:
                    events.append({"scope": scope, "sequence": self._change_sequence,
                                   "continuity": "unknown", "reason": "cursor_reset",
                                   "reasons": ["cursor_reset"]})
                elif latest and latest["sequence"] > sequence:
                    event = deepcopy(latest)
                    if unknown and unknown["sequence"] > sequence:
                        event.update(continuity="unknown", reason=unknown["reason"],
                                     reasons=list(dict.fromkeys(unknown["reasons"] + event["reasons"])))
                        if "error" in unknown:
                            event["error"] = deepcopy(unknown["error"])
                    events.append(event)
            return {"cursor": self.change_cursor(), "changed": reset or sequence != self._change_sequence,
                    "reset": reset, "events": events, "status": self.changes_status()}

    def invalidate_continuity(self, scope: str | None = None,
                              *, reason: str = "observation_gap") -> None:
        if scope is not None:
            _scope(scope)
        if not isinstance(reason, str) or not reason:
            raise V3EnvironmentError("INVALID_EVENT_REASON", "Continuity reason must be nonempty.")
        with self._lock:
            for affected in (SCOPES if scope is None else (scope,)):
                self._change(affected, reason)

    def close(self) -> dict:
        with self._changes_lifecycle:
            listener = self._changes
        if listener is not None:
            status = listener.close()  # Join outside adapter AND lifecycle locks.
            if status["closed"]:
                with self._changes_lifecycle:
                    with self._lock:
                        if self._changes is listener:
                            self._changes = None
            return status
        return self.changes_status()

    def _location(self, scope: str) -> tuple[Any, str]:
        _scope(scope)
        return ((self._reg.HKEY_CURRENT_USER, self.USER_KEY) if scope == "User" else
                (self._reg.HKEY_LOCAL_MACHINE, self.SYSTEM_KEY))

    @staticmethod
    def _native_error(exc: OSError, scope: str, operation: str) -> None:
        denied = isinstance(exc, PermissionError) or getattr(exc, "winerror", None) == 5
        raise V3EnvironmentError("PERMISSION_DENIED" if denied else "REGISTRY_IO",
                                 "Registry operation failed.",
                                 {"scope": scope, "operation": operation,
                                  "winerror": getattr(exc, "winerror", None),
                                  "errno": exc.errno}) from exc

    @contextmanager
    def _key(self, scope: str, *, writable: bool = False) -> Iterator[Any]:
        root, path = self._location(scope)
        access = self._reg.KEY_QUERY_VALUE
        if writable:
            access |= self._reg.KEY_SET_VALUE
        try:
            with self._reg.OpenKey(root, path, 0, access) as key:
                yield key
        except OSError as exc:
            self._native_error(exc, scope, "open/write" if writable else "read")

    def _decode(self, name: str, raw: Any, native_type: int) -> dict:
        if native_type == self._reg.REG_SZ or native_type == self._reg.REG_EXPAND_SZ:
            if not isinstance(raw, str):
                raise V3EnvironmentError("INVALID_REGISTRY_VALUE", "String type contains non-string data.")
            return _entry(name, raw, REG_SZ if native_type == self._reg.REG_SZ else REG_EXPAND_SZ)
        label = next((label for label in ("REG_NONE", "REG_BINARY", "REG_DWORD", "REG_MULTI_SZ",
                                           "REG_QWORD", "REG_LINK", "REG_DWORD_BIG_ENDIAN")
                      if getattr(self._reg, label, None) == native_type), f"REG_TYPE_{native_type}")
        return _entry(name, raw, label, supported=False, native_type=native_type)

    def _enumerate(self, key) -> list[dict]:
        items = []
        index = 0
        while True:
            try:
                name, raw, native_type = self._reg.EnumValue(key, index)
            except OSError as exc:
                if getattr(exc, "winerror", None) == 259:  # ERROR_NO_MORE_ITEMS only
                    return items
                raise
            items.append(self._decode(name, raw, native_type))
            index += 1

    def _query(self, key, name: str) -> dict | None:
        try:
            raw, native_type = self._reg.QueryValueEx(key, name)
        except FileNotFoundError:
            return None
        # Query uses the caller's untouched spelling; enumeration recovers stored
        # casing because QueryValueEx does not return the value name.
        stored_name = next((row["name"] for row in self._enumerate(key)
                            if canonical_name(row["name"]) == canonical_name(name)), name)
        return self._decode(stored_name, raw, native_type)

    def read(self, scope: str, name: str) -> dict:
        _target(scope, name)
        with self._lock:
            self._drain_changes()
            with self._key(scope) as key:
                entry = self._query(key, name)
                self._drain_changes()
                return self._snapshot(scope, name, entry)

    def list(self, scope: str | None = None) -> dict:
        if scope is not None:
            _scope(scope)
        with self._lock:
            self._drain_changes()
            collected = {}
            for s in (SCOPES if scope is None else (scope,)):
                with self._key(s) as key:
                    collected[s] = self._enumerate(key)
            # Build every snapshot AFTER draining signals received during any
            # scope's enumeration, so later scopes cannot invalidate revisions
            # that have already been placed in this result.
            self._drain_changes()
            items = []
            for s, rows in collected.items():
                items.extend(self._snapshot(s, row["name"], row) for row in rows)
                present = {canonical_name(row["name"]) for row in rows}
                for observed_scope, observed_name in tuple(self._observed):
                    if observed_scope == s and observed_name not in present:
                        self._snapshot(s, observed_name, None)
            return {"items": items}

    def write(self, scope: str, name: str, raw: str, type: str | None = None,
              *, expected: dict | None = None) -> dict:
        _write_input(scope, name, raw, type)
        with self._lock:
            self._drain_changes()
            with self._key(scope, writable=True) as key:
                before_rows = self._scope_rows(key, scope)
                entry = self._query(key, name)
                self._drain_changes()
                current = self._snapshot(scope, name, entry)
                self._check_expected(expected, current)
                value_type = self._write_type(current, type, name)
                native_type = self._reg.REG_SZ if value_type == REG_SZ else self._reg.REG_EXPAND_SZ
                try:
                    self._reg.SetValueEx(key, name, 0, native_type, raw)
                except Exception:
                    if before_rows is not None:
                        self._change(scope, "write_failed")
                    self._drain_changes()
                    raise
                desired = _entry(current["name"] if current["exists"] else name, raw, value_type)
                return self._finish_local_change(key, scope, name, before_rows, desired)

    def delete(self, scope: str, name: str, *, expected: dict | None = None) -> dict:
        _target(scope, name)
        with self._lock:
            self._drain_changes()
            with self._key(scope, writable=True) as key:
                before_rows = self._scope_rows(key, scope)
                entry = self._query(key, name)
                self._drain_changes()
                current = self._snapshot(scope, name, entry)
                self._check_expected(expected, current)
                self._require_supported(current)
                if current["exists"]:
                    try:
                        self._reg.DeleteValue(key, name)
                    except Exception:
                        if before_rows is not None:
                            self._change(scope, "write_failed")
                        self._drain_changes()
                        raise
                    return self._finish_local_change(key, scope, name, before_rows, None)
                return current

    def check_permission(self, scope: str) -> dict:
        _scope(scope)
        with self._lock:
            with self._key(scope, writable=True):
                pass  # Open only: no probe values, key creation or UAC invocation.
            return {"scope": scope, "writable": True}

    def notify(self) -> dict:
        with self._lock:
            if self._notifier is not None:
                try:
                    result = self._notifier()
                except OSError as exc:
                    raise V3EnvironmentError("NOTIFY_FAILED", "Environment notification failed.") from exc
                if not result:
                    raise V3EnvironmentError("NOTIFY_FAILED", "Environment notification failed.")
            else:
                self._broadcast()
            return {"notified": True}

    @staticmethod
    def _broadcast() -> None:
        # Explicit pointer-sized signatures are required on 64-bit Windows.
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            send = user32.SendMessageTimeoutW
            send.argtypes = (wintypes.HWND, wintypes.UINT, ctypes.c_size_t,
                             wintypes.LPCWSTR, wintypes.UINT, wintypes.UINT,
                             ctypes.POINTER(ctypes.c_size_t))
            send.restype = ctypes.c_ssize_t
            result = ctypes.c_size_t()
            if not send(0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000,
                        ctypes.byref(result)):
                raise V3EnvironmentError("NOTIFY_FAILED", "WM_SETTINGCHANGE failed or timed out.",
                                         {"winerror": ctypes.get_last_error()})
        except (OSError, AttributeError) as exc:
            raise V3EnvironmentError("NOTIFY_FAILED", "Windows notification is unavailable.") from exc
