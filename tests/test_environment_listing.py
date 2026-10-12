from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from envmanager.config import parse_config
from envmanager.models import RegistryState, ValidationError
from envmanager.persistence import StateRepository
from envmanager.service import EnvironmentService
from envmanager.store import MemoryStore, WindowsStore


class FakeKey:
    def __init__(self):
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True


class FakeRegistry:
    HKEY_CURRENT_USER = "HKCU"
    HKEY_LOCAL_MACHINE = "HKLM"
    KEY_READ = 0x20019

    def __init__(self, values=(), *, value_error=None, enum_error=None):
        self.values = tuple(values)
        self.value_error = value_error
        self.enum_error = enum_error
        self.opens = []
        self.key = FakeKey()

    def OpenKey(self, root, path, reserved, access):
        self.opens.append((root, path, reserved, access))
        if isinstance(self.value_error, FileNotFoundError):
            raise self.value_error
        if self.value_error:
            raise self.value_error
        return self.key

    def QueryInfoKey(self, key):
        return (0, len(self.values), 0)

    def EnumValue(self, key, index):
        if self.enum_error:
            raise self.enum_error
        return self.values[index]


def make_windows_store(registry):
    store = WindowsStore.__new__(WindowsStore)
    store.registry = registry
    return store


class EnvironmentListingTests(unittest.TestCase):
    def test_windows_store_enumerates_explicit_scope_and_preserves_raw_values(self):
        values = (
            ("Empty", "", 1),
            ("Path", "%ROOT%\\bin; ;X;X", 2),
            ("Plain", "a;b", 1),
        )
        registry = FakeRegistry(values)
        store = make_windows_store(registry)

        listed = store.list_values("User")

        self.assertEqual(
            listed,
            (
                RegistryState("Empty", "User", True, "", 1),
                RegistryState("Path", "User", True, "%ROOT%\\bin; ;X;X", 2),
                RegistryState("Plain", "User", True, "a;b", 1),
            ),
        )
        self.assertEqual(registry.opens, [("HKCU", "Environment", 0, registry.KEY_READ)])
        self.assertTrue(registry.key.closed)

    def test_windows_store_uses_system_location_and_forwards_read_errors(self):
        registry = FakeRegistry(())
        store = make_windows_store(registry)
        self.assertEqual(store.list_values("System"), ())
        self.assertEqual(registry.opens, [(
            "HKLM", r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
            0, registry.KEY_READ,
        )])

        failure = PermissionError("registry read denied")
        with self.assertRaisesRegex(PermissionError, "registry read denied"):
            make_windows_store(FakeRegistry(value_error=failure)).list_values("User")
        enum_failure = OSError("registry enumeration failed")
        with self.assertRaisesRegex(OSError, "registry enumeration failed"):
            make_windows_store(FakeRegistry((("A", "x", 1),), enum_error=enum_failure)).list_values("User")

    def test_windows_store_missing_key_is_empty_and_invalid_scope_rejected(self):
        self.assertEqual(
            make_windows_store(FakeRegistry(value_error=FileNotFoundError())).list_values("User"),
            (),
        )
        registry = FakeRegistry()
        with self.assertRaises(ValidationError):
            make_windows_store(registry).list_values("Process")
        self.assertEqual(registry.opens, [])

    def test_memory_store_lists_only_seeded_values_stably_and_returns_copies(self):
        store = MemoryStore((
            RegistryState("zeta", "User", True, "z", 1),
            RegistryState("Beta", "User", True, "b", 1),
            RegistryState("alpha", "System", True, "%ROOT%", 2),
            RegistryState("Absent", "User", False),
        ))

        first = store.list_values("User")
        self.assertEqual([item.name for item in first], ["Beta", "zeta"])
        self.assertEqual(first[0].value, "b")
        self.assertIsNot(first[0], store.values[("User", "BETA")])
        self.assertEqual(store.list_values("User")[0].value, "b")
        self.assertEqual([item.name for item in store.list_values("System")], ["alpha"])
        with self.assertRaises(ValidationError):
            store.list_values("Process")

    def test_service_delegates_read_only_without_changing_state_or_history(self):
        class RecordingStore(MemoryStore):
            def __init__(self, states=()):
                super().__init__(states)
                self.list_calls = []

            def list_values(self, scope):
                self.list_calls.append(scope)
                return super().list_values(scope)

        source = "[App.Default]\nA = { default_value = 'x', scope = 'User', mode = 'replace', editable = false }\n"
        store = RecordingStore((RegistryState("OutsideConfig", "System", True, "raw", 1),))
        with tempfile.TemporaryDirectory() as temporary:
            repository = StateRepository(Path(temporary) / "state")
            service = EnvironmentService(parse_config(source), store, repository)
            before_snapshot = repository.snapshot()
            before_history = repository.history()
            before_writes = list(store.writes)
            before_notifications = store.notifications

            self.assertEqual(service.list_environment("System"), (
                RegistryState("OutsideConfig", "System", True, "raw", 1),
            ))
            self.assertEqual(store.list_calls, ["System"])
            self.assertEqual(repository.snapshot(), before_snapshot)
            self.assertEqual(repository.history(), before_history)
            self.assertEqual(store.writes, before_writes)
            self.assertEqual(store.notifications, before_notifications)


if __name__ == "__main__":
    unittest.main()
