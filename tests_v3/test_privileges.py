"""Isolated UAC/helper tests. No real elevation, Registry, DLL or broadcast calls."""

from contextlib import contextmanager
import copy
import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from envmanager_v3.environment import FileEnvironment, WindowsEnvironment, V3EnvironmentError
from envmanager_v3 import privileges as p
from envmanager_v3 import system_helper as h
from envmanager_v3 import native_ipc as ipc


def pipe_outcome(result, digest, job_id, exit_code=0):
    """Explicit authenticated-transport double, never read from a writable file."""
    receipt = {"event": "terminal", "job_id": job_id, "digest": digest,
               "receiptSequence": 10, "result": copy.deepcopy(result)}
    return p._PipeOutcome(result, receipt, exit_code)


def snapshot(name, raw=None, value_type="REG_SZ", *, exists=True):
    return {"scope": "System", "name": name, "exists": exists,
            "raw": raw if exists else None, "type": value_type if exists else None,
            "revision": "parent-process-preview-token"}


def plan(name="A", before="old", after="new"):
    return {"scope": "System", "name": name,
            "before": snapshot(name, before, exists=before is not None),
            "after": {"exists": after is not None, "raw": after,
                      "type": "REG_SZ" if after is not None else None}}


def envelope(plans=None, *, restore=False):
    return {"protocol": 1, "job_id": "a" * 32, "restore": restore,
            "plans": [plan()] if plans is None else plans}


@contextmanager
def python_private_directory(path):
    path.mkdir(mode=0o700)
    yield


