from copy import deepcopy
from pathlib import Path
import shutil
import tempfile
import unittest

from envmanager_v3.planning import Planner, PlanningError


class PlannerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = Planner()

    def options(self, raw="A;; A;B", selected="C", mode="+append"):
        return {"target": {"scope": "User", "name": "Path"}, "mode": mode,
                "snapshot": {"exists": True, "raw": raw, "type": "REG_EXPAND_SZ", "revision": 3},
                "selectedRaw": selected}

    def test_server_uses_raw_preserving_existing_engine(self):
        plan = self.engine.call("build", self.options())
        self.assertEqual(plan["before"]["raw"], "A;; A;B")
        self.assertEqual(plan["after"]["raw"], "C;A;; A;B")
        self.assertEqual(plan["baseRevision"], 3)
        self.assertEqual(plan["after"]["type"], "REG_EXPAND_SZ")

    def test_explicit_empty_replacement_stays_existing_empty(self):
        plan = self.engine.call("build", self.options(selected="", mode="replace"))
        self.assertTrue(plan["after"]["exists"])
        self.assertEqual(plan["after"]["raw"], "")

    def test_unique_baseline_removal_keeps_unrelated_raw_slices(self):
        options = self.options(raw="A;;X", selected="A")
        baseline = self.engine.call("captureBaseline", options)
        state = self.engine.call("createRemovalState", args=[baseline])
        options.update(selectedRaw="B", baseline=baseline, removalState=state)
        plan = self.engine.call("build", options)
        self.assertEqual(plan["after"]["raw"], "B;;X")
        self.assertTrue(any(op["type"] == "remove" for op in plan["operations"]))

    def test_duplicate_actual_never_becomes_a_unique_removal(self):
        options = self.options(raw="A;A;X", selected="A")
        baseline = self.engine.call("captureBaseline", options)
        options.update(selectedRaw="B", baseline=baseline,
                       removalState=self.engine.call("createRemovalState", args=[baseline]))
        plan = self.engine.call("build", options)
        self.assertIn("A;A;X", plan["after"]["raw"])
        self.assertTrue(any(issue["reason"] == "duplicate" for issue in plan["issues"]))

    def test_special_semicolon_is_one_whole_value(self):
        options = self.options(selected='cmd /c "echo A;B"', mode="replace")
        options["whole"] = True
        plan = self.engine.call("build", options)
        self.assertEqual(plan["after"]["raw"], options["selectedRaw"])
        self.assertTrue(plan["special"])

    def test_no_arbitrary_operation_or_code_execution(self):
        with self.assertRaises(PlanningError):
            self.engine.call("constructor", {"code": "process.exit(0)"})

    def test_resource_change_requires_restart_and_binds_all_rule_files(self):
        with tempfile.TemporaryDirectory() as directory:
            assets = Path(directory)
            for name in ("demo-plan.js", "config-format.js"):
                shutil.copyfile(self.engine.assets / name, assets / name)
            planner = Planner(assets)
            self.assertEqual(planner.engine_hash, self.engine.engine_hash)
            with (assets / "config-format.js").open("a", encoding="utf-8") as stream:
                stream.write("\n// changed resource version\n")
            with self.assertRaises(PlanningError):
                planner.call("build", self.options())
            restarted = Planner(assets)
            self.assertNotEqual(restarted.engine_hash, planner.engine_hash)
            self.assertTrue(restarted.call("build", self.options())["valid"])

    def trusted_transition(self):
        options = self.options(raw="A;;X", selected="A")
        options["snapshot"]["revision"] = "epoch:7:before"
        baseline = self.engine.call("captureBaseline", options)
        state = self.engine.call("createRemovalState", args=[baseline])
        audit = {"before": "A;;X", "after": "Y;A;;X", "whole": False, "elements": [
            {"elementId": "new", "beforeIndex": None, "afterIndex": 0, "after": "Y",
             "added": True, "removed": False, "replaced": False, "moved": False},
            *[{"elementId": f"o:{i}", "beforeIndex": i, "afterIndex": i + 1, "before": value, "after": value,
               "added": False, "removed": False, "replaced": False, "moved": False}
              for i, value in enumerate(["A", "", "X"])]]}
        plan = self.engine.call("buildElementEdit", {"target": options["target"], "snapshot": options["snapshot"],
                                                     "afterRaw": audit["after"], "audit": audit})
        snapshot = {**plan["after"], "revision": "epoch:19:after", "scope": "User", "name": "PATH"}
        return {"baseline": baseline, "state": state, "plan": plan, "snapshot": snapshot}

    def test_trusted_opaque_audit_preserves_unchanged_original_and_real_revisions(self):
        options = self.trusted_transition()
        original = deepcopy(options)
        advanced = self.engine.advance_trusted_removal_state(**options)
        self.assertEqual(options, original)
        self.assertTrue(advanced["entries"][0]["eligible"])
        self.assertEqual(advanced["entries"][0]["currentIndex"], 1)
        self.assertEqual(advanced["snapshot"]["revision"], options["snapshot"]["revision"])
        self.assertEqual(advanced["baselineSnapshot"], options["state"]["baselineSnapshot"])
        plan = self.engine.call("build", {"target": options["plan"]["targetKey"], "snapshot": options["snapshot"],
            "mode": "+append", "selectedRaw": "B", "baseline": options["baseline"], "removalState": advanced})
        self.assertEqual(plan["after"]["raw"], "B;Y;;X")
        self.assertTrue(any(op["type"] == "remove" for op in plan["operations"]))

    def test_trusted_advance_rejects_invalid_complete_before_and_readback(self):
        original = self.trusted_transition()
        for field, value in (("raw", "wrong"), ("type", "REG_SZ"), ("exists", False), ("baseRevision", "other"),
                             ("missing-type", None), ("before-revision", "other"), ("after-revision", "epoch:7:before"),
                             ("after-raw", "wrong"), ("target", "System")):
            with self.subTest(field=field):
                options = deepcopy(original)
                if field == "baseRevision":
                    options["plan"][field] = value
                elif field == "missing-type":
                    options["plan"]["before"].pop("type")
                elif field == "before-revision":
                    options["plan"]["before"]["revision"] = value
                elif field.startswith("after-"):
                    options["snapshot"][field[6:]] = value
                elif field == "target":
                    options["plan"]["targetKey"]["scope"] = value
                else:
                    options["plan"]["before"][field] = value
                with self.assertRaises(PlanningError):
                    self.engine.call("advanceTrustedRemovalState", options)

    def test_trusted_advance_keeps_complete_audit_validation(self):
        options = self.trusted_transition()
        # Missing an untouched empty slice means no trusted complete mapping.
        options["plan"]["audit"]["elements"].pop(2)
        advanced = self.engine.call("advanceTrustedRemovalState", options)
        self.assertFalse(advanced["entries"][0]["eligible"])
        self.assertEqual(advanced["entries"][0]["reason"], "continuity")

    def test_unknown_opaque_refresh_revokes_and_cannot_become_trusted_later(self):
        options = self.trusted_transition()
        revoked = self.engine.call("syncRemovalState", {
            "baseline": options["baseline"], "state": options["state"], "snapshot": options["snapshot"]})
        self.assertFalse(revoked["entries"][0]["eligible"])
        plan = self.engine.call("build", {"target": options["plan"]["targetKey"], "snapshot": options["snapshot"],
                                         "mode": "+append", "selectedRaw": "Z"})
        advanced = self.engine.call("advanceTrustedRemovalState", {
            "baseline": options["baseline"], "state": revoked, "plan": plan,
            "snapshot": {**plan["after"], "revision": "epoch:20:next"}})
        self.assertFalse(advanced["entries"][0]["eligible"])

    def test_sequential_trusted_writes_keep_original_baseline_revision(self):
        options = self.trusted_transition()
        first = self.engine.call("advanceTrustedRemovalState", options)
        plan = self.engine.call("build", {"target": options["plan"]["targetKey"], "snapshot": options["snapshot"],
                                         "mode": "+append", "selectedRaw": "Z"})
        second = self.engine.call("advanceTrustedRemovalState", {
            "baseline": options["baseline"], "state": first, "plan": plan,
            "snapshot": {**plan["after"], "revision": "epoch:21:next"}})
        self.assertTrue(second["entries"][0]["eligible"])
        self.assertEqual(second["entries"][0]["currentIndex"], 2)
        self.assertEqual(second["baselineSnapshot"], options["state"]["baselineSnapshot"])

    def test_exact_trusted_noop_retains_opaque_snapshot(self):
        options = self.trusted_transition()
        snapshot = options["baseline"]["snapshot"]
        plan = self.engine.call("build", {"target": options["plan"]["targetKey"], "snapshot": snapshot,
                                         "mode": "+append", "selectedRaw": "A"})
        state = self.engine.call("advanceTrustedRemovalState", {
            "baseline": options["baseline"], "state": options["state"], "plan": plan, "snapshot": snapshot})
        self.assertEqual(state, options["state"])


if __name__ == "__main__":
    unittest.main()
