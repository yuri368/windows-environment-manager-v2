from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from envmanager.matching import SchemeStatus
from envmanager.elements import CleanupSettings
from envmanager.models import ConflictError, PermissionDenied, RegistryState, ValidationError
from envmanager.persistence import StateRepository
from envmanager.removal import RemovalRequest, RemovalScope
from envmanager.service import EnvironmentService
from envmanager.sharing import ConflictDecision
from envmanager.store import MemoryStore
from tests.test_detection_service import config, raw


class MutateThenFailDeleteStore(MemoryStore):
    def delete(self, scope, name):
        super().delete(scope, name)
        if name == "LATE" and getattr(self, "enabled", True):
            self.enabled = False
            raise OSError("delete completed before injected failure")


class RemovalServiceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = StateRepository(Path(temp.name))

    def make(self, configuration, states=(), store_type=MemoryStore):
        store = store_type(states)
        return EnvironmentService(configuration, store, self.repo), store

    def request(self, scope=RemovalScope.SCHEME, apps=("App",), options=None, keys=()):
        return RemovalRequest(scope, frozenset(apps), options or {}, keys)

    def remove(self, service, request, decisions=None):
        draft = service.prepare_remove(request)
        return service.execute(service.finalize_remove(draft.id, decisions))

    def test_manually_existing_complete_scheme_removed_without_prior_history(self):
        configuration = config(("App", "Default", [("HOME", "home", "replace", "User"),
                                                      ("Path", "X;Y", "+append", "User")]))
        service, store = self.make(configuration, [raw("HOME", "home", kind=2),
                                                   raw("Path", "BASE;X;Y;X;KEEP", kind=2)])
        original_preferences = deepcopy(self.repo.snapshot())
        draft = service.prepare_remove(self.request())
        self.assertEqual(len(draft.candidates.atoms), 3)
        self.assertEqual(store.writes, [])
        self.assertEqual(self.repo.history(), [])
        plan = service.finalize_remove(draft.id)
        self.assertEqual(store.writes, [])
        result = service.execute(plan)
        self.assertTrue(result.success)
        self.assertEqual(result.changed_count, 2)
        self.assertEqual(result.removed_element_count, 4)
        self.assertFalse(store.read("User", "HOME").exists)
        self.assertEqual(store.read("User", "Path").value, "BASE;KEEP")
        self.assertEqual(store.read("User", "Path").registry_type, 2)
        for key in ("last_values", "successful_applications", "active", "orders"):
            self.assertEqual(self.repo.snapshot()[key], original_preferences[key])
        self.assertEqual(service.detection_snapshot().schemes[("App", "Default")].status,
                         SchemeStatus.PARTIAL)
        logged = self.repo.operation(result.operation_id)
        self.assertEqual(logged["action"], "remove")
        self.assertEqual(logged["metadata"]["request"]["scope"], "scheme")
        self.assertEqual(logged["metadata"]["metrics"]["removed_element_count"], 4)

    def test_partial_match_skips_wrong_replace_and_missing_element(self):
        configuration = config(("App", "Default", [("HOME", "intended", "replace", "User"),
                                                      ("Path", "X;MISSING", "append", "User")]))
        service, store = self.make(configuration, [raw("HOME", "external"), raw("Path", "X;BASE")])
        result = self.remove(service, self.request())
        self.assertEqual(result.skipped_count, 2)
        self.assertEqual(store.read("User", "HOME").value, "external")
        self.assertEqual(store.read("User", "Path").value, "BASE")
        self.assertEqual([state.name for state in store.writes], ["Path"])

    def test_five_ranges_expand_into_correct_sources(self):
        configuration = config(("App", "One", [("A", "a", "replace", "User"),
                                                ("B", "b", "replace", "User")]),
                               ("App", "Two", [("C", "c", "replace", "User")]),
                               ("Other", "One", [("D", "d", "replace", "User")]),
                               ("Other", "Two", [("E", "e", "replace", "User")]))
        service, _ = self.make(configuration, [raw(name, name.lower()) for name in "ABCDE"])
        first = configuration.variables("App", "One")[0]
        requests = (
            (self.request(RemovalScope.VARIABLE, keys=(first.key,)), {first.key}),
            (self.request(RemovalScope.SCHEME), {spec.key for spec in configuration.variables("App", "One")}),
            (self.request(RemovalScope.APPLICATION_ALL), {spec.key for option in configuration.options("App")
                                                         for spec in configuration.variables("App", option)}),
            (self.request(RemovalScope.BATCH_CURRENT, ("App", "Other")),
             {spec.key for app in configuration.apps() for spec in configuration.variables(app, "One")}),
            (self.request(RemovalScope.BATCH_ALL, ("App", "Other")),
             {spec.key for app in configuration.apps() for option in configuration.options(app)
              for spec in configuration.variables(app, option)}))
        for request, expected in requests:
            with self.subTest(scope=request.scope):
                draft = service.prepare_remove(request)
                self.assertEqual({source.spec_key for source in draft.candidates.requested_sources}, expected)
        self.assertEqual(self.repo.history(), [])

    def test_selected_other_option_not_protected_and_unselected_all_options_are(self):
        configuration = config(("App", "Current", [("Path", "A;B;C", "+append", "User")]),
                               ("App", "Other", [("Path", "C", "+append", "User")]),
                               ("Protected", "Current", [("Path", "A", "+append", "User")]),
                               ("Protected", "Other", [("Path", "B", "+append", "User")]))
        service, store = self.make(configuration, [raw("Path", "BASE;A;B;C")])
        draft = service.prepare_remove(self.request(RemovalScope.BATCH_CURRENT))
        self.assertEqual([atom.display_value for atom in draft.ordinary_atoms], ["C"])
        self.assertEqual([conflict.atom.display_value for conflict in draft.shared_conflicts], ["A", "B"])
        self.assertEqual({consumer.option for conflict in draft.shared_conflicts for consumer in conflict.consumers},
                         {"Current", "Other"})
        decisions = {draft.shared_conflicts[1].atom.atom_id: ConflictDecision.REMOVE}
        result = service.execute(service.finalize_remove(draft.id, decisions))
        self.assertEqual(store.read("User", "Path").value, "BASE;A")
        self.assertEqual(result.removed_element_count, 2)
        self.assertEqual(result.kept_shared_count, 1)
        self.assertEqual(len(store.writes), 1)

    def test_all_keep_only_keeps_shared_objects_ordinary_removal_still_executes(self):
        configuration = config(("App", "Default", [("Path", "SHARED;ORDINARY", "+append", "User")]),
                               ("Other", "Default", [("Path", "SHARED", "+append", "User")]))
        service, store = self.make(configuration, [raw("Path", "BASE;SHARED;ORDINARY")])
        draft = service.prepare_remove(self.request())
        result = service.execute(service.finalize_remove(draft.id))
        self.assertEqual(store.read("User", "Path").value, "BASE;SHARED")
        self.assertEqual(result.kept_shared_count, 1)
        self.assertEqual(result.removed_element_count, 1)

    def test_same_target_names_collapsed_but_case_sensitive_elements_remain_independent(self):
        configuration = config(("App", "One", [("Path", "X", "+append", "User")]),
                               ("App", "Two", [("PATH", "x", "-append", "User")]))
        service, store = self.make(configuration, [raw("Path", "x;X", kind=2)])
        draft = service.prepare_remove(self.request(RemovalScope.APPLICATION_ALL))
        self.assertEqual(len(draft.candidates.atoms), 2)
        self.assertEqual([atom.element_key for atom in draft.candidates.atoms], ["X", "x"])
        self.assertEqual([len(atom.sources) for atom in draft.candidates.atoms], [1, 1])
        result = service.execute(service.finalize_remove(draft.id))
        self.assertEqual(result.changed_count, 1)
        self.assertEqual(result.removed_element_count, 2)
        self.assertFalse(store.read("User", "Path").exists)
        self.assertEqual(len(store.writes), 1)

    def test_delete_empty_replace_is_true_absence_and_restore_recovers_empty(self):
        configuration = config(("App", "Default", [("EMPTY", "", "replace", "User")]))
        service, store = self.make(configuration, [raw("EMPTY", "", kind=2)])
        result = self.remove(service, self.request())
        self.assertFalse(store.read("User", "EMPTY").exists)
        self.assertTrue(service.execute(service.plan_restore(result.operation_id)).success)
        self.assertTrue(store.read("User", "EMPTY").same_content(raw("EMPTY", "", kind=2)))
        self.assertEqual(self.repo.snapshot()["successful_applications"], {})

    def test_external_change_after_preview_rejected_before_journal_or_write(self):
        configuration = config(("App", "Default", [("A", "a", "replace", "User")]))
        service, store = self.make(configuration, [raw("A", "a")])
        draft = service.prepare_remove(self.request())
        plan = service.finalize_remove(draft.id)
        store.values[("User", "A")] = raw("A", "external")
        with self.assertRaises(ConflictError):
            service.execute(plan)
        self.assertEqual(store.writes, [])
        self.assertEqual(self.repo.history(), [])

    def test_external_shared_dependency_change_rejected_even_when_every_object_kept(self):
        configuration = config(("App", "Default", [("Path", "A", "+append", "User")]),
                               ("Other", "Default", [("Path", "A", "+append", "User")]))
        service, store = self.make(configuration, [raw("Path", "A")])
        draft = service.prepare_remove(self.request())
        plan = service.finalize_remove(draft.id)
        self.assertEqual(plan.changes, ())
        store.values[("User", "PATH")] = raw("Path", "A;NEW")
        with self.assertRaises(ConflictError):
            service.execute(plan)
        self.assertEqual(self.repo.history(), [])

    def test_new_shared_selected_input_after_preview_requires_new_consent(self):
        configuration = config(("App", "Default", [("Path", "A", "+append", "User")]),
                               ("Other", "Default", [("Path", "B", "+append", "User")]))
        service, store = self.make(configuration, [raw("Path", "A;BASE")])
        draft = service.prepare_remove(self.request())
        self.assertEqual(draft.shared_conflicts, ())
        plan = service.finalize_remove(draft.id)
        service.selections.edit(configuration.variables("Other", "Default")[0], "A")
        with self.assertRaises(ConflictError):
            service.execute(plan)
        self.assertEqual(store.writes, [])
        fresh = service.prepare_remove(self.request())
        self.assertEqual(len(fresh.shared_conflicts), 1)

    def test_source_only_change_and_change_back_invalidates_plan_and_draft(self):
        configuration = config(("App", "Default", [("A", "a", "replace", "User")]))
        service, store = self.make(configuration, [raw("A", "a")])
        spec = configuration.variables("App", "Default")[0]
        draft = service.prepare_remove(self.request())
        plan = service.finalize_remove(draft.id)
        service.selections.choose(spec, "custom")
        service.selections.choose(spec, "default")
        with self.assertRaises(ConflictError):
            service.finalize_remove(draft.id)
        with self.assertRaises(ConflictError):
            service.execute(plan)
        self.assertEqual(store.writes, [])

    def test_option_change_back_and_config_reload_invalidate_removal(self):
        configuration = config(("App", "One", [("A", "a", "replace", "User")]),
                               ("App", "Two", [("B", "b", "replace", "User")]))
        service, store = self.make(configuration, [raw("A", "a")])
        draft = service.prepare_remove(self.request())
        plan = service.finalize_remove(draft.id)
        service.select_option("App", "Two")
        service.select_option("App", "One")
        with self.assertRaises(ConflictError):
            service.execute(plan)
        with self.assertRaises(ConflictError):
            service.finalize_remove(draft.id)
        draft = service.prepare_remove(self.request())
        plan = service.finalize_remove(draft.id)
        service.reload(configuration)
        with self.assertRaises(ConflictError):
            service.execute(plan)
        self.assertEqual(store.writes, [])

    def test_removal_history_restore_returns_true_raw_snapshot_and_preferences_unchanged(self):
        configuration = config(("App", "Default", [("HOME", "home", "replace", "User"),
                                                      ("Path", "X", "+append", "User")]))
        original_path = " A ;X;X;;\t; B ;"
        service, store = self.make(configuration, [raw("HOME", "home"), raw("Path", original_path, kind=2)])
        preferences = deepcopy(self.repo.snapshot())
        removed = self.remove(service, self.request())
        self.assertEqual(store.read("User", "Path").value, "A;B")
        restored = service.execute(service.plan_restore(removed.operation_id))
        self.assertTrue(restored.success)
        self.assertTrue(store.read("User", "HOME").same_content(raw("HOME", "home")))
        self.assertTrue(store.read("User", "Path").same_content(raw("Path", original_path, kind=2)))
        for key in ("last_values", "successful_applications", "orders"):
            self.assertEqual(self.repo.snapshot()[key], preferences[key])
        self.assertEqual(self.repo.operation(removed.operation_id)["status"], "restored")

    def test_disabled_cleanup_removal_uses_exact_unmatched_raw_slices(self):
        configuration = config(("App", "Default", [("Path", "B", "replace", "User")]))
        service, store = self.make(configuration, [raw("Path", " A ;B;; C ", kind=2)])
        service.set_cleanup_settings(CleanupSettings(False, False, False))
        snapshot = service.detection_snapshot().schemes[("App", "Default")]
        self.assertEqual(snapshot.variables[0].matched_elements, ("B",))
        result = self.remove(service, self.request())
        self.assertTrue(result.success)
        self.assertTrue(store.read("User", "Path").same_content(raw("Path", " A ;; C ", kind=2)))

    def test_partial_replace_shared_element_defaults_keep_and_unshared_remove(self):
        configuration = config(("App", "Default", [("Path", "A;C", "replace", "User")]),
                               ("Other", "Default", [("Path", "A;B", "replace", "User")]))
        service, store = self.make(configuration, [raw("Path", "A;C", kind=2)])
        draft = service.prepare_remove(self.request())
        self.assertEqual([c.atom.display_value for c in draft.shared_conflicts], ["A"])
        self.assertEqual([a.display_value for a in draft.ordinary_atoms], ["C"])
        result = service.execute(service.finalize_remove(draft.id))
        self.assertTrue(result.success)
        self.assertTrue(store.read("User", "Path").same_content(raw("Path", "A", kind=2)))

    def test_partial_remove_and_kept_shared_content_retain_active_marker(self):
        configuration = config(("App", "Default", [("A", "a", "replace", "User"),
                                                      ("Path", "X", "+append", "User")]),
                               ("Other", "Default", [("Path", "X", "+append", "User")]))
        service, store = self.make(configuration)
        service.execute(service.plan("App", "Default"))
        prefs = deepcopy(self.repo.snapshot()["successful_applications"])
        variable = configuration.variables("App", "Default")[0]
        self.remove(service, self.request(RemovalScope.VARIABLE, keys=(variable.key,)))
        self.assertIn("App", self.repo.snapshot()["active"])
        self.remove(service, self.request())  # X shared, default KEEP.
        self.assertIn("App", self.repo.snapshot()["active"])
        draft = service.prepare_remove(self.request())
        self.assertEqual(len(draft.shared_conflicts), 1)
        self.remove(service, self.request(), {draft.shared_conflicts[0].atom.atom_id: ConflictDecision.REMOVE})
        self.assertNotIn("App", self.repo.snapshot()["active"])
        self.assertEqual(self.repo.snapshot()["successful_applications"], prefs)
        self.assertFalse(store.read("User", "Path").exists)

    def test_failure_after_second_delete_rolls_back_before_states_without_preferences(self):
        configuration = config(("App", "Default", [("EARLY", "a", "replace", "User"),
                                                      ("LATE", "b", "replace", "User")]))
        service, store = self.make(configuration, [raw("EARLY", "a"), raw("LATE", "b", kind=2)],
                                   MutateThenFailDeleteStore)
        before = deepcopy(self.repo.snapshot())
        result = self.remove(service, self.request())
        self.assertEqual(result.status, "rolled_back")
        self.assertTrue(store.read("User", "EARLY").same_content(raw("EARLY", "a")))
        self.assertTrue(store.read("User", "LATE").same_content(raw("LATE", "b", kind=2)))
        self.assertEqual(self.repo.snapshot(), before)
        self.assertEqual(self.repo.pending(), [])
        self.assertEqual(result.removed_element_count, 0)

    def test_permission_denial_and_invalid_shared_inputs_never_write(self):
        configuration = config(("App", "Default", [("Path", "A", "+append", "System")]),
                               ("Other", "Default", [("Path", "B", "+append", "System")]))
        service, store = self.make(configuration, [raw("Path", "A", "System")])
        draft = service.prepare_remove(self.request())
        store.denied_scopes.add("System")
        with self.assertRaises(PermissionDenied):
            service.finalize_remove(draft.id)
        service.selections.edit(configuration.variables("Other", "Default")[0], "B\0C")
        with self.assertRaises(ValidationError):
            service.prepare_remove(self.request())
        self.assertEqual(store.writes, [])
        self.assertEqual(self.repo.history(), [])

    def test_pending_execution_blocks_preparation(self):
        configuration = config(("App", "Default", [("A", "a", "replace", "User")]))
        service, store = self.make(configuration, [raw("A", "a")])
        self.repo.save_operation({"id": "pending", "created_at": "2000", "status": "writing"})
        with self.assertRaises(ConflictError):
            service.prepare_remove(self.request())
        self.assertEqual(store.writes, [])

    def test_persisted_revision_change_rejects_draft_and_final_plan(self):
        configuration = config(("App", "Default", [("A", "a", "replace", "User")]))
        service, store = self.make(configuration, [raw("A", "a")])
        draft = service.prepare_remove(self.request())
        plan = service.finalize_remove(draft.id)
        snapshot = self.repo.snapshot()
        unrelated = {"id": "external-window", "created_at": "2000", "status": "no_change",
                     "action": "apply", "changes": []}
        self.repo.save_operation(unrelated)
        self.repo.commit(snapshot["revision"], snapshot, unrelated)
        with self.assertRaises(ConflictError):
            service.finalize_remove(draft.id)
        with self.assertRaises(ConflictError):
            service.execute(plan)
        self.assertEqual(store.writes, [])

    def test_unrelated_invalid_draft_does_not_block_actual_removal(self):
        configuration = config(("App", "Default", [("A", "a", "replace", "User")]),
                               ("Other", "Default", [("Path", "B", "+append", "User")]))
        service, store = self.make(configuration, [raw("A", "a")])
        service.selections.edit(configuration.variables("Other", "Default")[0], "B\0C")
        result = self.remove(service, self.request())
        self.assertTrue(result.success)
        self.assertFalse(store.read("User", "A").exists)

    def test_direct_selection_mutation_without_revision_is_also_detected(self):
        configuration = config(("App", "Default", [("A", "a", "replace", "User")]))
        service, store = self.make(configuration, [raw("A", "a")])
        draft = service.prepare_remove(self.request())
        plan = service.finalize_remove(draft.id)
        service.selections.get(configuration.variables("App", "Default")[0]).source = "custom"
        with self.assertRaises(ConflictError):
            service.execute(plan)
        self.assertEqual(store.writes, [])


if __name__ == "__main__":
    unittest.main()
