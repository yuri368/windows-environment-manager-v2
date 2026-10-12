"""Compatibility deactivation uses live removal, never historical restoration.

Every registry write in these regressions targets an isolated MemoryStore.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from envmanager.config import parse_config
from envmanager.elements import CleanupSettings
from envmanager.models import ConflictError, RegistryState
from envmanager.persistence import StateRepository
from envmanager.removal import RemovalKind, RemovalRequest, RemovalScope
from envmanager.service import EnvironmentService
from envmanager.sharing import ConflictDecision
from envmanager.store import MemoryStore


def definition(value, mode="+append", special=False):
    return (f"{{default_value={value!r},scope='User',mode={mode!r},"
            f"editable=true,special={str(special).lower()}}}")


def configuration(*sections):
    return "\n".join(f"[{app}.{option}]\n" + "\n".join(
        f"{name}={value}" for name, value in variables.items())
        for app, option, variables in sections)


def existing(name, value, registry_type=2):
    return RegistryState(name, "User", True, value, registry_type)


class DeleteThenFailStore(MemoryStore):
    fail_deletion = False

    def delete(self, scope, name):
        super().delete(scope, name)
        if self.fail_deletion and name == "B":
            self.fail_deletion = False
            raise RuntimeError("deleted then failed")


class DeactivationCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repository = StateRepository(Path(self.temporary.name) / "state")

    def make(self, source, states=(), store_type=MemoryStore):
        store = store_type(states)
        service = EnvironmentService(parse_config(source), store, self.repository)
        self.assertTrue(service.execute(service.plan("Java", "Default")).success)
        return service, store

    def shared_path(self, scala_value="A", scala_mode="+append"):
        return configuration(
            ("Java", "Default", {"Path": definition("A")}),
            ("Scala", "Default", {"Path": definition("NOT_CURRENT")}),
            ("Scala", "Shared", {"PATH": definition(scala_value, scala_mode)}))

    def test_unactivated_scala_all_schemes_protect_matching_element(self):
        service, store = self.make(self.shared_path(), [existing("Path", "BASE")])
        self.assertNotIn("Scala", self.repository.snapshot()["active"])
        self.assertEqual(service.selected_option("Scala"), "Default")
        plan = service.plan_deactivate("Java")
        self.assertTrue(plan.can_execute)
        service.execute(plan)
        self.assertEqual(store.read("User", "Path").value, "A;BASE")
        self.assertEqual(plan.metadata["metrics"]["kept_shared_count"], 1)
        self.assertEqual(plan.metadata["shared_conflicts"][0]["consumers"][0]["option"], "Shared")
        self.assertIn("Java", self.repository.snapshot()["active"])

    def test_unactivated_partial_replace_consumer_also_protects_a(self):
        service, store = self.make(self.shared_path("A;B", "replace"),
                                   [existing("Path", "BASE")])
        plan = service.plan_deactivate("Java")
        self.assertTrue(service.execute(plan).success)
        self.assertEqual(store.read("User", "Path").value, "A;BASE")
        self.assertEqual(plan.metadata["metrics"]["kept_shared_count"], 1)

    def test_explicit_shared_decision_removes_only_selected_element(self):
        service, store = self.make(self.shared_path(), [existing("Path", "BASE")])
        draft = service.prepare_remove(RemovalRequest(
            RemovalScope.SCHEME, frozenset(("Java",)), {"Java": "Default"}))
        decisions = {draft.shared_conflicts[0].atom.atom_id: ConflictDecision.REMOVE}
        plan = service.plan_deactivate("Java", decisions=decisions)
        self.assertTrue(service.execute(plan).success)
        self.assertEqual(store.read("User", "Path").value, "BASE")
        self.assertNotIn("Java", self.repository.snapshot()["active"])
        self.assertTrue(any("Scala / Shared" in warning for warning in plan.warnings))

    def test_no_shared_dependency_removes_live_elements_without_restoring_snapshot(self):
        source = configuration(("Java", "Default", {"Path": definition("A")}))
        service, store = self.make(source, [existing("Path", " BASE ;; ")])
        plan = service.plan_deactivate("Java")
        self.assertEqual(plan.action, "deactivate")
        self.assertEqual(plan.changes[0].before.value, "A;BASE")
        self.assertEqual(plan.changes[0].after.value, "BASE")
        self.assertTrue(service.execute(plan).success)
        self.assertEqual(store.read("User", "Path").value, "BASE")
        self.assertEqual(store.read("User", "Path").registry_type, 2)

    def test_partial_replace_source_removes_a_and_preserves_external_c(self):
        source = configuration(("Java", "Default", {"Path": definition("A;B", "replace")}))
        service, store = self.make(source, [existing("Path", "BASE")])
        store.write(existing("Path", "A;C"))
        plan = service.plan_deactivate("Java")
        self.assertTrue(plan.can_execute, plan.conflicts)
        self.assertTrue(service.execute(plan).success)
        self.assertEqual(store.read("User", "Path").value, "C")

    def test_special_shared_value_is_kept_whole_and_can_explicitly_delete(self):
        source = configuration(
            ("Java", "Default", {"RAW": definition("  A;B  ", "replace", True)}),
            ("Scala", "Shared", {"RAW": definition("  A;B  ", "replace", True)}))
        service, store = self.make(source)
        plan = service.plan_deactivate("Java")
        self.assertTrue(service.execute(plan).success)
        self.assertEqual(store.read("User", "RAW").value, "  A;B  ")
        atom = plan.metadata["atoms"][0]
        self.assertEqual(atom["kind"], RemovalKind.WHOLE_KEY.value)
        self.assertTrue(service.execute(service.plan_deactivate(
            "Java", decisions={atom["atom_id"]: ConflictDecision.REMOVE})).success)
        self.assertFalse(store.read("User", "RAW").exists)

    def test_shared_empty_state_is_kept_and_can_explicitly_delete(self):
        source = configuration(
            ("Java", "Default", {"EMPTY": definition("", "replace")}),
            ("Scala", "Shared", {"EMPTY": definition("", "replace")}))
        service, store = self.make(source)
        plan = service.plan_deactivate("Java")
        self.assertTrue(service.execute(plan).success)
        self.assertTrue(store.read("User", "EMPTY").exists)
        self.assertEqual(store.read("User", "EMPTY").value, "")
        atom = plan.metadata["atoms"][0]
        self.assertEqual(atom["kind"], RemovalKind.EMPTY_VALUE.value)
        self.assertTrue(service.execute(service.plan_deactivate(
            "Java", decisions={atom["atom_id"]: ConflictDecision.REMOVE})).success)
        self.assertFalse(store.read("User", "EMPTY").exists)

    def test_matched_existing_empty_value_is_removed_despite_no_change_activation(self):
        source = configuration(("Java", "Default", {"EMPTY": definition("", "replace")}))
        service, store = self.make(source, [existing("EMPTY", "")])
        self.assertTrue(service.execute(service.plan_deactivate("Java")).success)
        self.assertFalse(store.read("User", "EMPTY").exists)

    def test_external_change_after_preview_refuses_all_writes(self):
        source = configuration(("Java", "Default", {"Path": definition("A")}))
        service, store = self.make(source, [existing("Path", "BASE")])
        plan = service.plan_deactivate("Java")
        store.write(existing("Path", "A;EXTERNAL"))
        writes = list(store.writes)
        history = service.history()
        with self.assertRaises(ConflictError):
            service.execute(plan)
        self.assertEqual(store.writes, writes)
        self.assertEqual(service.history(), history)
        self.assertEqual(store.read("User", "Path").value, "A;EXTERNAL")

    def test_selection_change_after_preview_invalidates_frozen_shared_decisions(self):
        service, store = self.make(self.shared_path(), [existing("Path", "BASE")])
        plan = service.plan_deactivate("Java")
        spec = service.config.variables("Scala", "Shared")[0]
        service.selections.edit(spec, "BASE")
        writes = list(store.writes)
        with self.assertRaises(ConflictError):
            service.execute(plan)
        self.assertEqual(store.writes, writes)
        self.assertEqual(store.read("User", "Path").value, "A;BASE")

    def test_recorded_active_scheme_is_removed_while_another_scheme_is_browsed(self):
        source = configuration(
            ("Java", "Default", {"Path": definition("A")}),
            ("Java", "Other", {"Path": definition("B")}))
        service, store = self.make(source, [existing("Path", "BASE")])
        service.select_option("Java", "Other")
        plan = service.plan_deactivate("Java")
        self.assertEqual(plan.option, "Default")
        self.assertEqual(service.selected_option("Java"), "Other")
        self.assertTrue(service.execute(plan).success)
        self.assertEqual(store.read("User", "Path").value, "BASE")

    def test_case_sensitive_elements_and_nonmatching_special_remain_untouched(self):
        source = configuration(("Java", "Default", {
            "Path": definition("A"), "RAW": definition(" A;B ", "replace", True)}))
        service, store = self.make(source)
        store.write(existing("Path", "a"))
        store.write(existing("RAW", "A;B"))
        plan = service.plan_deactivate("Java")
        self.assertTrue(service.execute(plan).success)
        self.assertEqual(store.read("User", "Path").value, "a")
        self.assertEqual(store.read("User", "RAW").value, "A;B")
        self.assertEqual(plan.metadata["atoms"], [])

    def test_disabled_cleanup_preserves_unmatched_raw_slices(self):
        source = configuration(("Java", "Default", {"Path": definition("B")}))
        service, store = self.make(source)
        store.write(existing("Path", " A ;B;; C "))
        service.set_cleanup_settings(CleanupSettings(False, False, False))
        self.assertTrue(service.execute(service.plan_deactivate("Java")).success)
        self.assertEqual(store.read("User", "Path").value, " A ;; C ")

    def test_failure_rolls_back_exact_current_state_and_preserves_activation(self):
        source = configuration(("Java", "Default", {
            "A": definition("x", "replace"), "B": definition("y", "replace")}))
        service, store = self.make(source, store_type=DeleteThenFailStore)
        store.write(existing("A", " x ;x;; "))
        store.write(existing("B", " y ;y;; "))
        original = [store.read("User", name) for name in ("A", "B")]
        active = self.repository.snapshot()["active"]
        store.fail_deletion = True
        result = service.execute(service.plan_deactivate("Java"))
        self.assertEqual(result.status, "rolled_back")
        self.assertEqual([store.read("User", name) for name in ("A", "B")], original)
        self.assertEqual(self.repository.snapshot()["active"], active)

    def test_history_restore_keeps_original_text_and_type(self):
        source = configuration(("Java", "Default", {"Path": definition("A")}))
        original = existing("Path", " BASE ;; ", 2)
        service, store = self.make(source, [original])
        applied = service.history()[0]
        self.assertTrue(service.execute(service.plan_restore(applied["id"])).success)
        self.assertEqual(store.read("User", "Path"), original)

    def test_missing_activation_stays_blocked(self):
        source = configuration(("Java", "Default", {"Path": definition("A")}))
        service = EnvironmentService(parse_config(source), MemoryStore(), self.repository)
        plan = service.plan_deactivate("Java")
        self.assertFalse(plan.can_execute)
        self.assertEqual(plan.changes, ())


if __name__ == "__main__":
    unittest.main()
