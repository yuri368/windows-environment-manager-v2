from __future__ import annotations

from copy import deepcopy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from envmanager.config import parse_config
from envmanager.models import ConflictError, RegistryState
from envmanager.persistence import StateRepository
from envmanager.service import EnvironmentService
from envmanager.store import MemoryStore


def variable(value, mode="+append"):
    return (f"{{default_value={value!r},scope='User',mode={mode!r},editable=true}}")


def configuration(*sections):
    return parse_config("\n".join(f"[{app}.{option}]\n" +
                                 "\n".join(f"{name}={definition}" for name, definition in variables.items())
                                 for app, option, variables in sections))


def raw(name, value):
    return RegistryState(name, "User", True, value, 1)


class FailLateStore(MemoryStore):
    """A second distinct target mutates, then reports a write failure once."""
    def write(self, state):
        super().write(state)
        if state.name == "LATE" and getattr(self, "enabled", True):
            self.enabled = False
            raise OSError("isolated injected write failure")


class BatchServiceReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.repository = StateRepository(self.directory)

    def service(self, config, store=None):
        return EnvironmentService(config, store or MemoryStore(), self.repository)

    def test_same_target_case_alias_written_once_and_saved_per_application(self):
        config = configuration(("First", "Default", {"Path": variable("X")}),
                               ("Second", "Default", {"PATH": variable("Y", "-append")}))
        store = MemoryStore([raw("Path", "BASE")])
        service = self.service(config, store)
        first, second = (config.variables(app, "Default")[0] for app in config.apps())
        service.selections.edit(first, "X;CUSTOM")
        result = service.execute(service.plan_batch(["Second", "First"]))
        self.assertTrue(result.success)
        self.assertEqual([(item.name, item.value) for item in store.writes], [("Path", "X;CUSTOM;BASE;Y")])
        self.assertEqual(store.notifications, 1)
        saved = self.repository.snapshot()["successful_applications"]
        self.assertEqual(saved["First"]["selections"][first.key], {"source": "custom", "value": "X;CUSTOM"})
        self.assertEqual(saved["Second"]["selections"][second.key], {"source": "default", "value": "Y"})

    def test_equal_text_source_change_invalidates_preview_without_writing(self):
        config = configuration(("App", "Default", {"VALUE": variable("same", "replace")}))
        service = self.service(config)
        item = config.variables("App", "Default")[0]
        plan = service.plan_batch(["App"])
        service.selections.choose(item, "custom")
        self.assertEqual(service.selections.get(item).value, "same")
        with self.assertRaises(ConflictError):
            service.execute(plan)
        self.assertEqual(service.store.writes, [])
        self.assertEqual(self.repository.history(), [])

    def test_switch_away_and_back_still_invalidates_original_preview(self):
        config = configuration(("App", "One", {"VALUE": variable("same", "replace")}),
                               ("App", "Two", {"VALUE": variable("same", "replace")}))
        service = self.service(config)
        plan = service.plan("App", "One")
        service.select_option("App", "Two")
        service.select_option("App", "One")
        with self.assertRaises(ConflictError):
            service.execute(plan)
        self.assertEqual(service.store.writes, [])

    def test_late_failure_keeps_all_prior_successful_preferences(self):
        config = configuration(("First", "Old", {"EARLY": variable("old1", "replace")}),
                               ("First", "New", {"EARLY": variable("new1", "replace")}),
                               ("Second", "Old", {"LATE": variable("old2", "replace")}),
                               ("Second", "New", {"LATE": variable("new2", "replace")}))
        store = FailLateStore([raw("EARLY", "base1"), raw("LATE", "base2")])
        store.enabled = False
        service = self.service(config, store)
        self.assertTrue(service.execute(service.plan_batch({"First": "Old", "Second": "Old"})).success)
        before = deepcopy(self.repository.snapshot())
        store.enabled = True
        result = service.execute(service.plan_batch({"First": "New", "Second": "New"}))
        self.assertEqual(result.status, "rolled_back")
        self.assertEqual(self.repository.snapshot(), before)
        self.assertEqual((store.read("User", "EARLY").value, store.read("User", "LATE").value), ("old1", "old2"))
        restarted = self.service(config, store)
        self.assertEqual((restarted.selected_option("First"), restarted.selected_option("Second")), ("Old", "Old"))

    def test_joint_switch_common_baseline_writes_only_final_combined_result(self):
        config = configuration(("First", "Old", {"Path": variable("X")}),
                               ("First", "New", {"Path": variable("N")}),
                               ("Second", "Old", {"Path": variable("Y")}),
                               ("Second", "New", {"Path": variable("M", "-append")}))
        store = MemoryStore([raw("Path", "BASE")])
        service = self.service(config, store)
        self.assertTrue(service.execute(service.plan_batch({"First": "Old", "Second": "Old"})).success)
        store.writes.clear()
        plan = service.plan_batch({"First": "New", "Second": "New"})
        self.assertTrue(plan.can_execute, plan.conflicts)
        self.assertEqual(len(plan.changes), 1)
        self.assertTrue(service.execute(plan).success)
        self.assertEqual([item.value for item in store.writes], ["N;BASE;M"])

    def test_joint_switch_different_historical_baselines_blocks_removal(self):
        config = configuration(("First", "Old", {"Path": variable("X")}),
                               ("First", "New", {"Path": variable("N")}),
                               ("Second", "Old", {"Path": variable("Y")}),
                               ("Second", "New", {"Path": variable("M")}))
        store = MemoryStore([raw("Path", "BASE")])
        service = self.service(config, store)
        service.execute(service.plan("First", "Old"))
        service.execute(service.plan("Second", "Old"))
        before = deepcopy(self.repository.snapshot())
        write_count = len(store.writes)
        plan = service.plan_batch({"First": "New", "Second": "New"})
        self.assertFalse(plan.can_execute)
        with self.assertRaises(ConflictError):
            service.execute(plan)
        self.assertEqual(len(store.writes), write_count)
        self.assertEqual(store.read("User", "Path").value, "Y;X;BASE")
        self.assertEqual(self.repository.snapshot(), before)

    def test_restore_keeps_latest_scheme_source_and_input_on_restart(self):
        config = configuration(("App", "Old", {"VALUE": variable("old", "replace")}),
                               ("App", "New", {"VALUE": variable("new", "replace")}))
        store = MemoryStore([raw("VALUE", "BASE")])
        service = self.service(config, store)
        self.assertTrue(service.execute(service.plan("App", "Old")).success)
        item = config.variables("App", "New")[0]
        service.selections.edit(item, "chosen")
        switched = service.execute(service.plan("App", "New"))
        success_before_restore = deepcopy(self.repository.snapshot()["successful_applications"])
        self.assertTrue(service.execute(service.plan_restore(switched.operation_id)).success)
        self.assertEqual(store.read("User", "VALUE").value, "old")
        self.assertEqual(self.repository.snapshot()["active"]["App"]["option"], "Old")
        self.assertEqual(self.repository.snapshot()["successful_applications"], success_before_restore)
        restarted = self.service(config, store)
        self.assertEqual(restarted.selected_option("App"), "New")
        self.assertEqual((restarted.selections.get(item).source, restarted.selections.get(item).value), ("custom", "chosen"))

    def test_legacy_snapshot_without_successful_applications_is_usable(self):
        config = configuration(("App", "Default", {"VALUE": variable("default", "replace")}),
                               ("App", "Chosen", {"VALUE": variable("chosen", "replace")}))
        item = config.variables("App", "Chosen")[0]
        legacy = {"revision": 3, "last_values": {item.key: "saved"}, "orders": {},
                  "active": {"App": {"option": "Chosen", "effects": []}}}
        with closing(sqlite3.connect(self.repository.path)) as connection:
            with connection:
                connection.execute("UPDATE snapshot SET data=? WHERE id=1", (json.dumps(legacy),))
        reopened = StateRepository(self.directory)
        service = EnvironmentService(config, MemoryStore([raw("VALUE", "saved")]), reopened)
        self.assertEqual(service.selected_option("App"), "Chosen")
        self.assertEqual((service.selections.get(item).source, service.selections.get(item).value), ("last", "saved"))
        self.assertTrue(service.execute(service.plan_batch(["App"])).success)
        self.assertEqual(reopened.snapshot()["successful_applications"]["App"]["option"], "Chosen")


if __name__ == "__main__":
    unittest.main()
