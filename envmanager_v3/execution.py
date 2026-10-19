"""Execute only server-owned, reviewed receipts; never construct a plan here.

Public methods return JSON dictionaries (including rejection results). ``lock`` is
the Database RLock and must also coordinate service mutations of dependencies.
Each Database.execution commit is durable under Database's synchronous=FULL WAL.
This is a recoverable sequence, not a cross-target Registry transaction.

on_committed(receipt, result) runs INSIDE the final SQLite transaction. It must
return None or {settings_key: JSON_value}; it must not open a transaction, write
the environment, or perform external effects. Executor saves those updates,
history, the consumed preview and the final result in that same transaction.
revalidate(receipt) returns None/that same receipt, or a NEW higher-version
receipt. A replacement is saved as a preview and is never executed implicitly.

system_session_factory(receipt, review_token, operation_id) is injected. It
receives a private copy of the ENTIRE frozen receipt only AFTER SQLite consumes
the review token. It returns a session: prepare() obtains one live, prepared
helper, with zero writes; commit(), conditional_compensate() and finish() accept
no replacement-plan arguments. Service may wrap system_review when constructing
the session, which must capture its review then. Keep it alive through the final
SQLite commit. finish() only closes; never retry, write,
or grant another elevation. Unknown channels require reconciliation, not replay.
After preparation, recheck all targets, write User, then commit System. Conditional
compensation uses this SAME live helper before reversing User writes.

system_runner(plans, restore=False) is a legacy injected TEST DOUBLE path.
Plans contain only scope/name/before/after; before is the complete original
snapshot and after the frozen value state (also when restore=True). The helper must
validate all targets, write ahead, use conditional compensation, and return a
durable result. Its status/targets/errors are retained, but local readback is
still authoritative. A cancelled or uncertain preparation never permits User writes.
Startup reconciliation only observes; it never replays or restores environment.
system_receipt_reader(operation) optionally reads the original protected System
receipt using Service-owned SID/operation/digest binding. It receives a private
copy; its receipt/error is additional evidence, never a SQLite commit credential.

Recovery previews are built by the service. Their intent is EXACTLY
{kind: 'recovery', operationId: str, resolution: 'restore_before'|'accept_current'}.
They cover every original target: restore_before freezes each original before as
after; accept_current freezes current snapshots as no-ops. Only the referenced
unfinished operation is exempted from the pending gate. Its resolution metadata
and the new commit are saved in the same transaction; original evidence/results
are retained. on_committed must only return settings, never mutate other DB rows.
"""
from __future__ import annotations

from copy import deepcopy
import hmac
import json
import secrets
import uuid

from .database import encode, now
from .environment import (MAX_UTF16_UNITS, SUPPORTED_TYPES, WindowsEnvironment,
                          canonical_name)


class ExecutionError(Exception):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code, self.details = code, details


def _error(error):
    if hasattr(error, "to_dict"):
        return error.to_dict()
    return {"code": getattr(error, "code", "EXECUTION_FAILED"),
            "message": str(error), "details": getattr(error, "details", {})}


def _same_value(left, right):
    return all(left.get(k) == right.get(k) for k in ("exists", "raw", "type"))


def _same_target(left, right):
    return (left.get("scope") == right.get("scope")
            and isinstance(left.get("name"), str) and isinstance(right.get("name"), str)
            and canonical_name(left["name"]) == canonical_name(right["name"]))


def _same_snapshot(left, right):
    return (_same_target(left, right) and _same_value(left, right)
            and left.get("revision") == right.get("revision"))


