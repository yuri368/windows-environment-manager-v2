"""Security protocol tests. Defaults: fake Win32/transport, file environment only.

Opt-in native test uses ordinary local processes and pipes ONLY; it never
constructs WindowsEnvironment, invokes UAC or writes Registry/ProgramData.
"""
from contextlib import contextmanager
from dataclasses import replace
import copy
import ctypes
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

from envmanager_v3 import native_ipc as n, privileges as p, system_helper as h
from envmanager_v3.environment import FileEnvironment
from tests_v3.test_privileges import envelope, plan, snapshot, pipe_outcome

SID = "S-1-5-21-101-102-103-1001"
NONCE = "b" * 64
JOB = "a" * 32


def reviewed(targets=None, rules="e" * 64):
    targets = [plan()] if targets is None else targets
    items = []
    for target in targets:
        options = {"target": {"scope": "System", "name": target["name"]}, "snapshot": target["before"], "selectedRaw": target["after"]["raw"], "mode": "replace"}
        items.append({"scope": "System", "name": target["name"], "beforeSnapshot": target["before"],
                      "plan": {"valid": True, "before": copy.deepcopy(target["before"]),
                               "after": copy.deepcopy(target["after"]), "operations": [{"type": "replace", "action": "REPLACE"}]},
                      "verification": {"method": "build", "options": options, "registryType": "REG_SZ"}})
    receipt = {"planId": "reviewed-plan", "version": 3, "status": "ready", "intent": {"kind": "apply"}, "engineHash": rules, "targets": items}
    receipt["digest"] = hashlib.sha256(p._json_bytes(receipt)).hexdigest()
    return {"protocol": 2, "plan_id": receipt["planId"], "version": 3, "plan_digest": receipt["digest"],
            "review_token": "already-consumed-database-token", "intent": receipt["intent"], "inputs": {"receipt": receipt},
            "operations": [t["plan"]["operations"] for t in items], "targets": targets, "rules_sha256": rules}


def rehash(review):
    receipt = review["inputs"]["receipt"]
    body = {k: v for k, v in receipt.items() if k != "digest"}
    receipt["digest"] = review["plan_digest"] = hashlib.sha256(p._json_bytes(body)).hexdigest()


def package_fixture():
    files = {"EnvManagerV3.exe": b"exe", "_internal/python313.dll": b"python", "_internal/base_library.zip": b"zip", "_internal/runtime/node.exe": b"node",
             "_internal/ui-v3/demo-plan.js": b"demo", "_internal/ui-v3/config-format.js": b"config", "_internal/envmanager_v3/engine_worker.cjs": b"worker"}
    rules = [{"name": name, "path": next(path for path in files if path.endswith("/" + name))} for name in n.RULE_NAMES]
    manifest = {"protocol": 2, "package_id": "EnvManagerV3", "version": "3.0", "entry": "EnvManagerV3.exe", "rules": rules,
                "files": {path: hashlib.sha256(data).hexdigest() for path, data in files.items()}}
    return files, manifest


class FakeWin:
    def __init__(self, root=None, files=None, manifest=None):
        self.calls, self.open, self.written = [], [], []
        self.root, self.files, self.manifest = root, files or {}, manifest
        self.owner = n.ADMIN

    def check(self, value, code="NATIVE_IPC"):
        if not value:
            n.fail(code, "Injected native failure")
        return value

    @contextmanager
    def security(self, sddl):
        self.calls.append(("security", sddl))
        yield ctypes.c_int(1)

    @contextmanager
    def file_handle(self, path, *args, **kwargs):
        path = str(path)
        self.calls.append(("file", path, args, kwargs))
        self.open.append(path)
        try:
            yield path
        finally:
            self.open.remove(path)

    def acl(self, handle):
        return self.owner, True, [(n.ADMIN, n.FULL), (n.SYSTEM, n.FULL), (n.AUTHENTICATED, 0x1200A9)]

    def read_all(self, handle, limit=n.MAX_REQUEST_BYTES):
        relative = Path(handle).relative_to(self.root).as_posix()
        return p._json_bytes(self.manifest) if relative == "system-package.json" else self.files[relative]

    def write_all(self, handle, data):
        self.written.append((handle, data))

    def kcall(self, name, restype, types, *args):
        self.calls.append((name, args))
        return 51 if name in ("CreateNamedPipeW", "CreateFileW") else True

    def close(self, handle):
        self.calls.append(("close", handle))


class MockOnly(unittest.TestCase):
    def setUp(self):
        for module, name in ((p, "_dll"), (n, "_dll"), (p, "_shell_execute"), (h, "WindowsEnvironment")):
            patch = mock.patch.object(module, name, side_effect=AssertionError("Unmocked privileged/native call forbidden"))
            patch.start(); self.addCleanup(patch.stop)

    def error(self, code, operation, *args, **kwargs):
        with self.assertRaises(p.ElevationError) as caught:
            operation(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)


