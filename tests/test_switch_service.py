from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from envmanager.matching import SchemeStatus
from envmanager.models import ConflictError, RegistryState
from envmanager.persistence import StateRepository
from envmanager.service import EnvironmentService
from envmanager.store import MemoryStore
from envmanager.switching import AmbiguousOldSchemeError
from tests.test_detection_service import config, raw


class FailAfterPathWriteStore(MemoryStore):
    def write(self, state):
        super().write(state)
        if state.name.upper() == "PATH" and getattr(self, "fail", True):
            self.fail = False
            raise OSError("injected write failure after mutation")


class SwitchServiceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = StateRepository(Path(temp.name))

    def make(self, configuration, states=(), store_type=MemoryStore):
        store = store_type(states)
        return EnvironmentService(configuration, store, self.repo), store

    @staticmethod
    def java_config(new_mode="+append", new_path="COMMON;NEW"):
        return config(
            ("Java", "8", [("JAVA_HOME", "old", "replace", "User"),
                              ("Path", "COMMON;OLD", "+append", "User")]),
            ("Java", "17", [("JAVA_HOME", "new", "replace", "User"),
                               ("Path", new_path, new_mode, "User")]),
        )

    def test_manual_complete_old_switches_without_active_history_and_coalesces_targets(self):
        configuration = self.java_config()
        service, store = self.make(configuration, [raw("JAVA_HOME", "old", kind=2),
                                                     raw("Path", "BASE;COMMON;OLD", kind=2)])
        draft = service.prepare_scheme_switch("Java", "17")
        self.assertEqual(draft.old_options, ("8",))
        self.assertEqual(self.repo.snapshot()["active"], {})
        self.assertEqual(store.writes, [])
        plan = service.finalize_scheme_switch(draft.id)
        self.assertEqual(len(plan.changes), 2)
        result = service.execute(plan)
        self.assertTrue(result.success)
        self.assertEqual(store.read("User", "JAVA_HOME").value, "new")
        path = store.read("User", "Path")
        self.assertEqual(path.value, "COMMON;NEW;BASE")
        self.assertEqual(path.registry_type, 2)
        self.assertEqual([state.name.upper() for state in store.writes].count("PATH"), 1)
        self.assertEqual(self.repo.snapshot()["active"]["Java"]["option"], "17")
        self.assertEqual(self.repo.snapshot()["successful_applications"]["Java"]["option"], "17")
        self.assertEqual(self.repo.operation(result.operation_id)["metadata"]["old_options"], ["8"])

    def test_multiple_complete_candidates_require_explicit_choice(self):
        configuration = config(
            ("Java", "A", [("HOME", "old", "replace", "User")]),
            ("Java", "B", [("HOME", "old", "replace", "User")]),
            ("Java", "New", [("HOME", "new", "replace", "User")]),
        )
        service, store = self.make(configuration, [raw("HOME", "old")])
        with self.assertRaises(AmbiguousOldSchemeError) as captured:
            service.prepare_scheme_switch("Java", "New")
        self.assertEqual(captured.exception.candidates.complete, ("A", "B"))
        self.assertEqual(store.writes, [])
        draft = service.prepare_scheme_switch("Java", "New", old_options=("B",))
        self.assertEqual(draft.old_options, ("B",))

    def test_partial_old_requires_explicit_selection(self):
        configuration = self.java_config()
        states = [raw("JAVA_HOME", "old"), raw("Path", "BASE;COMMON")]
        service, _ = self.make(configuration, states)
        inferred = service.prepare_scheme_switch("Java", "17")
        self.assertEqual(inferred.old_options, ())
        self.assertIn("8", inferred.candidates.partial)
        explicit = service.prepare_scheme_switch("Java", "17", old_options=("8",))
        self.assertEqual(explicit.old_options, ("8",))
        self.assertTrue(explicit.skipped)

    def test_shared_old_element_is_independent_and_defaults_keep(self):
        configuration = config(
            ("Java", "Old", [("Path", "SHARED;OLD", "+append", "User")]),
            ("Java", "New", [("Path", "NEW", "+append", "User")]),
            ("Other", "One", [("Path", "SHARED", "+append", "User")]),
            ("Other", "Two", [("Path", "SHARED", "-append", "User")]),
        )
        service, store = self.make(configuration, [raw("Path", "BASE;SHARED;OLD")])
        draft = service.prepare_scheme_switch("Java", "New")
        self.assertEqual(len(draft.shared_conflicts), 1)
        conflict = draft.shared_conflicts[0]
        self.assertEqual({item.option for item in conflict.consumers}, {"One", "Two"})
        result = service.execute(service.finalize_scheme_switch(draft.id))
        self.assertTrue(result.success)
        self.assertEqual(store.read("User", "Path").value, "NEW;BASE;SHARED")
        self.assertEqual(result.kept_shared_count, 1)

    def test_explicit_shared_remove_and_exact_replace_protection(self):
        configuration = config(
            ("Java", "Old", [("Path", "OLD", "+append", "User")]),
            ("Java", "New", [("Path", "NEW", "+append", "User")]),
            ("Other", "Exact", [("Path", "BASE;OLD", "replace", "User")]),
        )
        service, store = self.make(configuration, [raw("Path", "BASE;OLD")])
        draft = service.prepare_scheme_switch("Java", "New")
        self.assertEqual(len(draft.shared_conflicts), 1)
        kept = service.finalize_scheme_switch(draft.id)
        self.assertEqual(kept.changes[0].after.value, "NEW;BASE;OLD")
        decision = {draft.shared_conflicts[0].atom.atom_id: "remove"}
        result = service.execute(service.finalize_scheme_switch(draft.id, decision))
        self.assertTrue(result.success)
        self.assertEqual(store.read("User", "Path").value, "NEW;BASE")

    def test_append_order_is_confirmed_and_persisted(self):
        configuration = self.java_config(new_mode="append", new_path="COMMON;NEW")
        service, store = self.make(configuration, [raw("JAVA_HOME", "old"),
                                                     raw("Path", "BASE;COMMON;OLD")])
        draft = service.prepare_scheme_switch("Java", "17")
        self.assertTrue(draft.needs_order)
        path_spec = configuration.variables("Java", "17")[1]
        plan = service.finalize_scheme_switch(
            draft.id, orders={path_spec.key: ("BASE", "COMMON", "NEW")})
        self.assertFalse(plan.needs_order)
        service.execute(plan)
        self.assertEqual(store.read("User", "Path").value, "BASE;COMMON;NEW")
        self.assertEqual(self.repo.snapshot()["orders"][path_spec.key], ["BASE", "COMMON", "NEW"])

    def test_no_change_still_records_new_success_without_write(self):
        configuration = config(
            ("App", "Old", [("VALUE", "same", "replace", "User")]),
            ("App", "New", [("VALUE", "same", "replace", "User")]),
        )
        service, store = self.make(configuration, [raw("VALUE", "same")])
        draft = service.prepare_scheme_switch("App", "New", old_options=("Old",))
        result = service.execute(service.finalize_scheme_switch(draft.id))
        self.assertEqual(result.status, "no_change")
        self.assertEqual(store.writes, [])
        self.assertEqual(self.repo.snapshot()["active"]["App"]["option"], "New")

    def test_option_selection_alone_never_writes_or_creates_history(self):
        configuration = self.java_config()
        service, store = self.make(configuration, [raw("JAVA_HOME", "old"), raw("Path", "COMMON;OLD")])
        service.select_option("Java", "17")
        self.assertEqual(store.writes, [])
        self.assertEqual(self.repo.history(), [])
        self.assertEqual(self.repo.snapshot()["active"], {})

    def test_external_selection_and_option_changes_invalidate_plan(self):
        configuration = self.java_config()
        service, store = self.make(configuration, [raw("JAVA_HOME", "old"), raw("Path", "COMMON;OLD")])
        draft = service.prepare_scheme_switch("Java", "17")
        plan = service.finalize_scheme_switch(draft.id)
        store.values[("User", "PATH")] = raw("Path", "EXTERNAL")
        with self.assertRaises(ConflictError):
            service.execute(plan)
        self.assertEqual(store.writes, [])

        service, store = self.make(configuration, [raw("JAVA_HOME", "old"), raw("Path", "COMMON;OLD")])
        draft = service.prepare_scheme_switch("Java", "17")
        plan = service.finalize_scheme_switch(draft.id)
        service.selections.edit(configuration.variables("Java", "17")[0], "changed")
        with self.assertRaises(ConflictError):
            service.execute(plan)
        self.assertEqual(store.writes, [])

    def test_failure_rolls_back_all_written_targets_and_preferences(self):
        configuration = self.java_config()
        before = [raw("JAVA_HOME", "old", kind=2), raw("Path", "BASE;COMMON;OLD", kind=2)]
        service, store = self.make(configuration, before, FailAfterPathWriteStore)
        draft = service.prepare_scheme_switch("Java", "17")
        result = service.execute(service.finalize_scheme_switch(draft.id))
        self.assertEqual(result.status, "rolled_back")
        self.assertTrue(store.read("User", "JAVA_HOME").same_content(before[0]))
        self.assertTrue(store.read("User", "Path").same_content(before[1]))
        self.assertEqual(self.repo.snapshot()["successful_applications"], {})
        self.assertEqual(self.repo.snapshot()["active"], {})

    def test_success_refreshes_old_new_detection(self):
        configuration = self.java_config()
        service, _ = self.make(configuration, [raw("JAVA_HOME", "old"), raw("Path", "BASE;COMMON;OLD")])
        before = service.detection_snapshot()
        self.assertEqual(before.schemes[("Java", "8")].status, SchemeStatus.COMPLETE)
        draft = service.prepare_scheme_switch("Java", "17")
        service.execute(service.finalize_scheme_switch(draft.id))
        after = service.detection_snapshot()
        self.assertGreater(after.generation, before.generation)
        self.assertEqual(after.schemes[("Java", "17")].status, SchemeStatus.COMPLETE)
        self.assertEqual(after.schemes[("Java", "8")].status, SchemeStatus.PARTIAL)


if __name__ == "__main__":
    unittest.main()
