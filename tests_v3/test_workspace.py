"""Workspace contracts using temporary SQLite/config/FileEnvironment only."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from envmanager_v3.configuration import ConfigRepository
from envmanager_v3.database import Database
from envmanager_v3.environment import FileEnvironment
from envmanager_v3.planning import Planner
from envmanager_v3.workspace import Workspace, WorkspaceError


def variable(value="A", **overrides):
    return {"name": "Path", "scope": "User", "mode": "+append", "defaultValue": value,
            "choices": [{"label": "first", "value": "B"}, {"label": "second", "value": " C\r\n"}],
            **overrides}


def configuration():
    return [{"name": "Tool", "default": "One", "profiles": [
        {"name": "One", "vars": [variable()]}, {"name": "Two", "vars": [variable("C")]}]},
        {"name": "Other", "default": "One", "profiles": [{"name": "One", "vars": [variable("D")]}]}]


class WorkspaceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.planner = Planner()

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.db = Database(root / "state.sqlite3")
        self.addCleanup(self.db.close)
        self.environment = FileEnvironment(root / "environment.json")
        self.work = Workspace(self.db, ConfigRepository(root / "config.toml"), self.environment, self.planner)
        self.work.update_config(configuration())

    def record(self, app=0, profile=0):
        return self.work.data["apps"][app]["profiles"][profile]["vars"][0]

    def selection(self, app=0, profile=0):
        return self.work.data["selections"][self.record(app, profile)["recordId"]]

    def test_empty_workspace_identity_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = ConfigRepository(root / "empty.toml")
            with_database = Database(root / "empty.sqlite3")
            try:
                first = Workspace(with_database, config, FileEnvironment(root / "env.json"), self.planner)
                first_id = first.data["id"]
                self.assertEqual(first.data["apps"], [])
                self.assertEqual(with_database.get("workspace")["id"], first_id)
            finally:
                with_database.close()
            reopened = Database(root / "empty.sqlite3")
            try:
                second = Workspace(reopened, config, FileEnvironment(root / "env.json"), self.planner)
                self.assertEqual(second.data["id"], first_id)
                self.assertEqual(second.data["revision"], 0)
                self.assertEqual(second.data["apps"], [])
            finally:
                reopened.close()

    def test_scope_edit_preserves_uuid_input_and_ends_deletion_context(self):
        self.environment.write("User", "Path", "A;X")
        record_id = self.record()["recordId"]
        self.work.select(record_id, source="自定义值", raw=" B\r\n ")
        before = deepcopy(self.selection())
        self.assertIsNotNone(before["matchBaseline"])
        apps = self.work.projected_apps()
        apps[0]["profiles"][0]["vars"][0]["scope"] = "System"
        self.work.update_config(apps)
        self.assertEqual(self.record()["recordId"], record_id)
        self.assertEqual(self.selection()["selectedRaw"], before["selectedRaw"])
        self.assertEqual(self.selection()["custom"], before["custom"])
        self.assertIsNone(self.selection()["matchBaseline"])
        self.assertIsNone(self.selection()["removalState"])
        self.assertGreater(self.selection()["revision"], before["revision"])
        self.assertFalse(self.environment.read("System", "Path")["exists"])

    def test_default_source_scope_change_preserves_visible_input(self):
        apps = self.work.projected_apps()
        apps[0]["profiles"][0]["vars"][0].update(scope="System", defaultValue="new")
        self.work.update_config(apps)
        self.assertEqual(self.selection()["selectedRaw"], "A")
        self.assertEqual(self.selection()["source"], "自定义值")

    def test_idless_import_uses_semantic_paths_not_positions(self):
        ids = {(a["name"], p["name"], v["scope"], v["name"]): v["recordId"] for a, p, v in self.work.records()}
        incoming = configuration()[::-1]
        incoming[1]["profiles"].reverse()
        self.work.update_config(incoming)
        self.assertEqual({(a["name"], p["name"], v["scope"], v["name"]): v["recordId"]
                          for a, p, v in self.work.records()}, ids)

    def test_foreign_unknown_and_duplicate_ids_reject_atomically(self):
        original = deepcopy(self.work.data)
        for kind in ("app", "profile", "variable", "unknown", "duplicate"):
            with self.subTest(kind=kind):
                apps = self.work.projected_apps()
                apps[0]["profiles"][0]["vars"][0]["defaultValue"] = "must not leak"
                if kind == "app":
                    apps[1]["recordId"] = apps[0]["recordId"]
                elif kind == "profile":
                    apps[1]["profiles"][0]["recordId"] = apps[0]["profiles"][0]["recordId"]
                elif kind == "variable":
                    apps[0]["profiles"][1]["vars"][0]["recordId"] = self.record()["recordId"]
                elif kind == "unknown":
                    apps[1]["profiles"][0]["vars"][0]["recordId"] = "client-invented"
                else:
                    apps[0]["profiles"][0]["vars"].append(deepcopy(apps[0]["profiles"][0]["vars"][0]))
                with self.assertRaises(WorkspaceError) as error:
                    self.work.update_config(apps)
                self.assertEqual(error.exception.code, "INVALID_RECORD_ID")
                self.assertEqual(self.work.data, original)
                self.assertEqual(self.db.get("workspace"), original)

    def test_candidate_reorder_preserves_exact_value_and_updates_source(self):
        self.work.select(self.record()["recordId"], source="候选值 2")
        apps = self.work.projected_apps()
        apps[0]["profiles"][0]["vars"][0]["choices"].reverse()
        self.work.update_config(apps)
        self.assertEqual(self.selection()["source"], "候选值 1")
        self.assertEqual(self.selection()["selectedRaw"], " C\r\n")
        self.assertIsNone(self.selection()["matchBaseline"])

    def test_removed_candidate_falls_back_instead_of_adopting_old_index(self):
        self.work.select(self.record()["recordId"], source="候选值 1")
        apps = self.work.projected_apps()
        apps[0]["profiles"][0]["vars"][0]["choices"].pop(0)
        self.work.update_config(apps)
        self.assertEqual(self.selection()["source"], "默认值")
        self.assertEqual(self.selection()["selectedRaw"], "A")
        self.assertIsNone(self.selection()["matchBaseline"])

    def test_default_edit_changes_default_source_only(self):
        apps = self.work.projected_apps()
        apps[0]["profiles"][0]["vars"][0]["defaultValue"] = "new default"
        self.work.update_config(apps)
        self.assertEqual(self.selection()["selectedRaw"], "new default")
        self.work.select(self.record()["recordId"], source="自定义值", raw="own input")
        apps = self.work.projected_apps()
        apps[0]["profiles"][0]["vars"][0]["defaultValue"] = "next default"
        self.work.update_config(apps)
        self.assertEqual(self.selection()["selectedRaw"], "own input")

    def test_omitted_default_properties_keep_identity_and_input(self):
        apps = self.work.projected_apps()
        original = apps[0]["profiles"][0]["vars"][0]
        original.update(mode="replace", special=False, editable=True, choices=[])
        self.work.update_config(apps)
        self.work.select(self.record()["recordId"], source="自定义值", raw="input")
        before = deepcopy(self.work.data)
        apps = self.work.projected_apps()
        for field in ("scope", "mode", "special", "editable", "choices"):
            apps[0]["profiles"][0]["vars"][0].pop(field)
        self.work.update_config(apps)
        self.assertEqual(self.work.data, before)

    def test_protected_matches_all_profiles_with_js_whitespace_rules(self):
        self.environment.write("User", "Path", "A; C ;\ufeffD\ufeff;Z;;")
        # Non-current Tool.Two must protect C, and JS trim must match BOM-wrapped D.
        self.assertEqual(self.work.protected("User", "PATH", [self.record()["recordId"]]),
                         [" C ", "\ufeffD\ufeff"])
        self.assertEqual(self.work.protected("System", "Path", []), [])

    def test_protected_only_returns_matched_parts_of_multi_value_declaration(self):
        apps = self.work.projected_apps()
        apps[0]["profiles"][1]["vars"][0]["defaultValue"] = "C;missing"
        self.work.update_config(apps)
        snapshot = self.environment.write("User", "Path", " C ;X")
        self.assertEqual(self.work.protected("User", "Path", [], snapshot=snapshot), [" C "])

    def test_contributions_protect_mixed_dependencies_and_filter_inactive(self):
        self.environment.write("User", "Path", "X; Y ;Z")
        def contribution(ident, raw, owners, **overrides):
            return {"contributionId": ident, "scope": "User", "name": "PATH", "recordIds": owners,
                    "selectedRaw": raw, "baseline": None, "removalState": None, "active": True, **overrides}
        self.db.set("historyContributions", [
            contribution("mixed", "X", ["r1", "r2"]), contribution("single", "Y", ["r1"]),
            contribution("inactive", "Z", ["r2"], active=False),
            contribution("missing", "missing", ["r2"]), contribution("other-scope", "Z", ["r2"], scope="System")])
        self.assertEqual(self.work.protected("User", "Path", ["r1"]), ["X"])
        self.assertEqual(self.work.protected("User", "Path", ["r1", "r2"]), [])
        self.assertEqual(self.work.protected("User", "Path", []), ["X", " Y "])

    def test_special_protection_requires_whole_exact_match(self):
        apps = self.work.projected_apps()
        for app in apps:
            for profile in app["profiles"]:
                profile["vars"][0].update(special=True, mode="replace", defaultValue="A;B")
        self.work.update_config(apps)
        for _, _, record in self.work.records():
            self.work.select(record["recordId"], source="默认值")
        self.environment.write("User", "Path", "A;B;C")
        self.assertEqual(self.work.protected("User", "Path", []), [])
        self.environment.write("User", "Path", "A;B")
        self.assertEqual(self.work.protected("User", "Path", []), ["A;B"])

    def test_contribution_protection_uses_remaining_owners_after_withdrawal(self):
        self.environment.write("User", "Path", "X")
        contribution = {"contributionId": "shared", "scope": "User", "name": "PATH",
                        "recordIds": ["r1", "r2", "r3"], "withdrawnRecordIds": ["r1"],
                        "selectedRaw": "X", "baseline": None, "removalState": None, "active": True}
        self.db.set("historyContributions", [contribution])
        self.assertEqual(self.work.protected("User", "Path", ["r2", "r3"]), [])
        self.assertEqual(self.work.protected("User", "Path", ["r1", "r2"]), ["X"])
        self.assertEqual(self.work.protected("User", "Path", []), ["X"])

    def test_fully_withdrawn_contribution_has_no_protection_even_if_active(self):
        self.environment.write("User", "Path", "X")
        self.db.set("historyContributions", [{"contributionId": "withdrawn", "scope": "User", "name": "Path",
            "recordIds": ["r1", "r2"], "withdrawnRecordIds": ["r1", "r2"], "selectedRaw": "X",
            "baseline": None, "removalState": None, "active": True}])
        self.assertEqual(self.work.protected("User", "Path", []), [])

    def test_known_write_uses_opaque_adapter_for_all_same_target_sessions(self):
        self.environment.write("User", "Path", "A;C;X")
        for profile in (0, 1):
            self.work.select(self.record(profile=profile)["recordId"], source="自定义值", raw="pending")
        before = self.environment.read("User", "Path")
        plan = self.planner.call("build", {"target": {"scope": "User", "name": "Path"},
            "snapshot": before, "mode": "+append", "selectedRaw": "Y"})
        after = self.environment.write("User", "Path", plan["after"]["raw"], before["type"], expected=before)
        self.work.advance_known_write(plan, after)
        for profile in (0, 1):
            state = self.selection(profile=profile)["removalState"]
            self.assertTrue(state["entries"][0]["eligible"])
            self.assertEqual(state["entries"][0]["currentIndex"], profile + 1)
            self.assertEqual(state["snapshot"]["revision"], after["revision"])

    def test_known_write_after_unknown_gap_revokes_instead_of_trusting_gap(self):
        self.environment.write("User", "Path", "A;X")
        self.work.select(self.record()["recordId"], source="自定义值", raw="pending")
        self.environment.write("User", "Path", "A;external;X")
        before = self.environment.read("User", "Path")
        plan = self.planner.call("build", {"target": {"scope": "User", "name": "Path"},
            "snapshot": before, "mode": "+append", "selectedRaw": "Y"})
        after = self.environment.write("User", "Path", plan["after"]["raw"], before["type"], expected=before)
        self.work.advance_known_write(plan, after)
        state = self.selection()["removalState"]
        self.assertFalse(state["entries"][0]["eligible"])
        self.assertEqual(state["entries"][0]["reason"], "continuity")
        self.assertEqual(state["snapshot"]["revision"], after["revision"])


if __name__ == "__main__":
    unittest.main()