class IdentityTests(MockOnly):
    def test_helper_pid_creation_token_session_path_and_hash_are_all_bound(self):
        parent = n.ProcessIdentity(10, SID, 2, 123, "C:/fixed/EnvManagerV3.exe", "a" * 64, False, False)
        helper = replace(parent, pid=20, created=456, elevated=True, admin=True)
        n.verify_helper(helper, helper, parent)
        # Over-the-shoulder UAC may use another admin SID; actual launch handle pins it.
        n.verify_helper(replace(helper, sid="S-1-5-21-999"), replace(helper, sid="S-1-5-21-999"), parent)
        for field, value in (("pid", 21), ("created", 457), ("sid", "S-1-5-21-4"), ("image_hash", "c" * 64), ("session", 3), ("image", "C:/other.exe"), ("elevated", False), ("admin", False)):
            with self.subTest(field=field):
                self.error("PEER_IDENTITY", n.verify_helper, replace(helper, **{field: value}), helper, parent)

    def test_pipe_server_and_client_have_fixed_local_names_and_restricted_flags(self):
        win = FakeWin()
        with n.NativePipe.create(SID, win=win) as server:
            self.assertTrue(server.name.startswith(n.PIPE_PREFIX))
            create = next(args for name, *rest in win.calls if name == "CreateNamedPipeW" for args in rest)
            self.assertEqual(create[1], 3 | 0x40000000 | 0x80000)
            self.assertEqual(create[2], 8)  # PIPE_REJECT_REMOTE_CLIENTS
            self.assertEqual(create[3], 1)
            self.assertIn("0x12019b", win.calls[0][1])
            with n.NativePipe.connect(server.name, win=win):
                pass
        client = next(args for name, *rest in win.calls if name == "CreateFileW" for args in rest)
        self.assertEqual(client[1], 0x12019B)  # No create-instance access via generic write.
        self.assertEqual(client[5] & 0x110000, 0x110000)  # Identification SQOS
        for name in (r"\\remote\pipe\EnvManagerV3-" + "a" * 64, n.PIPE_PREFIX + "short", n.PIPE_PREFIX + "A" * 64):
            self.error("INVALID_PIPE", n.NativePipe.connect, name, win=win)

    def test_frames_reject_duplicate_nan_deep_invalid_and_oversized_json(self):
        for data in (b'{"x":1,"x":2}', b'{"x":NaN}', b'\xff', b'[' * 2000 + b']' * 2000):
            self.error("IPC_JSON", n.decode, data)
        self.error("IPC_SIZE", n.decode, b" " * (n.MAX_REQUEST_BYTES + 1))
        pipe = n.NativePipe(FakeWin(), 51, server=True)
        with mock.patch.object(pipe, "_read", return_value=(n.MAX_REQUEST_BYTES + 1).to_bytes(4, "little")) as read:
            self.error("IPC_SIZE", pipe.receive)
        read.assert_called_once_with(4)

    def test_pipe_reuse_and_zero_length_io_are_rejected(self):
        pipe = n.NativePipe(FakeWin(), 51, server=True)
        with mock.patch.object(pipe, "_io", return_value=0):
            pipe.accept()
            self.error("IPC_REPLAY", pipe.accept)
            self.error("IPC_DISCONNECTED", pipe.send, {"ok": True})
            self.error("IPC_DISCONNECTED", pipe._read, 1)

    def test_pending_pipe_timeout_cancels_and_completes_io_before_freeing_event(self):
        win = FakeWin()
        def call(name, restype, types, *args):
            win.calls.append((name, args))
            return {"CreateEventW": 77, "ConnectNamedPipe": False, "WaitForSingleObject": 258}.get(name, True)
        win.kcall = call
        pipe = n.NativePipe(win, 51, server=True, timeout=0.01)
        with mock.patch.object(n.C, "get_last_error", return_value=997, create=True):
            self.error("IPC_TIMEOUT", pipe.accept)
        names = [entry[0] for entry in win.calls]
        self.assertLess(names.index("CancelIoEx"), names.index("GetOverlappedResult"))
        completion = next(args for name, args in win.calls if name == "GetOverlappedResult")
        self.assertIs(completion[-1], True)
        self.assertEqual(win.calls[-1], ("close", 77))


class PackageTests(MockOnly):
    def test_live_provider_hashes_three_named_rules_and_pins_all_package_handles(self):
        files, manifest = package_fixture()
        base = Path("C:/Program Files")
        root = base / "EnvManagerV3"
        win = FakeWin(root, files, manifest)
        with mock.patch.object(n.os, "name", "nt"), mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", str(root / "EnvManagerV3.exe")), \
                mock.patch.object(sys, "_MEIPASS", str(root / "_internal"), create=True), mock.patch.object(n, "known_folder", return_value=base):
            with n.verified_package(win=win) as package:
                digest = hashlib.sha256()
                for rule in manifest["rules"]:
                    digest.update(rule["name"].encode() + b"\0" + files[rule["path"]] + b"\0")
                self.assertEqual(package["rules_sha256"], digest.hexdigest())
                self.assertEqual(package["image_hash"], manifest["files"]["EnvManagerV3.exe"])
                self.assertTrue(all(str(root / path) in win.open for path in files))
            self.assertFalse(win.open)
            win.files["_internal/ui-v3/config-format.js"] = b"changed"
            self.error("PACKAGE_HASH", lambda: n.verified_package(win=win).__enter__())

    def test_capability_is_live_provider_result_not_a_hardcoded_acceptance_flag(self):
        @contextmanager
        def installed():
            yield {"package_digest": "d" * 64, "rules_sha256": "e" * 64}
        with mock.patch.object(n, "verified_package", side_effect=installed):
            self.assertTrue(n.system_capability()["ready"])
            self.assertTrue(n.require_production()["review_required"])
        with mock.patch.object(n, "verified_package", side_effect=p.ElevationError("PACKAGE_ACL", "Writable")):
            state = n.system_capability()
            self.assertFalse(state["ready"]); self.assertEqual(state["reasons"], ["PACKAGE_ACL"])

    def test_manifest_rejects_unlisted_rules_path_escape_missing_runtime_and_case_aliases(self):
        _, valid = package_fixture()
        n.validate_manifest(valid)
        mutations = []
        item = copy.deepcopy(valid); item["files"]["../escape.dll"] = "a" * 64; mutations.append(item)
        item = copy.deepcopy(valid); item["files"]["X:ads"] = "a" * 64; mutations.append(item)
        item = copy.deepcopy(valid); item["rules"].reverse(); mutations.append(item)
        item = copy.deepcopy(valid); item["files"]["ENVMANAGERV3.EXE"] = "a" * 64; mutations.append(item)
        item = copy.deepcopy(valid); del item["files"]["_internal/runtime/node.exe"]; mutations.append(item)
        for value in mutations:
            self.error("PACKAGE_SCHEMA", n.validate_manifest, value)

    def test_package_acl_and_receipt_acl_reject_user_write_owner_and_weak_permissions(self):
        trusted = [(n.ADMIN, n.FULL), (n.SYSTEM, n.FULL), (SID, n.READ)]
        n.verify_receipt_acl(n.ADMIN, True, trusted, {SID})
        n.verify_package_acl(n.TRUSTED_INSTALLER, False, trusted)
        for owner, protected, entries in ((SID, True, trusted), (n.ADMIN, False, trusted),
                (n.ADMIN, True, trusted + [("S-1-1-0", n.READ)]),
                (n.ADMIN, True, trusted[:-1] + [(SID, n.FULL)]), (n.ADMIN, True, [])):
            self.error("UNSAFE_ACL", n.verify_receipt_acl, owner, protected, entries, {SID})
        for mask in (2, 4, 0x40, 0x10000, 0x40000, 0x80000, 0x40000000, 0x10000000):
            self.error("PACKAGE_ACL", n.verify_package_acl, n.ADMIN, True, [(SID, mask)])


