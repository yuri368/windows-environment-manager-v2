"""Fixed System helper. Production CLI accepts only a bound local pipe.

No commands, Registry paths, executables or User targets enter the payload.
The parent service owns approval, frozen-plan authority and the interprocess
commit lock; the helper rechecks content and uses its own local CAS snapshots.
Crash recovery is reconciliation by the service, never automatic request replay.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from .environment import (WindowsEnvironment, V3EnvironmentError, canonical_name,
                          SUPPORTED_TYPES, MAX_UTF16_UNITS)
from .privileges import (ElevationError, MAX_REQUEST_BYTES, REQUEST_FILE, RESULT_FILE,
                         JOURNAL_FILE, _json_bytes, _no_reparse, _locked_request)


def _invalid(message):
    raise ElevationError("INVALID_REQUEST", message)


def _name(name):
    if (not isinstance(name, str) or not name or "=" in name or "\0" in name
            or len(name.encode("utf-16-le", "surrogatepass")) // 2 > 16383):
        _invalid("Invalid environment target name.")
    try:
        name.encode("utf-16-le")
    except UnicodeError:
        _invalid("Target name contains unpaired UTF16 surrogates.")
    return name


def _state(state, name, *, before):
    required = {"scope", "name", "exists", "raw", "type"} if before else {"exists", "raw", "type"}
    allowed = {"scope", "name", "exists", "raw", "type", "revision", "supported"}
    if not isinstance(state, dict) or not required <= state.keys() or state.keys() - allowed:
        _invalid("Snapshot fields are incomplete or unsupported.")
    if state.get("scope", "System") != "System":
        _invalid("Helper accepts only System snapshots.")
    if canonical_name(_name(state.get("name", name))) != canonical_name(name):
        _invalid("Snapshot name does not match its target.")
    if type(state["exists"]) is not bool or state.get("supported", True) is not True:
        _invalid("Snapshot existence/support flags are invalid.")
    if "revision" in state and not isinstance(state["revision"], str):
        _invalid("Snapshot revision must be an opaque string.")
    raw, value_type = state["raw"], state["type"]
    if state["exists"]:
        if not isinstance(raw, str) or "\0" in raw or value_type not in SUPPORTED_TYPES:
            _invalid("Only raw REG_SZ/REG_EXPAND_SZ states are executable.")
        try:
            units = len(raw.encode("utf-16-le")) // 2
        except UnicodeError:
            _invalid("Raw value contains unpaired UTF16 surrogates.")
        if units > MAX_UTF16_UNITS:
            _invalid("Raw value exceeds the UTF16 limit.")
    elif raw is not None or value_type is not None:
        _invalid("Absent state must have null raw and type.")
    # Keep the parent snapshot (including its token) for the audit, never replace
    # its before with a helper read. Comparison deliberately ignores that token.
    result = dict(state)
    result.update(scope="System", name=state.get("name", name))
    return result


def validate_request(request, *, allow_type_change=False):
    if (not isinstance(request, dict)
            or set(request) != {"protocol", "job_id", "restore", "plans"}
            or type(request["protocol"]) is not int or request["protocol"] != 1
            or not isinstance(request["job_id"], str)
            or re.fullmatch(r"[0-9a-f]{32}", request["job_id"]) is None
            or type(request["restore"]) is not bool):
        _invalid("Invalid System helper envelope.")
    plans = request["plans"]
    if not isinstance(plans, list) or not 1 <= len(plans) <= 256:
        _invalid("A System batch must contain 1 to 256 targets.")
    normalized, seen = [], set()
    for plan in plans:
        if not isinstance(plan, dict) or set(plan) != {"scope", "name", "before", "after"}:
            _invalid("Frozen target fields must be scope/name/before/after only.")
        if plan["scope"] != "System":
            _invalid("User targets cannot be delegated to the System helper.")
        name = _name(plan["name"])
        key = canonical_name(name)
        if key in seen:
            _invalid("Duplicate System target.")
        seen.add(key)
        before = _state(plan["before"], name, before=True)
        after = _state(plan["after"], name, before=False)
        if not allow_type_change and before["exists"] and after["exists"] and before["type"] != after["type"]:
            raise ElevationError("TYPE_MISMATCH", "The storage adapter preserves existing types.")
        normalized.append({"scope": "System", "name": name, "before": before, "after": after})
    result = dict(request, plans=normalized)
    if len(_json_bytes(result)) > MAX_REQUEST_BYTES:
        raise ElevationError("REQUEST_TOO_LARGE", "System batch exceeds request size limit.")
    return result


def _same(actual, expected):
    return (isinstance(actual, dict) and actual.get("scope") == "System"
            and isinstance(actual.get("name"), str)
            and canonical_name(actual["name"]) == canonical_name(expected["name"])
            and actual.get("supported", True) is True
            and type(actual.get("exists")) is bool
            and all(key in actual and actual[key] == expected[key]
                    for key in ("exists", "raw", "type")))


def _error(exc):
    if isinstance(exc, V3EnvironmentError):
        return exc.to_dict()
    return {"code": "HELPER_FAILED", "message": "System helper operation failed.",
            "details": {"exception": type(exc).__name__}}


class _Journal:
    def __init__(self, path):
        self.failed = False
        try:
            # Exclusive creation refuses replay and symlinked journal files.
            self.stream = Path(path).open("xb")
        except OSError as exc:
            raise ElevationError("JOURNAL_IO", "Cannot create helper progress journal.") from exc

    def append(self, event, **data):
        try:
            self.stream.write(_json_bytes({"event": event, **data}) + b"\n")
            self.stream.flush()
            os.fsync(self.stream.fileno())
        except OSError as exc:
            self.failed = True
            raise ElevationError("JOURNAL_IO", "Cannot persist helper progress; writes stopped.") from exc

    def close(self):
        self.stream.close()


def _apply(environment, name, desired, current, *, journal=None, index=None, target=None, compensation=False):
    # The parent revision belongs to another adapter instance/process. Use the
    # newly verified local snapshot for adapter CAS, without weakening before.
    if desired["exists"]:
        if current["exists"] and current["type"] != desired["type"]:
            if journal is None or target is None:
                raise ElevationError("TYPE_MISMATCH", "Type transitions require an approved journalled helper plan.")
            journal.append("type_delete_intent", index=index, expected=current, compensation=compensation)
            deleted = environment.delete("System", name, expected=current)
            verified = environment.read("System", name)
            absent = {"scope": "System", "name": name, "exists": False, "raw": None, "type": None}
            if not _same(verified, absent) or verified.get("revision") != deleted.get("revision"):
                raise ElevationError("READBACK_MISMATCH", "Type-change intermediate deletion could not be verified.")
            target["intermediate"] = verified
            journal.append("type_deleted", index=index, snapshot=verified)
            journal.append("type_create_intent", index=index, expected=verified, desired=desired, compensation=compensation)
            current = verified
        return environment.write("System", name, desired["raw"], desired["type"], expected=current)
    else:
        return environment.delete("System", name, expected=current)


def execute_helper_request(request, environment, *, journal_path, journal_factory=None, source_snapshots=None,
                           approved_type_changes=False):
    """Injectable execution core. journal_path is selected by trusted code only.

    Every mutation follows a durable intent. Failures compensate only snapshots
    confirmed by this helper's writes; equal external values are retained too.
    restore failures retain completed restorations instead of rolling forward.
    """
    request = validate_request(request, allow_type_change=approved_type_changes)
    restore = request["restore"]
    plans = request["plans"]
    source_key, destination_key = ("after", "before") if restore else ("before", "after")
    result = {"job_id": request["job_id"], "restore": restore, "status": "rejected",
              "targets": [dict(plan, status="not_started") for plan in plans], "errors": []}
    journal = (journal_factory or _Journal)(journal_path)
    if source_snapshots is not None and len(source_snapshots) != len(plans):
        journal.close()
        _invalid("Incomplete same-helper compensation snapshots.")
    def source_matches(actual, plan, index):
        return (_same(actual, plan[source_key]) and (source_snapshots is None
                or actual.get("revision") == source_snapshots[index].get("revision")))
    attempted = []
    written_snapshots = {}
    try:
        journal.append("prepared", request=request)
        # Entire System batch is preflighted before the first write.
        environment.check_permission("System")
        for index, plan in enumerate(plans):
            actual = environment.read("System", plan["name"])
            result["targets"][index]["observed"] = actual
            if not source_matches(actual, plan, index):
                raise ElevationError("SNAPSHOT_MISMATCH", "System preflight differs from the frozen plan.",
                                     {"index": index, "current": actual})
        journal.append("preflight_verified")
        for index, plan in enumerate(plans):
            name, desired, source = plan["name"], plan[destination_key], plan[source_key]
            actual = environment.read("System", name)
            if not source_matches(actual, plan, index):
                raise ElevationError("SNAPSHOT_MISMATCH", "System target changed after preflight.",
                                     {"index": index, "current": actual})
            if _same(actual, desired):
                result["targets"][index].update(status="unchanged", result=actual)
                continue
            journal.append("write_intent", index=index, expected=actual, desired=desired)
            attempted.append(index)  # Include a write that raises after making its side effect.
            written_snapshots[index] = _apply(environment, name, desired, actual, journal=journal, index=index,
                                              target=result["targets"][index], compensation=restore)
            verified = environment.read("System", name)
            written = written_snapshots[index]
            if (not _same(verified, desired) or not isinstance(written, dict)
                    or not _same(verified, written) or not written.get("revision")
                    or verified.get("revision") != written["revision"]):
                raise ElevationError("READBACK_MISMATCH", "System write could not be verified.",
                                     {"index": index, "current": verified})
            result["targets"][index].update(status="compensated" if restore else "committed",
                                            result=verified)
            journal.append("write_verified", index=index, snapshot=verified)
        if attempted:
            environment.notify()  # Failure enters recovery/compensation, never fake success.
            journal.append("notified")
        # A later write/notification can race an earlier target. Re-read the
        # whole batch before issuing a success receipt, retaining frozen before.
        for index, plan in enumerate(plans):
            verified = environment.read("System", plan["name"])
            if not _same(verified, plan[destination_key]):
                raise ElevationError("READBACK_MISMATCH", "Final System batch verification failed.",
                                     {"index": index, "current": verified})
            result["targets"][index]["result"] = verified
        result["status"] = "compensated" if restore else "committed"
    except Exception as exc:
        result["errors"].append(_error(exc))
        result["status"] = "recovery_required" if restore or attempted else "rejected"
        if attempted and not journal.failed:
            unresolved = restore and any(target["status"] not in ("compensated", "unchanged")
                                         for target in result["targets"])
            for index in reversed(attempted):
                plan, target = plans[index], result["targets"][index]
                try:
                    own_after = written_snapshots.get(index)
                    actual = environment.read("System", plan["name"])
                    target["result"] = actual
                    if restore:
                        # Keep already restored before values, never reapply after.
                        if _same(actual, plan["before"]):
                            target["status"] = "compensated"
                        else:
                            unresolved, target["status"] = True, "recovery_required"
                        journal.append("restore_reconciled", index=index, snapshot=actual,
                                       status=target["status"])
                        continue
                    if _same(actual, plan["before"]):
                        target["status"] = "compensated"
                        journal.append("already_before", index=index, snapshot=actual)
                        continue
                    intermediate = target.get("intermediate")
                    own_absent = (isinstance(intermediate, dict) and _same(actual, intermediate)
                                  and actual.get("revision") == intermediate.get("revision"))
                    own_written = (isinstance(own_after, dict) and bool(own_after.get("revision"))
                                   and _same(actual, own_after) and _same(actual, plan["after"])
                                   and actual.get("revision") == own_after["revision"])
                    if not own_written and not own_absent:
                        raise ElevationError("COMPENSATION_CONFLICT", "External value preserved during compensation.",
                                             {"index": index, "current": actual})
                    journal.append("compensate_intent", index=index, expected=actual, desired=plan["before"])
                    _apply(environment, plan["name"], plan["before"], actual, journal=journal,
                           index=index, target=target, compensation=True)
                    verified = environment.read("System", plan["name"])
                    if not _same(verified, plan["before"]):
                        raise ElevationError("COMPENSATION_READBACK", "Compensation could not be verified.",
                                             {"index": index, "current": verified})
                    target.update(status="compensated", result=verified)
                    journal.append("compensated", index=index, snapshot=verified)
                except Exception as rollback_error:
                    unresolved, target["status"] = True, "recovery_required"
                    result["errors"].append(_error(rollback_error))
                    if journal.failed:
                        break  # No further mutation without durable progress.
            try:
                environment.notify()
                if not journal.failed:
                    journal.append("recovery_notified")
            except Exception as notify_error:
                unresolved = True
                result["errors"].append(_error(notify_error))
            result["status"] = "recovery_required" if unresolved or journal.failed else "compensated"
        elif attempted:
            for index in attempted:
                result["targets"][index]["status"] = "recovery_required"
    # A disconnected owner cannot authorize compensation or any further write.
    # PipeJournal sets failed on disconnect, so the branch above cannot mutate.
    if getattr(journal, "owner_lost", False):
        result["status"] = "recovery_required"
    try:
        if not journal.failed:
            journal.append("finished", result=result)
        else:
            result["status"] = "recovery_required"
    except Exception as exc:
        result["errors"].append(_error(exc))
        result["status"] = "recovery_required"
    finally:
        journal.close()
    return result


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _invalid("Duplicate JSON fields are not accepted.")
        result[key] = value
    return result


def _invalid_constant(value):
    _invalid("Non-JSON numeric constants are not accepted.")


def load_request(request_path, digest, *, allow_type_change=False):
    """Validate the fixed nonce/request.json shape and exact bytes before parsing."""
    path = Path(request_path)
    if (not path.is_absolute() or path.name != REQUEST_FILE
            or re.fullmatch(r"[0-9a-f]{32}", path.parent.name) is None
            or ".." in path.parts or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None):
        _invalid("Invalid helper request location or digest.")
    _no_reparse(path)
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_REQUEST_BYTES + 1)
    except OSError as exc:
        raise ElevationError("REQUEST_IO", "Cannot read the helper request.") from exc
    if len(data) > MAX_REQUEST_BYTES:
        raise ElevationError("REQUEST_TOO_LARGE", "Helper request exceeds the size limit.")
    if hashlib.sha256(data).hexdigest() != digest:
        raise ElevationError("DIGEST_MISMATCH", "Helper request bytes changed.")
    try:
        request = validate_request(json.loads(data, object_pairs_hook=_unique_object,
                                             parse_constant=_invalid_constant), allow_type_change=allow_type_change)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ElevationError("INVALID_REQUEST", "Cannot decode helper request JSON.") from exc
    if request["job_id"] != path.parent.name:
        _invalid("Request job identity differs from the nonce directory.")
    return request


def main(argv=None):
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--pipe", required=True)
    parser.add_argument("--parent", required=True, type=int)
    parser.add_argument("--operation", required=True)
    parser.add_argument("--nonce", required=True)
    arguments = parser.parse_args(argv)
    try:
        from .native_ipc import (require_production, validate_binding, NativePipe, verified_package,
                                 bind_helper, serve_frozen_request)
        require_production()  # Before native/adapter construction, even if already admin.
        validate_binding(arguments.nonce, arguments.operation)
        with verified_package() as package, NativePipe.connect(arguments.pipe) as pipe:
            with bind_helper(pipe, arguments.parent, arguments.nonce, arguments.operation) as parent:
                result = serve_frozen_request(pipe, parent, arguments.nonce, arguments.operation,
                                              package=package, environment_factory=_system_environment)
            return 0 if result["status"] in ("committed", "compensated") else 1
    except (V3EnvironmentError, OSError) as exc:
        print(json.dumps(_error(exc), ensure_ascii=True), file=sys.stderr)
        return 1


def _system_environment():
    """Fixed System-only continuity watch; never observe elevated-account HKCU.

    Reuse the adapter's existing locked listener integration, restricted to the
    System location. A failed watch prevents PREPARED instead of silently
    authorizing compensation without observation continuity.
    """
    from .environment_events import RegistryChangeListener
    environment = WindowsEnvironment()
    listener = RegistryChangeListener(environment._reg,
        {"System": environment._location("System")}, environment._lock, environment._change)
    environment._changes = listener
    status = listener.start()
    if not status.get("scopes", {}).get("System"):
        environment.close()
        raise ElevationError("SYSTEM_WATCH_FAILED", "System observation continuity could not be established.")
    return environment


if __name__ == "__main__":
    raise SystemExit(main())
