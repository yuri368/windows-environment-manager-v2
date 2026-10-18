from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from envmanager_v3.service import Service, ServiceError


CONFIG = '''# custom configuration
[Tool]
default_option = "Default"
[Tool.Default]
PATH = { default_value = "A", scope = "User", mode = "+append", editable = true }
HOME = { default_value = ' C:\\tools\\home ', scope = "User", mode = "replace", editable = true }
'''


class ProductServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = Service(self.root)

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def imported(self):
        return self.service.import_workspace({"text": CONFIG, "filename": "../../not-a-path.toml"})

    def record(self, state, index=0):
        return state["apps"][0]["profiles"][0]["vars"][index]["recordId"]

    def execute(self, receipt):
        reviewed = self.service.review(receipt["planId"], {"version": receipt["version"], "digest": receipt["digest"]})
        self.assertIn("reviewToken", reviewed, reviewed)
        return self.service.execute(receipt["planId"], {**reviewed, "idempotencyKey": uuid.uuid4().hex})

    def test_empty_bootstrap_does_not_invent_apps_or_windows_values(self):
        state = self.service.bootstrap()
        self.assertEqual(state["apps"], [])
        self.assertEqual(state["snapshots"], [])
        self.assertEqual(state["history"], [])
        self.assertFalse(self.service.config.path.exists())

    def test_import_apply_history_and_reload_use_real_files(self):
        state = self.imported()
        record = self.record(state)
        receipt = self.service.plan({"kind": "apply", "recordIds": [record]})
        self.assertEqual(receipt["targets"][0]["plan"]["after"]["raw"], "A")
        self.assertEqual(receipt["targets"][0]["plan"]["after"]["type"], "REG_EXPAND_SZ")
        result = self.execute(receipt)
        self.assertEqual(result["status"], "committed", result)
        self.assertEqual(self.service.environment.read("User", "PATH")["raw"], "A")
        self.assertEqual(result["state"]["apps"][0]["active"], "Default")
        self.assertEqual(len(self.service.history({})["items"]), 1)
        self.service.close()
        self.service = Service(self.root)
        self.assertEqual(self.service.environment.read("User", "PATH")["raw"], "A")
        self.assertEqual(len(self.service.history({})["items"]), 1)
        self.assertEqual(self.record(self.service.bootstrap()), record)

    def test_review_updates_related_snapshot_without_writing(self):
        record = self.record(self.imported())
        receipt = self.service.plan({"kind": "apply", "recordIds": [record]})
        self.service.environment.write("User", "PATH", "external")
        result = self.service.review(receipt["planId"], {"version": 1, "digest": receipt["digest"]})
        self.assertEqual(result["status"], "preview_updated")
        self.assertEqual(result["plan"]["version"], 2)
        self.assertEqual(self.service.environment.read("User", "PATH")["raw"], "external")

    def test_unrelated_selection_does_not_invalidate_receipt(self):
        state = self.imported()
        receipt = self.service.plan({"kind": "apply", "recordIds": [self.record(state)]})
        self.service.update_selection(self.record(state, 1), {"source": "自定义值", "raw": "another"})
        review = self.service.review(receipt["planId"], {"version": 1, "digest": receipt["digest"]})
        self.assertIn("reviewToken", review)

    def test_server_captures_deletion_baseline_and_rejects_forged_execution(self):
        state = self.imported()
        self.service.environment.write("User", "PATH", "A;;X")
        record = self.record(state)
        self.service.update_selection(record, {"source": "自定义值", "raw": "B"})
        receipt = self.service.plan({"kind": "apply", "recordIds": [record]})
        self.assertEqual(receipt["targets"][0]["plan"]["after"]["raw"], "B;;X")
        with self.assertRaises(ServiceError):
            self.service.execute(receipt["planId"], {"after": "arbitrary"})
        self.assertEqual(self.service.environment.read("User", "PATH")["raw"], "A;;X")

    def test_manual_session_distinguishes_empty_from_delete_and_rejects_foreign_id(self):
        self.service.environment.write("User", "DATA", "A;A;")
        edit = self.service.open_edit({"scope": "User", "name": "DATA"})
        with self.assertRaises(ServiceError):
            self.service.update_edit(edit["sessionId"], {"rows": [{"id": "fake", "value": "X"}], "expectedRevision": 0})
        with self.assertRaises(ServiceError) as ambiguous:
            self.service.update_edit(edit["sessionId"], {"rows": [{"id": edit["rows"][0]["id"], "value": "X;Y"}], "expectedRevision": 0})
        self.assertEqual(ambiguous.exception.code, "AMBIGUOUS_ELEMENT")
        self.service.update_edit(edit["sessionId"], {"rows": [{"id": edit["rows"][2]["id"], "value": ""}], "expectedRevision": 0})
        receipt = self.service.plan({"kind": "manual", "editSessionId": edit["sessionId"]})
        self.assertTrue(receipt["targets"][0]["plan"]["after"]["exists"])
        self.assertEqual(receipt["targets"][0]["plan"]["after"]["raw"], "")
        self.assertEqual(self.execute(receipt)["status"], "committed")
        edit = self.service.open_edit({"scope": "User", "name": "DATA"})
        self.service.update_edit(edit["sessionId"], {"deleteVariable": True, "expectedRevision": 0})
        receipt = self.service.plan({"kind": "manual", "editSessionId": edit["sessionId"]})
        self.assertFalse(receipt["targets"][0]["plan"]["after"]["exists"])
        self.assertEqual(self.execute(receipt)["status"], "committed")
        self.assertFalse(self.service.environment.read("User", "DATA")["exists"])

    def test_order_session_requires_complete_server_ids_and_freezes_order(self):
        state = self.imported()
        apps = deepcopy(state["apps"])
        apps[0]["profiles"][0]["vars"][0]["mode"] = "append"
        self.service.save_draft(state["workspace"]["id"], {"apps": apps, "expectedRevision": state["workspace"]["revision"]})
        self.service.environment.write("User", "PATH", "X;Y")
        intent = {"kind": "apply", "recordIds": [self.record(state)]}
        receipt = self.service.plan(intent)
        self.assertEqual(receipt["status"], "ordering_required")
        order = self.service.open_order({"planId": receipt["planId"]})
        with self.assertRaises(ServiceError):
            self.service.update_order(order["sessionId"], {"order": ["fake"], "expectedRevision": 0})
        ids = [r["id"] for r in reversed(order["rows"])]
        self.service.update_order(order["sessionId"], {"order": ids, "expectedRevision": 0})
        receipt = self.service.plan({**intent, "orderSessionId": order["sessionId"]})
        expected = ";".join(r["value"] for r in reversed(order["rows"]))
        self.assertEqual(receipt["targets"][0]["plan"]["after"]["raw"], expected)
        self.assertEqual(self.execute(receipt)["status"], "committed")

    def test_history_restore_refuses_external_overwrite(self):
        state = self.imported()
        record = self.record(state, 1)
        receipt = self.service.plan({"kind": "apply", "recordIds": [record]})
        self.assertEqual(self.execute(receipt)["status"], "committed")
        history_id = self.service.history({})["items"][0]["id"]
        self.service.environment.write("User", "HOME", "external")
        with self.assertRaises(ServiceError):
            self.service.plan({"kind": "restore_history", "historyIds": [history_id]})
        self.assertEqual(self.service.environment.read("User", "HOME")["raw"], "external")

    def test_history_restore_rejects_observed_aba(self):
        record = self.record(self.imported(), 1)
        self.execute(self.service.plan({"kind": "apply", "recordIds": [record]}))
        history_id = self.service.history({})["items"][0]["id"]
        previous = self.service.environment.read("User", "HOME")
        self.service.environment.write("User", "HOME", "temporary")
        self.service.environment.read("User", "HOME")
        self.service.environment.write("User", "HOME", previous["raw"], type=previous["type"])
        with self.assertRaises(ServiceError) as caught:
            self.service.plan({"kind": "restore_history", "historyIds": [history_id]})
        self.assertEqual(caught.exception.code, "UNDO_CONFLICT")

    def test_contribution_undo_preserves_other_record_from_same_batch(self):
        config = '''[First]\ndefault_option="P"\n[First.P]\nPATH={default_value="A",scope="User",mode="+append",editable=true}\n[Second]\ndefault_option="P"\n[Second.P]\nPATH={default_value="B",scope="User",mode="+append",editable=true}\n'''
        state = self.service.import_workspace({"text": config})
        first = state["apps"][0]["profiles"][0]["vars"][0]["recordId"]
        second = state["apps"][1]["profiles"][0]["vars"][0]["recordId"]
        self.service.environment.write("User", "PATH", "X")
        result = self.execute(self.service.plan({"kind": "apply", "recordIds": [first, second]}))
        self.assertEqual(result["status"], "committed", result)
        self.assertEqual(self.service.environment.read("User", "PATH")["raw"], "B;A;X")
        receipt = self.service.plan({"kind": "undo", "recordIds": [first]})
        self.assertEqual(receipt["targets"][0]["plan"]["after"]["raw"], "B;X")
        self.assertEqual(self.execute(receipt)["status"], "committed")
        self.assertEqual(self.service.environment.read("User", "PATH")["raw"], "B;X")
        with self.assertRaises(ServiceError) as caught:
            self.service.plan({"kind": "undo", "recordIds": [first]})
        self.assertEqual(caught.exception.code, "NO_UNDO")
        receipt = self.service.plan({"kind": "undo", "recordIds": [second]})
        self.assertEqual(receipt["targets"][0]["plan"]["after"]["raw"], "X")
        self.assertEqual(self.execute(receipt)["status"], "committed")

    def test_contribution_undo_remains_blocked_after_restart(self):
        record = self.record(self.imported())
        self.service.environment.write("User", "PATH", "X")
        self.execute(self.service.plan({"kind": "apply", "recordIds": [record]}))
        self.service.close()
        self.service = Service(self.root)
        receipt = self.service.plan({"kind": "undo", "recordIds": [record]})
        self.assertEqual(receipt["status"], "blocked")
        self.assertEqual(self.service.environment.read("User", "PATH")["raw"], "A;X")

    def test_default_configuration_edit_changes_only_default_source(self):
        state = self.imported()
        raw = deepcopy(state["apps"])
        raw[0]["profiles"][0]["vars"][0]["defaultValue"] = "new default"
        next_state = self.service.save_draft(state["workspace"]["id"], {"apps": raw, "expectedRevision": state["workspace"]["revision"]})
        self.assertEqual(next_state["apps"][0]["profiles"][0]["vars"][0]["selectedRaw"], "new default")
        record = self.record(next_state)
        self.service.update_selection(record, {"source": "自定义值", "raw": "own input"})
        state = self.service.bootstrap()
        raw = deepcopy(state["apps"])
        raw[0]["profiles"][0]["vars"][0]["defaultValue"] = "newer default"
        state = self.service.save_draft(state["workspace"]["id"], {"apps": raw, "expectedRevision": state["workspace"]["revision"]})
        self.assertEqual(state["apps"][0]["profiles"][0]["vars"][0]["selectedRaw"], "own input")

    def test_rejected_combined_draft_does_not_partially_persist_configuration(self):
        state = self.imported()
        original = deepcopy(self.service.work.data)
        apps = deepcopy(state["apps"])
        apps[0]["profiles"][0]["vars"][0]["defaultValue"] = "must not partially persist"
        for extra in ({"uiDrafts": []}, {"profileChoices": []}, {"profileChoices": {"missing-app": "missing-profile"}}):
            with self.subTest(extra=extra), self.assertRaises(Exception):
                self.service.save_draft(state["workspace"]["id"], {
                    "apps": apps, "expectedRevision": state["workspace"]["revision"], **extra})
            self.assertEqual(self.service.work.data, original)
            self.assertEqual(self.service.database.get("workspace"), original)

    def test_invalid_configuration_is_durable_without_replacing_valid_execution_tree(self):
        state = self.imported()
        record = self.record(state)
        valid_revision = state["workspace"]["validRevision"]
        apps = deepcopy(state["apps"])
        apps[0]["profiles"][0]["vars"][0]["defaultValue"] = "invalid\0draft"
        state = self.service.save_draft(state["workspace"]["id"], {
            "apps": apps, "expectedRevision": state["workspace"]["revision"]})
        self.assertTrue(state["workspace"]["errors"])
        self.assertEqual(state["workspace"]["validRevision"], valid_revision)
        self.assertEqual(state["apps"][0]["profiles"][0]["vars"][0]["defaultValue"], "invalid\0draft")
        self.assertEqual(state["workingApps"][0]["profiles"][0]["vars"][0]["defaultValue"], "A")
        receipt = self.service.plan({"kind": "apply", "recordIds": [record]})
        self.assertEqual(receipt["targets"][0]["plan"]["after"]["raw"], "A")
        with self.assertRaises(ServiceError):
            self.service.export_workspace(state["workspace"]["id"])
        with self.assertRaises(ServiceError):
            self.service.save_workspace(state["workspace"]["id"], {
                "expectedRevision": state["workspace"]["revision"], "expectedDiskHash": state["workspace"]["fileRevision"]})
        self.service.close()
        self.service = Service(self.root)
        state = self.service.update_selection(record, {"source": "自定义值", "raw": "live input"})
        self.assertEqual(state["apps"][0]["profiles"][0]["vars"][0]["defaultValue"], "invalid\0draft")
        self.assertEqual(state["workingApps"][0]["profiles"][0]["vars"][0]["selectedRaw"], "live input")
        fixed = deepcopy(state["apps"])
        fixed[0]["profiles"][0]["vars"][0]["defaultValue"] = "new valid default"
        state = self.service.save_draft(state["workspace"]["id"], {
            "apps": fixed, "expectedRevision": state["workspace"]["revision"]})
        self.assertFalse(state["workspace"]["errors"])
        self.assertEqual(state["apps"], state["workingApps"])
        self.assertEqual(state["workingApps"][0]["profiles"][0]["vars"][0]["recordId"], record)
        self.assertEqual(state["workingApps"][0]["profiles"][0]["vars"][0]["selectedRaw"], "live input")
        self.assertEqual(state["configurationBaseline"][0]["profiles"][0]["vars"][0]["defaultValue"], "A")

    def test_unsaved_configuration_baseline_survives_restart_until_conditional_save(self):
        state = self.imported()
        apps = deepcopy(state["apps"])
        apps[0]["profiles"][0]["vars"][0]["defaultValue"] = "unsaved default"
        self.service.save_draft(state["workspace"]["id"], {"apps": apps, "expectedRevision": state["workspace"]["revision"]})
        self.service.close()
        self.service = Service(self.root)
        state = self.service.bootstrap()
        self.assertEqual(state["apps"][0]["profiles"][0]["vars"][0]["defaultValue"], "unsaved default")
        self.assertEqual(state["configurationBaseline"][0]["profiles"][0]["vars"][0]["defaultValue"], "A")
        state = self.service.save_workspace(state["workspace"]["id"], {
            "expectedRevision": state["workspace"]["revision"], "expectedDiskHash": state["workspace"]["fileRevision"]})
        self.assertEqual(state["configurationBaseline"][0]["profiles"][0]["vars"][0]["defaultValue"], "unsaved default")

    def test_file_save_records_bytes_backups_and_detects_external_changes(self):
        state = self.imported()
        raw = deepcopy(state["apps"])
        raw[0]["profiles"][0]["vars"][0]["defaultValue"] = "B"
        state = self.service.save_draft(state["workspace"]["id"], {"apps": raw, "expectedRevision": state["workspace"]["revision"]})
        old = self.service.config.path.read_bytes()
        saved = self.service.save_workspace(state["workspace"]["id"], {"expectedDiskHash": state["workspace"]["fileRevision"], "expectedRevision": state["workspace"]["revision"]})
        self.assertTrue(any(p.read_bytes() == old for p in (self.service.config.path.parent / "backups").iterdir()))
        self.assertIn('"B"', self.service.config.path.read_text())
        self.service.config.path.write_text("# outside\n", encoding="utf-8")
        from envmanager_v3.configuration import ConfigurationError
        with self.assertRaises(ConfigurationError):
            self.service.save_workspace(saved["workspace"]["id"], {"expectedDiskHash": saved["workspace"]["fileRevision"], "expectedRevision": saved["workspace"]["revision"]})
        self.assertEqual(self.service.config.path.read_text(), "# outside\n")

    def test_config_file_replaced_before_db_failure_reconciles_on_restart(self):
        state = self.imported()
        apps = deepcopy(state["apps"])
        apps[0]["profiles"][0]["vars"][0]["defaultValue"] = "saved after interruption"
        state = self.service.save_draft(state["workspace"]["id"], {
            "apps": apps, "expectedRevision": state["workspace"]["revision"]})
        original = self.service.database.set

        def fail_state_commit(key, value, **kwargs):
            if key == "workspace" and kwargs.get("connection") is not None:
                raise OSError("injected database commit boundary")
            return original(key, value, **kwargs)

        with patch.object(self.service.database, "set", side_effect=fail_state_commit):
            with self.assertRaises(OSError):
                self.service.save_workspace(state["workspace"]["id"], {
                    "expectedDiskHash": state["workspace"]["fileRevision"],
                    "expectedRevision": state["workspace"]["revision"]})
        self.assertIsNotNone(self.service.database.get("config_save_intent"))
        self.assertIn("saved after interruption", self.service.config.path.read_text())
        self.service.close()
        self.service = Service(self.root)
        restored = self.service.bootstrap()
        self.assertFalse(restored["workspace"]["fileConflict"])
        self.assertIsNone(self.service.database.get("config_save_intent"))
        self.assertEqual(restored["workspace"]["fileRevision"], self.service.config.load()["revision"])
        self.assertEqual(restored["apps"][0]["profiles"][0]["vars"][0]["defaultValue"], "saved after interruption")
        self.assertEqual(restored["history"], [])

    def test_empty_append_order_keeps_absence_without_fake_history(self):
        state = self.service.import_workspace({"text": '[Tool.P]\nEMPTY={default_value="",mode="append",scope="User"}\n'})
        record = self.record(state)
        intent = {"kind": "apply", "recordIds": [record]}
        receipt = self.service.plan(intent)
        order = self.service.open_order({"planId": receipt["planId"]})
        self.assertEqual(order["rows"], [])
        self.service.update_order(order["sessionId"], {"order": [], "expectedRevision": 0})
        receipt = self.service.plan({**intent, "orderSessionId": order["sessionId"]})
        self.assertEqual(receipt["targets"][0]["plan"]["after"], {"exists": False, "raw": None, "type": None})
        self.assertEqual(self.execute(receipt)["status"], "committed")
        self.assertFalse(self.service.environment.read("User", "EMPTY")["exists"])
        self.assertEqual(self.service.history({})["items"], [])


if __name__ == "__main__":
    unittest.main()
