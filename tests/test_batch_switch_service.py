from __future__ import annotations

from collections import Counter
from pathlib import Path
import tempfile
import unittest

from envmanager.matching import SchemeStatus
from envmanager.models import BatchSchemeSwitchDraft, ConflictError
from envmanager.persistence import StateRepository
from envmanager.sharing import ConflictDecision
from envmanager.service import EnvironmentService
from envmanager.store import MemoryStore
from tests.test_detection_service import config, raw
from tests.test_switch_service import FailAfterPathWriteStore


class CountingStore(MemoryStore):
    def __init__(self, states=()):
        super().__init__(states)
        self.reads = []

    def read(self, scope, name):
        self.reads.append((scope, name.upper()))
        return super().read(scope, name)


class BatchSwitchServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = StateRepository(Path(self.temp.name))

    def make(self, configuration=None, states=None, store_type=CountingStore):
        configuration = configuration or config(
            ("Java", "8", [("JAVA_HOME", "old", "replace", "User"),
                             ("Path", "COMMON;OLD", "+append", "User")]),
            ("Java", "17", [("JAVA_HOME", "new", "replace", "User"),
                              ("Path", "COMMON;NEW", "+append", "User")]),
            ("Maven", "Old", [("Path", "MOLD", "+append", "User")]),
            ("Maven", "New", [("Path", "MNEW", "+append", "User")]))
        states = states if states is not None else [raw("JAVA_HOME", "old", kind=2),
                                                   raw("Path", "BASE;COMMON;OLD;MOLD", kind=2)]
        store = store_type(states)
        return EnvironmentService(configuration, store, self.repo), store

    def test_collective_switch_reads_each_target_once_and_writes_once_with_per_app_state(self):
        service, store = self.make()
        draft = service.prepare_batch_scheme_switch({"Java": "17", "Maven": "New"})
        self.assertIsInstance(draft, BatchSchemeSwitchDraft)
        self.assertEqual(Counter(store.reads), {("User", "JAVA_HOME"): 1, ("User", "PATH"): 1})
        self.assertEqual(store.writes, [])
        self.assertEqual(self.repo.history(), [])
        self.assertEqual(dict(draft.old_options_by_app), {"Java": ("8",), "Maven": ("Old",)})
        plan = service.finalize_batch_scheme_switch(draft.id)
        self.assertEqual(plan.metadata["request"]["scope"], "batch_scheme_switch")
        self.assertEqual(plan.metadata["switches"], [
            {"app": "Java", "old_options": ["8"], "new_option": "17"},
            {"app": "Maven", "old_options": ["Old"], "new_option": "New"}])
        result = service.execute(plan)
        self.assertTrue(result.success)
        self.assertEqual(store.read("User", "Path").value, "COMMON;NEW;MNEW;BASE")
        self.assertEqual([item.name.upper() for item in store.writes].count("PATH"), 1)
        saved = self.repo.snapshot()
        for app, option in draft.applications:
            self.assertEqual(saved["active"][app]["option"], option)
            expected = {item.key for item in service.config.variables(app, option)}
            self.assertEqual({item["record_key"] for item in saved["active"][app]["effects"]}, expected)
            self.assertEqual(set(saved["successful_applications"][app]["selections"]), expected)
        detected = service.detection_snapshot()
        self.assertEqual(detected.schemes[("Java", "17")].status, SchemeStatus.COMPLETE)
        self.assertEqual(detected.schemes[("Maven", "New")].status, SchemeStatus.COMPLETE)
        self.assertEqual(result.removed_element_count, 3)

    def test_one_checked_manual_scheme_uses_new_api_without_active_history(self):
        service, store = self.make()
        draft = service.prepare_batch_scheme_switch({"Java": "17"})
        result = service.execute(service.finalize_batch_scheme_switch(draft.id))
        self.assertTrue(result.success)
        self.assertEqual(store.read("User", "Path").value, "COMMON;NEW;BASE;MOLD")
        self.assertNotIn("Maven", self.repo.snapshot()["active"])

    def test_preserves_unselected_noncurrent_shared_element_and_reports_remove_decision(self):
        configuration = config(
            ("Java", "Old", [("Path", "OLD", "+append", "User")]),
            ("Java", "New", [("Path", "NEW", "+append", "User")]),
            ("Scala", "Default", [("Path", "ABSENT", "+append", "User")]),
            ("Scala", "Other", [("Path", "OLD", "+append", "User")]))
        service, store = self.make(configuration, [raw("Path", "BASE;OLD")])
        draft = service.prepare_batch_scheme_switch({"Java": "New"})
        kept = service.finalize_batch_scheme_switch(draft.id)
        self.assertIn("OLD", kept.changes[0].after.value.split(";"))
        conflict = draft.shared_conflicts[0]
        removed = service.finalize_batch_scheme_switch(draft.id, {conflict.atom.atom_id: ConflictDecision.REMOVE})
        result = service.execute(removed)
        self.assertTrue(result.success)
        self.assertNotIn("OLD", store.read("User", "Path").value.split(";"))
        self.assertEqual(removed.metadata["decisions"][conflict.atom.atom_id], ConflictDecision.REMOVE.value)

    def test_frozen_input_and_windows_dependency_changes_block_execution(self):
        for mutation in ("windows", "selection", "option"):
            with self.subTest(mutation=mutation):
                service, store = self.make()
                draft = service.prepare_batch_scheme_switch({"Java": "17", "Maven": "New"})
                plan = service.finalize_batch_scheme_switch(draft.id)
                if mutation == "windows":
                    store.values[("User", "PATH")] = raw("Path", "EXTERNAL")
                elif mutation == "selection":
                    service.selections.edit(service.config.variables("Maven", "New")[0], "CHANGED")
                else:
                    service.select_option("Maven", "Old")
                with self.assertRaises(ConflictError):
                    service.execute(plan)
                self.assertEqual(store.writes, [])

    def test_failure_rolls_back_all_targets_without_committing_any_app(self):
        before = [raw("JAVA_HOME", "old", kind=2), raw("Path", "BASE;COMMON;OLD;MOLD", kind=2)]
        service, store = self.make(states=before, store_type=FailAfterPathWriteStore)
        draft = service.prepare_batch_scheme_switch({"Java": "17", "Maven": "New"})
        result = service.execute(service.finalize_batch_scheme_switch(draft.id))
        self.assertEqual(result.status, "rolled_back")
        for item in before:
            self.assertTrue(store.read(item.scope, item.name).same_content(item))
        self.assertEqual(self.repo.snapshot()["active"], {})
        self.assertEqual(self.repo.snapshot()["successful_applications"], {})

    def test_batch_history_restore_recovers_original_manual_windows_state(self):
        service, store = self.make()
        draft = service.prepare_batch_scheme_switch({"Java": "17", "Maven": "New"})
        result = service.execute(service.finalize_batch_scheme_switch(draft.id))
        restore = service.plan_restore(result.operation_id)
        self.assertTrue(service.execute(restore).success)
        self.assertEqual(store.read("User", "Path").value, "BASE;COMMON;OLD;MOLD")
        self.assertEqual(store.read("User", "JAVA_HOME").value, "old")
        self.assertEqual(self.repo.snapshot()["active"], {})

    def test_append_sort_confirmation_remains_collective(self):
        configuration = config(
            ("Java", "Old", [("Path", "OLD", "+append", "User")]),
            ("Java", "New", [("Path", "NEW", "append", "User")]),
            ("Maven", "Old", [("Path", "MOLD", "+append", "User")]),
            ("Maven", "New", [("Path", "MNEW", "+append", "User")]))
        service, store = self.make(configuration, [raw("Path", "BASE;OLD;MOLD")])
        draft = service.prepare_batch_scheme_switch({"Java": "New", "Maven": "New"})
        self.assertTrue(draft.needs_order)
        change = draft.changes[0]
        plan = service.finalize_batch_scheme_switch(draft.id,
            orders={change.record_key: ("MNEW", "BASE", "NEW")})
        self.assertTrue(plan.can_execute)
        self.assertTrue(service.execute(plan).success)
        self.assertEqual(store.read("User", "Path").value, "MNEW;BASE;NEW")
        self.assertEqual(len(store.writes), 1)

    def test_compatibility_plan_batch_uses_live_switch_semantics(self):
        service, _ = self.make()
        plan = service.plan_batch({"Java": "17"})
        path_change = next(item for item in plan.changes if item.before.name.upper() == "PATH")
        self.assertNotIn("OLD", path_change.after.value.split(";"))
        self.assertEqual(plan.metadata["switches"], [
            {"app": "Java", "old_options": ["8"], "new_option": "17"}])

    def test_reprepare_sort_retains_explicit_old_choices_and_shared_keep(self):
        configuration = config(
            ("Java", "OldA", [("Path", "OLD", "+append", "User")]),
            ("Java", "OldB", [("Path", "OTHER", "+append", "User")]),
            ("Java", "New", [("Path", "NEW", "append", "User")]),
            ("Maven", "New", [("Path", "MNEW", "+append", "User")]),
            ("Scala", "Shared", [("Path", "OLD", "+append", "User")]))
        service, store = self.make(configuration, [raw("Path", "BASE;OLD;OTHER")])
        requested, explicit = {"Java": "New", "Maven": "New"}, {"Java": ("OldA",), "Maven": ()}
        draft = service.prepare_batch_scheme_switch(requested, old_options=explicit)
        preliminary = service.finalize_batch_scheme_switch(draft.id)
        change = preliminary.changes[0]
        ordered = tuple(reversed(change.calculation.items))
        refreshed = service.prepare_batch_scheme_switch(requested,
            old_options=dict(draft.old_options_by_app), orders={change.record_key: ordered})
        self.assertEqual(refreshed.old_options_by_app["Java"], ("OldA",))
        plan = service.finalize_batch_scheme_switch(refreshed.id)
        self.assertTrue(plan.can_execute)
        self.assertTrue(service.execute(plan).success)
        self.assertEqual(store.read("User", "Path").value, ";".join(ordered))
        self.assertIn("OLD", ordered)
        self.assertIn("OTHER", ordered)

    def test_single_and_batch_shared_keep_require_second_complete_order(self):
        configuration = config(
            ("Java", "Old", [("Path", "OLD", "+append", "User")]),
            ("Java", "New", [("Path", "NEW", "append", "User")]),
            ("Scala", "Shared", [("Path", "OLD", "+append", "User")]))
        for batch in (False, True):
            with self.subTest(batch=batch):
                service, store = self.make(configuration, [raw("Path", "BASE;OLD")])
                prepare = (lambda **kwargs: service.prepare_batch_scheme_switch({"Java": "New"}, **kwargs)) if batch else (
                    lambda **kwargs: service.prepare_scheme_switch("Java", "New", **kwargs))
                finalize = service.finalize_batch_scheme_switch if batch else service.finalize_scheme_switch
                first = prepare()
                key = first.changes[0].record_key
                ordered = prepare(orders={key: ("NEW", "BASE")})
                kept = finalize(ordered.id)
                self.assertTrue(kept.needs_order)
                completed = prepare(orders={key: ("BASE", "OLD", "NEW")})
                self.assertEqual(len(completed.shared_conflicts), 1)
                plan = finalize(completed.id)
                self.assertTrue(plan.can_execute)
                self.assertTrue(service.execute(plan).success)
                self.assertEqual(store.read("User", "Path").value, "BASE;OLD;NEW")

    def test_sort_projection_rejects_unknown_duplicate_and_missing_new_items(self):
        configuration = config(
            ("Java", "Old", [("Path", "OLD", "+append", "User")]),
            ("Java", "New", [("Path", "NEW", "append", "User")]),
            ("Scala", "Shared", [("Path", "OLD", "+append", "User")]))
        service, _ = self.make(configuration, [raw("Path", "BASE;OLD")])
        draft = service.prepare_batch_scheme_switch({"Java": "New"})
        key = draft.changes[0].record_key
        from envmanager.models import ValidationError
        for order in (("NEW", "BASE", "UNKNOWN"), ("NEW", "NEW", "BASE"),
                      ("NEW", "BASE", "OLD", "OLD"), ("BASE", "OLD")):
            with self.subTest(order=order), self.assertRaises(ValidationError):
                service.prepare_batch_scheme_switch({"Java": "New"}, orders={key: order})


if __name__ == "__main__":
    unittest.main()
