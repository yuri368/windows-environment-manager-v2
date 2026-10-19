"""Real temporary file/SQLite integration; no Registry, UAC, or non-stdlib tests."""
from copy import deepcopy
from contextlib import closing, nullcontext
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

from envmanager_v3.database import Database, encode
from envmanager_v3.environment import FileEnvironment, WindowsEnvironment, V3EnvironmentError
from envmanager_v3.execution import Executor, ExecutionError


class Crash(BaseException):
    pass


class ObservedEnvironment(FileEnvironment):
    def __init__(self, path):
        super().__init__(path)
        self.mutations = []
        self.before_mutation = None
        self.after_mutation = None
        self.notification_error = False
        self.denied = set()

    def write(self, scope, name, raw, type=None, *, expected=None):
        self.mutations.append(("write", scope, name, raw, type))
        if self.before_mutation:
            self.before_mutation(scope, name)
        result = super().write(scope, name, raw, type, expected=expected)
        if self.after_mutation:
            self.after_mutation(scope, name)
        return result

    def delete(self, scope, name, *, expected=None):
        self.mutations.append(("delete", scope, name))
        if self.before_mutation:
            self.before_mutation(scope, name)
        result = super().delete(scope, name, expected=expected)
        if self.after_mutation:
            self.after_mutation(scope, name)
        return result

    def check_permission(self, scope):
        return {"scope": scope, "writable": scope not in self.denied}

    def notify(self):
        if self.notification_error:
            raise V3EnvironmentError("NOTIFY_FAILED", "injected notification failure")
        return super().notify()


