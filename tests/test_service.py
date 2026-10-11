from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from envmanager.config import load_config, parse_config, save_config
from envmanager.models import ConflictError, PermissionDenied, RegistryState, ValidationError
from envmanager.persistence import StateRepository
from envmanager.service import EnvironmentService
from envmanager.store import MemoryStore


def record(value: str, mode: str = "replace", scope: str = "User", editable: bool = True) -> str:
    return ("{ default_value = " + repr(value) + ", scope = " + repr(scope) +
            ", mode = " + repr(mode) + ", editable = " + str(editable).lower() + " }")


def config(*sections: tuple[str, str, dict[str, str]]) -> str:
    return "\n".join("[" + app + "." + option + "]\n" +
                     "\n".join(name + " = " + definition for name, definition in variables.items())
                     for app, option, variables in sections) + "\n"


def existing(name: str, value: str, scope: str = "User", registry_type: int = 1) -> RegistryState:
    return RegistryState(name, scope, True, value, registry_type)


class MutateThenFailStore(MemoryStore):
    def __init__(self, states=(), fail_name="B", exception_type=RuntimeError):
        super().__init__(states)
        self.fail_name = fail_name
        self.exception_type = exception_type
        self.enabled = True

    def write(self, state):
        super().write(state)
        if self.enabled and state.name == self.fail_name:
            self.enabled = False
            raise self.exception_type("write mutated then failed")


class ExternalAfterWriteStore(MemoryStore):
    def __init__(self, states=()):
        super().__init__(states)
        self.triggered = False

    def write(self, state):
        super().write(state)
        if state.name == "A" and not self.triggered:
            self.triggered = True
            self.values[("User", "B")] = existing("B", "external")