class ReceiptBoundaryTests(MockOnly):
    def acl(self, reader=SID):
        return n.ADMIN, True, [(n.ADMIN, n.FULL), (n.SYSTEM, n.FULL), (reader, n.READ)]

    def test_existing_weak_directory_is_rejected_never_repaired_and_nonce_never_reused(self):
        win = FakeWin()
        win.kcall = mock.Mock(return_value=False)
        win.acl = mock.Mock(return_value=(SID, True, self.acl()[2]))
        store = n.ReceiptStore(SID, JOB, win=win)
        with mock.patch.object(n.C, "get_last_error", return_value=183, create=True):
            self.error("UNSAFE_ACL", store._directory, Path("C:/fixed/system-receipts"), {SID})
            self.error("RECEIPT_REPLAY", store._directory, Path("C:/fixed/system-receipts") / JOB, {SID}, exclusive=True)
        store.stack.close()
        self.assertTrue(all(call.args[0] == "CreateDirectoryW" for call in win.kcall.call_args_list))

    def test_receipt_append_flushes_journal_then_private_file_then_atomic_write_through_replace(self):
        win = FakeWin(); win.acl = lambda _: self.acl()
        store = n.ReceiptStore(SID, JOB, win=win)
        store.path, store.journal = Path("C:/protected") / JOB, "journal-handle"
        record = store.append("write_intent", job_id=JOB, digest="c" * 64)
        self.assertEqual(record["receiptSequence"], 1)
        self.assertEqual(win.written[0][0], "journal-handle")
        self.assertEqual(json.loads(win.written[0][1]), record)
        self.assertEqual(json.loads(win.written[1][1]), record)
        rename = next(args for name, *rest in win.calls if name == "MoveFileExW" for args in rest)
        self.assertEqual(rename[1], str(store.path / "receipt.json"))
        self.assertEqual(rename[2], 1 | 8)  # replace-existing + write-through
        self.assertTrue(all(n.receipt_sddl({SID}) == call[1] for call in win.calls if call[0] == "security"))
        win.write_all = mock.Mock(side_effect=OSError("Flush failure"))
        before = len([call for call in win.calls if call[0] == "MoveFileExW"])
        with self.assertRaises(OSError): store.append("write_verified")
        self.assertEqual(len([call for call in win.calls if call[0] == "MoveFileExW"]), before)

    def test_reconciliation_uses_only_original_sid_operation_digest_under_protected_ancestors(self):
        win = FakeWin()
        base = Path("C:/ProgramData")
        def acl(handle):
            return self.acl(n.AUTHENTICATED if Path(handle).name in ("EnvManagerV3", "system-receipts") else SID)
        win.acl = acl
        record = {"job_id": JOB, "digest": "c" * 64, "receiptSequence": 7, "event": "terminal"}
        win.read_all = lambda _: p._json_bytes(record)
        with mock.patch.object(n.ReceiptStore, "_program_data", return_value=base):
            actual = n.read_protected_receipt(SID, JOB, "c" * 64, win=win)
            self.assertEqual(actual, record)
            self.error("RECEIPT_INVALID", n.read_protected_receipt, SID, JOB, "d" * 64, win=win)
        expected = base / "EnvManagerV3/system-receipts" / SID / JOB / "receipt.json"
        self.assertIn(str(expected), [call[1] for call in win.calls if call[0] == "file"])
        self.assertFalse(win.open)
        self.error("INVALID_SID", n.ReceiptStore, "../../other", JOB, win=win)
        self.error("INVALID_REQUEST", n.ReceiptStore, SID, "../../other", win=win)

    def test_native_handle_path_check_rejects_reparse_object_before_acl_or_io(self):
        win = object.__new__(n.Win32)
        def call(name, restype, types, *args):
            if name == "CreateFileW": return 51
            if name == "GetFileInformationByHandleEx": args[2][0] = 0x400
            return True
        win.kcall = mock.Mock(side_effect=call)
        self.error("UNSAFE_PATH", lambda: win.file_handle("C:/fixed/junction", directory=True).__enter__())
        create = next(call for call in win.kcall.call_args_list if call.args[0] == "CreateFileW")
        self.assertEqual(create.args[8] & 0x00200000, 0x00200000)
        self.assertEqual(create.args[5] & 4, 0)  # Deny delete sharing while checking.
        self.assertEqual(win.kcall.call_args_list[-1].args[0], "CloseHandle")


class RuleRuntimeTests(MockOnly):
    def test_fixed_node_worker_arguments_scrubbed_environment_and_bounded_process(self):
        def windows_directory(buffer, size):
            buffer.value = "C:/Windows"
            return len(buffer.value)
        response = {"ok": True, "result": {"valid": True, "after": {}, "operations": []}}
        run = mock.Mock(return_value=type("Process", (), {"returncode": 0, "stdout": p._json_bytes(response)})())
        package = {"root": "C:/Program Files/EnvManagerV3", "rules_sha256": "e" * 64}
        with mock.patch.object(n, "_dll", return_value=object()), mock.patch.object(n, "_api", return_value=windows_directory), \
                mock.patch.object(n.subprocess, "run", run), mock.patch.dict(os.environ, {"NODE_OPTIONS": "--require malicious.js", "NODE_PATH": "C:/user"}):
            self.assertEqual(n._rule_call(package, "build", {"selectedRaw": "raw;verbatim"}), response["result"])
        args, kwargs = run.call_args.args, run.call_args.kwargs
        self.assertEqual(args[0], [str(Path(package["root"]) / "_internal/runtime/node.exe"), str(Path(package["root"]) / "_internal/envmanager_v3/engine_worker.cjs"), str(Path(package["root"]) / "_internal/ui-v3")])
        self.assertEqual(kwargs["timeout"], 30); self.assertFalse(kwargs["shell"])
        self.assertNotIn("NODE_OPTIONS", kwargs["env"]); self.assertNotIn("NODE_PATH", kwargs["env"])
        self.assertEqual(json.loads(kwargs["input"])["expectedEngineHash"], "e" * 64)
        self.error("RULE_PROOF", n._rule_call, package, "constructor", {})