class FakeWindows(WindowsEnvironment):
    """Only class identity is Windows: every I/O delegates to the temp file."""
    def __init__(self, backing):
        self.backing = backing

    def read(self, *args, **kwargs):
        return self.backing.read(*args, **kwargs)

    def write(self, *args, **kwargs):
        if args[0] != "User":
            raise AssertionError("Executor must not directly write Windows System")
        return self.backing.write(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if args[0] != "User":
            raise AssertionError("Executor must not directly delete Windows System")
        return self.backing.delete(*args, **kwargs)

    def check_permission(self, scope):
        if scope == "System":
            raise V3EnvironmentError("PERMISSION_DENIED", "requires helper")
        return self.backing.check_permission(scope)

    def notify(self):
        return self.backing.notify()


class PreparedFileSession:
    """One live grant over persistent files; rollback requires its exact own ACK."""
    def __init__(self, env, receipt, events):
        self.env, self.receipt, self.events = env, deepcopy(receipt), events
        self.records, self.written = [], []
        self.on_prepare = self.on_commit = self.on_compensate = self.on_finish = None

    def prepare(self):
        self.events.append("prepare")
        if self.on_prepare:
            return self.on_prepare()
        for target in self.receipt["targets"]:
            if target["scope"] == "System":
                actual = self.env.read("System", target["name"])
                if actual != target["beforeSnapshot"]:
                    return {"status": "rejected", "errors": [{"code": "STALE_SNAPSHOT"}]}
        return {"status": "prepared"}

    def commit(self):
        self.events.append("commit")
        if self.on_commit:
            return self.on_commit()
        return self.write_system()

    def write_system(self):
        try:
            for target in self.receipt["targets"]:
                if target["scope"] != "System":
                    continue
                before, after = target["beforeSnapshot"], target["plan"]["after"]
                if all(before[k] == after[k] for k in ("exists", "raw", "type")):
                    continue
                record = {"scope": "System", "name": target["name"], "before": before,
                          "after": after, "status": "writing"}
                self.records.append(record)
                result = self.apply(target["name"], after, before)
                record.update(status="committed", result=result)
                self.written.append(record)
            return {"status": "committed", "targets": deepcopy(self.records), "errors": []}
        except Exception as error:
            # Real helper rolls back its known failed batch before returning its
            # connected phase result; the parent must not send that command again.
            restored = self.rollback()
            return {"status": "compensated" if restored else "recovery_required",
                    "channel_state": "connected", "targets": deepcopy(self.records),
                    "errors": [{"code": getattr(error, "code", "WRITE_FAILED")}]}

    def apply(self, name, desired, expected):
        if not desired["exists"]:
            return self.env.delete("System", name, expected=expected)
        if expected["exists"] and expected["type"] != desired["type"]:
            expected = self.env.delete("System", name, expected=expected)
        return self.env.write("System", name, desired["raw"], desired["type"], expected=expected)

    def conditional_compensate(self):
        self.events.append("compensate")
        if self.on_compensate:
            return self.on_compensate()
        clean = self.rollback()
        return {"status": "compensated" if clean else "recovery_required", "channel_state": "connected", "errors": []}

    def rollback(self):
        clean = True
        for record in reversed(self.written):
            current = self.env.read("System", record["name"])
            if current != record["result"]:
                clean = False
                continue
            restored = self.apply(record["name"], record["before"], current)
            record.update(status="compensated", result=restored)
        return clean

    def finish(self):
        self.events.append("finish")
        if self.on_finish:
            self.on_finish()


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.env = ObservedEnvironment(self.root / "environment.json")
        self.db = Database(self.root / "state.sqlite3")
        self.addCleanup(lambda: self.db.close())
        self.executor = Executor(self.env, self.db)

    def seed(self, scope, name, raw, value_type="REG_SZ"):
        return FileEnvironment.write(self.env, scope, name, raw, value_type)

    def receipt(self, changes, *, plan_id="p1", version=1):
        targets = []
        for scope, name, raw in changes:
            before = self.env.read(scope, name)
            after = {"exists": raw is not None, "raw": raw,
                     "type": (before["type"] if before["exists"] else "REG_EXPAND_SZ") if raw is not None else None}
            changed = any(before[k] != after[k] for k in ("exists", "raw", "type"))
            targets.append({"scope": scope, "name": name, "beforeSnapshot": before,
                            "members": [f"record-{name}"],
                            "plan": {"targetKey": {"scope": scope, "name": name},
                                     "before": {k: before[k] for k in ("exists", "raw", "type")},
                                     "after": after, "operations": [{"type": "replace"}] if changed else [],
                                     "valid": True, "issues": [], "mode": "replace", "special": False,
                                     "audit": {"exact": "frozen audit"}}})
        receipt = {"planId": plan_id, "version": version, "targets": targets,
                   "dependencies": {"workspaceRevision": 1}}
        receipt["digest"] = hashlib.sha256(encode(receipt).encode()).hexdigest()
        self.db.preview(receipt)
        return receipt

    @staticmethod
    def identifiers(receipt):
        return {k: receipt[k] for k in ("planId", "version", "digest")}

    def request(self, receipt, key="request-1", executor=None):
        reviewed = (executor or self.executor).review(self.identifiers(receipt))
        self.assertIn("reviewToken", reviewed, reviewed)
        return {**self.identifiers(receipt), "reviewToken": reviewed["reviewToken"], "idempotencyKey": key}

    def execute(self, receipt, key="request-1", executor=None):
        executor = executor or self.executor
        return executor.execute(self.request(receipt, key, executor))

    @staticmethod
    def codes(result):
        codes = {result.get("code")}
        for error in result.get("errors", []):
            codes.add(error["code"])
            codes.update(e["code"] for e in error.get("details", {}).get("errors", []))
        return codes

    def test_success_restart_history_and_exact_raw_type_absence(self):
        self.seed("User", "A", "before\r\n; ;", "REG_EXPAND_SZ")
        self.seed("System", "Delete", "")
        receipt = self.receipt([("User", "A", "  %RAW%;\r\n;;"), ("System", "Empty", ""),
                                ("System", "Delete", None)])
        result = self.execute(receipt)
        self.assertEqual(result["status"], "committed", result)
        self.assertEqual(self.env.read("User", "A")["raw"], "  %RAW%;\r\n;;")
        self.assertEqual(self.env.read("User", "A")["type"], "REG_EXPAND_SZ")
        self.assertTrue(self.env.read("System", "Empty")["exists"])
        self.assertFalse(self.env.read("System", "Delete")["exists"])
        self.assertEqual([t["status"] for t in result["targets"]], ["verified"] * 3)
        self.db.close()
        self.db = Database(self.root / "state.sqlite3")
        history = self.db.history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["targets"][0]["plan"], receipt["targets"][0]["plan"])
        self.assertEqual(self.db.find_execution("request-1")["body"]["result"], result)
        restarted = Executor(FileEnvironment(self.env.path), self.db)
        self.assertEqual(restarted.reconcile_pending(), [])

    def test_rejects_extra_client_execution_fields_even_on_idempotent_retry(self):
        receipt = self.receipt([("User", "A", "new")])
        request = self.request(receipt)
        for extra in ("after", "operations", "targets", "force", "beforeSnapshot"):
            with self.subTest(extra=extra):
                result = self.executor.execute({**request, extra: []})
                self.assertIn("INVALID_REQUEST", self.codes(result))
        self.assertEqual(self.env.mutations, [])
        result = self.executor.execute(request)
        self.assertEqual(result["status"], "committed")
        self.assertIn("INVALID_REQUEST", self.codes(self.executor.execute({**request, "after": "bad"})))

    def test_version_digest_token_and_one_use_rejections(self):
        receipt = self.receipt([("User", "A", "new")])
        request = self.request(receipt)
        for key, value, code in (("version", 2, "VERSION_MISMATCH"),
                                 ("digest", "forged", "DIGEST_MISMATCH"),
                                 ("reviewToken", "forged", "INVALID_REVIEW_TOKEN")):
            with self.subTest(field=key):
                bad = {**request, key: value, "idempotencyKey": f"bad-{key}"}
                self.assertIn(code, self.codes(self.executor.execute(bad)))
        self.assertEqual(self.env.mutations, [])
        self.assertEqual(self.executor.execute(request)["status"], "committed")
        self.assertIn("PLAN_CONSUMED", self.codes(self.executor.execute({**request, "idempotencyKey": "new-key"})))
        self.assertEqual(len(self.env.mutations), 1)

    def test_rereview_invalidates_previous_token(self):
        receipt = self.receipt([("User", "A", "new")])
        old = self.request(receipt)
        fresh = self.request(receipt, "fresh")
        self.assertIn("INVALID_REVIEW_TOKEN", self.codes(self.executor.execute(old)))
        self.assertEqual(self.executor.execute(fresh)["status"], "committed")

    def test_all_targets_preflight_stale_means_zero_writes(self):
        self.seed("User", "A", "old")
        self.seed("System", "B", "old")
        receipt = self.receipt([("User", "A", "new"), ("System", "B", "new")])
        request = self.request(receipt)
        FileEnvironment.write(self.env, "System", "B", "external")
        result = self.executor.execute(request)
        self.assertIn("STALE_SNAPSHOT", self.codes(result))
        self.assertEqual(self.env.mutations, [])
        self.assertEqual(self.env.read("User", "A")["raw"], "old")

    def test_observed_aba_revision_blocks_write(self):
        self.seed("User", "A", "old")
        receipt = self.receipt([("User", "A", "new")])
        request = self.request(receipt)
        FileEnvironment.write(self.env, "User", "A", "other")
        FileEnvironment.write(self.env, "User", "A", "old")
        self.assertIn("STALE_SNAPSHOT", self.codes(self.executor.execute(request)))
        self.assertEqual(self.env.mutations, [])

    def test_permission_failure_in_last_target_prevents_entire_batch(self):
        receipt = self.receipt([("User", "A", "new"), ("System", "B", "new")])
        request = self.request(receipt)
        self.env.denied.add("System")
        self.assertIn("PERMISSION_DENIED", self.codes(self.executor.execute(request)))
        self.assertEqual(self.env.mutations, [])

    def fail_b(self, scope, name):
        if name == "B":
            raise OSError("injected second-target write failure")

    def test_second_write_failure_compensates_and_history_is_actual_subset(self):
        self.seed("User", "A", "old")
        receipt = self.receipt([("User", "A", "new"), ("User", "B", "second")])
        self.env.before_mutation = self.fail_b
        result = self.execute(receipt)
        self.assertEqual(result["status"], "compensated", result)
        self.assertEqual(self.env.read("User", "A")["raw"], "old")
        self.assertFalse(self.env.read("User", "B")["exists"])
        history = self.db.history()[0]
        self.assertEqual(history["status"], "compensated")
        self.assertEqual([t["name"] for t in history["targets"]], ["A"])
        self.assertEqual(history["targets"][0]["verifiedBefore"]["raw"], "old")

    def test_compensation_preserves_missing_empty_and_type(self):
        for name, before_raw, after_raw, value_type in (("Missing", None, "", "REG_SZ"),
                                                       ("Empty", "", "new", "REG_EXPAND_SZ"),
                                                       ("Delete", "%RAW%", None, "REG_EXPAND_SZ")):
            with self.subTest(name=name):
                if before_raw is not None:
                    self.seed("User", name, before_raw, value_type)
                receipt = self.receipt([("User", name, after_raw), ("User", "B", "fail")], plan_id=name)
                self.env.before_mutation = self.fail_b
                result = self.execute(receipt, name)
                self.assertEqual(result["status"], "compensated", result)
                actual = self.env.read("User", name)
                self.assertEqual(actual["exists"], before_raw is not None)
                self.assertEqual(actual["raw"], before_raw)
                self.assertEqual(actual["type"], value_type if before_raw is not None else None)

    def test_external_change_during_failure_is_never_overwritten(self):
        self.seed("User", "A", "old")
        receipt = self.receipt([("User", "A", "new"), ("User", "B", "second")])
        def fail(scope, name):
            if name == "B":
                FileEnvironment.write(self.env, "User", "A", "external")
                raise OSError("second write failed after external change")
        self.env.before_mutation = fail
        result = self.execute(receipt)
        self.assertEqual(result["status"], "recovery_required", result)
        self.assertEqual(self.env.read("User", "A")["raw"], "external")
        self.assertEqual([m[3] for m in self.env.mutations if m[2] == "A"], ["new"])
        history = self.db.history()[0]
        self.assertEqual(history["status"], "recovery_required")
        self.assertEqual(history["targets"][0]["verifiedAfter"]["raw"], "new")

    def test_final_readback_detects_earlier_target_changed_during_later_write(self):
        self.seed("User", "A", "old")
        receipt = self.receipt([("User", "A", "new"), ("User", "B", "second")])
        def external(scope, name):
            if name == "B":
                FileEnvironment.write(self.env, "User", "A", "external")
        self.env.after_mutation = external
        result = self.execute(receipt)
        self.assertEqual(result["status"], "recovery_required")
        self.assertEqual(self.env.read("User", "A")["raw"], "external")
        self.assertFalse(self.env.read("User", "B")["exists"])

    def test_same_key_returns_persisted_result_without_writes_or_callback(self):
        calls = []
        executor = Executor(self.env, self.db, on_committed=lambda r, result: calls.append(result))
        receipt = self.receipt([("User", "A", "new")])
        request = self.request(receipt, executor=executor)
        first = executor.execute(request)
        restart = Executor(FileEnvironment(self.env.path), self.db,
                           on_committed=lambda *_: self.fail("callback replayed"))
        self.assertEqual(restart.execute(request), first)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(self.env.mutations), 1)
        other = self.receipt([("User", "B", "other")], plan_id="p2")
        conflicting = self.request(other)
        self.assertIn("IDEMPOTENCY_CONFLICT", self.codes(restart.execute(conflicting)))
        self.assertEqual(len(self.env.mutations), 1)

    def test_notification_failure_warns_without_reverting_commit(self):
        receipt = self.receipt([("User", "A", "new")])
        self.env.notification_error = True
        result = self.execute(receipt)
        self.assertEqual(result["status"], "committed", result)
        self.assertEqual(result["warnings"][0]["code"], "NOTIFY_FAILED")
        self.assertEqual(self.env.read("User", "A")["raw"], "new")
        self.assertEqual(len(self.db.history()), 1)

    def test_zero_operations_commit_selection_without_environment_history(self):
        self.seed("User", "A", "")
        receipt = self.receipt([("User", "A", "")])
        seen = []
        def commit(receipt, result):
            seen.append((receipt, result))
            return {"selection": {"lastAppliedSelected": "", "pending": False}}
        executor = Executor(self.env, self.db, on_committed=commit)
        result = self.execute(receipt, executor=executor)
        self.assertEqual(result["status"], "committed")
        self.assertEqual(result["targets"][0]["status"], "noop")
        self.assertEqual(self.env.mutations, [])
        self.assertEqual(self.db.history(), [])
        self.assertEqual(len(seen), 1)
        self.assertEqual(self.db.get("selection"), {"lastAppliedSelected": "", "pending": False})

    def test_commit_callback_and_history_share_transaction(self):
        receipt = self.receipt([("User", "A", "new")])
        calls = []
        def callback(receipt, result):
            self.assertTrue(self.db.connection.in_transaction)
            calls.append(result)
            return {"selected": "new"}
        executor = Executor(self.env, self.db, on_committed=callback)
        append = self.db.append_history
        def fail_history(*args, **kwargs):
            self.assertEqual(self.db.get("selected"), "new")
            raise sqlite3.OperationalError("injected history disk full")
        self.db.append_history = fail_history
        result = self.execute(receipt, executor=executor)
        self.db.append_history = append
        self.assertEqual(result["status"], "recovery_required", result)
        self.assertEqual(self.env.read("User", "A")["raw"], "new")
        self.assertIsNone(self.db.get("selected"))
        self.assertEqual(self.db.history(), [])
        self.assertFalse(self.db.find_execution("request-1")["body"]["commitRecorded"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(executor.reconcile_pending()[0]["status"], "recovery_required")

    def test_callback_failure_and_zeroop_callback_failure_are_recovery(self):
        for changed in (True, False):
            with self.subTest(changed=changed):
                name = "new" if changed else "noop"
                self.seed("User", name, "old")
                receipt = self.receipt([("User", name, "after" if changed else "old")], plan_id=name)
                executor = Executor(self.env, self.db, on_committed=lambda *_: (_ for _ in ()).throw(OSError("state failure")))
                result = self.execute(receipt, key=name, executor=executor)
                self.assertEqual(result["status"], "recovery_required")
                self.assertEqual(self.db.history(), [])
                # Independent next subcase must not be blocked by this one.
                self.db.connection.execute("UPDATE executions SET status='cancelled'")

    def test_revalidate_review_persists_new_preview_without_token(self):
        receipt = self.receipt([("User", "A", "first")])
        replacement = deepcopy(receipt)
        replacement.update(version=2, digest="new-digest")
        executor = Executor(self.env, self.db, revalidate=lambda old: replacement)
        result = executor.review(self.identifiers(receipt))
        self.assertEqual(result, {"status": "preview_updated", "plan": replacement})
        self.assertIsNone(self.db.read_preview(receipt["planId"])["review_token"])
        self.assertEqual(self.env.mutations, [])

    def test_revalidate_execute_updates_only_preview_and_idempotent_result(self):
        receipt = self.receipt([("User", "A", "first")])
        request = self.request(receipt)
        replacement = deepcopy(receipt)
        replacement.update(version=2, digest="new-digest")
        calls = []
        executor = Executor(self.env, self.db, revalidate=lambda old: calls.append(old) or replacement)
        result = executor.execute(request)
        self.assertEqual(result["status"], "preview_updated", result)
        self.assertEqual(result["plan"], replacement)
        self.assertEqual(executor.execute(request), result)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.env.mutations, [])
        self.assertEqual(self.db.unfinished(), [])

    def test_write_intent_crash_at_before_requires_recovery_without_replaying(self):
        receipt = self.receipt([("User", "A", "new")])
        request = self.request(receipt)
        self.env.before_mutation = lambda *_: (_ for _ in ()).throw(Crash())
        with self.assertRaises(Crash):
            self.executor.execute(request)
        self.env.before_mutation = None
        mutations = list(self.env.mutations)
        restarted = Executor(FileEnvironment(self.env.path), self.db)
        report = restarted.reconcile_pending()
        self.assertEqual(report[0]["status"], "recovery_required", report)
        self.assertEqual(report[0]["targets"][0]["classification"], "before")
        self.assertEqual(self.env.mutations, mutations)
        self.assertFalse(self.env.read("User", "A")["exists"])
        self.assertEqual(self.db.history(), [])
        self.assertEqual(restarted.execute(request)["status"], "recovery_required")

    def test_crash_after_environment_write_requires_recovery_and_blocks_new_write(self):
        receipt = self.receipt([("User", "A", "new")])
        request = self.request(receipt)
        self.env.after_mutation = lambda *_: (_ for _ in ()).throw(Crash())
        with self.assertRaises(Crash):
            self.executor.execute(request)
        self.env.after_mutation = None
        restarted = Executor(FileEnvironment(self.env.path), self.db)
        reports = restarted.reconcile_pending()
        self.assertEqual(reports[0]["status"], "recovery_required")
        self.assertEqual(reports[0]["targets"][0]["classification"], "after")
        self.assertEqual(restarted.execute(request)["status"], "recovery_required")
        other = self.receipt([("User", "B", "other")], plan_id="other")
        other_request = self.request(other, "other-request")
        result = restarted.execute(other_request)
        self.assertIn("RECOVERY_REQUIRED", self.codes(result))
        self.assertFalse(self.env.read("User", "B")["exists"])

    def test_prepared_journal_is_durable_before_first_environment_write(self):
        receipt = self.receipt([("User", "A", "new")])
        observed = []
        def inspect(scope, name):
            with closing(sqlite3.connect(self.db.path)) as other:
                row = other.execute("SELECT status,body FROM executions").fetchone()
            observed.append(json.loads(row[1]))
            self.assertEqual(row[0], "executing")
            self.assertEqual(observed[-1]["targets"][0]["status"], "writing")
            self.assertEqual(observed[-1]["receipt"], receipt)
        self.env.before_mutation = inspect
        self.assertEqual(self.execute(receipt)["status"], "committed")
        self.assertEqual(len(observed), 1)

    def test_no_default_system_helper_or_uac(self):
        receipt = self.receipt([("System", "S", "system"), ("User", "U", "user")])
        executor = Executor(FakeWindows(self.env), self.db)
        reviewed = executor.review(self.identifiers(receipt))
        self.assertIn("SYSTEM_RUNNER_REQUIRED", self.codes(reviewed))
        self.assertEqual(self.env.mutations, [])

    def test_fake_system_runner_cancel_does_not_write_user(self):
        receipt = self.receipt([("User", "U", "user"), ("System", "S", "system")])
        calls = []
        def runner(plans, restore=False):
            calls.append((plans, restore))
            self.assertEqual({p["scope"] for p in plans}, {"System"})
            self.assertEqual(self.env.mutations, [])
            return {"status": "cancelled", "targets": [], "errors": []}
        executor = Executor(FakeWindows(self.env), self.db, system_runner=runner)
        result = self.execute(receipt, executor=executor)
        self.assertEqual(result["status"], "cancelled", result)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.env.mutations, [])
        self.assertEqual(self.db.history(), [])

    def system_runner(self, plans, restore=False):
        # Match the actual helper's strict payload contract without launching it.
        from envmanager_v3.system_helper import validate_request
        validate_request({"protocol": 1, "job_id": "a" * 32, "restore": restore, "plans": plans})
        for plan in plans:
            desired = plan["before"] if restore else plan["after"]
            expected = self.env.read(plan["scope"], plan["name"])
            if desired["exists"]:
                self.env.write(plan["scope"], plan["name"], desired["raw"], desired["type"], expected=expected)
            else:
                self.env.delete(plan["scope"], plan["name"], expected=expected)
        return {"status": "compensated" if restore else "committed", "targets": plans, "errors": []}

    def test_system_executes_before_user_and_restores_original_before_on_failure(self):
        self.seed("System", "S", "old-system", "REG_EXPAND_SZ")
        receipt = self.receipt([("User", "B", "user"), ("System", "S", "new-system")])
        self.env.before_mutation = self.fail_b
        calls = []
        def runner(plans, restore=False):
            calls.append((deepcopy(plans), restore))
            return self.system_runner(plans, restore)
        executor = Executor(FakeWindows(self.env), self.db, system_runner=runner)
        result = self.execute(receipt, executor=executor)
        self.assertEqual(result["status"], "compensated", result)
        self.assertEqual(self.env.read("System", "S")["raw"], "old-system")
        self.assertEqual([c[1] for c in calls], [False, True])
        self.assertEqual(calls[1][0][0]["before"]["raw"], "old-system")
        self.assertEqual(calls[1][0][0]["after"]["raw"], "new-system")
        self.assertEqual([(m[1], m[2]) for m in self.env.mutations], [("System", "S"), ("User", "B"), ("System", "S")])

    def test_system_success_then_user_and_file_system_never_calls_helper(self):
        receipt = self.receipt([("User", "U", "user"), ("System", "S", "system")])
        executor = Executor(FakeWindows(self.env), self.db, system_runner=self.system_runner)
        result = self.execute(receipt, executor=executor)
        self.assertEqual(result["status"], "committed", result)
        self.assertEqual([m[1] for m in self.env.mutations], ["System", "User"])
        other = self.receipt([("System", "File", "isolated")], plan_id="file")
        executor = Executor(self.env, self.db, system_runner=lambda *_: self.fail("FileEnvironment used UAC runner"))
        self.assertEqual(self.execute(other, "file", executor)["status"], "committed")

    def test_uncertain_system_helper_does_not_restore_or_start_user(self):
        receipt = self.receipt([("User", "U", "user"), ("System", "S", "system")])
        calls = []
        def uncertain(plans, restore=False):
            calls.append(restore)
            self.system_runner(plans)
            return {"status": "recovery_required", "launch_status": "uncertain", "targets": [], "errors": []}
        executor = Executor(FakeWindows(self.env), self.db, system_runner=uncertain)
        result = self.execute(receipt, executor=executor)
        self.assertEqual(result["status"], "recovery_required", result)
        self.assertEqual(calls, [False])
        self.assertFalse(self.env.read("User", "U")["exists"])
        self.assertEqual(self.env.read("System", "S")["raw"], "system")

    def test_explicit_type_change_is_not_zeroop_and_can_compensate(self):
        self.seed("User", "A", "same", "REG_SZ")
        receipt = self.receipt([("User", "A", "same"), ("User", "B", "fail")])
        receipt["targets"][0]["plan"]["after"]["type"] = "REG_EXPAND_SZ"
        receipt["targets"][0]["plan"]["operations"] = [{"type": "TYPE_CHANGE"}]
        self.db.preview(receipt)
        self.env.before_mutation = self.fail_b
        result = self.execute(receipt)
        self.assertEqual(result["status"], "compensated", result)
        self.assertEqual(self.env.read("User", "A")["type"], "REG_SZ")
        self.assertEqual([m[0] for m in self.env.mutations if m[2] == "A"], ["delete", "write", "delete", "write"])

    def test_concurrent_executors_share_one_lock_and_one_durable_result(self):
        receipt = self.receipt([("User", "A", "new")])
        request = self.request(receipt)
        other = Executor(self.env, self.db)
        self.assertIs(self.executor.lock, other.lock)
        barrier, results = threading.Barrier(3), []
        def work(executor):
            barrier.wait()
            results.append(executor.execute(request))
        threads = [threading.Thread(target=work, args=(e,)) for e in (self.executor, other)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0]["status"], "committed")
        self.assertEqual(len(self.env.mutations), 1)

    def test_snapshot_and_plan_before_must_agree_for_the_entire_batch(self):
        for field, value in (("raw", "forged"), ("type", "REG_SZ"),
                             ("scope", "System"), ("name", "Different"),
                             ("revision", "forged-revision")):
            with self.subTest(field=field):
                receipt = self.receipt([("User", "A", "new"), ("User", "B", "new")])
                receipt["targets"][1]["plan"]["before"][field] = value
                self.db.preview(receipt)
                result = self.executor.review(self.identifiers(receipt))
                self.assertIn("INVALID_RECEIPT", self.codes(result))
                self.assertEqual(self.env.mutations, [])

    def test_invalid_last_target_is_rejected_before_first_write(self):
        for invalid in ("duplicate", "oversize", "unsupported", "absent", "ordering", "operations"):
            with self.subTest(invalid=invalid):
                receipt = self.receipt([("User", "A", "new"), ("User", "B", "new")])
                last = receipt["targets"][-1]
                if invalid == "duplicate":
                    last.update(name="a", beforeSnapshot=deepcopy(receipt["targets"][0]["beforeSnapshot"]))
                elif invalid == "oversize":
                    last["plan"]["after"]["raw"] = "\U0001f600" * 16384
                elif invalid == "unsupported":
                    last["plan"]["after"]["type"] = "REG_BINARY"
                elif invalid == "absent":
                    last["plan"]["after"]["exists"] = False
                elif invalid == "ordering":
                    last["plan"]["needsOrdering"] = True
                else:
                    last["plan"]["operations"] = []
                self.db.preview(receipt)
                self.assertIn("INVALID_RECEIPT", self.codes(self.executor.review(self.identifiers(receipt))))
                self.assertEqual(self.env.mutations, [])

    def test_preflight_collects_all_read_and_permission_errors(self):
        receipt = self.receipt([("User", "A", "new"), ("System", "B", "new")])
        request = self.request(receipt)
        read = self.env.read
        def fail_read(scope, name):
            if name == "A":
                raise V3EnvironmentError("READ_FAILED", "injected read failure")
            return read(scope, name)
        self.env.read = fail_read
        self.env.denied.add("System")
        result = self.executor.execute(request)
        self.assertTrue({"READ_FAILED", "PERMISSION_DENIED"} <= self.codes(result), result)
        self.assertEqual(self.env.mutations, [])

    def test_expected_comparison_blocks_race_after_batch_preflight(self):
        receipt = self.receipt([("User", "A", "new"), ("User", "B", "new")])
        def external(scope, name):
            if name == "A":
                FileEnvironment.write(self.env, scope, name, "external")
        self.env.before_mutation = external
        result = self.execute(receipt)
        self.assertEqual(result["status"], "recovery_required", result)
        self.assertEqual(self.env.read("User", "A")["raw"], "external")
        self.assertFalse(self.env.read("User", "B")["exists"])
        self.assertEqual(self.db.history(), [])

    def test_observed_aba_after_write_prevents_compensation(self):
        self.seed("User", "A", "old")
        receipt = self.receipt([("User", "A", "new"), ("User", "B", "fail")])
        def external(scope, name):
            if name == "B":
                FileEnvironment.write(self.env, "User", "A", "external")
                FileEnvironment.write(self.env, "User", "A", "new")
                raise OSError("second target failed")
        self.env.before_mutation = external
        result = self.execute(receipt)
        self.assertEqual(result["status"], "recovery_required", result)
        self.assertEqual(self.env.read("User", "A")["raw"], "new")
        self.assertEqual([m[3] for m in self.env.mutations if m[2] == "A"], ["new"])

    def test_adapter_error_after_side_effect_has_no_confirmed_write_evidence(self):
        receipt = self.receipt([("User", "A", "new"), ("User", "B", "later")])
        self.env.after_mutation = lambda *_: (_ for _ in ()).throw(OSError("lost adapter acknowledgement"))
        result = self.execute(receipt)
        self.assertEqual(result["status"], "recovery_required", result)
        self.assertEqual(self.env.read("User", "A")["raw"], "new")
        self.assertFalse(self.env.read("User", "B")["exists"])
        self.assertEqual(len(self.env.mutations), 1)
        self.assertEqual(self.db.history(), [])

    def test_type_change_create_failure_restores_confirmed_intermediate(self):
        self.seed("User", "A", "same", "REG_SZ")
        receipt = self.receipt([("User", "A", "same")])
        receipt["targets"][0]["plan"]["after"]["type"] = "REG_EXPAND_SZ"
        receipt["targets"][0]["plan"]["operations"] = [{"type": "TYPE_CHANGE"}]
        self.db.preview(receipt)
        def fail_create(scope, name):
            if len(self.env.mutations) == 2:
                raise OSError("create after type-change delete failed")
        self.env.before_mutation = fail_create
        result = self.execute(receipt)
        self.assertEqual(result["status"], "compensated", result)
        self.assertEqual(self.env.read("User", "A")["type"], "REG_SZ")
        self.assertEqual(self.env.read("User", "A")["raw"], "same")
        self.assertEqual(result["targets"][0]["status"], "compensated")
        history = self.db.history()[0]
        self.assertEqual(history["status"], "compensated")
        self.assertIsNone(history["targets"][0]["verifiedAfter"])
        self.assertFalse(history["targets"][0]["intermediate"]["exists"])
        self.assertEqual(history["targets"][0]["verifiedBefore"]["raw"], "same")

    def test_journal_failure_before_intent_prevents_environment_writes(self):
        receipt = self.receipt([("User", "A", "new")])
        save = self.db.execution
        def fail_intent(*args):
            if args[-1].get("attemptOrder"):
                raise sqlite3.OperationalError("write-intent journal disk full")
            return save(*args)
        self.db.execution = fail_intent
        result = self.execute(receipt)
        self.assertEqual(result["status"], "recovery_required", result)
        self.assertEqual(self.env.mutations, [])
        self.assertTrue(self.db.unfinished())

    def test_transient_journal_failure_after_first_write_stops_all_mutations(self):
        receipt = self.receipt([("User", "A", "new"), ("User", "B", "later")])
        save, failures = self.db.execution, []
        def fail_written(*args):
            targets = args[-1]["targets"]
            if targets and targets[0]["status"] == "written" and not failures:
                failures.append(True)
                raise sqlite3.OperationalError("journal temporarily unavailable")
            return save(*args)
        self.db.execution = fail_written
        result = self.execute(receipt)
        self.assertEqual(result["status"], "recovery_required", result)
        self.assertEqual(self.env.read("User", "A")["raw"], "new")
        self.assertFalse(self.env.read("User", "B")["exists"])
        self.assertEqual(len(self.env.mutations), 1)
        self.assertEqual(self.db.find_execution("request-1")["body"]["result"], result)

    def test_journal_crash_after_first_write_is_observation_only_on_restart(self):
        receipt = self.receipt([("User", "A", "new"), ("User", "B", "later")])
        request = self.request(receipt)
        save = self.db.execution
        def crash_written(*args):
            targets = args[-1]["targets"]
            if targets and targets[0]["status"] == "written":
                raise Crash()
            return save(*args)
        self.db.execution = crash_written
        with self.assertRaises(Crash):
            self.executor.execute(request)
        self.db.execution = save
        pending = self.db.find_execution("request-1")["body"]
        self.assertEqual(pending["targets"][0]["status"], "writing")
        self.assertEqual(pending["targets"][1]["status"], "pending")
        report = Executor(FileEnvironment(self.env.path), self.db).reconcile_pending()[0]
        self.assertEqual(report["status"], "recovery_required")
        self.assertEqual([t["classification"] for t in report["targets"]], ["after", "before"])
        self.assertEqual(len(self.env.mutations), 1)

    def test_failed_compensation_intent_stops_remaining_restores(self):
        receipt = self.receipt([("User", "A", "a"), ("User", "C", "c"), ("User", "B", "fail")])
        self.env.before_mutation = self.fail_b
        save = self.db.execution
        def fail_restore_intent(*args):
            if any(t["status"] == "compensating" for t in args[-1]["targets"]):
                raise sqlite3.OperationalError("compensation journal disk full")
            return save(*args)
        self.db.execution = fail_restore_intent
        result = self.execute(receipt)
        self.assertEqual(result["status"], "recovery_required", result)
        self.assertEqual(self.env.read("User", "A")["raw"], "a")
        self.assertEqual(self.env.read("User", "C")["raw"], "c")
        self.assertEqual([m[0] for m in self.env.mutations], ["write"] * 3)

    def test_noop_denied_permissions_need_neither_helper_nor_notification(self):
        self.seed("System", "S", "old")
        receipt = self.receipt([("System", "S", "old")])
        self.env.denied.add("System")
        self.env.notify = lambda: self.fail("no-op must not notify")
        executor = Executor(FakeWindows(self.env), self.db)
        result = self.execute(receipt, executor=executor)
        self.assertEqual(result["status"], "committed", result)
        self.assertEqual(self.env.mutations, [])
        self.assertEqual(self.db.history(), [])

    def test_empty_receipt_commits_settings_once_without_history(self):
        receipt = self.receipt([])
        calls = []
        executor = Executor(self.env, self.db, on_committed=lambda r, s: calls.append(s) or {"empty": True})
        request = self.request(receipt, executor=executor)
        result = executor.execute(request)
        self.assertEqual(result["status"], "committed", result)
        self.assertEqual(executor.execute(request), result)
        self.assertEqual(len(calls), 1)
        self.assertIs(self.db.get("empty"), True)
        self.assertEqual(self.db.history(), [])

    def test_invalid_callback_updates_roll_back_selection_and_history(self):
        receipt = self.receipt([("User", "A", "new")])
        executor = Executor(self.env, self.db, on_committed=lambda *_: ["not", "settings"])
        result = self.execute(receipt, executor=executor)
        self.assertEqual(result["status"], "recovery_required", result)
        self.assertIn("INVALID_COMMIT_UPDATES", self.codes(result))
        self.assertEqual(self.db.history(), [])
        self.assertEqual(self.env.read("User", "A")["raw"], "new")

    def test_callbacks_receive_copies_of_frozen_receipt_and_result(self):
        receipt = self.receipt([("User", "A", "new")])
        def callback(r, result):
            r["targets"].clear()
            result["targets"].clear()
            return {"selection": "saved"}
        executor = Executor(self.env, self.db, on_committed=callback)
        result = self.execute(receipt, executor=executor)
        self.assertEqual(len(result["targets"]), 1)
        self.assertEqual(len(self.db.history()[0]["targets"]), 1)
        self.assertEqual(self.db.find_execution("request-1")["body"]["receipt"], receipt)

    def test_refresh_atomically_persists_complete_idempotent_result(self):
        receipt = self.receipt([("User", "A", "first")])
        request = self.request(receipt)
        replacement = deepcopy(receipt)
        replacement.update(version=2, digest="next-digest")
        save = self.db.execution
        def refuse_second_terminal_save(*args):
            if args[3] == "cancelled":
                self.fail("refresh result must be committed in its final transaction")
            return save(*args)
        self.db.execution = refuse_second_terminal_save
        executor = Executor(self.env, self.db, revalidate=lambda _: replacement)
        result = executor.execute(request)
        self.assertEqual(result["status"], "preview_updated", result)
        self.assertEqual(result["plan"], replacement)
        self.assertEqual(self.db.find_execution("request-1")["body"]["result"], result)
        self.assertEqual(executor.execute(request), result)
        self.assertEqual(self.env.mutations, [])

    def test_invalid_refresh_never_replaces_the_reviewed_receipt(self):
        for updates in ({"version": 1, "digest": "changed-without-version"},
                        {"version": 2, "planId": "other"}, {"version": 2, "digest": ""}):
            with self.subTest(updates=updates):
                receipt = self.receipt([("User", "A", "new")])
                replacement = deepcopy(receipt)
                replacement.update(updates)
                executor = Executor(self.env, self.db, revalidate=lambda _: replacement)
                result = executor.review(self.identifiers(receipt))
                self.assertIn("INVALID_REFRESH", self.codes(result))
                self.assertEqual(self.db.read_preview("p1")["body"], receipt)
                self.assertEqual(self.env.mutations, [])

    def test_new_reviewed_version_can_execute_after_refresh(self):
        receipt = self.receipt([("User", "A", "first")])
        request = self.request(receipt)
        replacement = deepcopy(receipt)
        replacement.update(version=2, digest="new-digest")
        replacement["targets"][0]["plan"]["after"]["raw"] = "refreshed"
        executor = Executor(self.env, self.db, revalidate=lambda r: replacement if r["version"] == 1 else None)
        self.assertEqual(executor.execute(request)["status"], "preview_updated")
        result = self.execute(replacement, "new-key", executor)
        self.assertEqual(result["status"], "committed", result)
        self.assertEqual(self.env.read("User", "A")["raw"], "refreshed")
        self.assertEqual(len(self.env.mutations), 1)

    def test_unreadable_reconciliation_does_not_invoke_callbacks_or_mutate(self):
        receipt = self.receipt([("User", "A", "new")])
        request = self.request(receipt)
        self.env.before_mutation = lambda *_: (_ for _ in ()).throw(Crash())
        with self.assertRaises(Crash):
            self.executor.execute(request)
        self.env.read = lambda *_: (_ for _ in ()).throw(OSError("temporarily unreadable"))
        restarted = Executor(self.env, self.db, on_committed=lambda *_: self.fail("startup callback"))
        count = len(self.env.mutations)
        report = restarted.reconcile_pending()[0]
        self.assertEqual(report["status"], "recovery_required")
        self.assertEqual(report["targets"][0]["classification"], "unreadable")
        self.assertEqual(len(self.env.mutations), count)

    def test_system_runner_exception_cancel_precedes_any_user_mutation(self):
        receipt = self.receipt([("User", "U", "new"), ("System", "S", "new")])
        def cancel(*args, **kwargs):
            raise V3EnvironmentError("UAC_CANCELLED", "mock user cancelled")
        executor = Executor(FakeWindows(self.env), self.db, system_runner=cancel)
        result = self.execute(receipt, executor=executor)
        self.assertEqual(result["status"], "cancelled", result)
        self.assertEqual(self.env.mutations, [])
        self.assertEqual(self.db.history(), [])

    def test_unknown_system_reply_is_uncertain_and_never_starts_user_or_restore(self):
        receipt = self.receipt([("User", "U", "new"), ("System", "S", "new")])
        calls = []
        def malformed(plans, restore=False):
            calls.append(restore)
            self.system_runner(plans)
            return {"status": "unknown"}
        executor = Executor(FakeWindows(self.env), self.db, system_runner=malformed)
        result = self.execute(receipt, executor=executor)
        self.assertEqual(result["status"], "recovery_required", result)
        self.assertEqual(calls, [False])
        self.assertFalse(self.env.read("User", "U")["exists"])
        self.assertEqual(self.env.read("System", "S")["raw"], "new")

    def test_notification_observes_durable_commit_selection_and_history(self):
        receipt = self.receipt([("User", "A", "new")])
        def notify():
            with closing(sqlite3.connect(self.db.path)) as other:
                self.assertEqual(other.execute("SELECT status FROM executions").fetchone()[0], "committed")
                self.assertEqual(json.loads(other.execute("SELECT value FROM settings WHERE key='selected'").fetchone()[0]), "new")
                self.assertEqual(other.execute("SELECT COUNT(*) FROM history").fetchone()[0], 1)
            return {"notified": True}
        self.env.notify = notify
        executor = Executor(self.env, self.db, on_committed=lambda *_: {"selected": "new"})
        self.assertEqual(self.execute(receipt, executor=executor)["status"], "committed")

    def test_commit_transaction_failure_does_not_broadcast_success(self):
        receipt = self.receipt([("User", "A", "new")])
        self.env.notify = lambda: self.fail("uncommitted operation must not broadcast")
        executor = Executor(self.env, self.db, on_committed=lambda *_: (_ for _ in ()).throw(OSError("settings failed")))
        result = self.execute(receipt, executor=executor)
        self.assertEqual(result["status"], "recovery_required")

    def test_generic_notification_failure_is_durable_warning_and_never_replayed(self):
        receipt = self.receipt([("User", "A", "new")])
        calls = []
        def notify():
            calls.append(True)
            raise OSError("broadcast unavailable")
        self.env.notify = notify
        request = self.request(receipt)
        result = self.executor.execute(request)
        self.assertEqual(result["status"], "committed", result)
        self.assertEqual(result["warnings"][0]["code"], "NOTIFY_FAILED")
        self.assertEqual(self.db.history()[0]["warnings"], result["warnings"])
        self.assertEqual(self.executor.execute(request), result)
        self.assertEqual(len(calls), 1)

    def test_notification_warning_journal_failure_cannot_undo_durable_commit(self):
        receipt = self.receipt([("User", "A", "new")])
        request = self.request(receipt)
        transaction = self.db.transaction
        def notify():
            def fail_transaction():
                raise sqlite3.OperationalError("disk became full after commit")
            self.db.transaction = fail_transaction
            raise OSError("notify failed")
        self.env.notify = notify
        result = self.executor.execute(request)
        self.db.transaction = transaction
        self.assertEqual(result["status"], "committed", result)
        self.assertEqual([w["code"] for w in result["warnings"]], ["NOTIFY_FAILED", "NOTIFY_JOURNAL_FAILED"])
        durable = self.db.find_execution("request-1")["body"]["result"]
        self.assertEqual(durable["status"], "committed")
        self.assertEqual(durable["warnings"], [])  # Explicitly unsaved warning, not a fake saved result.
        self.assertEqual(self.executor.execute(request), durable)
        self.assertEqual(len(self.env.mutations), 1)

    def test_crash_during_notification_keeps_committed_result_without_rebroadcast(self):
        receipt = self.receipt([("User", "A", "new")])
        request = self.request(receipt)
        self.env.notify = lambda: (_ for _ in ()).throw(Crash())
        with self.assertRaises(Crash):
            self.executor.execute(request)
        result = self.executor.execute(request)
        self.assertEqual(result["status"], "committed")
        self.assertEqual(self.executor.reconcile_pending(), [])
        self.assertEqual(len(self.db.history()), 1)

    def test_unexpected_acknowledged_value_is_never_automatically_restored(self):
        receipt = self.receipt([("User", "A", "new")])
        def wrong_write(scope, name, raw, type=None, *, expected=None):
            return FileEnvironment.write(self.env, scope, name, "unexpected", type, expected=expected)
        self.env.write = wrong_write
        result = self.execute(receipt)
        self.assertEqual(result["status"], "recovery_required", result)
        self.assertIn("VERIFY_FAILED", self.codes(result))
        self.assertEqual(self.env.read("User", "A")["raw"], "unexpected")
        self.assertEqual(self.db.history(), [])

    def unresolved_operation(self):
        """A real failed batch with verified evidence and retained history."""
        self.seed("User", "A", "original", "REG_SZ")
        receipt = self.receipt([("User", "A", "applied"), ("User", "B", "later")], plan_id="source")
        def external_failure(scope, name):
            if name == "B":
                FileEnvironment.write(self.env, "User", "A", "external")
                raise OSError("second write failed after external edit")
        self.env.before_mutation = external_failure
        result = self.execute(receipt, "source-key")
        self.env.before_mutation = None
        self.assertEqual(result["status"], "recovery_required", result)
        return self.db.find_execution("source-key")

    def recovery_receipt(self, origin, resolution="restore_before", *, plan_id="recovery", version=1):
        changes = []
        for original in origin["body"]["targets"]:
            current = self.env.read(original["scope"], original["name"])
            desired = original["before"] if resolution == "restore_before" else current
            changes.append((original["scope"], original["name"], desired["raw"]))
        receipt = self.receipt(changes, plan_id=plan_id, version=version)
        receipt["intent"] = {"kind": "recovery", "operationId": origin["id"], "resolution": resolution}
        for target, original in zip(receipt["targets"], origin["body"]["targets"]):
            desired = original["before"] if resolution == "restore_before" else target["beforeSnapshot"]
            target["plan"]["after"] = {k: desired[k] for k in ("exists", "raw", "type")}
            changed = any(target["beforeSnapshot"][k] != desired[k] for k in ("exists", "raw", "type"))
            target["plan"]["operations"] = [{"type": "RECOVERY_RESTORE"}] if changed else []
        receipt.pop("digest")
        receipt["digest"] = hashlib.sha256(encode(receipt).encode()).hexdigest()
        self.db.preview(receipt)
        return receipt

    def test_recovery_restores_exact_before_and_atomically_resolves_original(self):
        origin = self.unresolved_operation()
        original_history = deepcopy(self.db.history())
        receipt = self.recovery_receipt(origin)
        seen = []
        def callback(receipt, result):
            self.assertTrue(self.db.connection.in_transaction)
            local = self.db.find_execution("source-key")
            self.assertEqual(local["status"], "compensated")
            with closing(sqlite3.connect(self.db.path)) as other:
                # Resolution is not visible outside the final transaction yet.
                status = other.execute("SELECT status FROM executions WHERE id=?", (origin["id"],)).fetchone()[0]
            self.assertEqual(status, "recovery_required")
            seen.append(result)
            return {"recovery-selection": "saved"}
        executor = Executor(self.env, self.db, on_committed=callback)
        request = self.request(receipt, "recover-key", executor)
        result = executor.execute(request)
        self.assertEqual(result["status"], "committed", result)
        self.assertEqual(self.env.read("User", "A")["raw"], "original")
        self.assertEqual(self.env.read("User", "A")["type"], "REG_SZ")
        self.assertFalse(self.env.read("User", "B")["exists"])
        resolved = self.db.find_execution("source-key")
        self.assertEqual(resolved["status"], "compensated")
        self.assertEqual(resolved["body"]["restoredBy"], result["operationId"])
        self.assertEqual(resolved["body"]["resolvedBy"], result["operationId"])
        self.assertEqual(resolved["body"]["resolution"], "restore_before")
        for key in ("receipt", "targets", "errors", "result", "reconciliation"):
            self.assertEqual(resolved["body"].get(key), origin["body"].get(key))
        self.assertEqual(self.db.history()[1:], original_history)
        self.assertEqual(self.db.get("recovery-selection"), "saved")
        count = len(self.env.mutations)
        self.assertEqual(executor.execute(request), result)
        self.assertEqual(len(self.env.mutations), count)
        self.assertEqual(len(seen), 1)
        next_receipt = self.receipt([("User", "Next", "ok")], plan_id="next")
        self.assertEqual(self.execute(next_receipt, "next-key")["status"], "committed")

    def test_accept_current_is_reviewed_zeroop_and_preserves_all_evidence(self):
        origin = self.unresolved_operation()
        history = deepcopy(self.db.history())
        receipt = self.recovery_receipt(origin, "accept_current")
        count, calls = len(self.env.mutations), []
        executor = Executor(self.env, self.db, on_committed=lambda r, s: calls.append(s) or {"accepted": True})
        request = self.request(receipt, "accept-key", executor)
        result = executor.execute(request)
        self.assertEqual(result["status"], "committed", result)
        self.assertTrue(all(t["status"] == "noop" for t in result["targets"]))
        self.assertEqual(len(self.env.mutations), count)
        self.assertEqual(self.env.read("User", "A")["raw"], "external")
        resolved = self.db.find_execution("source-key")
        self.assertEqual(resolved["status"], "resolved_current")
        self.assertEqual(resolved["body"]["resolvedBy"], result["operationId"])
        self.assertEqual(resolved["body"]["resolution"], "accept_current")
        self.assertNotIn("restoredBy", resolved["body"])
        for key in ("receipt", "targets", "errors", "result"):
            self.assertEqual(resolved["body"][key], origin["body"][key])
        self.assertEqual(self.db.history(), history)
        self.assertEqual(executor.execute(request), result)
        self.assertEqual(len(calls), 1)
        self.assertTrue(self.db.get("accepted"))
        self.assertEqual(executor.reconcile_pending(), [])
        self.assertEqual(self.db.find_execution("source-key")["status"], "resolved_current")
        next_receipt = self.receipt([("User", "Next", "ok")], plan_id="next")
        self.assertEqual(self.execute(next_receipt, "next-key")["status"], "committed")

    def test_recovery_intent_requires_exact_fields_and_supported_resolution(self):
        origin = self.unresolved_operation()
        for mutation in ("missing", "extra", "mode", "blank", "non-string"):
            with self.subTest(mutation=mutation):
                receipt = self.recovery_receipt(origin)
                intent = receipt["intent"]
                if mutation == "missing":
                    intent.pop("resolution")
                elif mutation == "extra":
                    intent["force"] = True
                elif mutation == "mode":
                    intent["resolution"] = "ack"
                elif mutation == "blank":
                    intent["operationId"] = ""
                else:
                    intent["operationId"] = 1
                self.db.preview(receipt)
                count = len(self.env.mutations)
                result = self.executor.review(self.identifiers(receipt))
                self.assertIn("INVALID_RECOVERY_INTENT", self.codes(result))
                self.assertEqual(len(self.env.mutations), count)

    def test_recovery_wrong_or_terminal_operation_is_rejected(self):
        origin = self.unresolved_operation()
        receipt = self.recovery_receipt(origin)
        receipt["intent"]["operationId"] = "does-not-exist"
        self.db.preview(receipt)
        self.assertIn("RECOVERY_NOT_FOUND", self.codes(self.executor.review(self.identifiers(receipt))))
        receipt["intent"]["operationId"] = origin["id"]
        self.db.preview(receipt)
        for status in ("committed", "compensated", "cancelled", "resolved_current"):
            with self.subTest(status=status):
                self.db.connection.execute("UPDATE executions SET status=? WHERE id=?", (status, origin["id"]))
                self.assertIn("RECOVERY_NOT_PENDING", self.codes(self.executor.review(self.identifiers(receipt))))

    def test_recovery_after_review_rechecks_original_is_still_unfinished(self):
        origin = self.unresolved_operation()
        receipt = self.recovery_receipt(origin)
        request = self.request(receipt, "recover-key")
        self.db.connection.execute("UPDATE executions SET status='compensated' WHERE id=?", (origin["id"],))
        count = len(self.env.mutations)
        result = self.executor.execute(request)
        self.assertIn("RECOVERY_NOT_PENDING", self.codes(result))
        self.assertEqual(len(self.env.mutations), count)

    def test_recovery_only_bypasses_its_own_original_pending_operation(self):
        origin = self.unresolved_operation()
        receipt = self.recovery_receipt(origin)
        request = self.request(receipt, "recover-key")
        unrelated = deepcopy(origin["body"])
        unrelated.update(operationId="other-operation", idempotencyKey="other-key")
        self.db.execution("other-operation", "other-plan", "other-key", "recovery_required", unrelated)
        count = len(self.env.mutations)
        result = self.executor.execute(request)
        self.assertIn("RECOVERY_REQUIRED", self.codes(result))
        self.assertEqual(len(self.env.mutations), count)
        self.assertEqual(self.db.find_execution("source-key")["status"], "recovery_required")

    def test_non_recovery_intent_cannot_resolve_another_operation(self):
        origin = self.unresolved_operation()
        receipt = self.recovery_receipt(origin)
        receipt["intent"]["kind"] = "manual"
        self.db.preview(receipt)
        count = len(self.env.mutations)
        result = self.execute(receipt, "not-recovery")
        self.assertIn("RECOVERY_REQUIRED", self.codes(result))
        self.assertEqual(len(self.env.mutations), count)
        self.assertEqual(self.db.find_execution("source-key")["body"], origin["body"])

    def test_recovery_must_cover_all_original_targets_with_exact_original_before(self):
        origin = self.unresolved_operation()
        for mutation in ("omit", "extra", "wrong-raw", "wrong-type"):
            with self.subTest(mutation=mutation):
                receipt = self.recovery_receipt(origin)
                if mutation == "omit":
                    receipt["targets"].pop()
                elif mutation == "extra":
                    extra = deepcopy(receipt["targets"][1])
                    extra.update(name="Extra", beforeSnapshot=self.env.read("User", "Extra"))
                    extra["plan"]["targetKey"]["name"] = "Extra"
                    receipt["targets"].append(extra)
                elif mutation == "wrong-raw":
                    receipt["targets"][0]["plan"]["after"]["raw"] = "unapproved"
                else:
                    receipt["targets"][0]["plan"]["after"]["type"] = "REG_EXPAND_SZ"
                self.db.preview(receipt)
                self.assertIn("INVALID_RECOVERY_TARGETS", self.codes(self.executor.review(self.identifiers(receipt))))

    def test_accept_current_rejects_any_changed_target_or_visible_operation(self):
        origin = self.unresolved_operation()
        for mutation in ("changed", "operations"):
            with self.subTest(mutation=mutation):
                receipt = self.recovery_receipt(origin, "accept_current")
                receipt["targets"][0]["plan"]["operations"] = [{"type": "replace"}]
                if mutation == "changed":
                    receipt["targets"][0]["plan"]["after"]["raw"] = "other"
                self.db.preview(receipt)
                self.assertIn("INVALID_RECOVERY_TARGETS", self.codes(self.executor.review(self.identifiers(receipt))))

    def test_accept_current_stale_snapshot_cannot_acknowledge_new_external_value(self):
        origin = self.unresolved_operation()
        receipt = self.recovery_receipt(origin, "accept_current")
        request = self.request(receipt, "accept-key")
        FileEnvironment.write(self.env, "User", "A", "external-later")
        result = self.executor.execute(request)
        self.assertIn("STALE_SNAPSHOT", self.codes(result))
        self.assertEqual(self.db.find_execution("source-key")["status"], "recovery_required")
        self.assertEqual(self.env.read("User", "A")["raw"], "external-later")

    def test_recovery_callback_failure_rolls_back_original_resolution(self):
        origin = self.unresolved_operation()
        history = deepcopy(self.db.history())
        receipt = self.recovery_receipt(origin)
        executor = Executor(self.env, self.db, on_committed=lambda *_: (_ for _ in ()).throw(OSError("settings unavailable")))
        result = self.execute(receipt, "recover-key", executor)
        self.assertEqual(result["status"], "recovery_required", result)
        self.assertEqual(self.env.read("User", "A")["raw"], "original")
        self.assertEqual(self.db.find_execution("source-key")["body"], origin["body"])
        self.assertEqual(self.db.find_execution("source-key")["status"], "recovery_required")
        self.assertEqual(self.db.history(), history)

    def test_recovery_history_failure_rolls_back_resolution_and_settings_together(self):
        origin = self.unresolved_operation()
        receipt = self.recovery_receipt(origin)
        executor = Executor(self.env, self.db, on_committed=lambda *_: {"resolved": True})
        self.db.append_history = lambda *_args, **_kwargs: (_ for _ in ()).throw(sqlite3.OperationalError("history failed"))
        result = self.execute(receipt, "recover-key", executor)
        self.assertEqual(result["status"], "recovery_required", result)
        self.assertIsNone(self.db.get("resolved"))
        self.assertEqual(self.db.find_execution("source-key")["body"], origin["body"])
        self.assertEqual(self.db.find_execution("source-key")["status"], "recovery_required")

    def test_crash_during_recovery_write_does_not_resolve_original_or_replay(self):
        origin = self.unresolved_operation()
        receipt = self.recovery_receipt(origin)
        request = self.request(receipt, "recover-key")
        self.env.after_mutation = lambda *_: (_ for _ in ()).throw(Crash())
        with self.assertRaises(Crash):
            self.executor.execute(request)
        self.env.after_mutation = None
        self.assertEqual(self.db.find_execution("source-key")["body"], origin["body"])
        self.assertEqual(self.db.find_execution("source-key")["status"], "recovery_required")
        count = len(self.env.mutations)
        result = self.executor.execute(request)
        self.assertEqual(result["status"], "recovery_required")
        self.assertEqual(len(self.env.mutations), count)
        self.assertEqual(self.db.find_execution("source-key")["status"], "recovery_required")

    def test_crash_in_final_recovery_transaction_rolls_back_original_resolution(self):
        origin = self.unresolved_operation()
        receipt = self.recovery_receipt(origin)
        executor = Executor(self.env, self.db, on_committed=lambda *_: (_ for _ in ()).throw(Crash()))
        request = self.request(receipt, "recover-key", executor)
        with self.assertRaises(Crash):
            executor.execute(request)
        self.assertEqual(self.db.find_execution("source-key")["body"], origin["body"])
        self.assertEqual(self.db.find_execution("source-key")["status"], "recovery_required")
        self.assertEqual(self.db.find_execution("recover-key")["status"], "executing")

    def test_mixed_system_recovery_committed_response_loss_retries_without_lock_or_write(self):
        self.seed("System", "S", "old-system")
        receipt = self.receipt([("System", "S", "new-system"), ("User", "U", "new-user")], plan_id="source")
        broken = Executor(FakeWindows(self.env), self.db, system_runner=self.system_runner,
                          on_committed=lambda *_: (_ for _ in ()).throw(OSError("application commit failed")))
        self.assertEqual(self.execute(receipt, "source-key", broken)["status"], "recovery_required")
        origin = self.db.find_execution("source-key")
        recovery = self.recovery_receipt(origin)
        executor = Executor(FakeWindows(self.env), self.db, system_runner=self.system_runner)
        request = self.request(recovery, "recover-key", executor)
        self.env.notify = lambda: (_ for _ in ()).throw(Crash())
        with self.assertRaises(Crash):
            executor.execute(request)
        self.assertEqual(self.env.read("System", "S")["raw"], "old-system")
        self.assertFalse(self.env.read("User", "U")["exists"])
        self.assertEqual(self.db.find_execution("source-key")["status"], "compensated")
        count = len(self.env.mutations)
        restarted = Executor(FakeWindows(self.env), self.db,
                             system_runner=lambda *_args, **_kwargs: self.fail("system recovery replayed"))
        result = restarted.execute(request)
        self.assertEqual(result["status"], "committed")
        self.assertEqual(self.db.find_execution("source-key")["body"]["restoredBy"], result["operationId"])
        self.assertEqual(len(self.env.mutations), count)
        self.assertEqual(restarted.reconcile_pending(), [])

    def test_service_builder_review_and_executor_recovery_roundtrip(self):
        from envmanager_v3.service import Service
        config = '[Tool]\ndefault_option = "Default"\n[Tool.Default]\nVALUE = { default_value = "new", scope = "User", mode = "replace", editable = true }\n'
        for resolution in ("restore_before", "accept_current"):
            with self.subTest(resolution=resolution):
                service = Service(self.root / resolution)
                try:
                    state = service.import_workspace({"text": config, "filename": "config.toml"})
                    record_id = state["apps"][0]["profiles"][0]["vars"][0]["recordId"]
                    source = service.plan({"kind": "apply", "recordIds": [record_id]})
                    reviewed = service.review(source["planId"], {"version": source["version"], "digest": source["digest"]})
                    callback = service.executor.on_committed
                    service.executor.on_committed = lambda *_: (_ for _ in ()).throw(OSError("injected app-state failure"))
                    failed = service.execute(source["planId"], {**reviewed, "idempotencyKey": "source"})
                    self.assertEqual(failed["status"], "recovery_required", failed)
                    service.executor.on_committed = callback
                    recovery = service.plan({"kind": "recovery", "operationId": failed["operationId"], "resolution": resolution})
                    reviewed = service.review(recovery["planId"], {"version": recovery["version"], "digest": recovery["digest"]})
                    self.assertIn("reviewToken", reviewed, reviewed)
                    result = service.execute(recovery["planId"], {**reviewed, "idempotencyKey": "recovery"})
                    self.assertEqual(result["status"], "committed", result)
                    self.assertEqual(service.database.unfinished(), [])
                    self.assertEqual(service.recovery()["items"], [])
                    current = service.environment.read("User", "VALUE")
                    self.assertEqual(current["exists"], resolution == "accept_current")
                    original = service.database.find_execution("source")
                    self.assertEqual(original["status"], "compensated" if resolution == "restore_before" else "resolved_current")
                    self.assertEqual(original["body"]["resolvedBy"], result["operationId"])
                finally:
                    service.close()

    def test_resolved_failure_history_projects_resolution_without_rewriting_snapshots(self):
        from envmanager_v3.service import Service
        from tests_v3.browser_failure_acceptance import FaultFileEnvironment, seed
        for resolution in ("restore_before", "accept_current"):
            with self.subTest(resolution=resolution):
                root = self.root / ("history-" + resolution)
                service = Service(root, environment=FaultFileEnvironment(root / "environment.json"))
                try:
                    summary = seed(service, service.environment)
                    operation_id = summary["operations"]["recoveryRequired"]["operationId"]
                    stored_before = service.database.history()
                    receipt = service.plan({"kind": "recovery", "operationId": operation_id, "resolution": resolution})
                    reviewed = service.review(receipt["planId"], {k: receipt[k] for k in ("version", "digest")})
                    result = service.execute(receipt["planId"], {**reviewed, "idempotencyKey": "resolve-history"})
                    self.assertEqual(result["status"], "committed")
                    projected = service.history_record(operation_id)
                    self.assertEqual(projected["status"], "recovery_required")
                    self.assertEqual(projected["resolutionStatus"], "compensated" if resolution == "restore_before" else "resolved_current")
                    self.assertEqual(projected["resolvedBy"], result["operationId"])
                    self.assertEqual(projected["action"], "原操作已恢复处理")
                    self.assertEqual(service.recovery()["items"], [])
                    stored_after = service.database.history()
                    self.assertEqual(next(h for h in stored_after if h["operationId"] == operation_id),
                                     next(h for h in stored_before if h["operationId"] == operation_id))
                finally:
                    service.close()


    def session_executor(self, receipt, configure=None, *, on_committed=None):
        events, sessions, received = [], [], []

        def factory(frozen, token, operation_id):
            # Inspect committed SQLite from a distinct connection, not its writer.
            with closing(sqlite3.connect(self.db.path)) as connection:
                row = connection.execute("SELECT status,review_token FROM previews WHERE id=?",
                                         (receipt["planId"],)).fetchone()
                self.assertEqual(row, ("executing", None))
                body = json.loads(connection.execute("SELECT body FROM executions WHERE id=?",
                                                     (operation_id,)).fetchone()[0])
            self.assertEqual(body["receipt"], receipt)
            self.assertTrue(body["claimed"])
            self.assertNotIn(token, encode(body))
            self.assertEqual(frozen, receipt)
            events.append("factory")
            received.append((deepcopy(frozen), token, operation_id))
            session = PreparedFileSession(self.env, frozen, events)
            sessions.append(session)
            if configure:
                configure(session)
            # Prove the callback cannot mutate Executor's frozen journal/receipt.
            frozen["targets"][0]["plan"]["after"]["raw"] = "factory mutation"
            return session

        executor = Executor(FakeWindows(self.env), self.db, system_session_factory=factory,
                            system_runner=lambda *_a, **_kw: self.fail("Session fell back to legacy runner"),
                            on_committed=on_committed)
        return executor, events, sessions, received

    def test_prepared_session_consumes_review_before_full_receipt_factory_and_journals_order(self):
        receipt = self.receipt([("System", "S2", "s2"), ("User", "U2", "u2"),
                                ("System", "S1", "s1"), ("User", "U1", "u1")])
        receipt["targets"][0]["verification"] = {
            "method": "build", "options": {"type": "REG_EXPAND_SZ", "delete": False},
            "orderIndices": [1, 0]}
        receipt["intent"] = {"kind": "manual_edit", "nested": {"exact": "原文"}}
        receipt.pop("digest")
        receipt["digest"] = hashlib.sha256(encode(receipt).encode()).hexdigest()
        self.db.preview(receipt)
        phases = []
        original = self.db.execution

        def record(*args):
            phases.append(deepcopy(args[-1]))
            return original(*args)

        self.db.execution = record
        executor, events, sessions, received = self.session_executor(receipt)
        request = self.request(receipt, executor=executor)
        result = executor.execute(request)
        self.assertEqual(result["status"], "committed", result)
        self.assertEqual(events, ["factory", "prepare", "commit", "finish"])
        self.assertEqual([m[2] for m in self.env.mutations], ["U2", "U1", "S2", "S1"])
        self.assertEqual(received[0], (receipt, request["reviewToken"], result["operationId"]))
        body = self.db.find_execution(request["idempotencyKey"])["body"]
        self.assertEqual(body["attemptOrder"], [1, 3, 0, 2])
        self.assertEqual(body["receipt"], receipt)
        first_write = next(p for p in phases if any(t["status"] == "writing" for t in p["targets"]))
        self.assertEqual(first_write["systemPhase"], "prepared")
        self.assertTrue(any(p.get("systemPhase") == "commit_intent" for p in phases))
        self.assertTrue(any(p.get("systemPhase") == "commit_result" for p in phases))
        mutations = deepcopy(self.env.mutations)
        self.assertEqual(executor.execute(request), result)
        self.assertEqual(self.env.mutations, mutations)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(self.db.history()[0]["intent"], receipt["intent"])

    def test_prepared_session_cancel_reply_and_exception_are_zero_write_one_use(self):
        for index, throws in enumerate((False, True)):
            receipt = self.receipt([("User", "U", "u"), ("System", "S", "s")], plan_id=f"p{index}")

            def configure(session):
                def cancel():
                    if throws:
                        raise ExecutionError("UAC_CANCELLED", "cancelled")
                    return {"status": "cancelled"}
                session.on_prepare = cancel

            executor, events, sessions, _ = self.session_executor(receipt, configure)
            request = self.request(receipt, key=f"cancel{index}", executor=executor)
            result = executor.execute(request)
            self.assertEqual(result["status"], "cancelled", result)
            self.assertEqual(executor.execute(request), result)
            self.assertEqual(events, ["factory", "prepare", "finish"])
            self.assertEqual(self.db.read_preview(receipt["planId"])["review_token"], None)
            again = executor.execute({**request, "idempotencyKey": f"new-cancel{index}"})
            self.assertIn("PLAN_CONSUMED", self.codes(again))
            self.assertEqual(len(sessions), 1)
        self.assertEqual(self.env.mutations, [])
        self.assertEqual(self.db.history(), [])

    def test_system_ack_revision_is_helper_local_and_parent_binds_its_own_snapshot(self):
        receipt = self.receipt([("System", "S", "s"), ("User", "U", "u")])

        def configure(session):
            def commit():
                reply = session.write_system()
                reply["targets"][0]["result"]["revision"] = "different-helper-process-epoch"
                return reply
            session.on_commit = commit

        executor, events, _, _ = self.session_executor(receipt, configure)
        result = self.execute(receipt, executor=executor)
        self.assertEqual(result["status"], "committed", result)
        target = result["targets"][0]
        self.assertEqual(target["helperVerifiedAfter"]["revision"], "different-helper-process-epoch")
        self.assertEqual(target["verifiedAfter"], self.env.read("System", "S"))
        self.assertNotEqual(target["helperVerifiedAfter"]["revision"], target["verifiedAfter"]["revision"])
        self.assertEqual(events, ["factory", "prepare", "commit", "finish"])

    def test_helper_already_compensated_failure_does_not_receive_second_restore(self):
        receipt = self.receipt([("System", "S", "s"), ("User", "U", "u")])

        def configure(session):
            def commit():
                session.write_system()
                # Model the real core's automatic rollback on a known failure.
                record = session.written[0]
                restored = session.apply("S", record["before"], record["result"])
                record.update(status="compensated", result=restored)
                return {"status": "compensated", "targets": deepcopy(session.records), "errors": []}
            session.on_commit = commit
            session.on_compensate = lambda: self.fail("Second compensation against already-compensated helper")

        executor, events, _, _ = self.session_executor(receipt, configure)
        result = self.execute(receipt, executor=executor)
        self.assertEqual(result["status"], "compensated", result)
        self.assertEqual([m[2] for m in self.env.mutations], ["U", "S", "S", "U"])
        self.assertEqual(events, ["factory", "prepare", "commit", "finish"])

    def test_prepared_session_preflight_failure_is_zero_write_rejected(self):
        receipt = self.receipt([("User", "U", "u"), ("System", "S", "s")])
        for throws in (False, True):
            def configure(session):
                def fail():
                    if throws:
                        raise ExecutionError("PREFLIGHT_FAILED", "System before changed")
                    return {"status": "rejected", "errors": [{"code": "PREFLIGHT_FAILED"}]}
                session.on_prepare = fail

            executor, events, _, _ = self.session_executor(receipt, configure)
            result = self.execute(receipt, str(throws), executor)
            self.assertEqual(result["status"], "rejected", result)
            self.assertEqual(events, ["factory", "prepare", "finish"])
            self.assertEqual(self.env.mutations, [])
            self.db.preview(receipt)

    def test_prepare_unknown_stays_pending_even_when_every_target_still_before(self):
        receipt = self.receipt([("User", "U", "u"), ("System", "S", "s")])

        def configure(session):
            session.on_prepare = lambda: {"status": "recovery_required", "launch_status": "uncertain"}

        executor, events, _, _ = self.session_executor(receipt, configure)
        request = self.request(receipt, executor=executor)
        result = executor.execute(request)
        self.assertEqual(result["status"], "recovery_required", result)
        self.assertEqual(self.env.mutations, [])
        self.db.close()
        self.db = Database(self.root / "state.sqlite3")
        restarted = Executor(FakeWindows(self.env), self.db,
                             system_session_factory=lambda *_: self.fail("Unknown prepare replayed"))
        self.assertEqual(restarted.reconcile_pending()[0]["status"], "recovery_required")
        self.assertEqual(restarted.execute(request), result)
        later = self.receipt([("User", "Later", "new")], plan_id="later")
        blocked = self.execute(later, "later", restarted)
        self.assertIn("RECOVERY_REQUIRED", self.codes(blocked))
        self.assertEqual(self.env.mutations, [])
        self.assertEqual(events, ["factory", "prepare", "finish"])

    def test_prepared_session_rechecks_entire_batch_after_waiting_for_uac(self):
        receipt = self.receipt([("User", "U", "u"), ("System", "S", "s")])

        def configure(session):
            def change():
                FileEnvironment.write(self.env, "User", "U", "external")
                return {"status": "prepared"}
            session.on_prepare = change

        executor, events, _, _ = self.session_executor(receipt, configure)
        result = self.execute(receipt, executor=executor)
        self.assertEqual(result["status"], "rejected", result)
        self.assertIn("STALE_SNAPSHOT", self.codes(result))
        self.assertEqual(self.env.read("User", "U")["raw"], "external")
        self.assertEqual(self.env.mutations, [])
        self.assertEqual(events, ["factory", "prepare", "finish"])

    def test_user_failure_closes_prepared_helper_without_system_commit_or_compensation(self):
        receipt = self.receipt([("System", "S", "s"), ("User", "A", "a"), ("User", "B", "b")])
        executor, events, _, _ = self.session_executor(receipt)
        self.env.before_mutation = self.fail_b
        result = self.execute(receipt, executor=executor)
        self.assertEqual(result["status"], "compensated", result)
        self.assertEqual(events, ["factory", "prepare", "finish"])
        self.assertFalse(self.env.read("System", "S")["exists"])
        self.assertFalse(self.env.read("User", "A")["exists"])
        self.assertEqual([m[2] for m in self.env.mutations], ["A", "B", "A"])

    def test_system_failure_reverses_system_then_user_with_same_live_grant(self):
        receipt = self.receipt([("System", "S1", "s1"), ("User", "U1", "u1"),
                                ("System", "S2", "s2"), ("User", "U2", "u2")])
        executor, events, sessions, _ = self.session_executor(receipt)

        def fail(scope, name):
            if name == "S2":
                raise ExecutionError("WRITE_FAILED", "injected second System failure")

        self.env.before_mutation = fail
        result = self.execute(receipt, executor=executor)
        self.assertEqual(result["status"], "compensated", result)
        self.assertEqual(events, ["factory", "prepare", "commit", "finish"])
        self.assertEqual(len(sessions), 1)
        self.assertEqual([m[2] for m in self.env.mutations], ["U1", "U2", "S1", "S2", "S1", "U2", "U1"])
        self.assertTrue(all(not self.env.read(t["scope"], t["name"])["exists"] for t in receipt["targets"]))
        self.assertEqual(self.db.unfinished(), [])

    def test_system_compensation_preserves_external_edit_and_observed_aba(self):
        for index, aba in enumerate((False, True)):
            with self.subTest(aba=aba):
                # Independent durable operation for each conflict.
                name = f"S{index}"
                receipt = self.receipt([("User", f"U{index}", "u"), ("System", name, "s"),
                                        ("System", f"Fail{index}", "fail")], plan_id=f"conflict{index}")
                executor, events, _, _ = self.session_executor(receipt)

                def fail(scope, target_name):
                    if target_name == f"Fail{index}":
                        FileEnvironment.write(self.env, "System", name, "external")
                        if aba:
                            FileEnvironment.write(self.env, "System", name, "s")
                        raise ExecutionError("WRITE_FAILED", "failure after third-party change")

                self.env.before_mutation = fail
                result = self.execute(receipt, f"conflict{index}", executor)
                self.assertEqual(result["status"], "recovery_required", result)
                self.assertEqual(self.env.read("System", name)["raw"], "s" if aba else "external")
                self.assertFalse(self.env.read("User", f"U{index}")["exists"])
                self.assertEqual(events, ["factory", "prepare", "commit", "finish"])
                # The next subcase gets a fresh DB; unresolved state is not silently cleared.
                if index == 0:
                    self.db.close()
                    self.db = Database(self.root / "second.sqlite3")

    def test_unknown_system_commit_keeps_user_and_system_values_without_retry_or_compensation(self):
        receipt = self.receipt([("System", "S", "s"), ("User", "U", "u")])

        def configure(session):
            def unknown():
                session.write_system()
                raise ExecutionError("OWNER_UNKNOWN", "parent/helper channel disconnected after write")
            session.on_commit = unknown

        executor, events, sessions, _ = self.session_executor(receipt, configure)
        request = self.request(receipt, executor=executor)
        result = executor.execute(request)
        self.assertEqual(result["status"], "recovery_required", result)
        self.assertEqual([m[1] for m in self.env.mutations], ["User", "System"])
        self.assertEqual(events, ["factory", "prepare", "commit", "finish"])
        self.assertEqual(executor.execute(request), result)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(executor.reconcile_pending()[0]["status"], "recovery_required")
        self.assertEqual(len(self.env.mutations), 2)

    def test_system_commit_crash_is_observed_on_restart_never_replayed(self):
        receipt = self.receipt([("System", "S", "s"), ("User", "U", "u")])

        def configure(session):
            def crash():
                session.write_system()
                raise Crash()
            session.on_commit = crash

        executor, events, _, _ = self.session_executor(receipt, configure)
        request = self.request(receipt, executor=executor)
        with self.assertRaises(Crash):
            executor.execute(request)
        self.db.close()
        self.db = Database(self.root / "state.sqlite3")
        restarted = Executor(FakeWindows(self.env), self.db,
                             system_session_factory=lambda *_: self.fail("Crashed command replayed"))
        self.assertEqual(restarted.reconcile_pending()[0]["status"], "recovery_required")
        self.assertEqual(restarted.execute(request)["status"], "recovery_required")
        self.assertEqual(events, ["factory", "prepare", "commit", "finish"])
        self.assertEqual(len(self.env.mutations), 2)

    def test_unknown_system_compensation_stops_remaining_user_restores(self):
        receipt = self.receipt([("System", "S", "s"), ("User", "U", "u")])

        def configure(session):
            def commit():
                reply = session.write_system()
                FileEnvironment.write(self.env, "User", "U", "external")
                return reply
            session.on_commit = commit
            session.on_compensate = lambda: {"status": "recovery_required", "launch_status": "uncertain"}

        executor, events, _, _ = self.session_executor(receipt, configure)
        request = self.request(receipt, executor=executor)
        result = executor.execute(request)
        self.assertEqual(result["status"], "recovery_required", result)
        self.assertEqual(events, ["factory", "prepare", "commit", "compensate", "finish"])
        self.assertEqual(len(self.env.mutations), 2)
        self.assertEqual(executor.execute(request), result)
        self.assertEqual(len(self.env.mutations), 2)

    def test_session_prepare_intent_disk_failure_prevents_factory_and_all_writes(self):
        receipt = self.receipt([("User", "U", "u"), ("System", "S", "s")])
        executor, events, _, _ = self.session_executor(receipt)
        original = self.db.execution

        def fail(*args):
            if args[-1].get("systemPhase") == "prepare_intent":
                raise sqlite3.OperationalError("disk full before prepare")
            return original(*args)

        self.db.execution = fail
        result = self.execute(receipt, executor=executor)
        self.assertEqual(result["status"], "recovery_required", result)
        self.assertEqual(events, [])
        self.assertEqual(self.env.mutations, [])

    def test_session_result_disk_failure_stops_all_compensation_mutations(self):
        receipt = self.receipt([("User", "U", "u"), ("System", "S", "s")])
        executor, events, _, _ = self.session_executor(receipt)
        original = self.db.execution

        def fail(*args):
            if args[-1].get("systemPhase") == "commit_result":
                raise sqlite3.OperationalError("disk full after System ACK")
            return original(*args)

        self.db.execution = fail
        result = self.execute(receipt, executor=executor)
        self.assertEqual(result["status"], "recovery_required", result)
        self.assertEqual(events, ["factory", "prepare", "commit", "finish"])
        self.assertEqual(len(self.env.mutations), 2)

    def test_session_compensation_intent_disk_failure_never_sends_restore_command(self):
        receipt = self.receipt([("User", "U", "u"), ("System", "S", "s")])

        def configure(session):
            def commit():
                reply = session.write_system()
                FileEnvironment.write(self.env, "User", "U", "external")
                return reply
            session.on_commit = commit

        executor, events, _, _ = self.session_executor(receipt, configure)
        original = self.db.execution

        def fail(*args):
            if args[-1].get("systemPhase") == "compensate_intent":
                raise sqlite3.OperationalError("disk full before compensation")
            return original(*args)

        self.db.execution = fail
        result = self.execute(receipt, executor=executor)
        self.assertEqual(result["status"], "recovery_required", result)
        self.assertEqual(events, ["factory", "prepare", "commit", "finish"])
        self.assertEqual(len(self.env.mutations), 2)

    def test_session_lives_through_commit_callback_failure_without_implicit_restore(self):
        receipt = self.receipt([("User", "U", "u"), ("System", "S", "s")])
        events = None

        def callback(*_):
            self.assertNotIn("finish", events)
            raise sqlite3.OperationalError("final app state commit failed")

        executor, events, _, _ = self.session_executor(receipt, on_committed=callback)
        result = self.execute(receipt, executor=executor)
        self.assertEqual(result["status"], "recovery_required", result)
        self.assertEqual(events, ["factory", "prepare", "commit", "finish"])
        self.assertEqual(len(self.env.mutations), 2)
        self.assertEqual(self.db.history(), [])

    def test_session_finish_failure_is_durable_warning_never_a_second_grant(self):
        receipt = self.receipt([("User", "U", "u"), ("System", "S", "s")])

        def configure(session):
            def fail():
                raise OSError("cleanup failed")
            session.on_finish = fail

        executor, events, _, _ = self.session_executor(receipt, configure)
        request = self.request(receipt, executor=executor)
        result = executor.execute(request)
        self.assertEqual(result["status"], "committed", result)
        self.assertIn("SYSTEM_FINISH_FAILED", {w["code"] for w in result["warnings"]})
        self.assertEqual(executor.execute(request), result)
        self.assertEqual(events, ["factory", "prepare", "commit", "finish"])
        self.assertEqual(len(self.env.mutations), 2)

    def test_noop_system_and_file_backend_do_not_construct_prepared_session(self):
        self.seed("System", "S", "s")
        receipt = self.receipt([("System", "S", "s"), ("User", "U", "u")])
        factory = lambda *_: self.fail("No-op/File backend constructed System helper")
        executor = Executor(FakeWindows(self.env), self.db, system_session_factory=factory)
        self.assertEqual(self.execute(receipt, executor=executor)["status"], "committed")
        other = self.receipt([("System", "S", "file")], plan_id="file")
        executor = Executor(self.env, self.db, system_session_factory=factory)
        self.assertEqual(self.execute(other, "file", executor)["status"], "committed")

    def test_history_persists_exact_frozen_intent_without_changing_audit(self):
        receipt = self.receipt([("User", "U", "u")])
        receipt["intent"] = {"kind": "history_restore", "operationId": "original", "options": ["原样"]}
        receipt.pop("digest")
        receipt["digest"] = hashlib.sha256(encode(receipt).encode()).hexdigest()
        self.db.preview(receipt)
        result = self.execute(receipt)
        self.assertEqual(result["status"], "committed", result)
        self.db.close()
        self.db = Database(self.root / "state.sqlite3")
        history = self.db.history()[0]
        self.assertEqual(history["intent"], receipt["intent"])
        self.assertEqual(history["targets"][0]["plan"], receipt["targets"][0]["plan"])
        self.assertEqual(history["targets"], result["targets"])

    def test_committed_system_compensates_on_same_session_before_user_conflict_is_retained(self):
        receipt = self.receipt([("System", "S", "s"), ("User", "U", "u")])

        def configure(session):
            def commit():
                reply = session.write_system()
                FileEnvironment.write(self.env, "User", "U", "external")
                return reply
            session.on_commit = commit

        executor, events, sessions, _ = self.session_executor(receipt, configure)
        result = self.execute(receipt, executor=executor)
        self.assertEqual(result["status"], "recovery_required", result)
        self.assertEqual(events, ["factory", "prepare", "commit", "compensate", "finish"])
        self.assertEqual(len(sessions), 1)
        self.assertFalse(self.env.read("System", "S")["exists"])
        self.assertEqual(self.env.read("User", "U")["raw"], "external")
        self.assertEqual([m[2] for m in self.env.mutations], ["U", "S", "S"])

    def test_dependency_refresh_after_prepare_requires_new_review_and_zero_writes(self):
        receipt = self.receipt([("System", "S", "s"), ("User", "U", "u")])
        replacement = deepcopy(receipt)
        replacement.update(version=2, digest="new-frozen-digest")
        changed = False

        def configure(session):
            def prepare():
                nonlocal changed
                changed = True
                return {"status": "prepared"}
            session.on_prepare = prepare

        executor, events, _, _ = self.session_executor(receipt, configure)
        executor.revalidate = lambda _receipt: replacement if changed else None
        request = self.request(receipt, executor=executor)
        result = executor.execute(request)
        self.assertEqual(result["status"], "preview_updated", result)
        self.assertEqual(result["plan"], replacement)
        self.assertEqual(self.env.mutations, [])
        self.assertEqual(events, ["factory", "prepare", "finish"])
        self.assertEqual(self.db.read_preview(receipt["planId"])["status"], "ready")
        self.assertIsNone(self.db.read_preview(receipt["planId"])["review_token"])
        self.assertEqual(executor.execute(request), result)

    def test_owner_unknown_marker_never_retries_even_with_a_success_status(self):
        receipt = self.receipt([("User", "U", "u"), ("System", "S", "s")])

        def configure(session):
            def commit():
                reply = session.write_system()
                reply["channel_state"] = "owner_unknown"
                return reply
            session.on_commit = commit

        executor, events, _, _ = self.session_executor(receipt, configure)
        request = self.request(receipt, executor=executor)
        result = executor.execute(request)
        self.assertEqual(result["status"], "recovery_required", result)
        self.assertEqual(executor.execute(request), result)
        self.assertEqual(events, ["factory", "prepare", "commit", "finish"])
        self.assertEqual(len(self.env.mutations), 2)

    def test_service_system_factory_receives_consumed_token_and_captures_full_review_with_mocks_only(self):
        from envmanager_v3 import native_ipc, privileges
        from envmanager_v3.service import Service
        from envmanager_v3.system_helper import validate_request

        receipt = self.receipt([("System", "S", "s"), ("System", "Noop", None), ("User", "U", "u")])
        receipt.update(status="ready", engineHash="e" * 64, intent={"kind": "manual_edit"})
        receipt["targets"][0]["verification"] = {"method": "buildElementEdit", "options": {"raw": "s"}}
        receipt.pop("digest")
        receipt["digest"] = hashlib.sha256(encode(receipt).encode()).hexdigest()
        self.db.preview(receipt)
        owner = Service.__new__(Service)  # No Service startup, inventory, worker or Registry.
        owner.data_dir = self.root
        events, captured = [], []

        def prepare_batch(plans, root, *, job_id):
            body = self.db.find_execution("request-1")["body"]
            self.assertEqual(body["operationId"], job_id)
            self.assertTrue(body["claimed"])
            self.assertIsNone(self.db.read_preview(receipt["planId"])["review_token"])
            self.assertEqual(root, self.root / "system-jobs")
            normalized = validate_request({"protocol": 1, "job_id": job_id, "restore": False, "plans": plans})["plans"]
            envelope = native_ipc.consume_review(normalized, receipt["engineHash"])
            captured.append(envelope)
            self.assertEqual(len(plans), 1)  # Excludes unchanged System and every User target.
            return PreparedFileSession(self.env, receipt, events)

        executor = Executor(FakeWindows(self.env), self.db, system_session_factory=owner._system_session)
        request = self.request(receipt, executor=executor)
        with mock.patch.object(privileges, "prepare_system_batch", side_effect=prepare_batch) as factory, \
                mock.patch.object(privileges, "_launch_helper", side_effect=AssertionError("No native launch")), \
                mock.patch.object(native_ipc, "Win32", side_effect=AssertionError("No Windows APIs")):
            result = executor.execute(request)
            self.assertEqual(result["status"], "committed", result)
            self.assertEqual(executor.execute(request), result)
            self.assertEqual(factory.call_count, 1)
        self.assertEqual(captured[0]["inputs"]["receipt"], receipt)
        self.assertEqual(captured[0]["review_token"], request["reviewToken"])
        self.assertEqual(captured[0]["intent"], receipt["intent"])
        self.assertEqual(captured[0]["operations"], [receipt["targets"][0]["plan"]["operations"]])
        self.assertEqual(events, ["prepare", "commit", "finish"])
        self.assertIsNone(native_ipc._review_context.get())

    def test_real_prepared_session_thread_api_with_file_helper_and_mocked_native_launch(self):
        from envmanager_v3 import privileges
        from envmanager_v3.system_helper import execute_helper_request

        receipt = self.receipt([("System", "S", "s"), ("User", "U", "u")])
        helper_env = ObservedEnvironment(self.env.path)  # Deliberately different adapter epoch.
        events = []

        class Journal:
            failed = False

            def append(self, event, **_data):
                events.append(event)

            def close(self):
                pass

        def launch(path, digest, controller):
            events.append("launch")
            request = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(self.env.mutations, [])
            self.assertEqual(controller.prepared(), "commit_batch")
            self.assertEqual([m[1] for m in self.env.mutations], ["User"])
            result = execute_helper_request(request, helper_env, journal_path=None,
                                            journal_factory=lambda _: Journal())
            result.update(digest=digest, channel_state="connected")
            self.assertEqual(controller.phase(result), "finish")
            with closing(sqlite3.connect(self.db.path)) as connection:
                self.assertEqual(connection.execute("SELECT status FROM executions WHERE id=?",
                                                    (request["job_id"],)).fetchone()[0], "committed")
            return privileges._PipeOutcome(result, {}, 0)

        def factory(frozen, _token, operation_id):
            plans = [{"scope": "System", "name": t["name"], "before": t["beforeSnapshot"],
                      "after": t["plan"]["after"]} for t in frozen["targets"] if t["scope"] == "System"]
            return privileges.prepare_system_batch(plans, self.root / "mock-jobs", job_id=operation_id)

        executor = Executor(FakeWindows(self.env), self.db, system_session_factory=factory)
        with mock.patch.object(privileges, "_launch_helper", side_effect=launch) as launcher, \
                mock.patch.object(privileges, "_no_reparse"), \
                mock.patch.object(privileges, "_create_private_directory", side_effect=lambda p: p.mkdir()), \
                mock.patch.object(privileges, "_locked_request", side_effect=lambda _: nullcontext()):
            request = self.request(receipt, executor=executor)
            result = executor.execute(request)
            self.assertEqual(result["status"], "committed", result)
            self.assertEqual(executor.execute(request), result)
            self.assertEqual(launcher.call_count, 1)
        self.assertEqual(len(helper_env.mutations), 1)
        self.assertEqual(events[0], "launch")
        self.assertNotEqual(result["targets"][0]["helperVerifiedAfter"]["revision"],
                            result["targets"][0]["verifiedAfter"]["revision"])

    def test_system_success_without_target_ack_is_unknown_not_inferred_from_equal_value(self):
        receipt = self.receipt([("System", "S", "s"), ("User", "U", "u")])

        def configure(session):
            def commit():
                session.write_system()
                return {"status": "committed", "targets": [], "errors": []}
            session.on_commit = commit

        executor, events, _, _ = self.session_executor(receipt, configure)
        request = self.request(receipt, executor=executor)
        result = executor.execute(request)
        self.assertEqual(result["status"], "recovery_required", result)
        self.assertIn("HELPER_UNCERTAIN", self.codes(result))
        self.assertEqual(executor.execute(request), result)
        self.assertEqual(events, ["factory", "prepare", "commit", "finish"])
        self.assertEqual(len(self.env.mutations), 2)

    def test_parent_observed_system_aba_vetoes_compensation_even_if_helper_missed_it(self):
        receipt = self.receipt([("System", "S", "s"), ("User", "U", "u")])
        armed = False
        original_read = self.env.read

        def configure(session):
            def commit():
                nonlocal armed
                reply = session.write_system()
                FileEnvironment.write(self.env, "User", "U", "external")
                armed = True
                return reply
            session.on_commit = commit
            session.on_compensate = lambda: self.fail("Known parent continuity loss authorized System restoration")

        def read(scope, name):
            nonlocal armed
            result = original_read(scope, name)
            if armed and scope == "User" and name == "U":
                armed = False
                FileEnvironment.write(self.env, "System", "S", "external")
                FileEnvironment.write(self.env, "System", "S", "s")
            return result

        executor, events, _, _ = self.session_executor(receipt, configure)
        self.env.read = read
        result = self.execute(receipt, executor=executor)
        self.assertEqual(result["status"], "recovery_required", result)
        self.assertEqual(events, ["factory", "prepare", "commit", "finish"])
        self.assertEqual(self.env.read("System", "S")["raw"], "s")
        self.assertEqual(self.env.read("User", "U")["raw"], "external")
        self.assertEqual(len(self.env.mutations), 2)

    def test_pristine_prepared_crash_is_cancelled_interrupted_without_claiming_compensation(self):
        receipt = self.receipt([("User", "A", "new")])
        request = self.request(receipt)

        def before_write_intent(*_):
            raise Crash()

        self.executor._write = before_write_intent
        with self.assertRaises(Crash):
            self.executor.execute(request)
        pending = self.db.find_execution("request-1")["body"]
        self.assertNotIn("attemptOrder", pending)
        self.assertEqual(pending["targets"][0]["status"], "pending")
        restarted = Executor(FileEnvironment(self.env.path), self.db)
        report = restarted.reconcile_pending()[0]
        self.assertEqual(report["status"], "cancelled", report)
        self.assertEqual(report["reason"], "interrupted_before_write_intent")
        self.assertEqual(restarted.execute(request)["status"], "cancelled")
        self.assertEqual(self.env.mutations, [])
        self.assertEqual(self.db.history(), [])
        self.assertEqual(self.db.unfinished(), [])

    def test_durable_compensated_ack_before_terminal_commit_can_preserve_compensation(self):
        self.seed("User", "A", "old")
        receipt = self.receipt([("User", "A", "new"), ("User", "B", "fail")])
        request = self.request(receipt)
        self.env.before_mutation = self.fail_b
        original_save = self.db.execution

        def crash_after_restored_ack(*args):
            original_save(*args)
            if args[-1]["targets"] and args[-1]["targets"][0]["status"] == "compensated":
                raise Crash()

        self.db.execution = crash_after_restored_ack
        with self.assertRaises(Crash):
            self.executor.execute(request)
        self.db.execution = original_save
        pending = self.db.find_execution("request-1")["body"]
        self.assertEqual(pending["targets"][0]["status"], "compensated")
        self.assertEqual(pending["targets"][0]["verifiedBefore"]["raw"], "old")
        before = self.env.path.read_bytes()
        restarted = Executor(FileEnvironment(self.env.path), self.db)
        report = restarted.reconcile_pending()[0]
        self.assertEqual(report["status"], "compensated", report)
        self.assertEqual(report["reason"], "durable_compensation_verified")
        self.assertEqual(restarted.execute(request)["status"], "compensated")
        self.assertEqual(self.env.path.read_bytes(), before)
        self.assertEqual(self.db.history(), [])

    def pending_unknown_system(self):
        receipt = self.receipt([("System", "S", "s"), ("User", "U", "u")])

        def configure(session):
            def unknown():
                session.write_system()
                raise ExecutionError("OWNER_UNKNOWN", "lost ACK")
            session.on_commit = unknown

        executor, _, _, _ = self.session_executor(receipt, configure)
        request = self.request(receipt, executor=executor)
        result = executor.execute(request)
        self.assertEqual(result["status"], "recovery_required", result)
        return request, deepcopy(self.db.find_execution("request-1")["body"])

    def test_startup_protected_system_receipt_is_durable_evidence_never_a_sqlite_commit(self):
        request, original = self.pending_unknown_system()
        original_history = deepcopy(self.db.history())
        protected = {"status": "recovery_required", "trusted": True,
                     "job_id": original["operationId"], "digest": "bound-system-request-digest",
                     "receipt": {"event": "terminal", "receiptSequence": 8,
                                 "result": {"status": "committed"}}, "errors": []}
        calls = []

        def reader(operation):
            calls.append(deepcopy(operation))
            operation["commitRecorded"] = True  # Private input must not alter journal authority.
            operation["receipt"]["targets"][0]["plan"]["after"]["raw"] = "not approved"
            return protected

        environment_bytes = self.env.path.read_bytes()
        restarted = Executor(FakeWindows(self.env), self.db, system_receipt_reader=reader,
                             system_session_factory=lambda *_: self.fail("Startup relaunched helper"),
                             on_committed=lambda *_: self.fail("Startup replayed commit callback"))
        report = restarted.reconcile_pending()[0]
        self.assertEqual(report["status"], "recovery_required", report)
        saved = self.db.find_execution("request-1")["body"]
        self.assertEqual(calls, [original])
        self.assertFalse(saved["commitRecorded"])
        self.assertEqual(saved["protectedSystemReceipt"], protected)
        self.assertEqual(saved["receipt"], original["receipt"])
        self.assertEqual(saved["targets"], original["targets"])
        self.assertEqual(saved["result"], original["result"])
        self.assertEqual(restarted.execute(request), original["result"])
        self.assertEqual(self.env.path.read_bytes(), environment_bytes)
        self.assertEqual(self.db.history(), original_history)
        self.db.close()
        self.db = Database(self.root / "state.sqlite3")
        self.assertEqual(self.db.find_execution("request-1")["body"]["protectedSystemReceipt"], protected)

    def test_missing_or_untrusted_protected_receipt_keeps_prior_receipt_and_original_unknown_result(self):
        request, original = self.pending_unknown_system()
        original_history = deepcopy(self.db.history())
        protected = {"trusted": True, "receipt": {"event": "phase", "receiptSequence": 4}}
        observer = Executor(FakeWindows(self.env), self.db, system_receipt_reader=lambda _: protected)
        observer.reconcile_pending()
        failed = {"status": "recovery_required", "trusted": False, "receipt": None,
                  "errors": [{"code": "UNSAFE_ACL", "message": "protected receipt ACL invalid"}]}

        def missing(_):
            raise ExecutionError("RECEIPT_MISSING", "Original protected receipt not found")

        for reader, code in ((lambda _: failed, "SYSTEM_RECEIPT_UNTRUSTED"), (missing, "RECEIPT_MISSING")):
            with self.subTest(code=code):
                observer.system_receipt_reader = reader
                self.assertEqual(observer.reconcile_pending()[0]["status"], "recovery_required")
                saved = self.db.find_execution("request-1")["body"]
                self.assertEqual(saved["protectedSystemReceipt"], protected)
                self.assertEqual(saved["protectedSystemReceiptError"]["code"], code)
                self.assertEqual(saved["receipt"], original["receipt"])
                self.assertEqual(saved["result"], original["result"])
                self.assertEqual(observer.execute(request), original["result"])
        self.assertEqual(saved["protectedSystemReceiptObservation"], failed)
        self.assertEqual(len(self.env.mutations), 2)
        self.assertEqual(self.db.history(), original_history)

    def test_user_only_pending_reconciliation_never_invokes_system_receipt_reader(self):
        receipt = self.receipt([("User", "A", "new")])
        request = self.request(receipt)
        self.env.before_mutation = lambda *_: (_ for _ in ()).throw(Crash())
        with self.assertRaises(Crash):
            self.executor.execute(request)
        restarted = Executor(FileEnvironment(self.env.path), self.db,
                             system_receipt_reader=lambda _: self.fail("User-only operation read System receipt"))
        self.assertEqual(restarted.reconcile_pending()[0]["status"], "recovery_required")


if __name__ == "__main__":
    unittest.main()