class Executor:
    def __init__(self, environment, database, *, system_runner=None, system_session_factory=None,
                 system_receipt_reader=None,
                 on_committed=None, revalidate=None):
        self.environment, self.database = environment, database
        self.system_runner = system_runner
        self.system_session_factory = system_session_factory
        self.system_receipt_reader = system_receipt_reader
        self.on_committed, self.revalidate = on_committed, revalidate
        self.lock = database.lock

    @staticmethod
    def _request(request, execute=False):
        fields = {"planId", "version", "digest"}
        if execute:
            fields |= {"reviewToken", "idempotencyKey"}
        if not isinstance(request, dict) or set(request) != fields:
            raise ExecutionError("INVALID_REQUEST", "Only receipt identifiers are accepted.")
        if (type(request["version"]) is not int or request["version"] < 1
                or any(not isinstance(request[k], str) or not request[k]
                       for k in fields - {"version"})):
            raise ExecutionError("INVALID_REQUEST", "Invalid receipt identifiers.")

    def _receipt(self, request):
        row = self.database.read_preview(request["planId"])
        if not row:
            raise ExecutionError("PLAN_NOT_FOUND", "Server receipt was not found.")
        if row["version"] != request["version"]:
            raise ExecutionError("VERSION_MISMATCH", "Preview version changed.")
        if not hmac.compare_digest(row["digest"].encode(), request["digest"].encode()):
            raise ExecutionError("DIGEST_MISMATCH", "Preview digest changed.")
        if row["status"] != "ready":
            raise ExecutionError("PLAN_CONSUMED", "Preview is no longer executable.")
        return row, deepcopy(row["body"])

    def _refresh(self, receipt):
        if self.revalidate is None:
            return None
        updated = self.revalidate(deepcopy(receipt))
        if updated is None or updated == receipt:
            return None
        if (not isinstance(updated, dict) or updated.get("planId") != receipt["planId"]
                or type(updated.get("version")) is not int
                or updated["version"] <= receipt["version"]
                or not isinstance(updated.get("digest"), str) or not updated["digest"]):
            raise ExecutionError("INVALID_REFRESH", "Replacement must have a new receipt version.")
        self.database.preview(updated)  # Invalidates every old review token.
        return {"status": "preview_updated", "plan": deepcopy(updated)}

    @staticmethod
    def _value(state):
        if not isinstance(state, dict) or type(state.get("exists")) is not bool:
            raise ExecutionError("INVALID_RECEIPT", "A complete value state is required.")
        if not state["exists"]:
            if "raw" not in state or "type" not in state or state["raw"] is not None or state["type"] is not None:
                raise ExecutionError("INVALID_RECEIPT", "Absent values require null raw and type.")
        elif (not isinstance(state.get("raw"), str) or "\0" in state["raw"]
              or state.get("type") not in SUPPORTED_TYPES
              or len(state["raw"].encode("utf-16-le", errors="surrogatepass")) // 2 > MAX_UTF16_UNITS):
            raise ExecutionError("INVALID_RECEIPT", "Unsupported frozen raw value or type.")

    def _targets(self, receipt):
        if not isinstance(receipt.get("targets"), list):
            raise ExecutionError("INVALID_RECEIPT", "Receipt targets must be an array.")
        targets, seen = [], set()
        for item in receipt["targets"]:
            if not isinstance(item, dict):
                raise ExecutionError("INVALID_RECEIPT", "Invalid target.")
            scope, name = item.get("scope"), item.get("name")
            if scope not in ("User", "System") or not isinstance(name, str) or not name or "=" in name or "\0" in name:
                raise ExecutionError("INVALID_RECEIPT", "Invalid target identity.")
            key = (scope, canonical_name(name))
            if key in seen:
                raise ExecutionError("INVALID_RECEIPT", "Duplicate canonical target.")
            seen.add(key)
            before, plan = item.get("beforeSnapshot"), item.get("plan")
            self._value(before)
            if (not _same_target(item, before) or not isinstance(before.get("revision"), str)
                    or not before["revision"] or before.get("supported") is False
                    or not isinstance(plan, dict) or plan.get("valid") is not True
                    or not isinstance(plan.get("operations"), list) or plan.get("needsOrdering")):
                raise ExecutionError("INVALID_RECEIPT", "Invalid snapshot or blocked plan.")
            self._value(plan.get("before"))
            self._value(plan.get("after"))
            if not _same_value(before, plan["before"]):
                raise ExecutionError("INVALID_RECEIPT", "Plan before differs from snapshot.")
            for key in ("scope", "name", "revision"):
                if key in plan["before"] and plan["before"][key] != before[key]:
                    raise ExecutionError("INVALID_RECEIPT", "Plan before identity/revision differs from snapshot.")
            if "targetKey" in plan and not _same_target(item, plan["targetKey"]):
                raise ExecutionError("INVALID_RECEIPT", "Plan target differs from snapshot.")
            changed = not _same_value(before, plan["after"])
            if changed and not plan["operations"]:
                raise ExecutionError("INVALID_RECEIPT", "Changed value requires visible operations.")
            targets.append({"scope": scope, "name": name, "before": deepcopy(before),
                            "after": deepcopy(plan["after"]), "plan": deepcopy(plan),
                            "members": deepcopy(item.get("members", [])), "changed": changed,
                            "status": "pending", "verifiedAfter": None, "verifiedBefore": None})
        return targets

    def _preflight(self, targets):
        errors = []
        for target in targets:
            try:
                current = self.environment.read(target["scope"], target["name"])
                if not _same_snapshot(target["before"], current):
                    raise ExecutionError("STALE_SNAPSHOT", "Target changed since preview.",
                                         scope=target["scope"], name=target["name"], actual=current)
                if not target["changed"]:
                    continue  # Read-only no-ops need no write permission or elevation.
                try:
                    permission = self.environment.check_permission(target["scope"])
                    permitted = permission.get("writable") is True
                except Exception as error:
                    if getattr(error, "code", None) != "PERMISSION_DENIED":
                        raise
                    permitted = False
                elevated = isinstance(self.environment, WindowsEnvironment) and target["scope"] == "System"
                helper = self.system_session_factory is not None or self.system_runner is not None
                if elevated and target["changed"] and not helper:
                    raise ExecutionError("SYSTEM_RUNNER_REQUIRED", "System helper was not supplied.")
                if not permitted and not (elevated and helper):
                    raise ExecutionError("PERMISSION_DENIED", "Target is not writable.",
                                         scope=target["scope"], name=target["name"])
            except Exception as error:
                errors.append(_error(error))
        if errors:
            raise ExecutionError("PREFLIGHT_FAILED", "Entire batch rejected before writing.", errors=errors)

    @staticmethod
    def _recovery_intent(receipt):
        intent = receipt.get("intent")
        if not isinstance(intent, dict) or intent.get("kind") != "recovery":
            return None
        if (set(intent) != {"kind", "operationId", "resolution"}
                or not isinstance(intent["operationId"], str) or not intent["operationId"]
                or intent["resolution"] not in ("restore_before", "accept_current")):
            raise ExecutionError("INVALID_RECOVERY_INTENT", "Recovery requires one exact operation and resolution.")
        return intent

    def _pending(self):
        # Database.unfinished must also exclude resolved_current for service/UI.
        # Filtering here keeps already resolved evidence out of startup/gating.
        return [row for row in self.database.unfinished() if row["status"] != "resolved_current"]

    def _recovery_origin(self, receipt, targets):
        intent = self._recovery_intent(receipt)
        if intent is None:
            return None
        row = self.database.connection.execute("SELECT * FROM executions WHERE id=?",
                                               (intent["operationId"],)).fetchone()
        if row is None:
            raise ExecutionError("RECOVERY_NOT_FOUND", "Original operation was not found.")
        if row["status"] not in ("prepared", "executing", "recovery_required"):
            raise ExecutionError("RECOVERY_NOT_PENDING", "Original operation is already terminal.")
        body = json.loads(row["body"])
        originals = body.get("targets", [])
        if not isinstance(originals, list):
            raise ExecutionError("INVALID_RECOVERY_TARGETS", "Original target evidence is invalid.")
        by_key = {(t["scope"], canonical_name(t["name"])): t for t in originals}
        keys = {(t["scope"], canonical_name(t["name"])) for t in targets}
        if len(by_key) != len(originals) or keys != set(by_key) or len(targets) != len(originals):
            raise ExecutionError("INVALID_RECOVERY_TARGETS", "Recovery must cover every original target exactly once.")
        for target in targets:
            original = by_key[(target["scope"], canonical_name(target["name"]))]
            if intent["resolution"] == "restore_before":
                self._value(original.get("before"))
                if not _same_value(target["after"], original["before"]):
                    raise ExecutionError("INVALID_RECOVERY_TARGETS", "Restoration must use the exact original before.")
            elif target["changed"] or target["plan"]["operations"]:
                raise ExecutionError("INVALID_RECOVERY_TARGETS", "Accept-current recovery must contain only no-ops.")
        return {"id": row["id"], "status": row["status"], "body": body}

    def _pending_gate(self, receipt, targets, operation_id):
        origin = self._recovery_origin(receipt, targets)
        exempt = {operation_id}
        if origin is not None:
            exempt.add(origin["id"])
        if any(row["id"] not in exempt for row in self._pending()):
            raise ExecutionError("RECOVERY_REQUIRED", "Unresolved operations block new writes.")
        return origin

    def _resolve_original(self, connection, operation):
        origin = self._pending_gate(operation["receipt"], operation["targets"], operation["operationId"])
        if origin is None:
            return
        intent = operation["receipt"]["intent"]
        body = origin["body"]
        status = "compensated" if intent["resolution"] == "restore_before" else "resolved_current"
        body.update(status=status, resolution=intent["resolution"], resolvedBy=operation["operationId"])
        if intent["resolution"] == "restore_before":
            body["restoredBy"] = operation["operationId"]
        # Preserve original before/after, readbacks, errors, and delivered result.
        connection.execute("UPDATE executions SET status=?,body=?,updated_at=? WHERE id=?",
                           (status, encode(body), now(), origin["id"]))

    def review(self, request):
        try:
            self._request(request)
            with self.lock:
                _, receipt = self._receipt(request)
                updated = self._refresh(receipt)
                if updated:
                    return updated
                targets = self._targets(receipt)
                self._recovery_origin(receipt, targets)
                self._preflight(targets)
                token = secrets.token_urlsafe(32)
                self.database.preview(receipt, review_token=token)
                return {"reviewToken": token, **{k: receipt[k] for k in ("planId", "version", "digest")}}
        except Exception as error:
            return {"status": "rejected", "code": getattr(error, "code", "REVIEW_FAILED"),
                    "errors": [_error(error)]}

    def _save(self, operation, status="executing"):
        try:
            self.database.execution(operation["operationId"], operation["planId"],
                                    operation["idempotencyKey"], status, operation)
        except Exception:
            operation["journalFailed"] = True
            raise

    @staticmethod
    def _result(operation, status):
        return {"operationId": operation["operationId"], "planId": operation["planId"],
                "version": operation["version"], "digest": operation["digest"],
                "status": status, "targets": deepcopy(operation["targets"]),
                "warnings": deepcopy(operation["warnings"]), "errors": deepcopy(operation["errors"])}

    def _finish(self, operation, status, *, committed=False, plan=None):
        result = self._result(operation, status)
        if plan is not None:
            result["plan"] = deepcopy(plan)
        with self.database.transaction() as connection:
            if committed:
                self._resolve_original(connection, operation)
            if committed and self.on_committed:
                updates = self.on_committed(deepcopy(operation["receipt"]), deepcopy(result))
                if updates is not None:
                    if not isinstance(updates, dict) or any(not isinstance(k, str) for k in updates):
                        raise ExecutionError("INVALID_COMMIT_UPDATES", "Commit callback must return a settings dictionary.")
                    for key, value in updates.items():
                        self.database.set(key, value, connection=connection)
            verified = [deepcopy(t) for t in operation["targets"]
                        if t["verifiedAfter"] is not None or t.get("intermediate") is not None]
            if verified:
                history = {"operationId": operation["operationId"], "planId": operation["planId"],
                           "version": operation["version"], "digest": operation["digest"],
                           "intent": deepcopy(operation["receipt"].get("intent")),
                           "status": status, "targets": verified,
                           "errors": deepcopy(operation["errors"]), "warnings": deepcopy(operation["warnings"])}
                self.database.append_history(uuid.uuid4().hex, operation["operationId"], history,
                                             connection=connection)
            body = deepcopy(operation)
            body.update(result=result, commitRecorded=committed, status=status)
            # Rejections/refreshes are terminal attempts but do not consume a preview.
            db_status = "cancelled" if status in ("rejected", "preview_updated") else status
            connection.execute("UPDATE executions SET status=?,body=?,updated_at=? WHERE id=?",
                               (db_status, encode(body), now(), operation["operationId"]))
            if operation.get("claimed"):
                connection.execute("UPDATE previews SET status=?,review_token=NULL WHERE id=? AND version=?",
                                   (status, operation["planId"], operation["version"]))
        operation.update(body)
        return result

    def _restore_or_write(self, target, desired, expected, operation):
        scope, name = target["scope"], target["name"]
        if not desired["exists"]:
            return self.environment.delete(scope, name, expected=expected)
        if expected["exists"] and expected["type"] != desired["type"]:
            # Adapter intentionally preserves existing types. Explicit TYPE_CHANGE
            # therefore has a journalled delete/create intermediate state.
            expected = self.environment.delete(scope, name, expected=expected)
            target["intermediate"] = deepcopy(expected)
            self._save(operation)
        return self.environment.write(scope, name, desired["raw"], desired["type"], expected=expected)

    def _write(self, target, operation):
        target["status"] = "writing"
        operation.setdefault("attemptOrder", []).append(operation["targets"].index(target))
        self._save(operation)  # durable intent BEFORE any environment write
        written = self._restore_or_write(target, target["after"], target["before"], operation)
        target["written"] = deepcopy(written)
        target["status"] = "written"
        self._save(operation)
        current = self.environment.read(target["scope"], target["name"])
        if not _same_target(target, current) or not _same_value(current, target["after"]) or not _same_snapshot(written, current):
            raise ExecutionError("VERIFY_FAILED", "Written value did not verify.", actual=current)
        target["status"], target["verifiedAfter"] = "verified", current
        self._save(operation)

    @staticmethod
    def _helper_plan(target):
        return {"scope": target["scope"], "name": target["name"],
                "before": deepcopy(target["before"]), "after": deepcopy(target["after"])}

    @staticmethod
    def _unknown_reply(reply):
        return (not isinstance(reply, dict)
                or reply.get("status") not in ("prepared", "committed", "success", "compensated",
                                                "rejected", "cancelled", "uac_cancelled", "recovery_required")
                or reply.get("launch_status") == "uncertain"
                or reply.get("owner_lost") is True
                or reply.get("channel_state") == "owner_unknown"
                or (reply.get("status") == "recovery_required" and reply.get("channel_state") != "connected")
                or any(e.get("code") in ("HELPER_UNCERTAIN", "OWNER_UNKNOWN", "EXECUTION_UNKNOWN")
                       for e in reply.get("errors", []) if isinstance(e, dict)))

    def _prepare_system(self, operation, token, holder):
        operation["systemSession"] = True
        operation["systemPhase"] = "prepare_intent"
        operation["systemUncertain"] = True
        self._save(operation)
        try:
            session = self.system_session_factory(deepcopy(operation["receipt"]), token,
                                                  operation["operationId"])
            holder.append(session)  # Also close a partially prepared session.
            reply = session.prepare()
        except Exception as error:
            if getattr(error, "code", None) in (
                    "UAC_CANCELLED", "PREFLIGHT_FAILED", "PERMISSION_DENIED", "ACCESS_DENIED",
                    "REVIEW_REQUIRED", "INVALID_REVIEW", "REVIEW_MISMATCH", "RULES_MISMATCH",
                    "PRODUCTION_REQUIRED", "SYSTEM_NOT_READY"):
                operation["systemUncertain"] = False
                operation["systemPhase"] = "prepare_rejected"
                self._save(operation)
            raise
        operation["systemPrepareResult"] = deepcopy(reply)
        # A preparation recovery result cannot prove that its owner is alive.
        operation["systemUncertain"] = (self._unknown_reply(reply)
                                        or reply.get("status") not in ("prepared", "rejected", "cancelled", "uac_cancelled"))
        ready = isinstance(reply, dict) and reply.get("status") == "prepared" and not operation["systemUncertain"]
        operation["systemPhase"] = "prepared" if ready else "prepare_unknown" if operation["systemUncertain"] else "prepare_rejected"
        if ready:
            for target in operation["targets"]:
                if target["scope"] == "System" and target["changed"]:
                    target["status"] = "prepared"
        self._save(operation)
        if not ready:
            cancelled = isinstance(reply, dict) and reply.get("status") in ("cancelled", "uac_cancelled")
            raise ExecutionError("UAC_CANCELLED" if cancelled else "SYSTEM_PREPARE_FAILED",
                                 "System helper was not prepared; no User writes started.", result=reply)
        return session

    def _commit_system(self, targets, operation, session):
        operation["systemPhase"] = "commit_intent"
        operation["systemCommitAttempted"] = True
        operation["systemUncertain"] = True
        for target in targets:
            target["status"] = "writing"
            operation.setdefault("attemptOrder", []).append(operation["targets"].index(target))
        self._save(operation)
        reply = session.commit()  # Never retry after a lost ACK.
        operation["systemResult"] = deepcopy(reply)
        operation["systemUncertain"] = self._unknown_reply(reply)
        operation["systemPhase"] = "commit_result"
        self._save(operation)  # Retain helper evidence even when local readback fails.
        if operation["systemUncertain"]:
            raise ExecutionError("HELPER_UNCERTAIN", "System result is unknown; reconcile the original operation.")
        success = reply.get("status") in ("committed", "success")
        records = reply.get("targets", [])
        if not isinstance(records, list):
            operation["systemUncertain"] = True
            raise ExecutionError("HELPER_UNCERTAIN", "Invalid System target evidence.")
        for target in targets:
            evidence = [r for r in records if isinstance(r, dict) and _same_target(target, r)]
            record = evidence[0] if len(evidence) == 1 else {}
            target["helperResult"] = deepcopy(record)
            snapshot = record.get("result") or record.get("verifiedAfter")
            # Only helper-acknowledged snapshots establish continuity for rollback.
            if (isinstance(snapshot, dict) and _same_target(target, snapshot)
                    and _same_value(snapshot, target["after"]) and snapshot.get("revision")):
                target["helperVerifiedAfter"] = deepcopy(snapshot)
                current = self.environment.read(target["scope"], target["name"])
                # Revisions belong to individual adapters/processes. Bind this
                # process's continuity to its own read of the acknowledged value.
                if _same_target(target, current) and _same_value(snapshot, current):
                    target["written"] = deepcopy(current)
                    target["status"], target["verifiedAfter"] = "verified", current
            elif success:
                operation["systemUncertain"] = True
                raise ExecutionError("HELPER_UNCERTAIN", "System success lacks acknowledged target evidence.")
            self._save(operation)
            if success and target["verifiedAfter"] is None:
                raise ExecutionError("SYSTEM_VERIFY_FAILED", "System acknowledgement/readback did not verify.")
        if not success:
            raise ExecutionError("SYSTEM_FAILED", "System helper did not commit.", result=reply)

    def _compensate_system(self, operation, session):
        targets = [operation["targets"][i] for i in operation.get("attemptOrder", [])
                   if operation["targets"][i]["scope"] == "System"]
        if not targets:
            return True  # User failed before the prepared helper was told to commit.
        prior = operation.get("systemResult", {})
        if prior.get("status") in ("compensated", "rejected", "recovery_required"):
            # The live helper already rolled back its failed batch, or rejected
            # it before writing. Do not send a second compensation command.
            clean = prior.get("status") != "recovery_required"
            for target in reversed(targets):
                current = self.environment.read(target["scope"], target["name"])
                if _same_target(target, current) and _same_value(current, target["before"]):
                    target["status"], target["verifiedBefore"] = "compensated", current
                else:
                    clean = False
                    target["status"] = "recovery_required"
                self._save(operation)
            return clean
        # The helper also checks its OWN process-local snapshots. Our known
        # continuity loss must veto a restore even if the helper missed an ABA.
        for target in targets:
            evidence = target["verifiedAfter"] or target.get("written")
            if evidence is not None:
                current = self.environment.read(target["scope"], target["name"])
                if not _same_snapshot(evidence, current):
                    target["status"] = "recovery_required"
                    target["recoveryError"] = _error(ExecutionError(
                        "RECOVERY_CONFLICT", "Parent observed a System change; current value retained.", actual=current))
                    self._save(operation)
                    return False
        operation["systemPhase"] = "compensate_intent"
        operation["systemUncertain"] = True
        for target in reversed(targets):
            target["status"] = "compensating"
        self._save(operation)
        try:
            reply = session.conditional_compensate()  # Same helper; no new grant/plan.
            operation["systemCompensationResult"] = deepcopy(reply)
            operation["systemUncertain"] = self._unknown_reply(reply)
            operation["systemPhase"] = "compensate_result"
            self._save(operation)
            if operation["systemUncertain"]:
                raise ExecutionError("HELPER_UNCERTAIN", "System compensation result is unknown.")
            clean = reply.get("status") in ("compensated", "success")
            for target in reversed(targets):
                current = self.environment.read(target["scope"], target["name"])
                if _same_target(target, current) and _same_value(current, target["before"]):
                    target["status"], target["verifiedBefore"] = "compensated", current
                else:
                    clean = False
                    target["status"] = "recovery_required"
                    target["recoveryError"] = _error(ExecutionError(
                        "RECOVERY_CONFLICT", "System restoration did not verify; current value retained.", actual=current))
                self._save(operation)
            return clean
        except Exception as error:
            operation["errors"].append(_error(error))
            return False

    def _close_system(self, operation, holder):
        if not holder:
            return
        try:
            holder[0].finish()  # Cleanup only, including partially prepared/lost sessions.
        except Exception as error:
            # Cleanup cannot change a durably committed result into a rollback.
            operation["warnings"].append(_error(ExecutionError(
                "SYSTEM_FINISH_FAILED", "System session cleanup failed.", cause=_error(error))))

    def _system(self, targets, operation):
        for target in targets:
            target["status"] = "writing"
        operation.setdefault("attemptOrder", []).extend(operation["targets"].index(t) for t in targets)
        operation["systemUncertain"] = True
        self._save(operation)
        try:
            reply = self.system_runner([self._helper_plan(t) for t in targets], restore=False)
        except Exception as error:
            if getattr(error, "code", None) == "UAC_CANCELLED":
                operation["systemUncertain"] = False
            raise
        operation["systemResult"] = deepcopy(reply)
        operation["systemUncertain"] = (not isinstance(reply, dict)
                                        or reply.get("status") not in ("committed", "success", "compensated", "rejected", "cancelled", "uac_cancelled")
                                        or reply.get("launch_status") == "uncertain")
        # Never trust a helper's success without observing each exact frozen after.
        success = isinstance(reply, dict) and reply.get("status") in ("committed", "success")
        for target in targets:
            current = self.environment.read(target["scope"], target["name"])
            if _same_target(target, current) and _same_value(current, target["after"]):
                target["status"], target["verifiedAfter"] = "verified", current
            elif success:
                raise ExecutionError("SYSTEM_VERIFY_FAILED", "System helper result did not verify.")
        self._save(operation)
        if not success:
            cancelled = isinstance(reply, dict) and reply.get("status") in ("cancelled", "uac_cancelled")
            raise ExecutionError("UAC_CANCELLED" if cancelled else "SYSTEM_FAILED",
                                 "System helper did not commit.", result=reply)

    def _compensate(self, operation, system_session=None):
        if operation.get("systemSession") and operation.get("systemUncertain"):
            return False  # Lost owner cannot authorize ANY more automatic mutation.
        clean = True
        if system_session is not None:
            try:
                clean = self._compensate_system(operation, system_session)
            except Exception as error:
                operation["errors"].append(_error(error))
                return False
            if operation.get("systemUncertain") or operation.get("journalFailed"):
                return False
        for index in reversed(operation.get("attemptOrder", [])):
            target = operation["targets"][index]
            if operation.get("systemSession") and target["scope"] == "System":
                continue
            if not target["changed"] or target["status"] == "pending":
                continue
            try:
                current = self.environment.read(target["scope"], target["name"])
                if (target["scope"] == "System" and isinstance(self.environment, WindowsEnvironment)
                        and operation.get("systemUncertain")):
                    raise ExecutionError("HELPER_UNCERTAIN", "Uncertain helper requires observation, never an automatic restore.")
                if not _same_target(target, current):
                    raise ExecutionError("RECOVERY_CONFLICT", "Target identity changed.")
                if _same_value(current, target["before"]):
                    target["status"], target["verifiedBefore"] = "unchanged", current
                    self._save(operation)
                    continue
                evidence = target["verifiedAfter"] or target.get("written")
                intermediate = target.get("intermediate")
                if intermediate and _same_snapshot(current, intermediate):
                    evidence = intermediate
                elif evidence is not None and not _same_value(evidence, target["after"]):
                    evidence = None  # An acknowledged unexpected value is not the frozen after.
                if evidence is None or not _same_snapshot(current, evidence):
                    raise ExecutionError("RECOVERY_CONFLICT", "External change retained.", actual=current)
                if _same_value(current, target["after"]):
                    target["verifiedAfter"] = current
                target["status"] = "compensating"
                self._save(operation)  # no unjournalled recovery writes
                if isinstance(self.environment, WindowsEnvironment) and target["scope"] == "System":
                    reply = self.system_runner([self._helper_plan(target)], restore=True)
                    target["restoreResult"] = deepcopy(reply)
                    if not isinstance(reply, dict) or reply.get("status") not in ("committed", "compensated", "success"):
                        raise ExecutionError("RESTORE_FAILED", "System restoration was not confirmed.")
                else:
                    self._restore_or_write(target, target["before"], current, operation)
                restored = self.environment.read(target["scope"], target["name"])
                if not _same_target(target, restored) or not _same_value(restored, target["before"]):
                    raise ExecutionError("RESTORE_VERIFY_FAILED", "Restored value did not verify.", actual=restored)
                target["status"], target["verifiedBefore"] = "compensated", restored
                self._save(operation)
            except Exception as error:
                clean = False
                target["status"], target["recoveryError"] = "recovery_required", _error(error)
                operation["errors"].append(_error(error))
                if operation.get("journalFailed"):
                    break  # Disk failure stops every further recovery mutation too.
        return clean

    def _notify(self, operation):
        if not any(t["verifiedAfter"] is not None or t.get("intermediate") is not None
                   for t in operation["targets"]):
            return
        try:
            reply = self.environment.notify()
            if reply.get("notified") is not True and reply.get("reason") != "isolated_file_backend":
                raise ExecutionError("NOTIFY_FAILED", "Environment notification was not confirmed.")
        except Exception as error:
            operation["warnings"].append(_error(ExecutionError(
                "NOTIFY_FAILED", "Environment notification was not confirmed.", cause=_error(error))))

    def _notify_committed(self, operation, result):
        """Broadcast only after COMMITTED; never undo a committed environment.

        A crash between commit and broadcast may leave notification undelivered.
        Retrying an operation only returns its result; it never rebroadcasts.
        """
        self._notify(operation)
        if operation["warnings"] == result["warnings"]:
            return result
        result["warnings"] = deepcopy(operation["warnings"])
        operation["result"] = deepcopy(result)
        try:
            with self.database.transaction() as connection:
                connection.execute("UPDATE executions SET body=?,updated_at=? WHERE id=?",
                                   (encode(operation), now(), operation["operationId"]))
                row = connection.execute("SELECT body FROM history WHERE execution_id=?",
                                         (operation["operationId"],)).fetchone()
                if row:
                    history = json.loads(row[0])
                    history["warnings"] = deepcopy(operation["warnings"])
                    connection.execute("UPDATE history SET body=? WHERE execution_id=?",
                                       (encode(history), operation["operationId"]))
        except Exception as error:
            # COMMITTED is already durable. Report this warning on this response;
            # persistence failure must not turn it into an uncommitted operation.
            result["warnings"].append(_error(ExecutionError(
                "NOTIFY_JOURNAL_FAILED", "Notification warning could not be saved.", cause=_error(error))))
        return result

    def _recovery(self, operation, error):
        operation["errors"].append(_error(error))
        operation["status"] = "recovery_required"
        result = self._result(operation, "recovery_required")
        operation["result"] = result
        try:
            self._save(operation, "recovery_required")
        except Exception as journal_error:
            # The earlier prepared/executing record remains a startup write block.
            result["errors"].append(_error(journal_error))
        return result

    def execute(self, request):
        try:
            self._request(request, execute=True)
        except Exception as error:
            return {"status": "rejected", "code": getattr(error, "code", "INVALID_REQUEST"),
                    "errors": [_error(error)]}
        with self.lock:
            operation = None
            try:
                previous = self.database.find_execution(request["idempotencyKey"])
                if previous:
                    body = previous["body"]
                    if any(body.get(k) != request[k] for k in ("planId", "version", "digest")):
                        raise ExecutionError("IDEMPOTENCY_CONFLICT", "Key is bound to another receipt.")
                    return deepcopy(body.get("result") or self._result(body, "recovery_required"))
                operation = {"operationId": uuid.uuid4().hex,
                             **{k: request[k] for k in ("planId", "version", "digest", "idempotencyKey")},
                             "status": "prepared", "targets": [], "warnings": [], "errors": [],
                             "claimed": False, "commitRecorded": False}
                self._save(operation, "prepared")  # claim key before checking live dependencies
                row, receipt = self._receipt(request)
                token = row["review_token"]
                if not isinstance(token, str) or not hmac.compare_digest(token.encode(), request["reviewToken"].encode()):
                    raise ExecutionError("INVALID_REVIEW_TOKEN", "Review token is invalid or consumed.")
                operation["receipt"] = receipt
                updated = self._refresh(receipt)
                if updated:
                    return self._finish(operation, "preview_updated", plan=updated["plan"])
                operation["targets"] = self._targets(receipt)
                self._pending_gate(receipt, operation["targets"], operation["operationId"])
                self._preflight(operation["targets"])
                operation["claimed"] = True
                # Consume token + seal the fully frozen write-ahead record atomically.
                with self.database.transaction() as connection:
                    consumed = connection.execute(
                        "UPDATE previews SET status='executing',review_token=NULL "
                        "WHERE id=? AND version=? AND digest=? AND status='ready' AND review_token=?",
                        (operation["planId"], operation["version"], operation["digest"], token))
                    if consumed.rowcount != 1:
                        operation["claimed"] = False
                        raise ExecutionError("INVALID_REVIEW_TOKEN", "Review token is invalid or consumed.")
                    connection.execute("UPDATE executions SET status='prepared',body=?,updated_at=? WHERE id=?",
                                       (encode(operation), now(), operation["operationId"]))
            except Exception as error:
                if operation is not None:
                    operation["errors"].append(_error(error))
                    try:
                        return self._finish(operation, "rejected")
                    except Exception as journal_error:
                        return self._recovery(operation, journal_error)
                return {"status": "rejected", "code": getattr(error, "code", "EXECUTION_FAILED"),
                        "errors": [_error(error)]}
            return self._execute_claimed(operation, token)

    def _execute_claimed(self, operation, token):
        holder = []
        result = None
        try:
            result = self._run_claimed(operation, token, holder)
        finally:
            cleanup_warnings = len(operation["warnings"])
            self._close_system(operation, holder)
        if len(operation["warnings"]) > cleanup_warnings:
            result["warnings"].extend(deepcopy(operation["warnings"][cleanup_warnings:]))
            operation["result"] = deepcopy(result)
            try:
                # Preserve the existing terminal classification on cleanup failure.
                with self.database.transaction() as connection:
                    connection.execute("UPDATE executions SET body=?,updated_at=? WHERE id=?",
                                       (encode(operation), now(), operation["operationId"]))
            except Exception:
                pass  # Original durable execution/result still wins on restart.
        return result

    def _run_claimed(self, operation, token, holder):
        session = None
        try:
            system = [t for t in operation["targets"] if t["changed"] and t["scope"] == "System"
                      and isinstance(self.environment, WindowsEnvironment)]
            if system:
                if self.system_session_factory is not None:
                    session = self._prepare_system(operation, token, holder)
                    updated = self._refresh(operation["receipt"])
                    if updated:
                        return self._finish(operation, "preview_updated", plan=updated["plan"])
                    self._preflight(operation["targets"])  # UAC waiting may outlive the preview.
                else:
                    self._system(system, operation)  # Legacy test-double ordering only.
            for target in operation["targets"]:
                if not target["changed"]:
                    target["status"] = "noop"
                elif target not in system:
                    self._write(target, operation)
            if session is not None:
                self._commit_system(system, operation, session)
            # Earlier targets may have changed while later targets were written.
            for target in operation["targets"]:
                current = self.environment.read(target["scope"], target["name"])
                expected = target["verifiedAfter"] if target["changed"] else target["before"]
                if not _same_snapshot(expected, current):
                    raise ExecutionError("VERIFY_FAILED", "Batch final readback changed.", actual=current)
        except Exception as error:
            operation["errors"].append(_error(error))
            if operation.get("journalFailed"):
                return self._recovery(operation, error)
            clean = self._compensate(operation, session)
            self._notify(operation)
            status = ("cancelled" if getattr(error, "code", None) == "UAC_CANCELLED"
                      and not any(t["verifiedAfter"] for t in operation["targets"]) else "compensated")
            if (operation.get("systemSession") and not operation.get("attemptOrder")
                    and status != "cancelled" and clean):
                status = "rejected"
            try:
                return self._finish(operation, status if clean else "recovery_required")
            except Exception as journal_error:
                return self._recovery(operation, journal_error)
        try:
            result = self._finish(operation, "committed", committed=True)
        except Exception as error:
            # Environment is verified; an application-state failure is unresolved,
            # never a fake success or an implicit replay of on_committed.
            return self._recovery(operation, error)
        return self._notify_committed(operation, result)

    def reconcile_pending(self):
        """Observe pending before/after/other; never write or call commit callbacks.

        Equality with before does NOT prove an interrupted write/compensation
        completed. Only a pristine PREPARED record without write intent can be
        cancelled as interrupted; durable per-target restored acknowledgements
        can preserve compensation. Other incomplete attempts need recovery even
        when currently before. Protected System receipts are imported read-only;
        they never substitute for SQLite COMMITTED or authorize more commands.
        """
        reports = []
        with self.lock:
            for pending in self._pending():
                operation = pending["body"]
                if operation.get("systemSession") and self.system_receipt_reader is not None:
                    try:
                        receipt = self.system_receipt_reader(deepcopy(operation))
                        if not isinstance(receipt, dict):
                            raise ExecutionError("SYSTEM_RECEIPT_INVALID", "Protected receipt must be an object.")
                        if receipt.get("trusted") is False:
                            operation["protectedSystemReceiptObservation"] = deepcopy(receipt)
                            raise ExecutionError("SYSTEM_RECEIPT_UNTRUSTED", "Protected receipt could not be verified.",
                                                 errors=deepcopy(receipt.get("errors", [])))
                        operation["protectedSystemReceipt"] = deepcopy(receipt)
                        operation.pop("protectedSystemReceiptError", None)
                    except Exception as error:
                        # Keep previous receipts, original snapshots and delivered
                        # results. Missing/invalid receipts never release the gate.
                        operation["protectedSystemReceiptError"] = _error(error)
                classifications = []
                for target in operation.get("targets", []):
                    try:
                        actual = self.environment.read(target["scope"], target["name"])
                        same_target = _same_target(target, actual)
                        before = same_target and _same_value(actual, target["before"])
                        after = same_target and _same_value(actual, target["after"])
                        classification = "unchanged" if before and after else "before" if before else "after" if after else "other"
                        classifications.append({"scope": target["scope"], "name": target["name"],
                                                "classification": classification, "actual": actual})
                    except Exception as error:
                        classifications.append({"scope": target["scope"], "name": target["name"],
                                                "classification": "unreadable", "error": _error(error)})
                all_before = all(c["classification"] in ("before", "unchanged") for c in classifications)
                all_after = all(c["classification"] in ("after", "unchanged") for c in classifications)
                targets = operation.get("targets", [])
                pristine = (pending["status"] in ("prepared", "executing")
                            and operation.get("status") in ("prepared", "executing")
                            and not operation.get("attemptOrder")
                            and not operation.get("systemCommitAttempted")
                            and not operation.get("journalFailed") and "result" not in operation
                            and all(t.get("status") in ("pending", "noop", "prepared")
                                    and not any(t.get(k) is not None for k in
                                                ("written", "intermediate", "verifiedAfter", "verifiedBefore"))
                                    for t in targets))
                attempted = operation.get("attemptOrder", [])
                restored = (any(t.get("status") == "compensated" for t in targets)
                            and all(not t["changed"]
                                    or (index not in attempted and t.get("status") == "pending")
                                    or (t.get("status") in ("compensated", "unchanged")
                                        and isinstance(t.get("verifiedBefore"), dict)
                                        and _same_target(t, t["verifiedBefore"])
                                        and _same_value(t["before"], t["verifiedBefore"]))
                                    for index, t in enumerate(targets)))
                if operation.get("commitRecorded") and all_after:
                    status, reason = "committed", "durable_commit"
                elif operation.get("systemSession") or operation.get("systemUncertain"):
                    status, reason = "recovery_required", "system_operation_unresolved"
                elif all_before and restored:
                    status, reason = "compensated", "durable_compensation_verified"
                elif all_before and pristine:
                    status, reason = "cancelled", "interrupted_before_write_intent"
                else:
                    status, reason = "recovery_required", "unresolved_execution_evidence"
                operation["reconciliation"] = classifications
                operation["reconciliationReason"] = reason
                operation["status"] = status
                # Keep an already delivered terminal result stable for its key.
                if "result" not in operation:
                    operation["result"] = self._result(operation, status)
                self._save(operation, status)
                reports.append({"operationId": operation["operationId"], "status": status, "reason": reason,
                                "targets": classifications})
        return reports