class ReviewTests(MockOnly):
    def test_full_receipt_hash_plan_identity_visible_operations_and_rules_are_bound(self):
        review = reviewed()
        targets = h.validate_request(envelope())["plans"]
        n.validate_review(review, targets, "e" * 64)
        for field, value in (("plan_id", "other"), ("version", 4), ("plan_digest", "f" * 64), ("operations", [[]])):
            changed = copy.deepcopy(review); changed[field] = value
            self.error("REVIEW_MISMATCH", n.validate_review, changed)
        self.error("RULES_MISMATCH", n.validate_review, review, targets, "f" * 64)
        changed = copy.deepcopy(review); changed["inputs"]["receipt"]["targets"][0]["plan"]["after"]["raw"] = "unreviewed"
        self.error("REVIEW_MISMATCH", n.validate_review, changed)

    def test_review_is_frozen_thread_local_one_use_even_on_failed_attempt(self):
        review = reviewed()
        with n.system_review(review):
            review["targets"][0]["after"]["raw"] = "changed"
            threads = []
            def other_thread():
                try:
                    n.consume_review(h.validate_request(envelope())["plans"], "e" * 64)
                except p.ElevationError as exc:
                    threads.append(exc.code)
            task = threading.Thread(target=other_thread); task.start(); task.join()
            self.assertEqual(threads, ["REVIEW_REQUIRED"])
            n.consume_review(h.validate_request(envelope())["plans"], "e" * 64)
            self.error("REVIEW_REQUIRED", n.consume_review, [], "e" * 64)
        self.error("REVIEW_REQUIRED", n.consume_review, [], "e" * 64)

    def test_same_rule_recalculation_compares_after_and_real_operations(self):
        review = reviewed()
        target = review["inputs"]["receipt"]["targets"][0]
        computed = copy.deepcopy(target["plan"])
        call = mock.Mock(return_value=computed)
        n.verify_rule_proofs(review, {"rules_sha256": "e" * 64}, rule_call=call)
        call.assert_called_once_with({"rules_sha256": "e" * 64}, "build", target["verification"]["options"])
        computed["after"]["raw"] = "wrong"
        self.error("RULE_PROOF", n.verify_rule_proofs, review, {}, rule_call=call)
        computed["after"]["raw"] = "new"; computed["operations"] = []
        self.error("RULE_PROOF", n.verify_rule_proofs, review, {}, rule_call=call)

    def test_sorting_requires_complete_indices_and_preserves_duplicate_empty_slices(self):
        review = reviewed([plan(after=";A;A")])
        target = review["inputs"]["receipt"]["targets"][0]
        computed = copy.deepcopy(target["plan"]); computed["after"]["raw"] = "A;;A"
        target["verification"]["orderIndices"] = [1, 0, 2]
        target["plan"]["operations"].append({"type": "move", "action": "MOVE", "order": ["opaque-b", "opaque-a", "opaque-c"]})
        n.verify_rule_proofs(review, {}, rule_call=lambda *_: computed)
        for order in ([0, 0, 2], [0, 1], [True, 0, 2], [0, 1, 3]):
            target["verification"]["orderIndices"] = order
            self.error("RULE_PROOF", n.verify_rule_proofs, review, {}, rule_call=lambda *_: computed)

    def test_whole_restore_checks_original_snapshot_and_complete_revision(self):
        review = reviewed([plan(before="new", after="old")])
        target = review["inputs"]["receipt"]["targets"][0]
        before = target["beforeSnapshot"]
        source = snapshot("A", "old"); source["revision"] = "older"
        target["verification"] = {"method": "restore", "origin": {"scope": "System", "name": "A", "before": source, "verifiedAfter": copy.deepcopy(before)},
                                  "desiredSnapshot": source, "requiresExplicitReview": True}
        target["plan"].update(mode="history-restore", operations=[{"type": "restore", "action": "RESTORE_HISTORY"}])
        n.verify_rule_proofs(review, {}, rule_call=mock.Mock(side_effect=AssertionError("No Node for explicit whole restore")))
        target["verification"]["origin"]["verifiedAfter"]["revision"] = "other-revision"
        self.error("RULE_PROOF", n.verify_rule_proofs, review, {})

    def test_absent_append_empty_order_is_noop_even_beside_a_changed_system_target(self):
        absent = plan("Empty", before=None, after=None)
        review = reviewed([plan(), absent])
        target = review["inputs"]["receipt"]["targets"][1]
        target["plan"]["operations"] = []
        target["verification"].update(orderIndices=[], registryType="REG_EXPAND_SZ")
        computed = {t["name"]: copy.deepcopy(t["plan"]) for t in review["inputs"]["receipt"]["targets"]}
        n.verify_rule_proofs(review, {}, rule_call=lambda pkg, method, options: computed[options["target"]["name"]])
        target["verification"]["orderIndices"] = [0]
        self.error("RULE_PROOF", n.verify_rule_proofs, review, {}, rule_call=lambda pkg, method, options: computed[options["target"]["name"]])

    def test_type_override_is_only_authorized_by_recomputed_manual_edit(self):
        review = reviewed()
        target = review["inputs"]["receipt"]["targets"][0]
        computed = copy.deepcopy(target["plan"])
        target["plan"]["after"]["type"] = "REG_EXPAND_SZ"
        target["verification"]["registryType"] = "REG_EXPAND_SZ"
        self.error("RULE_PROOF", n.verify_rule_proofs, review, {}, rule_call=lambda *_: computed)
        target["verification"].update(method="buildElementEdit", deleteVariable=False)
        target["plan"]["operations"].append({"type": "type-change", "action": "TYPE_CHANGE", "before": "REG_SZ", "after": "REG_EXPAND_SZ"})
        n.verify_rule_proofs(review, {}, rule_call=lambda *_: computed)
        target["plan"]["operations"].pop()
        self.error("RULE_PROOF", n.verify_rule_proofs, review, {}, rule_call=lambda *_: computed)


class MemoryStore:
    def __init__(self, sid=SID, job_id=JOB):
        self.sid, self.job_id, self.sequence, self.records = sid, job_id, 0, []
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def append(self, event, **data):
        self.sequence += 1
        record = copy.deepcopy({"event": event, "receiptSequence": self.sequence, "binding": getattr(self, "binding", {}), **data})
        self.records.append(record)
        return record


class AckPipe:
    def __init__(self, request, *, drop=None):
        self.request, self.last, self.sent, self.drop = request, None, [], drop
    def send(self, value):
        self.last = copy.deepcopy(value); self.sent.append(self.last)
    def receive(self):
        if self.last is None: return self.request
        value = self.last
        if value["kind"] == "phase":
            return {k: v for k, v in dict(value, kind="finish").items() if k != "result"}
        record = value["record"]
        if record["event"] == self.drop: raise OSError("Owner disconnected")
        command = {"preflight_verified": "commit_batch", "write_intent": "commit", "compensate_intent": "compensate"}.get(record["event"], "continue")
        return {"protocol": 2, "kind": command, "nonce": NONCE, "job_id": JOB, "digest": value["digest"], "receiptSequence": record["receiptSequence"]}