class UncertainWriteStore(MemoryStore):
    def write(self, state):
        super().write(state)
        self.values[(state.scope, state.name.upper())] = existing(state.name, "external", state.scope)
        raise RuntimeError("write outcome changed externally")


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = StateRepository(self.root / "state")

    def service(self, text, states=(), store_type=MemoryStore):
        store = store_type(states)
        return EnvironmentService(parse_config(text), store, self.repo), store

    def test_no_change_commits_last_and_active_without_write(self):
        source = config(("App", "Default", {"A": record("same")}))
        path = self.root / "config.toml"
        save_config(path, source)
        service = EnvironmentService(load_config(path), MemoryStore([existing("A", "same")]), self.repo)
        option = service.config.variables("App", "Default")[0]
        plan = service.plan("App", "Default")
        self.assertFalse(plan.changes[0].changed)
        result = service.execute(plan)
        self.assertEqual(result.status, "no_change")
        self.assertEqual(service.store.writes, [])
        self.assertEqual(service.store.notifications, 0)
        snapshot = self.repo.snapshot()
        self.assertEqual(snapshot["last_values"][option.key], "same")
        self.assertEqual(snapshot["active"]["App"]["option"], "Default")
        self.assertEqual(path.read_text(encoding="utf-8"), source)

    def test_no_change_append_commits_input_not_full_result(self):
        source = config(("App", "Default", {"Path": record("X", "+append")}))
        service, store = self.service(source, [existing("Path", "X;A")])
        item = service.config.variables("App", "Default")[0]
        result = service.execute(service.plan("App", "Default"))
        self.assertEqual(result.status, "no_change")
        self.assertEqual(store.writes, [])
        self.assertEqual(self.repo.snapshot()["last_values"][item.key], "X")

    def test_apply_system_and_user_same_name_remain_independent(self):
        source = '''[App.Default]
Path = [
 {default_value='U',scope='User',mode='replace',editable=false},
 {default_value='S',scope='System',mode='replace',editable=false},
]
'''
        service, store = self.service(source, [existing("Path", "u0"), existing("Path", "s0", "System")])
        result = service.execute(service.plan("App", "Default"))
        self.assertEqual(result.status, "success")
        self.assertEqual(store.read("User", "Path").value, "U")
        self.assertEqual(store.read("System", "Path").value, "S")
        self.assertEqual(len(self.repo.snapshot()["last_values"]), 2)

    def test_permission_and_utf16_reject_before_journal(self):
        source = config(("App", "Default", {"A": record("x", scope="System")}))
        service, store = self.service(source)
        store.denied_scopes.add("System")
        with self.assertRaises(PermissionDenied):
            service.plan("App", "Default")
        self.assertEqual(self.repo.history(), [])
        store.denied_scopes.clear()
        item = service.config.variables("App", "Default")[0]
        service.selections.edit(item, "😀" * 16384)
        with self.assertRaises(ValidationError):
            service.plan("App", "Default")
        self.assertEqual(self.repo.history(), [])

    def test_plan_rejects_external_change_and_selection_change(self):
        source = config(("App", "Default", {"A": record("new")}))
        service, store = self.service(source, [existing("A", "old")])
        plan = service.plan("App", "Default")
        store.write(existing("A", "external"))
        with self.assertRaises(ConflictError):
            service.execute(plan)
        self.assertEqual(self.repo.history(), [])
        plan = service.plan("App", "Default")
        service.selections.edit(service.config.variables("App", "Default")[0], "changed")
        with self.assertRaises(ConflictError):
            service.execute(plan)
        self.assertEqual(store.read("User", "A").value, "external")

    def test_old_plan_invalid_after_reload(self):
        source = config(("App", "Default", {"A": record("new")}))
        service, store = self.service(source, [existing("A", "old")])
        plan = service.plan("App", "Default")
        service.reload(parse_config(config(("App", "Default", {"A": record("newer")}))))
        with self.assertRaises(ConflictError):
            service.execute(plan)
        self.assertEqual(store.read("User", "A").value, "old")

    def test_switch_undo_baseline_then_deactivate(self):
        source = config(("App", "Old", {"Path": record("X", "+append")}),
                        ("App", "New", {"Path": record("Y", "+append")}))
        service, store = self.service(source, [existing("Path", "A")])
        first = service.execute(service.plan("App", "Old"))
        self.assertEqual(first.status, "success")
        self.assertEqual(store.read("User", "Path").value, "X;A")
        switch = service.plan("App", "New")
        self.assertEqual(switch.action, "switch")
        self.assertTrue(switch.can_execute)
        self.assertEqual(switch.changes[0].after.value, "Y;A")
        self.assertEqual(service.execute(switch).status, "success")
        self.assertEqual(store.read("User", "Path").value, "Y;A")
        self.assertEqual(self.repo.snapshot()["active"]["App"]["option"], "New")
        self.assertEqual(service.execute(service.plan_deactivate("App")).status, "success")
        self.assertEqual(store.read("User", "Path").value, "A")
        old = service.config.variables("App", "Old")[0]
        self.assertEqual(self.repo.snapshot()["last_values"][old.key], "X")

    def test_switch_without_matching_old_preserves_external_snapshot_in_preview(self):
        source = config(("App", "Old", {"A": record("old")}),
                        ("App", "New", {"A": record("new")}))
        service, store = self.service(source, [existing("A", "base")])
        service.execute(service.plan("App", "Old"))
        store.write(existing("A", "external"))
        plan = service.plan("App", "New")
        self.assertTrue(plan.can_execute)
        self.assertEqual(plan.changes[0].before.value, "external")
        self.assertEqual(plan.metadata["switches"][0]["old_options"], [])
        self.assertEqual(store.read("User", "A").value, "external")

    def test_partial_old_is_not_implicitly_removed_or_restored_from_history(self):
        source = config(("App", "Old", {"A": record("a1"), "B": record("b1")}),
                        ("App", "New", {"A": record("a2")}))
        service, store = self.service(source, [existing("A", "a0"), existing("B", "b0")])
        self.assertTrue(service.execute(service.plan("App", "Old")).success)
        store.write(existing("B", "external"))
        plan = service.plan("App", "New")
        self.assertTrue(plan.can_execute)
        self.assertEqual(plan.metadata["switches"][0]["old_options"], [])
        self.assertEqual(tuple(c.before.name for c in plan.changes), ("A",))
        self.assertEqual(store.read("User", "B").value, "external")
        self.assertEqual(self.repo.snapshot()["active"]["App"]["option"], "Old")

    def test_shared_target_replace_must_block_destructive_overwrite(self):
        source = config(("First", "Default", {"Path": record("FIRST")}),
                        ("Second", "Default", {"Path": record("SECOND")}))
        service, store = self.service(source, [existing("Path", "base")])
        self.assertTrue(service.execute(service.plan("First", "Default")).success)
        plan = service.plan("Second", "Default")
        self.assertFalse(plan.can_execute, "另一活动方案的同一目标值不得仅提示后直接覆盖")
        self.assertEqual(store.read("User", "Path").value, "FIRST")

    def test_shared_target_prevents_undo(self):
        source = config(("First", "Default", {"Path": record("X", "+append")}),
                        ("Second", "Default", {"Path": record("Y", "+append")}))
        service, store = self.service(source, [existing("Path", "A")])
        service.execute(service.plan("First", "Default"))
        service.execute(service.plan("Second", "Default"))
        plan = service.plan_deactivate("First")
        self.assertFalse(plan.can_execute)
        self.assertEqual(store.read("User", "Path").value, "Y;X;A")

    def test_shared_ordinary_replace_elements_allow_safe_append(self):
        source = config(("First", "Default", {"Path": record("X", "replace")}),
                        ("Second", "Default", {"Path": record("Y", "+append")}))
        service, store = self.service(source, [existing("Path", "A")])
        self.assertTrue(service.execute(service.plan("First", "Default")).success)
        plan = service.plan("Second", "Default")
        self.assertTrue(plan.can_execute)
        self.assertEqual(plan.changes[0].after.value, "Y;X")
        self.assertEqual(store.read("User", "Path").value, "X")

    def test_write_mutates_then_raises_rolls_back_all_attempts(self):
        source = config(("App", "Default", {"A": record("a1"), "B": record("b1")}))
        store = MutateThenFailStore([existing("A", "a0"), existing("B", "b0")])
        service = EnvironmentService(parse_config(source), store, self.repo)
        result = service.execute(service.plan("App", "Default"))
        self.assertEqual(result.status, "rolled_back")
        self.assertEqual((store.read("User", "A").value, store.read("User", "B").value), ("a0", "b0"))
        self.assertEqual(self.repo.snapshot()["last_values"], {})
        self.assertEqual(self.repo.snapshot()["active"], {})
        journal = self.repo.operation(result.operation_id)
        self.assertEqual(journal["attempted"], [0, 1])
        self.assertEqual(journal["status"], "rolled_back")
        self.assertEqual(service.pending(), [])

    def test_external_change_during_multi_write_rolls_back_only_own_target(self):
        source = config(("App", "Default", {"A": record("a1"), "B": record("b1")}))
        store = ExternalAfterWriteStore([existing("A", "a0"), existing("B", "b0")])
        service = EnvironmentService(parse_config(source), store, self.repo)
        result = service.execute(service.plan("App", "Default"))
        self.assertEqual(result.status, "rolled_back")
        self.assertEqual(store.read("User", "A").value, "a0")
        self.assertEqual(store.read("User", "B").value, "external")
        self.assertEqual(self.repo.snapshot()["active"], {})

    def test_uncertain_write_kept_pending_for_manual_recovery(self):
        source = config(("App", "Default", {"A": record("a1")}))
        store = UncertainWriteStore([existing("A", "a0")])
        service = EnvironmentService(parse_config(source), store, self.repo)
        result = service.execute(service.plan("App", "Default"))
        self.assertEqual(result.status, "rollback_incomplete")
        self.assertEqual(store.read("User", "A").value, "external")
        self.assertEqual(len(service.pending()), 1)
        recovery = service.plan_recover(result.operation_id)
        self.assertFalse(recovery.can_execute)

    def test_crash_after_mutating_write_recovered_from_pending_journal(self):
        source = config(("App", "Default", {"A": record("a1")}))
        store = MutateThenFailStore([existing("A", "a0")], fail_name="A", exception_type=KeyboardInterrupt)
        service = EnvironmentService(parse_config(source), store, self.repo)
        plan = service.plan("App", "Default")
        with self.assertRaises(KeyboardInterrupt):
            service.execute(plan)
        self.assertEqual(store.read("User", "A").value, "a1")
        self.assertEqual(self.repo.pending()[0]["attempted"], [0])
        restarted = EnvironmentService(parse_config(source), store, StateRepository(self.root / "state"))
        with self.assertRaises(ConflictError):
            restarted.execute(restarted.plan("App", "Default"))
        recovery = restarted.plan_recover(plan.id)
        self.assertTrue(recovery.can_execute)
        self.assertTrue(restarted.execute(recovery).success)
        self.assertEqual(store.read("User", "A").value, "a0")
        self.assertEqual(restarted.pending(), [])
        self.assertEqual(restarted.state.operation(plan.id)["status"], "recovered")

    def test_failed_recovery_creates_second_pending_then_recovers_in_order(self):
        source = config(("App", "Default", {"A": record("a1"), "B": record("b1")}))
        store = MutateThenFailStore([existing("A", "a0"), existing("B", "b0")],
                                    fail_name="B", exception_type=KeyboardInterrupt)
        service = EnvironmentService(parse_config(source), store, self.repo)
        original = service.plan("App", "Default")
        with self.assertRaises(KeyboardInterrupt):
            service.execute(original)
        self.assertEqual((store.read("User", "A").value, store.read("User", "B").value),
                         ("a1", "b1"))

        def uncertain_recovery_write(state):
            MemoryStore.write(store, state)
            if state.name == "B" and state.value == "b0":
                store.values[("User", "B")] = existing("B", "external")
                raise RuntimeError("external modification during recovery")

        store.write = uncertain_recovery_write
        restarted = EnvironmentService(parse_config(source), store, StateRepository(self.root / "state"))
        recovery = restarted.plan_recover(original.id)
        self.assertTrue(recovery.can_execute)
        incomplete = restarted.execute(recovery)
        self.assertEqual(incomplete.status, "rollback_incomplete")
        self.assertEqual(len(restarted.pending()), 2)
        self.assertEqual(store.read("User", "A").value, "a1")
        self.assertEqual(store.read("User", "B").value, "external")
        self.assertFalse(restarted.plan_recover(incomplete.operation_id).can_execute)

        store.write = lambda state: MemoryStore.write(store, state)
        store.write(existing("B", "b1"))  # User resolves the external conflict to the known write state.
        self.assertTrue(restarted.execute(restarted.plan_recover(incomplete.operation_id)).success)
        self.assertEqual(len(restarted.pending()), 1)
        self.assertTrue(restarted.execute(restarted.plan_recover(original.id)).success)
        self.assertEqual(restarted.pending(), [])
        self.assertEqual((store.read("User", "A").value, store.read("User", "B").value),
                         ("a0", "b0"))

    def test_prepared_journal_failure_never_writes(self):
        source = config(("App", "Default", {"A": record("a1")}))
        service, store = self.service(source, [existing("A", "a0")])
        plan = service.plan("App", "Default")
        def fail_save(operation):
            raise OSError("disk full")
        self.repo.save_operation = fail_save
        with self.assertRaises(OSError):
            service.execute(plan)
        self.assertEqual(store.read("User", "A").value, "a0")
        self.assertEqual(store.writes, [])

    def test_state_commit_failure_rolls_back_and_does_not_commit_last(self):
        source = config(("App", "Default", {"A": record("a1")}))
        service, store = self.service(source, [existing("A", "a0")])
        def fail_commit(*args, **kwargs):
            raise OSError("disk full")
        self.repo.commit = fail_commit
        result = service.execute(service.plan("App", "Default"))
        self.assertEqual(result.status, "rolled_back")
        self.assertEqual(store.read("User", "A").value, "a0")
        self.assertEqual(self.repo.snapshot()["last_values"], {})

    def test_commit_return_error_after_durable_commit_keeps_registry_consistent(self):
        source = config(("App", "Default", {"A": record("a1")}))
        service, store = self.service(source, [existing("A", "a0")])
        original_commit = self.repo.commit

        def committed_then_failed(*args, **kwargs):
            original_commit(*args, **kwargs)
            raise OSError("post-commit acknowledgement failed")

        self.repo.commit = committed_then_failed
        result = service.execute(service.plan("App", "Default"))
        snapshot = self.repo.snapshot()
        self.assertEqual(snapshot["active"]["App"]["option"], "Default")
        self.assertEqual(store.read("User", "A").value, "a1")
        self.assertTrue(result.success)

    def test_commit_outcome_unknown_preserves_verified_registry_for_recovery(self):
        source = config(("App", "Default", {"A": record("a1")}))
        service, store = self.service(source, [existing("A", "a0")])
        original_snapshot = self.repo.snapshot
        unreadable = False

        def unknown_commit(*args, **kwargs):
            nonlocal unreadable
            unreadable = True
            raise OSError("commit result unavailable")

        def temporarily_unreadable_snapshot():
            if unreadable:
                raise OSError("database temporarily unavailable")
            return original_snapshot()

        self.repo.commit = unknown_commit
        self.repo.snapshot = temporarily_unreadable_snapshot
        plan = service.plan("App", "Default")
        result = service.execute(plan)
        self.assertEqual(result.status, "state_commit_failed")
        self.assertEqual(store.read("User", "A").value, "a1")
        self.repo.snapshot = original_snapshot
        self.assertEqual(len(self.repo.pending()), 1)
        restarted = EnvironmentService(parse_config(source), store, StateRepository(self.root / "state"))
        self.assertTrue(restarted.execute(restarted.plan_recover(plan.id)).success)
        self.assertEqual(store.read("User", "A").value, "a0")

    def test_restore_restores_registry_and_retains_successful_preferences(self):
        source = config(("App", "Default", {"A": record("a1")}))
        service, store = self.service(source, [existing("A", "a0")])
        applied = service.execute(service.plan("App", "Default"))
        self.assertTrue(applied.success)
        restore = service.plan_restore(applied.operation_id)
        self.assertTrue(restore.can_execute)
        self.assertTrue(service.execute(restore).success)
        self.assertEqual(store.read("User", "A").value, "a0")
        snapshot = self.repo.snapshot()
        self.assertEqual(snapshot["active"], {})
        item = service.config.variables("App", "Default")[0]
        self.assertEqual(snapshot["last_values"], {item.key: "a1"})
        self.assertEqual(snapshot["successful_applications"]["App"]["option"], "Default")
        self.assertEqual(self.repo.operation(applied.operation_id)["status"], "restored")

    def test_restore_blocks_external_change(self):
        source = config(("App", "Default", {"A": record("a1")}))
        service, store = self.service(source, [existing("A", "a0")])
        applied = service.execute(service.plan("App", "Default"))
        store.write(existing("A", "external"))
        restore = service.plan_restore(applied.operation_id)
        self.assertFalse(restore.can_execute)
        with self.assertRaises(ConflictError):
            service.execute(restore)
        self.assertEqual(store.read("User", "A").value, "external")

    def test_reload_changed_definition_discards_old_custom_draft(self):
        source = config(("App", "Default", {"A": record("old")}))
        service, _ = self.service(source)
        previous = service.config.variables("App", "Default")[0]
        service.selections.edit(previous, "my draft")
        changed = parse_config(config(("App", "Default", {"A": record("new")})))
        service.reload(changed)
        current = changed.variables("App", "Default")[0]
        self.assertEqual((service.selections.get(current).source, service.selections.get(current).value),
                         ("default", "new"))


if __name__ == "__main__":
    unittest.main()
