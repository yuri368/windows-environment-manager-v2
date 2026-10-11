from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from envmanager.config import parse_config
from envmanager.matching import SchemeStatus, TargetKey
from envmanager.models import ConflictError, RegistryState
from envmanager.persistence import StateRepository
from envmanager.service import EnvironmentService
from envmanager.store import MemoryStore


def config(*sections):
    return parse_config("\n".join(f"[{app}.{option}]\n" + "\n".join(
        f"{name}={{default_value={value!r},scope={scope!r},mode={mode!r},editable=true}}"
        for name, value, mode, scope in variables)
        for app, option, variables in sections))


def raw(name, value, scope="User", kind=1):
    return RegistryState(name, scope, True, value, kind)


class ReadOnlyStore(MemoryStore):
    def __init__(self, states=()):
        super().__init__(states)
        self.reads = []

    def read(self, scope, name):
        self.reads.append((scope, name.upper()))
        return super().read(scope, name)

    def check_permission(self, scope):
        raise AssertionError("Detection must never request write permissions")


class DetectionServiceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = StateRepository(Path(temp.name))

    def make(self, configuration, states=(), store_type=ReadOnlyStore):
        store = store_type(states)
        return EnvironmentService(configuration, store, self.repo), store

    def test_all_options_scope_separated_and_each_target_read_once(self):
        configuration = config(
            ("App", "Old", [("Path", "A;B", "+append", "User")]),
            ("App", "New", [("PATH", "B", "append", "User")]),
            ("Other", "Default", [("Path", "A", "-append", "System")]))
        service, store = self.make(configuration, [raw("Path", "B;BASE;A")])
        snapshot = service.detection_snapshot()
        self.assertEqual(store.reads, [("User", "PATH"), ("System", "PATH")])
        self.assertEqual(snapshot.schemes[("App", "Old")].status, SchemeStatus.COMPLETE)
        self.assertEqual(snapshot.schemes[("App", "New")].status, SchemeStatus.COMPLETE)
        self.assertEqual(snapshot.schemes[("Other", "Default")].status, SchemeStatus.UNCONFIGURED)
        self.assertEqual(self.repo.history(), [])
        self.assertEqual(self.repo.snapshot()["active"], {})
        self.assertEqual(store.writes, [])

    def test_same_generation_cached_immutable_and_source_edit_reuses_windows(self):
        configuration = config(("App", "Default", [("VALUE", "old", "replace", "User")]))
        service, store = self.make(configuration, [raw("VALUE", "chosen")])
        first = service.detection_snapshot()
        self.assertIs(first, service.detection_snapshot())
        with self.assertRaises(TypeError):
            first.targets[TargetKey("User", "VALUE")] = raw("VALUE", "changed")
        spec = configuration.variables("App", "Default")[0]
        service.selections.edit(spec, "chosen")
        second = service.detection_snapshot()
        self.assertGreater(second.generation, first.generation)
        self.assertEqual(len(store.reads), 1)
        self.assertEqual(second.schemes[("App", "Default")].status, SchemeStatus.COMPLETE)
        self.assertEqual(first.schemes[("App", "Default")].status, SchemeStatus.PARTIAL)

    def test_force_refresh_observes_external_edit_without_logging(self):
        configuration = config(("App", "Default", [("VALUE", "match", "replace", "User")]))
        service, store = self.make(configuration)
        first = service.detection_snapshot()
        store.values[("User", "VALUE")] = raw("VALUE", "match")
        self.assertIs(service.detection_snapshot(), first)
        refreshed = service.scan_all()
        self.assertGreater(refreshed.generation, first.generation)
        self.assertEqual(refreshed.schemes[("App", "Default")].status, SchemeStatus.COMPLETE)
        self.assertEqual(self.repo.history(), [])

    def test_unseen_option_uses_its_saved_current_input_and_explicit_empty(self):
        configuration = config(("App", "One", [("A", "a", "replace", "User")]),
                               ("App", "Two", [("B", "default", "replace", "User")]))
        second = configuration.variables("App", "Two")[0]
        snapshot = self.repo.snapshot()
        snapshot["last_values"][second.key] = ""
        operation = {"id": "seed", "created_at": "2000", "app": "App", "option": "Two",
                     "action": "apply", "status": "no_change", "changes": []}
        self.repo.save_operation(operation)
        self.repo.commit(0, snapshot, operation)
        service, store = self.make(configuration, [raw("B", "")])
        detected = service.detection_snapshot()
        match = detected.schemes[("App", "Two")].variables[0]
        self.assertEqual(match.selected_value, "")
        self.assertEqual(detected.schemes[("App", "Two")].status, SchemeStatus.COMPLETE)
        self.assertEqual(service.selected_option("App"), "One")
        self.assertEqual(store.writes, [])

    def test_reload_invalidates_raw_snapshot_and_does_not_change_preferences(self):
        original = config(("App", "Default", [("VALUE", "old", "replace", "User")]))
        service, store = self.make(original, [raw("VALUE", "new")])
        first = service.detection_snapshot()
        service.reload(config(("App", "Default", [("VALUE", "new", "replace", "User")]),
                              ("Other", "Default", [("EXTRA", "x", "replace", "User")])))
        current = service.detection_snapshot()
        self.assertGreater(current.generation, first.generation)
        self.assertEqual(len(store.reads), 3)
        self.assertEqual(current.schemes[("App", "Default")].status, SchemeStatus.COMPLETE)
        self.assertEqual(self.repo.snapshot()["last_values"], {})

    def test_read_error_separate_from_business_status_with_other_schemes_available(self):
        class FailingReadStore(ReadOnlyStore):
            def read(self, scope, name):
                if name.upper() == "BROKEN":
                    self.reads.append((scope, name.upper()))
                    raise PermissionError("denied read")
                return super().read(scope, name)
        configuration = config(("App", "Default", [("BROKEN", "x", "replace", "User")]),
                               ("Other", "Default", [("GOOD", "y", "replace", "User")]))
        service, _ = self.make(configuration, [raw("GOOD", "y")], FailingReadStore)
        snapshot = service.detection_snapshot()
        self.assertIsNone(snapshot.schemes[("App", "Default")].status)
        self.assertEqual(snapshot.schemes[("App", "Default")].errors[0].kind, "read")
        self.assertEqual(snapshot.schemes[("Other", "Default")].status, SchemeStatus.COMPLETE)

    def test_prepared_scan_reads_only_in_worker_phase_and_reconciles_ui_edit(self):
        configuration = config(("App", "Default", [("VALUE", "old", "replace", "User")]))
        service, store = self.make(configuration, [raw("VALUE", "chosen")])
        request = service.prepare_detection_scan()
        self.assertEqual(store.reads, [])
        snapshot = service.run_detection_scan(request)
        self.assertEqual(store.reads, [("User", "VALUE")])
        spec = configuration.variables("App", "Default")[0]
        service.selections.edit(spec, "chosen")
        accepted = service.accept_detection_scan(snapshot)
        self.assertEqual(len(store.reads), 1)
        self.assertEqual(accepted.selection_revision, service.selections.revision)
        self.assertEqual(accepted.schemes[("App", "Default")].status, SchemeStatus.COMPLETE)
        self.assertIs(service.detection_snapshot(), accepted)

    def test_prepared_scan_rejects_result_after_configuration_reload(self):
        configuration = config(("App", "Default", [("VALUE", "old", "replace", "User")]))
        service, _ = self.make(configuration, [raw("VALUE", "old")])
        request = service.prepare_detection_scan()
        snapshot = service.run_detection_scan(request)
        service.reload(config(("App", "Default", [("VALUE", "new", "replace", "User")])))
        with self.assertRaisesRegex(ConflictError, "配置已变化"):
            service.accept_detection_scan(snapshot)


if __name__ == "__main__":
    unittest.main()