class ProtocolTests(MockOnly):
    def setUp(self):
        super().setUp()
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.env = FileEnvironment(Path(self.temporary.name) / "environment.json")
        self.env.write("System", "A", "old")
        self.request = h.validate_request(envelope())
        self.digest = hashlib.sha256(p._json_bytes(self.request)).hexdigest()
        self.package = {"package_digest": "d" * 64, "rules_sha256": "e" * 64}
        self.message = {"protocol": 2, "kind": "request", "nonce": NONCE, "job_id": JOB, "digest": self.digest,
                        "request": self.request, "review": reviewed(), "package_digest": "d" * 64}

    def serve(self, pipe, store):
        with mock.patch.object(n, "verify_rule_proofs"):
            return n.serve_frozen_request(pipe, type("Operator", (), {"sid": SID})(), NONCE, JOB, package=self.package,
                                         environment_factory=lambda: self.env, store_factory=lambda *_: store)

    def test_frozen_request_durable_events_readback_and_phase_are_one_sequence(self):
        store, pipe = MemoryStore(), AckPipe(self.message)
        result = self.serve(pipe, store)
        self.assertEqual(result["status"], "committed")
        self.assertEqual(self.env.read("System", "A")["raw"], "new")
        self.assertEqual([r["receiptSequence"] for r in store.records], list(range(1, len(store.records) + 1)))
        events = [r["event"] for r in store.records]
        self.assertLess(events.index("write_intent"), events.index("write_verified"))
        self.assertEqual(events[-2:], ["phase", "terminal"])
        self.assertEqual(store.records[-1]["result"], result)

    def test_disconnection_before_write_is_zero_write_and_after_write_never_rolls_back(self):
        for event, expected in (("preflight_verified", "old"), ("write_intent", "old"), ("write_verified", "new")):
            with self.subTest(event=event):
                self.env.write("System", "A", "old")
                store = MemoryStore()
                result = self.serve(AckPipe(self.message, drop=event), store)
                self.assertEqual(result["status"], "recovery_required")
                self.assertEqual(self.env.read("System", "A")["raw"], expected)
                self.assertNotIn("compensate_intent", [r["event"] for r in store.records])
                self.assertEqual(store.records[-1]["event"], "terminal")

    def test_invalid_nonce_digest_user_scope_or_package_never_constructs_environment(self):
        for key, value, code in (("nonce", "c" * 64, "IPC_BINDING"), ("digest", "f" * 64, "DIGEST_MISMATCH"), ("package_digest", "f" * 64, "PACKAGE_HASH")):
            message = dict(self.message, **{key: value})
            factory = mock.Mock(side_effect=AssertionError("Adapter construction forbidden"))
            self.error(code, n.serve_frozen_request, AckPipe(message), type("Operator", (), {"sid": SID})(), NONCE, JOB,
                       package=self.package, environment_factory=factory, store_factory=MemoryStore)
            factory.assert_not_called()

    def test_journal_failure_sends_no_ack_or_mutation(self):
        store = MemoryStore()
        store.append = mock.Mock(side_effect=OSError("Disk full"))
        pipe = AckPipe(self.message)
        journal = n.PipeJournal(store, pipe, self.digest, NONCE)
        with self.assertRaises(OSError): journal.append("write_intent")
        self.assertTrue(journal.failed); self.assertFalse(pipe.sent)

    def test_rule_rejection_has_durable_receipt_and_never_constructs_adapter(self):
        store, pipe = MemoryStore(), AckPipe(self.message)
        factory = mock.Mock(side_effect=AssertionError("No Registry on invalid rule proof"))
        with mock.patch.object(n, "verify_rule_proofs", side_effect=p.ElevationError("RULE_PROOF", "Recomputed after differs")):
            result = n.serve_frozen_request(pipe, type("Operator", (), {"sid": SID})(), NONCE, JOB,
                     package=self.package, environment_factory=factory, store_factory=lambda *_: store)
        factory.assert_not_called()
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["channel_state"], "connected")
        self.assertEqual(store.records[-1]["result"], result)
        self.assertNotIn("write_intent", [r["event"] for r in store.records])


class QueuePipe:
    def __init__(self, incoming, outgoing):
        self.incoming, self.outgoing = incoming, outgoing
    def send(self, value): self.outgoing.put(copy.deepcopy(value))
    def receive(self):
        result = self.incoming.get(timeout=5)
        if result is None: raise OSError("Peer disconnected")
        return result