class Base(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        # Tests opt in to explicit doubles; an accidental native call fails here.
        self.native_guards = {}
        for module, name in ((p, "_dll"), (p, "_launch_helper"), (h, "WindowsEnvironment")):
            guard = mock.patch.object(module, name, side_effect=AssertionError("Native access forbidden"))
            guard.start()
            self.native_guards[name] = guard
            self.addCleanup(guard.stop)

    def error(self, code, operation, *args, **kwargs):
        with self.assertRaises(V3EnvironmentError) as caught:
            operation(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        json.dumps(caught.exception.to_dict(), allow_nan=False)
        return caught.exception


class ValidationTests(Base):
    def test_validated_envelope_does_not_share_mutable_plan_or_snapshots(self):
        source = envelope()
        frozen = h.validate_request(source)
        source["plans"][0]["before"]["raw"] = "changed"
        source["plans"][0]["after"]["raw"] = "changed"
        source["plans"].clear()
        self.assertEqual(frozen["plans"][0]["before"]["raw"], "old")
        self.assertEqual(frozen["plans"][0]["after"]["raw"], "new")

    def test_reparse_components_and_non_nonce_paths_rejected(self):
        fake_info = mock.Mock(st_file_attributes=0x400, st_mode=0o100600)
        with mock.patch.object(Path, "lstat", return_value=fake_info):
            self.error("UNSAFE_PATH", p._no_reparse, self.root / "request.json")
        for directory in ("not-a-nonce", "A" * 32, "a" * 31):
            self.error("INVALID_REQUEST", h.load_request,
                       self.root / directory / "request.json", "a" * 64)

    def test_request_json_is_bounded_and_strict_before_adapter_construction(self):
        job = self.root / ("a" * 32)
        job.mkdir()
        path = job / "request.json"
        for data in (b'{"x":NaN}', b'{"x":Infinity}', b'{"x":' + b'[' * 2000 + b']' * 2000 + b'}',
                     b'\xff', b'[]'):
            path.write_bytes(data)
            self.error("INVALID_REQUEST", h.load_request, path, hashlib.sha256(data).hexdigest())
        data = p._json_bytes(envelope())
        path.write_bytes(data)
        with mock.patch.object(h, "MAX_REQUEST_BYTES", len(data) - 1):
            self.error("REQUEST_TOO_LARGE", h.load_request, path, hashlib.sha256(data).hexdigest())

    def test_exact_schema_system_only_and_no_command_or_key_inputs(self):
        valid = h.validate_request(envelope())
        self.assertEqual(valid["plans"][0]["before"]["revision"], "parent-process-preview-token")
        mutations = []
        request = envelope(); request["command"] = "whoami"; mutations.append(request)
        request = envelope(); request["plans"][0]["key"] = "Software\\Other"; mutations.append(request)
        request = envelope(); request["plans"][0]["scope"] = "User"; mutations.append(request)
        request = envelope(); request["plans"][0]["before"]["scope"] = "User"; mutations.append(request)
        request = envelope(); request["plans"][0]["after"]["scope"] = "User"; mutations.append(request)
        request = envelope(); del request["plans"][0]["before"]["raw"]; mutations.append(request)
        request = envelope(); request["restore"] = 1; mutations.append(request)
        for request in mutations:
            self.error("INVALID_REQUEST", h.validate_request, request)

    def test_names_types_raw_and_utf16_limits(self):
        for name in ("", "a=b", "a\0b", "\ud800", "x" * 16384, None):
            self.error("INVALID_REQUEST", h.validate_request, envelope([plan(name)]))
        for raw in (False, "x\0y", "\ud800", "😀" * 16384, "x" * 32767):
            self.error("INVALID_REQUEST", h.validate_request, envelope([plan(after=raw)]))
        for value_type in ("REG_DWORD", 1, None, "REG_MULTI_SZ"):
            request = envelope(); request["plans"][0]["after"]["type"] = value_type
            self.error("INVALID_REQUEST", h.validate_request, request)
        request = envelope(); request["plans"][0]["after"]["type"] = "REG_EXPAND_SZ"
        self.error("TYPE_MISMATCH", h.validate_request, request)
        request = envelope(); request["plans"][0]["before"]["supported"] = False
        self.error("INVALID_REQUEST", h.validate_request, request)
        request = envelope(); request["plans"][0]["after"] = {"exists": False, "raw": "", "type": None}
        self.error("INVALID_REQUEST", h.validate_request, request)
        self.assertEqual(h.validate_request(envelope([plan(after="😀" * 16383)]))["protocol"], 1)

    def test_duplicate_canonical_target_and_sharp_s_distinction(self):
        self.error("INVALID_REQUEST", h.validate_request, envelope([plan("Path"), plan("PATH")]))
        self.assertEqual(len(h.validate_request(envelope([plan("ß"), plan("SS")]))["plans"]), 2)
        self.error("INVALID_REQUEST", h.validate_request, envelope([]))

    def test_digest_and_path_validation_before_execution(self):
        job = self.root / ("a" * 32)
        job.mkdir()
        path = job / "request.json"
        data = p._json_bytes(envelope())
        digest = hashlib.sha256(data).hexdigest()
        path.write_bytes(data)
        self.assertEqual(h.load_request(path, digest)["job_id"], "a" * 32)
        path.write_bytes(data.replace(b"old", b"OLD"))
        self.error("DIGEST_MISMATCH", h.load_request, path, digest)
        self.error("INVALID_REQUEST", h.load_request, job / "arbitrary.json", digest)
        self.error("INVALID_REQUEST", h.load_request, Path("request.json"), digest)
        self.error("INVALID_REQUEST", h.load_request, path, "not-a-digest")
        request = envelope(); request["job_id"] = "b" * 32
        changed = p._json_bytes(request); path.write_bytes(changed)
        self.error("INVALID_REQUEST", h.load_request, path, hashlib.sha256(changed).hexdigest())
        duplicate = data.replace(b'"protocol":1', b'"protocol":1,"protocol":1')
        path.write_bytes(duplicate)
        self.error("INVALID_REQUEST", h.load_request, path, hashlib.sha256(duplicate).hexdigest())


class CoreTests(Base):
    def setUp(self):
        super().setUp()
        self.environment = FileEnvironment(self.root / "environment.json")
        self.environment.write("System", "A", "old")
        self.environment.write("System", "B", "before-b")
        self.environment.write("User", "A", "untouched-user")

    def execute(self, request=None, environment=None):
        return h.execute_helper_request(envelope() if request is None else request,
                                        self.environment if environment is None else environment,
                                        journal_path=self.root / "progress.jsonl")

    def events(self):
        return [json.loads(line) for line in (self.root / "progress.jsonl").read_bytes().splitlines()]

    def test_permission_denied_has_no_writes_and_returns_rejected_receipt(self):
        with mock.patch.object(self.environment, "check_permission",
                               side_effect=V3EnvironmentError("ACCESS_DENIED", "Denied")), \
                mock.patch.object(self.environment, "write") as write:
            result = self.execute()
        write.assert_not_called()
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["errors"][0]["code"], "ACCESS_DENIED")

    def test_change_after_preflight_is_rechecked_without_overwrite(self):
        original = self.environment.read
        reads = []
        def read(scope, name):
            reads.append(name)
            if len(reads) == 2:
                self.environment.write("System", name, "external")
            return original(scope, name)
        with mock.patch.object(self.environment, "read", side_effect=read):
            result = self.execute()
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["errors"][0]["code"], "SNAPSHOT_MISMATCH")
        self.assertEqual(original("System", "A")["raw"], "external")

    def test_adapter_cas_rejects_race_after_last_read(self):
        original = self.environment.write
        def race(scope, name, raw, *args, **kwargs):
            if raw == "new":
                original(scope, name, "external")
            return original(scope, name, raw, *args, **kwargs)
        with mock.patch.object(self.environment, "write", side_effect=race):
            result = self.execute()
        self.assertEqual(result["status"], "recovery_required")
        self.assertEqual(result["errors"][0]["code"], "CONFLICT")
        self.assertEqual(self.environment.read("System", "A")["raw"], "external")

    def test_write_without_effect_is_not_a_successful_commit(self):
        with mock.patch.object(self.environment, "write", return_value=None):
            result = self.execute()
        self.assertEqual(result["status"], "compensated")
        self.assertEqual(result["errors"][0]["code"], "READBACK_MISMATCH")
        self.assertEqual(self.environment.read("System", "A")["raw"], "old")

    def _assert_external_destination_race(self, after):
        from tests_v3.test_environment import FakeWinreg
        registry = FakeWinreg()
        registry.rows["System"] = [("A", "old", registry.REG_SZ)]
        environment = WindowsEnvironment(registry, notifier=lambda: True)
        real_append = h._Journal.append
        def race(journal, event, **data):
            real_append(journal, event, **data)
            if event == "write_intent":
                # External mutation after helper preflight, before adapter CAS.
                registry.rows["System"] = ([] if after is None else [("A", after, registry.REG_SZ)])
        with mock.patch.object(h._Journal, "append", new=race):
            result = self.execute(envelope([plan(after=after)]), environment)
        self.assertEqual(result["status"], "recovery_required")
        self.assertEqual(result["targets"][0]["status"], "recovery_required")
        self.assertEqual(result["errors"][0]["code"], "CONFLICT")
        self.assertIn("COMPENSATION_CONFLICT", [error["code"] for error in result["errors"]])
        self.assertEqual([call for call in registry.calls if call[0] in ("set", "delete")], [])
        actual = environment.read("System", "A")
        self.assertEqual(actual["exists"], after is not None)
        self.assertEqual(actual["raw"], after)

    def test_rejected_write_preserves_external_value_equal_to_planned_after(self):
        self._assert_external_destination_race("new")

    def test_rejected_delete_does_not_recreate_externally_deleted_value(self):
        self._assert_external_destination_race(None)

    def test_final_readback_detects_earlier_target_changed_during_notification(self):
        def notify():
            self.environment.write("System", "A", "external")
        with mock.patch.object(self.environment, "notify", side_effect=notify):
            result = self.execute()
        self.assertEqual(result["status"], "recovery_required")
        self.assertEqual(result["errors"][0]["code"], "READBACK_MISMATCH")
        self.assertEqual(self.environment.read("System", "A")["raw"], "external")

    def test_compensation_uses_cas_and_preserves_new_external_value(self):
        original = self.environment.write
        def race(scope, name, raw, *args, **kwargs):
            if name == "B":
                raise V3EnvironmentError("INJECTED_FAILURE", "Write failed")
            if raw == "old":
                original(scope, name, "external-during-compensation")
            return original(scope, name, raw, *args, **kwargs)
        with mock.patch.object(self.environment, "write", side_effect=race):
            result = self.execute(envelope([plan(), plan("B", "before-b", "after-b")]))
        self.assertEqual(result["status"], "recovery_required")
        self.assertEqual(self.environment.read("System", "A")["raw"], "external-during-compensation")
        self.assertIn("CONFLICT", [error["code"] for error in result["errors"]])

    def test_failed_compensation_readback_stays_recovery_required(self):
        original = self.environment.write
        def fail(scope, name, raw, *args, **kwargs):
            if name == "B":
                raise V3EnvironmentError("INJECTED_FAILURE", "Write failed")
            if raw == "old":
                return None  # A failed adapter silently did not restore.
            return original(scope, name, raw, *args, **kwargs)
        with mock.patch.object(self.environment, "write", side_effect=fail):
            result = self.execute(envelope([plan(), plan("B", "before-b", "after-b")]))
        self.assertEqual(result["status"], "recovery_required")
        self.assertIn("COMPENSATION_READBACK", [error["code"] for error in result["errors"]])

    def test_final_journal_failure_never_returns_committed(self):
        real_append = h._Journal.append
        def append(journal, event, **data):
            if event == "finished":
                journal.failed = True
                raise p.ElevationError("JOURNAL_IO", "Disk full")
            return real_append(journal, event, **data)
        with mock.patch.object(h._Journal, "append", new=append):
            result = self.execute()
        self.assertEqual(result["status"], "recovery_required")
        self.assertEqual(self.environment.read("System", "A")["raw"], "new")

    def test_parent_revision_is_not_used_as_cross_process_cas(self):
        with mock.patch.object(self.environment, "write", wraps=self.environment.write) as write:
            result = self.execute()
        self.assertEqual(result["status"], "committed")
        self.assertEqual(self.environment.read("System", "A")["raw"], "new")
        self.assertEqual(self.environment.read("User", "A")["raw"], "untouched-user")
        self.assertNotEqual(write.call_args.kwargs["expected"]["revision"],
                            "parent-process-preview-token")
        self.assertEqual(result["targets"][0]["before"]["raw"], "old")
        events = [item["event"] for item in self.events()]
        self.assertLess(events.index("write_intent"), events.index("write_verified"))
        self.assertEqual(events[-1], "finished")

    def test_all_targets_preflight_before_any_write(self):
        request = envelope([plan(), plan("B", "wrong-before", "after-b")])
        with mock.patch.object(self.environment, "write", wraps=self.environment.write) as write:
            result = self.execute(request)
        write.assert_not_called()
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["errors"][0]["code"], "SNAPSHOT_MISMATCH")
        self.assertEqual(self.environment.read("System", "A")["raw"], "old")

    def test_create_delete_and_restore_use_exact_original_types_and_values(self):
        request = envelope([plan("A", "old", None), plan("New", None, " \r\n;;重复;重复; ")])
        request["plans"][1]["after"]["type"] = "REG_EXPAND_SZ"
        first = self.execute(request)
        self.assertEqual(first["status"], "committed")
        self.assertFalse(self.environment.read("System", "A")["exists"])
        self.assertEqual(self.environment.read("System", "New")["raw"], " \r\n;;重复;重复; ")
        (self.root / "progress.jsonl").rename(self.root / "first.jsonl")
        request["restore"] = True
        restored = self.execute(request)
        self.assertEqual(restored["status"], "compensated")
        self.assertEqual(self.environment.read("System", "A")["raw"], "old")
        self.assertEqual(self.environment.read("System", "A")["type"], "REG_SZ")
        self.assertFalse(self.environment.read("System", "New")["exists"])

    def test_failed_later_write_compensates_confirmed_prior_but_retains_uncertain_write(self):
        request = envelope([plan(), plan("B", "before-b", "after-b")])
        original = self.environment.write
        def fail_after_write(scope, name, raw, *args, **kwargs):
            value = original(scope, name, raw, *args, **kwargs)
            if name == "B" and raw == "after-b":
                raise V3EnvironmentError("INJECTED_FAILURE", "Failure after side effect")
            return value
        with mock.patch.object(self.environment, "write", side_effect=fail_after_write):
            result = self.execute(request)
        self.assertEqual(result["status"], "recovery_required")
        self.assertEqual(self.environment.read("System", "A")["raw"], "old")
        self.assertEqual(self.environment.read("System", "B")["raw"], "after-b")
        self.assertEqual([item["status"] for item in result["targets"]],
                         ["compensated", "recovery_required"])

    def test_compensation_preserves_external_new_values(self):
        request = envelope([plan(), plan("B", "before-b", "after-b")])
        original = self.environment.write
        def conflicting_write(scope, name, raw, *args, **kwargs):
            if name == "B" and raw == "after-b":
                original("System", "A", "external-new-value")
                raise V3EnvironmentError("INJECTED_FAILURE", "Cannot write B")
            return original(scope, name, raw, *args, **kwargs)
        with mock.patch.object(self.environment, "write", side_effect=conflicting_write):
            result = self.execute(request)
        self.assertEqual(result["status"], "recovery_required")
        self.assertEqual(self.environment.read("System", "A")["raw"], "external-new-value")
        self.assertIn("COMPENSATION_CONFLICT", [error["code"] for error in result["errors"]])

    def test_restore_precheck_conflict_never_overwrites_external_value(self):
        self.environment.write("System", "A", "external")
        request = envelope(restore=True)
        with mock.patch.object(self.environment, "write", wraps=self.environment.write) as write:
            result = self.execute(request)
        write.assert_not_called()
        self.assertEqual(result["status"], "recovery_required")
        self.assertEqual(self.environment.read("System", "A")["raw"], "external")

    def test_restore_partial_failure_keeps_successful_restorations(self):
        self.environment.write("System", "A", "new")
        self.environment.write("System", "B", "after-b")
        request = envelope([plan(), plan("B", "before-b", "after-b")], restore=True)
        original = self.environment.write
        def fail_b(scope, name, raw, *args, **kwargs):
            if name == "B":
                raise V3EnvironmentError("INJECTED_FAILURE", "Cannot restore B")
            return original(scope, name, raw, *args, **kwargs)
        with mock.patch.object(self.environment, "write", side_effect=fail_b):
            result = self.execute(request)
        self.assertEqual(result["status"], "recovery_required")
        self.assertEqual(self.environment.read("System", "A")["raw"], "old")
        self.assertEqual(self.environment.read("System", "B")["raw"], "after-b")

    def test_notification_failure_does_not_report_commit_success(self):
        with mock.patch.object(self.environment, "notify",
                               side_effect=V3EnvironmentError("NOTIFY_FAILED", "Timeout")):
            result = self.execute()
        self.assertEqual(result["status"], "recovery_required")
        self.assertEqual(self.environment.read("System", "A")["raw"], "old")
        self.assertIn("NOTIFY_FAILED", [error["code"] for error in result["errors"]])

    def test_journal_failure_before_write_stops_all_writes(self):
        real_append = h._Journal.append
        def fail_intent(journal, event, **data):
            if event == "write_intent":
                journal.failed = True
                raise p.ElevationError("JOURNAL_IO", "Full disk")
            return real_append(journal, event, **data)
        with mock.patch.object(h._Journal, "append", new=fail_intent), \
                mock.patch.object(self.environment, "write", wraps=self.environment.write) as write:
            result = self.execute()
        write.assert_not_called()
        self.assertNotEqual(result["status"], "committed")
        self.assertEqual(self.environment.read("System", "A")["raw"], "old")

    def test_journal_failure_after_write_requires_reconciliation_not_unlogged_rollback(self):
        real_append = h._Journal.append
        def fail_verified(journal, event, **data):
            if event == "write_verified":
                journal.failed = True
                raise p.ElevationError("JOURNAL_IO", "Full disk")
            return real_append(journal, event, **data)
        with mock.patch.object(h._Journal, "append", new=fail_verified):
            result = self.execute()
        self.assertEqual(result["status"], "recovery_required")
        self.assertEqual(self.environment.read("System", "A")["raw"], "new")
        self.assertEqual(result["targets"][0]["status"], "recovery_required")

    def test_journal_is_fsynced_before_registry_mutation_and_refuses_replay(self):
        events = []
        real_fsync = os.fsync
        original = self.environment.write
        def fsync(fd):
            events.append("fsync")
            real_fsync(fd)
        def write(*args, **kwargs):
            self.assertEqual(events[-1], "fsync")
            events.append("write")
            return original(*args, **kwargs)
        with mock.patch.object(h.os, "fsync", side_effect=fsync), \
                mock.patch.object(self.environment, "write", side_effect=write):
            self.assertEqual(self.execute()["status"], "committed")
        self.error("JOURNAL_IO", self.execute)

    def test_noop_neither_writes_nor_notifies(self):
        with mock.patch.object(self.environment, "write") as write, \
                mock.patch.object(self.environment, "notify") as notify:
            result = self.execute(envelope([plan(after="old")]))
        self.assertEqual(result["status"], "committed")
        self.assertEqual(result["targets"][0]["status"], "unchanged")
        write.assert_not_called(); notify.assert_not_called()


