from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from envmanager.config import parse_config
from envmanager.models import ConflictError, PermissionDenied
from envmanager.persistence import StateRepository
from envmanager.service import EnvironmentService
from envmanager.store import MemoryStore
from tests.test_service import config, existing, record, MutateThenFailStore


class BatchServiceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.repo = StateRepository(self.directory / "state")

    def make(self, text, states=(), store_type=MemoryStore):
        store = store_type(states)
        return EnvironmentService(parse_config(text), store, self.repo), store

    def test_defaults_then_complete_success_restore_and_runtime_edits(self):
        text = '''[Java]
default_option = "New"
[Java.Old]
JAVA_HOME = {default_value='old',scope='User',mode='replace',editable=true,choices=[{label="JDK 17",value="jdk17"},{label="JDK 21",value="jdk21"}]}
[Java.New]
JAVA_HOME = {default_value='new',scope='User',mode='replace',editable=true}
[Python.Default]
PYTHON_HOME = {default_value='python',scope='User',mode='replace',editable=true}
'''
        service, store = self.make(text)
        self.assertEqual(service.selected_option("Java"), "New")
        service.select_option("Java", "Old")
        java = service.config.variables("Java", "Old")[0]
        python = service.config.variables("Python", "Default")[0]
        service.selections.choose(java, "choice:0")
        service.selections.edit(python, "custom-python")
        plan = service.plan_batch(["Python", "Java"])
        self.assertEqual(plan.applications, (("Java", "Old"), ("Python", "Default")))
        self.assertTrue(service.execute(plan).success)
        restarted = EnvironmentService(parse_config(text), store, StateRepository(self.repo.directory))
        self.assertEqual(restarted.selected_option("Java"), "Old")
        self.assertEqual((restarted.selections.get(java).source, restarted.selections.get(java).value),
                         ("choice:0", "jdk17"))
        self.assertEqual((restarted.selections.get(python).source, restarted.selections.get(python).value),
                         ("custom", "custom-python"))
        restarted.selections.edit(java, "my-current-value")
        current = restarted.plan_batch(["Java"])
        self.assertEqual(dict(current.selected_values)[java.key], "my-current-value")
        # Planning alone never replaces the previous successful snapshot.
        self.assertEqual(self.repo.snapshot()["successful_applications"]["Java"]["selections"][java.key]["value"], "jdk17")

    def test_shared_target_written_once_and_individual_inputs_recorded(self):
        text = config(("FrontA", "Default", {"Path": record("X", "+append")}),
                      ("FrontB", "Default", {"PATH": record("Y", "+append")}),
                      ("Back", "Default", {"Path": record("Z", "-append")}))
        service, store = self.make(text, [existing("Path", "A;B;B;C", registry_type=2)])
        plan = service.plan_batch(["Back", "FrontB", "FrontA"])
        self.assertEqual(len(plan.changes), 1)
        self.assertEqual(plan.changes[0].after.value, "X;Y;A;B;C;Z")
        self.assertEqual(len(plan.changes[0].contributions), 3)
        self.assertTrue(service.execute(plan).success)
        self.assertEqual(len(store.writes), 1)
        self.assertEqual(store.notifications, 1)
        state = self.repo.snapshot()
        for app, value in (("FrontA", "X"), ("FrontB", "Y"), ("Back", "Z")):
            spec = service.config.variables(app, "Default")[0]
            self.assertEqual(state["last_values"][spec.key], value)
            self.assertEqual(state["successful_applications"][app]["selections"][spec.key]["value"], value)
        repeated = service.execute(service.plan_batch(["FrontA", "FrontB", "Back"]))
        self.assertEqual(repeated.status, "no_change")
        self.assertEqual(len(store.writes), 1)

    def test_direction_conflict_blocks_every_target(self):
        text = config(("One", "Default", {"A": record("changed"), "Path": record("X", "+append")}),
                      ("Two", "Default", {"Path": record("X", "-append")}))
        service, store = self.make(text, [existing("A", "before"), existing("Path", "P")])
        plan = service.plan_batch(["One", "Two"])
        self.assertFalse(plan.can_execute)
        self.assertTrue(any("置前和置后" in message for message in plan.conflicts))
        with self.assertRaises(ConflictError):
            service.execute(plan)
        self.assertEqual(store.writes, [])
        self.assertEqual(self.repo.history(), [])
        self.assertEqual(self.repo.snapshot()["successful_applications"], {})

    def test_mixed_manual_sort_resolves_direction_conflict_once(self):
        text = config(("Front", "Default", {"Path": record("X", "+append")}),
                      ("Back", "Default", {"Path": record("x", "-append")}),
                      ("Manual", "Default", {"Path": record("Y", "append")}))
        service, store = self.make(text, [existing("Path", "A;B;B")])
        pending = service.plan_batch(["Front", "Back", "Manual"])
        self.assertEqual(len(pending.changes), 1)
        self.assertEqual(pending.conflicts, ())
        self.assertTrue(pending.needs_order)
        with self.assertRaises(ConflictError):
            service.execute(pending)
        change = pending.changes[0]
        order = ("Y", "B", "X", "A", "x")
        plan = service.plan_batch(["Front", "Back", "Manual"], {change.record_key: order})
        self.assertTrue(plan.can_execute)
        self.assertTrue(service.execute(plan).success)
        self.assertEqual(store.read("User", "Path").value, "Y;B;X;A;x")
        self.assertEqual(len(store.writes), 1)
        for app in ("Front", "Back", "Manual"):
            key = service.config.variables(app, "Default")[0].key
            self.assertEqual(self.repo.snapshot()["orders"][key], list(order))

    def test_replacement_compatible_only_for_identical_raw_strings(self):
        for index, (second, executable) in enumerate((("X", True), ("x", False))):
            with self.subTest(second=second):
                repo = StateRepository(self.directory / f"case-{index}")
                text = config(("One", "Default", {"Same": record("X")}),
                              ("Two", "Default", {"SAME": record(second)}))
                store = MemoryStore()
                service = EnvironmentService(parse_config(text), store, repo)
                plan = service.plan_batch(["One", "Two"])
                self.assertEqual(plan.can_execute, executable)
                if executable:
                    self.assertTrue(service.execute(plan).success)
                    self.assertEqual(len(store.writes), 1)
                else:
                    self.assertEqual(store.writes, [])

    def test_scope_isolation_and_permission_preflight(self):
        text = config(("UserApp", "Default", {"Path": record("U", scope="User")}),
                      ("SystemApp", "Default", {"PATH": record("S", scope="System")}))
        service, store = self.make(text)
        store.denied_scopes.add("System")
        with self.assertRaises(PermissionDenied):
            service.plan_batch(["UserApp", "SystemApp"])
        self.assertEqual(store.writes, [])
        self.assertEqual(self.repo.history(), [])
        store.denied_scopes.clear()
        plan = service.plan_batch(["UserApp", "SystemApp"])
        self.assertEqual(len(plan.changes), 2)
        self.assertTrue(service.execute(plan).success)
        self.assertEqual(store.read("User", "Path").value, "U")
        self.assertEqual(store.read("System", "Path").value, "S")

    def test_partial_write_failure_restores_absence_and_empty_without_preferences(self):
        text = config(("One", "Default", {"A": record("new-a")}),
                      ("Two", "Default", {"B": record("new-b")}))
        service, store = self.make(text, [existing("B", "", registry_type=2)], MutateThenFailStore)
        result = service.execute(service.plan_batch(["One", "Two"]))
        self.assertFalse(result.success)
        self.assertFalse(store.read("User", "A").exists)
        self.assertTrue(store.read("User", "B").same_content(existing("B", "", registry_type=2)))
        self.assertEqual(self.repo.snapshot()["successful_applications"], {})
        self.assertEqual(self.repo.snapshot()["active"], {})
        self.assertEqual(self.repo.pending(), [])

    def test_source_change_with_same_value_and_option_change_invalidate_preview(self):
        text = '''[One.Default]
A = {default_value="same",scope="User",mode="replace",editable=true,choices=[{label="same choice",value="same"}]}
[One.Other]
A = {default_value="other",scope="User",mode="replace",editable=true}
[Two.Default]
B = {default_value="b",scope="User",mode="replace",editable=true}
'''
        service, store = self.make(text)
        plan = service.plan_batch(["One", "Two"])
        spec = service.config.variables("One", "Default")[0]
        service.selections.choose(spec, "choice:0")
        with self.assertRaises(ConflictError):
            service.execute(plan)
        plan = service.plan_batch(["One", "Two"])
        service.select_option("One", "Other")
        with self.assertRaises(ConflictError):
            service.execute(plan)
        self.assertEqual(store.writes, [])

    def test_batch_deactivation_is_element_level_and_joint_switch_preserves_baseline(self):
        text = config(("One", "Old", {"Path": record("X", "+append")}),
                      ("One", "New", {"Path": record("M", "+append")}),
                      ("Two", "Old", {"Path": record("Y", "-append")}),
                      ("Two", "New", {"Path": record("N", "-append")}))
        service, store = self.make(text, [existing("Path", "A")])
        self.assertTrue(service.execute(service.plan_batch({"One": "Old", "Two": "Old"})).success)
        deactivation = service.plan_deactivate("One")
        self.assertTrue(deactivation.can_execute)
        self.assertEqual(deactivation.changes[0].after.value, "A;Y")
        switched = service.plan_batch({"One": "New", "Two": "New"})
        self.assertTrue(switched.can_execute, switched.conflicts)
        self.assertEqual(switched.changes[0].after.value, "M;A;N")
        self.assertTrue(service.execute(switched).success)
        self.assertEqual(store.read("User", "Path").value, "M;A;N")
        for app in ("One", "Two"):
            self.assertEqual(self.repo.snapshot()["successful_applications"][app]["option"], "New")

    def test_restore_retains_success_choices_for_restart_and_reapply(self):
        text = config(("One", "Default", {"A": record("new")}),
                      ("Two", "Default", {"B": record("new-b")}))
        service, store = self.make(text, [existing("A", "old")])
        spec = service.config.variables("One", "Default")[0]
        service.selections.edit(spec, "selected-custom")
        applied = service.execute(service.plan_batch(["One", "Two"]))
        self.assertTrue(service.execute(service.plan_restore(applied.operation_id)).success)
        self.assertEqual(store.read("User", "A").value, "old")
        self.assertFalse(store.read("User", "B").exists)
        restarted = EnvironmentService(parse_config(text), store, StateRepository(self.repo.directory))
        self.assertEqual(restarted.selections.get(spec).source, "custom")
        self.assertEqual(restarted.selections.get(spec).value, "selected-custom")
        self.assertEqual(restarted.state.snapshot()["active"], {})

    def test_app_histories_update_independently(self):
        text = config(("One", "Default", {"A": record("first")}),
                      ("Two", "Default", {"B": record("second")}))
        service, store = self.make(text)
        self.assertTrue(service.execute(service.plan_batch(["One", "Two"])).success)
        one = service.config.variables("One", "Default")[0]
        service.selections.edit(one, "new-first")
        self.assertTrue(service.execute(service.plan_batch(["One"])).success)
        restarted = EnvironmentService(parse_config(text), store, StateRepository(self.repo.directory))
        two = service.config.variables("Two", "Default")[0]
        self.assertEqual(restarted.selections.get(one).value, "new-first")
        self.assertEqual(restarted.selections.get(two).value, "second")


if __name__ == "__main__":
    unittest.main()