class LiveSessionTests(MockOnly):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env = FileEnvironment(self.root / "environment.json")
        self.env.write("System", "A", "old")
        self.store, self.launches, self.helpers = MemoryStore(), [], []
        self.package = {"package_digest": "d" * 64, "rules_sha256": "e" * 64}
        directory = mock.patch.object(p, "_create_private_directory", side_effect=lambda path: path.mkdir())
        directory.start(); self.addCleanup(directory.stop)
        @contextmanager
        def locked(_): yield
        lock = mock.patch.object(p, "_locked_request", side_effect=locked)
        lock.start(); self.addCleanup(lock.stop)
        self.review = reviewed()
        computed = copy.deepcopy(self.review["inputs"]["receipt"]["targets"][0]["plan"])
        rules = mock.patch.object(n, "_rule_call", return_value=computed)
        self.rules = rules.start(); self.addCleanup(rules.stop)
        launch = mock.patch.object(p, "_launch_helper", side_effect=self.launch)
        launch.start(); self.addCleanup(launch.stop)

    def launch(self, path, digest, controller):
        self.launches.append(str(path))
        request = h.load_request(path, digest, allow_type_change=True)
        review = n.consume_review(request["plans"], self.package["rules_sha256"])
        left, right = queue.Queue(), queue.Queue()
        parent, helper = QueuePipe(left, right), QueuePipe(right, left)
        error = []
        def work():
            self.helpers.append(1)
            try:
                n.serve_frozen_request(helper, type("Operator", (), {"sid": SID})(), NONCE, request["job_id"],
                                      package=self.package, environment_factory=lambda: self.env,
                                      store_factory=lambda *_: self.store)
            except BaseException as exc:
                error.append(exc)
            finally:
                helper.send(None)
        thread = threading.Thread(target=work, daemon=True); thread.start()
        def verify_phase(message):
            self.assertEqual(self.store.records[-1]["result"], message["result"])
            p._verified_result(dict(request, restore=message["result"]["restore"]), digest, message["result"])
        terminal = n.drive_frozen_request(parent, request, digest, NONCE, review=review, package=self.package,
                                          controller=controller, verify_phase=verify_phase)
        thread.join(timeout=6)
        self.assertFalse(thread.is_alive()); self.assertFalse(error, error)
        result = terminal["result"]
        return p._PipeOutcome(result, self.store.records[-1], 0 if result["status"] in ("committed", "compensated") else 1)

    def create(self):
        with n.system_review(self.review):
            return p.prepare_system_batch(self.review["targets"], self.root / "jobs", job_id=JOB)

    def test_service_factory_freezes_full_envelope_and_captures_review_context(self):
        from envmanager_v3.service import Service
        service = object.__new__(Service)
        service.data_dir = self.root
        receipt = reviewed()["inputs"]["receipt"]
        session = service._system_session(receipt, "consumed-review-token", JOB)
        receipt["targets"][0]["plan"]["after"]["raw"] = "changed-after-factory"
        self.assertEqual(session.prepare()["status"], "prepared")
        self.assertEqual(session.commit()["status"], "committed")
        self.assertEqual(self.env.read("System", "A")["raw"], "new")
        session.finish()
        self.assertEqual(session.request["job_id"], JOB)
        self.assertEqual(len(self.launches), 1)

    def test_zero_write_prepare_user_before_system_and_same_helper_compensation(self):
        session = self.create()
        self.assertEqual(session.prepare()["status"], "prepared")
        self.assertEqual(self.env.read("System", "A")["raw"], "old")
        self.assertNotIn("write_intent", [r["event"] for r in self.store.records])
        self.env.write("User", "U", "ordinary-user-value")
        self.assertEqual(session.commit()["status"], "committed")
        self.assertEqual(self.env.read("System", "A")["raw"], "new")
        self.assertEqual(session.conditional_compensate()["status"], "compensated")
        self.assertEqual(self.env.read("System", "A")["raw"], "old")
        self.assertEqual(self.env.read("User", "U")["raw"], "ordinary-user-value")
        self.assertEqual(session.finish()["status"], "compensated")
        self.assertEqual(len(self.launches), 1); self.assertEqual(len(self.helpers), 1)
        self.assertEqual(self.store.records[-1]["event"], "terminal")
        self.error("IPC_REPLAY", session.commit)
        self.error("IPC_REPLAY", session.prepare)

    def test_user_failure_after_prepare_finishes_without_system_write(self):
        session = self.create()
        self.assertEqual(session.prepare()["status"], "prepared")
        result = session.finish()
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(self.env.read("System", "A")["raw"], "old")
        self.assertNotIn("write_intent", [r["event"] for r in self.store.records])
        self.assertEqual(len(self.launches), 1)

    def test_failed_prepare_zero_write_finishes_without_another_grant(self):
        self.env.write("System", "A", "external-before")
        session = self.create()
        self.assertEqual(session.prepare()["status"], "rejected")
        session.finish()
        self.assertEqual(self.env.read("System", "A")["raw"], "external-before")
        self.assertEqual(len(self.launches), 1)

    def test_conditional_compensation_preserves_external_value(self):
        session = self.create(); session.prepare(); session.commit()
        self.env.write("System", "A", "external-after")
        result = session.conditional_compensate()
        self.assertEqual(result["status"], "recovery_required")
        session.finish()
        self.assertEqual(self.env.read("System", "A")["raw"], "external-after")
        self.assertEqual(len(self.launches), 1)

    def test_observed_external_aba_revision_blocks_same_helper_compensation(self):
        session = self.create(); session.prepare(); committed = session.commit()
        original_revision = committed["targets"][0]["result"]["revision"]
        self.env.write("System", "A", "external")
        self.env.write("System", "A", "new")
        self.assertNotEqual(self.env.read("System", "A")["revision"], original_revision)
        result = session.conditional_compensate()
        self.assertEqual(result["status"], "recovery_required")
        self.assertEqual(result["channel_state"], "connected")
        self.assertNotEqual(session.state, "unknown")
        session.finish()
        self.assertEqual(self.env.read("System", "A")["raw"], "new")

    def test_manual_type_change_and_reverse_use_one_live_grant(self):
        target = self.review["inputs"]["receipt"]["targets"][0]
        target["verification"].update(method="buildElementEdit", registryType="REG_EXPAND_SZ", deleteVariable=False)
        target["plan"]["after"]["type"] = self.review["targets"][0]["after"]["type"] = "REG_EXPAND_SZ"
        target["plan"]["operations"].append({"type": "type-change", "action": "TYPE_CHANGE", "before": "REG_SZ", "after": "REG_EXPAND_SZ"})
        rehash(self.review)
        session = self.create(); self.assertEqual(session.prepare()["status"], "prepared")
        self.assertEqual(session.commit()["status"], "committed")
        self.assertEqual(self.env.read("System", "A")["type"], "REG_EXPAND_SZ")
        self.assertEqual(session.conditional_compensate()["status"], "compensated")
        session.finish()
        self.assertEqual(self.env.read("System", "A")["type"], "REG_SZ")
        self.assertEqual(len(self.launches), 1)

    def test_unknown_launch_or_cancel_is_never_retried(self):
        for code, status in (("UAC_CANCELLED", "cancelled"), ("HELPER_UNCERTAIN", "recovery_required")):
            with mock.patch.object(p, "_launch_helper", side_effect=p.ElevationError(code, "Injected")) as launch:
                with n.system_review(reviewed()):
                    session = p.prepare_system_batch([plan()], self.root / code)
                self.assertEqual(session.prepare()["status"], status)
                session.finish()
                launch.assert_called_once()
                self.error("IPC_REPLAY", session.commit)