class LauncherTests(Base):
    def setUp(self):
        super().setUp()
        # Never call the real Windows DACL/CreateFile APIs in file-based tests.
        self.directory = mock.patch.object(p, "_create_private_directory",
                                           side_effect=lambda path: path.mkdir(mode=0o700))
        self.directory.start(); self.addCleanup(self.directory.stop)
        @contextmanager
        def lock(path):
            yield
        self.lock = mock.patch.object(p, "_locked_request", side_effect=lock)
        self.lock.start(); self.addCleanup(self.lock.stop)

    def test_noop_batch_does_not_launch_or_create_job_directory(self):
        for restore in (False, True):
            with self.subTest(restore=restore), mock.patch.object(p, "_launch_helper") as launch:
                result = p.run_system_batch([plan(after="old")], self.root / "jobs", restore=restore)
            launch.assert_not_called()
            self.assertFalse((self.root / "jobs").exists())
            self.assertEqual(result["status"], "compensated" if restore else "committed")
            self.assertEqual(result["targets"][0]["status"], "unchanged")

    def test_nonce_jobs_are_unique_request_bytes_fsynced_and_hash_bound(self):
        digests = []
        def launch(path, digest):
            data = path.read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), digest)
            request = h.load_request(path, digest)
            self.assertEqual(path.parent.name, request["job_id"])
            self.assertEqual(len(path.parent.name), 32)
            digests.append(digest)
            raise p.ElevationError("UAC_CANCELLED", "Cancelled")
        with mock.patch.object(p, "_launch_helper", side_effect=launch), \
                mock.patch.object(p.os, "fsync", wraps=os.fsync) as fsync:
            first = p.run_system_batch([plan()], self.root / "jobs")
            second = p.run_system_batch([plan()], self.root / "jobs")
        self.assertNotEqual(first["job_dir"], second["job_dir"])
        self.assertNotEqual(*digests)
        self.assertEqual(fsync.call_count, 2)

    def test_receipt_is_bound_to_frozen_plans_and_verified_target_results(self):
        def changed_job(result): result["job_id"] = "b" * 32
        def changed_digest(result): result["digest"] = "0" * 64
        def changed_restore(result): result["restore"] = True
        def missing_target(result): result["targets"].clear()
        def other_target(result): result["targets"][0]["name"] = "B"
        def other_before(result): result["targets"][0]["before"]["raw"] = "not-reviewed"
        def other_after(result): result["targets"][0]["after"]["raw"] = "not-reviewed"
        def unfinished(result): result["targets"][0]["status"] = "not_started"
        def wrong_readback(result): result["targets"][0]["result"]["raw"] = "wrong"
        def missing_readback(result): del result["targets"][0]["result"]
        def bad_status(result): result["status"] = "success"
        def errors_with_success(result): result["errors"] = [{"code": "FAILED"}]
        mutations = (changed_job, changed_digest, changed_restore, missing_target, other_target,
                     other_before, other_after, unfinished, wrong_readback, missing_readback,
                     bad_status, errors_with_success)
        for mutate in mutations:
            with self.subTest(mutate=mutate.__name__):
                def launch(path, digest):
                    request = h.load_request(path, digest)
                    target = copy.deepcopy(request["plans"][0])
                    target.update(status="committed", result=copy.deepcopy(target["after"]))
                    result = {"job_id": request["job_id"], "digest": digest, "restore": False,
                              "status": "committed", "targets": [target], "errors": []}
                    mutate(result)
                    return pipe_outcome(result, digest, request["job_id"])
                with mock.patch.object(p, "_launch_helper", side_effect=launch) as launcher:
                    result = p.run_system_batch([plan()], self.root)
                self.assertEqual(result["status"], "recovery_required")
                self.assertEqual(result["errors"][0]["code"], "HELPER_UNCERTAIN")
                launcher.assert_called_once()

    def test_corrupt_deep_duplicate_and_oversized_receipts_stay_uncertain(self):
        for data in (b'{"x":NaN}', b'{"x":1,"x":2}', b'{"x":' + b'[' * 2000 + b']' * 2000 + b'}',
                     b'\xff', b'[]', b' ' * (p.MAX_REQUEST_BYTES + 1)):
            with self.subTest(size=len(data)):
                def launch(path, digest):
                    (path.parent / "result.json").write_bytes(data)
                    return 0
                with mock.patch.object(p, "_launch_helper", side_effect=launch) as launcher:
                    result = p.run_system_batch([plan()], self.root)
                self.assertEqual(result["status"], "recovery_required")
                self.assertEqual(result["errors"][0]["code"], "HELPER_UNCERTAIN")
                launcher.assert_called_once()

    def test_nonzero_exit_with_success_receipt_is_not_success(self):
        def launch(path, digest):
            request = h.load_request(path, digest)
            environment = FileEnvironment(path.parent / "environment.json")
            environment.write("System", "A", "old")
            result = h.execute_helper_request(request, environment, journal_path=path.parent / "progress.jsonl")
            result["digest"] = digest
            return pipe_outcome(result, digest, request["job_id"], 1)
        with mock.patch.object(p, "_launch_helper", side_effect=launch):
            result = p.run_system_batch([plan()], self.root)
        self.assertEqual(result["status"], "recovery_required")
        self.assertEqual(result["errors"][0]["code"], "HELPER_UNCERTAIN")

    def test_launcher_accepts_each_real_helper_terminal_status(self):
        for expected in ("committed", "compensated", "rejected", "recovery_required"):
            with self.subTest(status=expected):
                def launch(path, digest):
                    request = h.load_request(path, digest)
                    environment = FileEnvironment(path.parent / "isolated.json")
                    environment.write("System", "A", "wrong" if expected == "rejected" else "old")
                    environment.write("System", "B", "before-b")
                    environment.write("System", "C", "before-c")
                    original = environment.write
                    def write(scope, name, raw, *args, **kwargs):
                        if name == "B" and expected in ("compensated", "recovery_required"):
                            if expected == "recovery_required":
                                original("System", "A", "external")
                            raise V3EnvironmentError("INJECTED_FAILURE", "B cannot be written")
                        return original(scope, name, raw, *args, **kwargs)
                    with mock.patch.object(environment, "write", side_effect=write):
                        receipt = h.execute_helper_request(request, environment,
                                                           journal_path=path.parent / "progress.jsonl")
                    self.assertEqual(receipt["status"], expected)
                    receipt["digest"] = digest
                    return pipe_outcome(receipt, digest, request["job_id"],
                                        0 if expected in ("committed", "compensated") else 1)
                with mock.patch.object(p, "_launch_helper", side_effect=launch) as launcher:
                    result = p.run_system_batch([plan(), plan("B", "before-b", "after-b"),
                                                 plan("C", "before-c", "after-c")], self.root)
                self.assertEqual(result["status"], expected)
                self.assertNotIn("launch_status", result)
                launcher.assert_called_once()

    def test_launcher_accepts_verified_explicit_restore_receipt(self):
        def launch(path, digest):
            request = h.load_request(path, digest)
            environment = FileEnvironment(path.parent / "isolated.json")
            environment.write("System", "A", "new")
            receipt = h.execute_helper_request(request, environment, journal_path=path.parent / "progress.jsonl")
            receipt["digest"] = digest
            return pipe_outcome(receipt, digest, request["job_id"])
        with mock.patch.object(p, "_launch_helper", side_effect=launch):
            result = p.run_system_batch([plan()], self.root, restore=True)
        self.assertEqual(result["status"], "compensated")
        self.assertTrue(result["restore"])
        self.assertEqual(result["targets"][0]["result"]["raw"], "old")

    def test_launch_oserror_is_uncertain_and_never_retried(self):
        with mock.patch.object(p, "_launch_helper", side_effect=OSError("Cannot wait")) as launch:
            result = p.run_system_batch([plan()], self.root)
        self.assertEqual(result["status"], "recovery_required")
        launch.assert_called_once()

    def test_shell_execute_success_waits_reads_exit_code_and_closes_handle(self):
        def execute(pointer):
            pointer._obj.hProcess = 123
            return True
        def exit_code(handle, pointer):
            pointer._obj.value = 7
            return True
        shell = mock.Mock(ShellExecuteExW=mock.Mock(side_effect=execute))
        kernel = mock.Mock(WaitForSingleObject=mock.Mock(return_value=0),
                           GetExitCodeProcess=mock.Mock(side_effect=exit_code))
        with mock.patch.object(p, "_dll", side_effect=lambda name: shell if name == "shell32" else kernel):
            self.assertEqual(p._shell_execute(["python.exe", "--system-helper"], self.root), 7)
        kernel.WaitForSingleObject.assert_called_once_with(123, 30000)
        kernel.CloseHandle.assert_called_once_with(123)

    def test_shell_execute_wait_failure_is_uncertain_and_closes_handle(self):
        def execute(pointer):
            pointer._obj.hProcess = 123
            return True
        shell = mock.Mock(ShellExecuteExW=mock.Mock(side_effect=execute))
        kernel = mock.Mock(WaitForSingleObject=mock.Mock(return_value=0xFFFFFFFF))
        with mock.patch.object(p, "_dll", side_effect=lambda name: shell if name == "shell32" else kernel):
            self.error("HELPER_UNCERTAIN", p._shell_execute, ["python.exe"], self.root)
        kernel.CloseHandle.assert_called_once_with(123)

    def test_uac_cancel_does_not_execute_and_restore_cancel_requires_recovery(self):
        with mock.patch.object(p, "_launch_helper", side_effect=p.ElevationError("UAC_CANCELLED", "Cancelled")):
            result = p.run_system_batch([plan()], self.root)
            rollback = p.run_system_batch([plan()], self.root, restore=True)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(rollback["status"], "recovery_required")
        self.assertEqual(rollback["errors"][0]["code"], "UAC_CANCELLED")
        self.assertTrue((Path(result["job_dir"]) / "request.json").is_file())
        self.assertFalse((Path(result["job_dir"]) / "progress.jsonl").exists())

    def test_launcher_writes_fsyncs_locks_and_receives_fixed_helper_result(self):
        active = []
        @contextmanager
        def locked(path):
            active.append(path)
            try:
                yield
            finally:
                active.remove(path)
        environment = FileEnvironment(self.root / "isolated.json")
        environment.write("System", "A", "old")
        def fake_launch(path, digest):
            self.assertIn(path, active)
            request = h.load_request(path, digest)
            result = h.execute_helper_request(request, environment, journal_path=path.parent / "progress.jsonl")
            result["digest"] = digest
            return pipe_outcome(result, digest, request["job_id"])
        with mock.patch.object(p, "_locked_request", side_effect=locked), \
                mock.patch.object(p, "_launch_helper", side_effect=fake_launch):
            result = p.run_system_batch([plan()], self.root / "jobs")
        self.assertEqual(result["status"], "committed")
        self.assertEqual(environment.read("System", "A")["raw"], "new")
        self.assertFalse(active)

    def test_invalid_plan_never_launches_or_creates_job(self):
        target = plan(); target["scope"] = "User"
        with mock.patch.object(p, "_launch_helper") as launch:
            self.error("INVALID_REQUEST", p.run_system_batch, [target], self.root / "jobs")
        launch.assert_not_called()
        self.assertFalse((self.root / "jobs").exists())

    def test_missing_or_invalid_receipt_is_uncertain_and_never_relaunched(self):
        with mock.patch.object(p, "_launch_helper", return_value=0) as launch:
            result = p.run_system_batch([plan()], self.root)
        self.assertEqual(result["status"], "recovery_required")
        self.assertEqual(result["errors"][0]["code"], "HELPER_UNCERTAIN")
        launch.assert_called_once()

    def test_fixed_pipe_command_source_and_unready_package_never_launch(self):
        self.native_guards["_launch_helper"].stop()
        name, nonce, job = ipc.pipe_name(), "a" * 64, "b" * 32
        self.error("SYSTEM_CAPABILITY_NOT_READY", p._helper_command, name, 12, job, nonce)
        with mock.patch.object(p, "_shell_execute") as elevation, mock.patch.object(p.subprocess, "run") as run:
            self.error("SYSTEM_CAPABILITY_NOT_READY", p._launch_helper, self.root / "request.json", "a" * 64)
        elevation.assert_not_called(); run.assert_not_called()
        with mock.patch.object(sys, "frozen", True, create=True):
            command = p._helper_command(name, 12, job, nonce)
        self.assertEqual(command, [str(Path(sys.executable).resolve()), "--system-helper",
                                  "--pipe", name, "--parent", "12", "--operation", job, "--nonce", nonce])

    def test_shell_execute_runas_hidden_and_cancelled_using_mock_api(self):
        seen = {}
        def execute(pointer):
            info = pointer._obj
            seen.update(verb=info.lpVerb, file=info.lpFile, parameters=info.lpParameters,
                        show=info.nShow, mask=info.fMask)
            return False
        shell = mock.Mock(ShellExecuteExW=mock.Mock(side_effect=execute))
        kernel = mock.Mock()
        command = [r"C:\Trusted Python\python.exe", "-m", "envmanager_v3.system_helper",
                   "--request", r"C:\Jobs\request.json", "--digest", "a" * 64]
        with mock.patch.object(p, "_dll", side_effect=lambda name: shell if name == "shell32" else kernel), \
                mock.patch.object(p.ctypes, "get_last_error", return_value=1223, create=True):
            self.error("UAC_CANCELLED", p._shell_execute, command, self.root)
        self.assertEqual(seen["verb"], "runas")
        self.assertEqual(seen["file"], command[0])
        self.assertEqual(seen["show"], 0)
        self.assertEqual(seen["parameters"], subprocess.list2cmdline(command[1:]))

    def test_windows_request_handle_allows_read_sharing_only(self):
        self.lock.stop()  # Exercise the actual wrapper, not setUp's core-test double.
        create, close = mock.Mock(return_value=123), mock.Mock(return_value=True)
        kernel = mock.Mock(CreateFileW=create, CloseHandle=close)
        with mock.patch.object(p.os, "name", "nt"), mock.patch.object(p, "_dll", return_value=kernel):
            with p._locked_request(self.root / "request.json"):
                close.assert_not_called()
        args = create.call_args.args
        self.assertEqual(args[1], 0x80000000)  # GENERIC_READ
        self.assertEqual(args[2], 1)  # FILE_SHARE_READ, no WRITE or DELETE
        close.assert_called_once_with(123)

    def test_windows_private_directory_uses_security_attributes(self):
        self.directory.stop()
        class Attributes(ctypes.Structure):
            _fields_ = [("placeholder", ctypes.c_int)]
        attributes = Attributes(1)
        @contextmanager
        def security():
            yield attributes
        create = mock.Mock(return_value=True)
        with mock.patch.object(p.os, "name", "nt"), \
                mock.patch.object(p, "_private_security", side_effect=security), \
                mock.patch.object(p, "_dll", return_value=mock.Mock(CreateDirectoryW=create)):
            p._create_private_directory(self.root / "private")
        self.assertIs(create.call_args.args[1]._obj, attributes)


class CliTests(Base):
    def test_cli_unready_capability_never_constructs_pipe_or_registry_adapter(self):
        args = ["--pipe", ipc.pipe_name(), "--parent", "12", "--operation", "a" * 32, "--nonce", "b" * 64]
        with mock.patch.object(h, "WindowsEnvironment") as factory, \
                mock.patch.object(ipc.NativePipe, "connect") as connect, mock.patch.object(h.sys, "stderr"):
            self.assertEqual(h.main(args), 1)
        factory.assert_not_called(); connect.assert_not_called()

    def test_tampered_cli_request_never_constructs_registry_adapter(self):
        job = self.root / ("a" * 32); job.mkdir()
        path = job / "request.json"; path.write_bytes(p._json_bytes(envelope()))
        with mock.patch.object(h, "WindowsEnvironment") as factory, \
                mock.patch.object(h.sys, "stderr"):
            with self.assertRaises(SystemExit) as caught:
                h.main(["--request", str(path), "--digest", "0" * 64])
        self.assertEqual(caught.exception.code, 2)  # Old file CLI cannot execute, even when elevated.
        factory.assert_not_called()
        self.assertFalse((job / "result.json").exists())


if __name__ == "__main__":
    unittest.main()