class TypeChangeTests(MockOnly):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env = FileEnvironment(self.root / "environment.json")
        self.env.write("System", "A", "old", "REG_SZ")
        self.request = envelope()
        self.request["plans"][0]["after"]["type"] = "REG_EXPAND_SZ"
        self.counter = 0

    def run_helper(self, request=None, **kwargs):
        self.counter += 1
        return h.execute_helper_request(request or self.request, self.env,
                    journal_path=self.root / f"journal-{self.counter}.jsonl", approved_type_changes=True, **kwargs)

    def test_proof_gated_type_transition_journals_delete_absent_and_create(self):
        self.error("TYPE_MISMATCH", h.execute_helper_request, self.request, self.env, journal_path=self.root / "forbidden.jsonl")
        result = self.run_helper()
        self.assertEqual(result["status"], "committed")
        self.assertEqual(self.env.read("System", "A")["type"], "REG_EXPAND_SZ")
        self.assertFalse(result["targets"][0]["intermediate"]["exists"])
        events = [json.loads(line)["event"] for line in (self.root / "journal-1.jsonl").read_text().splitlines()]
        self.assertLess(events.index("type_delete_intent"), events.index("type_deleted"))
        self.assertLess(events.index("type_deleted"), events.index("type_create_intent"))
        restored = self.run_helper(dict(self.request, restore=True), source_snapshots=[result["targets"][0]["result"]])
        self.assertEqual(restored["status"], "compensated")
        self.assertEqual(self.env.read("System", "A")["type"], "REG_SZ")

    def test_failed_create_restores_original_from_exact_owned_absent(self):
        write = self.env.write
        def fail_create(scope, name, raw, kind=None, **kwargs):
            if kind == "REG_EXPAND_SZ": raise OSError("Injected create failure after delete")
            return write(scope, name, raw, kind, **kwargs)
        with mock.patch.object(self.env, "write", side_effect=fail_create):
            result = self.run_helper()
        self.assertEqual(result["status"], "compensated")
        current = self.env.read("System", "A")
        self.assertEqual((current["raw"], current["type"]), ("old", "REG_SZ"))

    def test_foreign_create_after_intermediate_delete_is_never_overwritten(self):
        write = self.env.write
        def external_create(scope, name, raw, kind=None, **kwargs):
            if kind == "REG_EXPAND_SZ":
                write(scope, name, "external", "REG_SZ")
                raise OSError("Create race")
            return write(scope, name, raw, kind, **kwargs)
        with mock.patch.object(self.env, "write", side_effect=external_create):
            result = self.run_helper()
        self.assertEqual(result["status"], "recovery_required")
        self.assertEqual(self.env.read("System", "A")["raw"], "external")

    def test_type_intermediate_disconnect_stops_without_recreation_or_compensation(self):
        store, pipe = MemoryStore(), AckPipe({}, drop="type_deleted")
        journal = n.PipeJournal(store, pipe, "c" * 64, NONCE)
        # AckPipe also recognizes the fixed per-mutation authorizations.
        receive = pipe.receive
        def acknowledge():
            if pipe.last and pipe.last.get("record", {}).get("event") in ("type_delete_intent", "type_create_intent"):
                r = pipe.last["record"]
                return {"protocol": 2, "kind": "compensate" if r.get("compensation") else "commit", "nonce": NONCE,
                        "job_id": JOB, "digest": "c" * 64, "receiptSequence": r["receiptSequence"]}
            return receive()
        with mock.patch.object(pipe, "receive", side_effect=acknowledge):
            result = self.run_helper(journal_factory=lambda _: journal)
        self.assertEqual(result["status"], "recovery_required")
        self.assertFalse(self.env.read("System", "A")["exists"])
        self.assertNotIn("compensate_intent", [r["event"] for r in store.records])


class SystemOnlyWatchTests(MockOnly):
    def test_helper_watches_only_fixed_system_location_and_closes_on_watch_failure(self):
        from envmanager_v3 import environment_events
        environment = mock.Mock()
        environment._reg, environment._lock = object(), object()
        environment._location.return_value = ("HKLM", "fixed-System-environment")
        listener = mock.Mock()
        listener.start.return_value = {"scopes": {"System": True}}
        with mock.patch.object(h, "WindowsEnvironment", return_value=environment), \
                mock.patch.object(environment_events, "RegistryChangeListener", return_value=listener) as create:
            self.assertIs(h._system_environment(), environment)
            environment._location.assert_called_once_with("System")
            self.assertEqual(create.call_args.args[1], {"System": ("HKLM", "fixed-System-environment")})
            self.assertIs(environment._changes, listener)
            listener.start.return_value = {"scopes": {"System": False}}
            self.error("SYSTEM_WATCH_FAILED", h._system_environment)
            environment.close.assert_called_once()


class OperationReceiptReaderTests(MockOnly):
    def setUp(self):
        super().setUp()
        self.review = reviewed()
        receipt = self.review["inputs"]["receipt"]
        self.operation = {"operationId": JOB, "planId": receipt["planId"], "version": receipt["version"],
                          "digest": receipt["digest"], "receipt": copy.deepcopy(receipt), "status": "recovery_required"}
        self.request = h.validate_request(envelope())
        self.digest = hashlib.sha256(p._json_bytes(self.request)).hexdigest()
        self.binding = {"operator_sid": SID, "plan_id": receipt["planId"], "version": receipt["version"],
                        "plan_digest": receipt["digest"], "rules_sha256": receipt["engineHash"],
                        "review_digest": hashlib.sha256(p._json_bytes(self.review)).hexdigest(), "package_digest": "d" * 64}
        target = copy.deepcopy(self.request["plans"][0])
        target.update(status="committed", result={**target["after"], "revision": "helper-epoch:2"})
        result = {"job_id": JOB, "digest": self.digest, "restore": False, "status": "committed", "targets": [target],
                  "errors": [], "review_digest": self.binding["review_digest"], "package_digest": "d" * 64}
        self.record = {"event": "terminal", "job_id": JOB, "digest": self.digest, "receiptSequence": 9,
                       "binding": self.binding, "result": result}
        self.win = mock.Mock()
        @contextmanager
        def process(pid):
            self.assertEqual(pid, os.getpid())
            yield "kernel-current-process-handle"
        self.win.process.side_effect = process
        self.win.identity.return_value = n.ProcessIdentity(os.getpid(), SID, 2, 123, "C:/ordinary/python.exe", "a" * 64, False, False)
        native = mock.patch.object(n, "Win32", return_value=self.win)
        native.start(); self.addCleanup(native.stop)
        reader = mock.patch.object(n, "read_protected_receipt", return_value=self.record)
        self.reader = reader.start(); self.addCleanup(reader.stop)
        for name in ("consume_review", "verify_rule_proofs", "require_production", "_rule_call"):
            guard = mock.patch.object(n, name, side_effect=AssertionError("Startup reader cannot authorize/launch/recompute"))
            guard.start(); self.addCleanup(guard.stop)

    def test_current_kernel_sid_original_request_digest_and_review_are_bound_without_replay(self):
        original = copy.deepcopy(self.operation)
        self.operation.update(operator_sid="S-1-5-21-evil", job_dir="C:/arbitrary", digest_from_helper="f" * 64)
        evidence = n.read_operation_receipt(self.operation)
        self.reader.assert_called_once_with(SID, JOB, self.digest, win=self.win)
        self.assertTrue(evidence["trusted"])
        self.assertEqual(evidence["status"], "recovery_required")
        self.assertEqual(evidence["operator_sid"], SID)
        self.assertEqual(evidence["errors"], [])
        self.assertEqual(self.operation["status"], original["status"])
        evidence["receipt"]["result"]["status"] = "changed-local-copy"
        self.assertEqual(self.record["result"]["status"], "committed")

    def test_missing_acl_or_invalid_binding_are_explicit_untrusted_error_evidence(self):
        for failure, code in ((FileNotFoundError("Missing"), "RECEIPT_MISSING"),
                              (p.ElevationError("SECURE_FILE", "Missing", {"winerror": 2}), "RECEIPT_MISSING"),
                              (p.ElevationError("UNSAFE_ACL", "Writable receipt"), "UNSAFE_ACL")):
            self.reader.side_effect = failure
            evidence = n.read_operation_receipt(self.operation)
            self.assertFalse(evidence["trusted"]); self.assertIsNone(evidence["receipt"])
            self.assertEqual(evidence["status"], "recovery_required")
            self.assertEqual(evidence["errors"][0]["code"], code)
        self.reader.side_effect = None
        for field, value in (("operator_sid", "S-1-5-21-other"), ("plan_id", "other-plan"), ("plan_digest", "f" * 64),
                             ("rules_sha256", "f" * 64), ("version", 4), ("review_digest", "bad")):
            changed = copy.deepcopy(self.record); changed["binding"][field] = value
            self.reader.return_value = changed
            evidence = n.read_operation_receipt(self.operation)
            self.assertFalse(evidence["trusted"], field)
            self.assertEqual(evidence["errors"][0]["code"], "RECEIPT_REVIEW_MISMATCH", field)

    def test_full_review_and_terminal_result_mismatch_do_not_become_trusted(self):
        for mutate in (lambda r: r["result"].update(review_digest="f" * 64),
                       lambda r: r["result"]["targets"][0].update(name="Other"),
                       lambda r: r.update(digest="f" * 64),
                       lambda r: r.pop("binding")):
            changed = copy.deepcopy(self.record); mutate(changed); self.reader.return_value = changed
            evidence = n.read_operation_receipt(self.operation)
            self.assertFalse(evidence["trusted"])
        changed = copy.deepcopy(self.record)
        changed.update(event="review_bound", review=copy.deepcopy(self.review)); changed.pop("result")
        self.reader.return_value = changed
        self.assertTrue(n.read_operation_receipt(self.operation)["trusted"])
        changed["review"]["review_token"] = "other-confirmation-token"
        self.assertFalse(n.read_operation_receipt(self.operation)["trusted"])

    def test_partial_progress_is_trusted_evidence_and_old_unbound_receipt_is_an_error(self):
        partial = {k: v for k, v in self.record.items() if k != "result"}
        partial.update(event="type_delete_intent", index=0)
        self.reader.return_value = partial
        evidence = n.read_operation_receipt(self.operation)
        self.assertTrue(evidence["trusted"]); self.assertEqual(evidence["status"], "recovery_required")
        partial.pop("binding")
        evidence = n.read_operation_receipt(self.operation)
        self.assertFalse(evidence["trusted"])
        self.assertEqual(evidence["errors"][0]["code"], "RECEIPT_BINDING_MISSING")

    def test_modified_original_receipt_or_non_nonce_id_never_reads_a_path(self):
        for mutate in (lambda op: op.update(operationId="../escape"),
                       lambda op: op.update(planId="other"),
                       lambda op: op["receipt"]["targets"][0]["plan"]["after"].update(raw="unreviewed")):
            changed = copy.deepcopy(self.operation); mutate(changed)
            evidence = n.read_operation_receipt(changed)
            self.assertFalse(evidence["trusted"])
        self.reader.assert_not_called()

    def test_explicit_manual_type_change_reconstructs_original_allow_type_change_digest(self):
        receipt = self.operation["receipt"]
        receipt["targets"][0]["plan"]["after"]["type"] = "REG_EXPAND_SZ"
        body = {k: v for k, v in receipt.items() if k != "digest"}
        receipt["digest"] = self.operation["digest"] = hashlib.sha256(p._json_bytes(body)).hexdigest()
        request = copy.deepcopy(self.request)
        request["plans"][0]["after"]["type"] = "REG_EXPAND_SZ"
        expected_digest = hashlib.sha256(p._json_bytes(h.validate_request(request, allow_type_change=True))).hexdigest()
        self.reader.side_effect = FileNotFoundError("Missing is still evidence")
        result = n.read_operation_receipt(self.operation)
        self.reader.assert_called_once_with(SID, JOB, expected_digest, win=self.win)
        self.assertEqual(result["errors"][0]["code"], "RECEIPT_MISSING")


@unittest.skipUnless(os.name == "nt" and os.environ.get("ENVMANAGER_V3_NATIVE_PIPE_TESTS") == "1", "Opt-in ordinary-user local pipe test")
class NativeOrdinaryPipeTests(unittest.TestCase):
    def test_real_pipe_cross_process_pid_framing_and_ordinary_token(self):
        win = n.Win32()
        with win.process(os.getpid()) as handle:
            identity = win.identity(handle)
        if identity.elevated:
            self.skipTest("Native smoke must run with an ordinary token")
        code = ("import os,sys; from envmanager_v3.native_ipc import NativePipe; "
                "p=NativePipe.connect(sys.argv[1]); value=p.receive(); "
                "p.send({'pid':os.getpid(),'length':len(value['data'])}); p.close()")
        with mock.patch.object(h, "WindowsEnvironment", side_effect=AssertionError("Registry forbidden")), \
                mock.patch.object(p, "_shell_execute", side_effect=AssertionError("UAC forbidden")), \
                n.NativePipe.create(identity.sid, win=win, timeout=5) as pipe:
            # A Windows venv python.exe can be a redirector that spawns a second
            # PID. Launch the actual base interpreter; preserve the strict OS
            # peer-PID == returned process PID assertion used by frozen releases.
            executable = getattr(sys, "_base_executable", sys.executable)
            process = subprocess.Popen([executable, "-B", "-c", code, pipe.name], creationflags=0x08000000,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                pipe.accept()
                self.assertEqual(pipe.peer_pid(), process.pid)
                pipe.send({"data": "x" * 150000})
                self.assertEqual(pipe.receive(), {"pid": process.pid, "length": 150000})
                out, err = process.communicate(timeout=8)
                self.assertEqual(process.returncode, 0, err.decode(errors="replace"))
            finally:
                if process.poll() is None: process.kill(); process.wait()


if __name__ == "__main__":
    unittest.main()
