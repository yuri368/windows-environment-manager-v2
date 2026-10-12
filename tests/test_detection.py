from __future__ import annotations

from dataclasses import FrozenInstanceError
import unittest

from envmanager.detection import recompute_for_selection, refresh_targets, scan_all
from envmanager.matching import SchemeStatus, TargetKey, VariableMatchStatus
from envmanager.models import Configuration, RegistryState, VariableSpec
from envmanager.store import MemoryStore


def record(app, option, value="A", scope="User", name="Path", mode="append"):
    return VariableSpec(app, option, name, scope, mode, True, value)


def config_for(*records):
    applications = {}
    for item in records:
        options = applications.setdefault(item.app, {})
        options[item.option] = (*options.get(item.option, ()), item)
    return Configuration(applications, "test-digest")


class CountingStore(MemoryStore):
    def __init__(self, states=()):
        super().__init__(states)
        self.reads = []
        self.failures = {}

    def read(self, scope, name):
        self.reads.append((scope, name))
        error = self.failures.get((scope, name.upper()))
        if error:
            raise error
        return super().read(scope, name)

    def check_permission(self, scope):
        raise AssertionError("Read-only detection must not request write permission")

    def write(self, state):
        raise AssertionError("Read-only detection must not write")

    def delete(self, scope, name):
        raise AssertionError("Read-only detection must not delete")

    def notify(self):
        raise AssertionError("Read-only detection must not notify")


class DetectionTests(unittest.TestCase):
    def test_all_apps_all_schemes_with_one_read_per_target(self):
        records = tuple(record(app, option, "A", name="pAtH" if option == "One" else "PATH")
                        for app in ("Java", "Maven", "Scala") for option in ("One", "Two"))
        config = config_for(*records)
        store = CountingStore((RegistryState("Path", "User", True, "Extra; A ", 2),))
        snapshot = scan_all(config, {}, store, generation=8, selection_revision=4)
        self.assertEqual(len(snapshot.schemes), 6)
        self.assertTrue(all(result.status is SchemeStatus.COMPLETE for result in snapshot.schemes.values()))
        self.assertEqual(store.reads, [("User", "pAtH")])
        self.assertEqual(store.writes, [])
        self.assertEqual(store.notifications, 0)
        self.assertEqual((snapshot.generation, snapshot.selection_revision, snapshot.config_digest),
                         (8, 4, "test-digest"))
        self.assertIsNotNone(snapshot.scanned_at.tzinfo)

    def test_every_scheme_uses_its_own_selection_and_explicit_empty(self):
        first = record("App", "First", "DEFAULT", mode="replace")
        second = record("App", "Second", "DEFAULT", mode="replace")
        third = record("App", "Empty", "DEFAULT", name="EMPTY", mode="replace")
        store = CountingStore((RegistryState("Path", "User", True, "Chosen", 1),
                               RegistryState("EMPTY", "User", True, "", 1)))
        snapshot = scan_all(config_for(first, second, third),
                            {first.key: "Chosen", second.key: "Other", third.key: ""}, store)
        self.assertIs(snapshot.schemes[("App", "First")].status, SchemeStatus.COMPLETE)
        self.assertIs(snapshot.schemes[("App", "Second")].status, SchemeStatus.PARTIAL)
        self.assertIs(snapshot.schemes[("App", "Empty")].status, SchemeStatus.COMPLETE)
        self.assertEqual(snapshot.schemes[("App", "Empty")].variables[0].selected_value, "")

    def test_scope_isolation(self):
        user = record("App", "User", "A")
        system = record("App", "System", "A", scope="System")
        store = CountingStore((RegistryState("Path", "User", True, "A", 1),))
        snapshot = scan_all(config_for(user, system), {}, store)
        self.assertIs(snapshot.schemes[("App", "User")].status, SchemeStatus.COMPLETE)
        self.assertIs(snapshot.schemes[("App", "System")].status, SchemeStatus.UNCONFIGURED)
        self.assertEqual(len(store.reads), 2)

    def test_read_failure_fans_out_without_hiding_successful_variables(self):
        bad1 = record("Java", "One")
        bad2 = record("Scala", "Two")
        good = record("Java", "One", "JDK", name="JAVA_HOME", mode="replace")
        independent = record("Maven", "One", "Maven", name="MAVEN_HOME", mode="replace")
        store = CountingStore((RegistryState("JAVA_HOME", "User", True, "JDK", 1),
                               RegistryState("MAVEN_HOME", "User", True, "Maven", 1)))
        store.failures[("User", "PATH")] = PermissionError("Access denied")
        snapshot = scan_all(config_for(bad1, bad2, good, independent), {}, store)
        self.assertEqual(store.reads.count(("User", "Path")), 1)
        self.assertEqual(len(snapshot.errors), 2)
        for key in (("Java", "One"), ("Scala", "Two")):
            self.assertIsNone(snapshot.schemes[key].status)
            self.assertFalse(snapshot.schemes[key].available)
        self.assertEqual(snapshot.schemes[("Java", "One")].matched_count, 1)
        self.assertIs(snapshot.schemes[("Maven", "One")].status, SchemeStatus.COMPLETE)
        self.assertNotIn(TargetKey("User", "PATH"), snapshot.targets)
        failed = snapshot.schemes[("Scala", "Two")].variables[0]
        self.assertIsNone(failed.windows_state)
        self.assertIsNone(failed.status)
        self.assertEqual(failed.error.kind, "read")

    def test_invalid_input_does_not_make_other_consumer_unavailable(self):
        first = record("App", "Invalid")
        second = record("App", "Valid")
        store = CountingStore((RegistryState("Path", "User", True, "A", 1),))
        snapshot = scan_all(config_for(first, second), {first.key: "A\0"}, store)
        self.assertIsNone(snapshot.schemes[("App", "Invalid")].status)
        self.assertEqual(snapshot.errors[0].kind, "validation")
        self.assertIs(snapshot.schemes[("App", "Valid")].status, SchemeStatus.COMPLETE)
        self.assertEqual(len(store.reads), 1)

    def test_unsupported_registry_type_is_read_error(self):
        store = CountingStore((RegistryState("Path", "User", True, "A", 3),))
        snapshot = scan_all(config_for(record("App", "One")), {}, store)
        self.assertIsNone(snapshot.schemes[("App", "One")].status)
        self.assertEqual(snapshot.errors[0].kind, "read")
        self.assertEqual(len(snapshot.read_errors), 1)

    def test_empty_scheme_is_independent_configuration_error(self):
        snapshot = scan_all(Configuration({"App": {"Empty": ()}}, "digest"), {}, CountingStore())
        self.assertIsNone(snapshot.schemes[("App", "Empty")].status)
        self.assertEqual(snapshot.errors[0].kind, "config")

    def test_snapshot_nested_mappings_are_immutable(self):
        store = CountingStore((RegistryState("Path", "User", True, "A", 1),))
        snapshot = scan_all(config_for(record("App", "One")), {}, store)
        with self.assertRaises(FrozenInstanceError):
            snapshot.generation = 100
        for mapping in (snapshot.targets, snapshot.schemes, snapshot.read_errors):
            with self.assertRaises(TypeError):
                mapping["unexpected"] = None

    def test_full_refresh_observes_external_change_all_consumers(self):
        config = config_for(record("App", "One"), record("App", "Two"))
        store = CountingStore((RegistryState("Path", "User", True, "A", 1),))
        before = scan_all(config, {}, store, generation=1)
        store.values[("User", "PATH")] = RegistryState("Path", "User", True, "B", 2)
        after = scan_all(config, {}, store, generation=2)
        self.assertTrue(all(s.status is SchemeStatus.COMPLETE for s in before.schemes.values()))
        self.assertTrue(all(s.status is SchemeStatus.PARTIAL for s in after.schemes.values()))
        self.assertEqual(len(store.reads), 2)
        self.assertEqual(before.targets[TargetKey("User", "PATH")].value, "A")

    def test_selection_recompute_uses_cache_without_reading_or_writing(self):
        first = record("App", "One")
        second = record("App", "Two")
        config = config_for(first, second)
        store = CountingStore((RegistryState("Path", "User", True, "A", 1),))
        before = scan_all(config, {}, store, generation=3, selection_revision=1)
        after = recompute_for_selection(config, {first.key: "B"}, before, selection_revision=2)
        self.assertIs(after.schemes[("App", "One")].status, SchemeStatus.PARTIAL)
        self.assertIs(after.schemes[("App", "Two")].status, SchemeStatus.COMPLETE)
        self.assertEqual(len(store.reads), 1)
        self.assertEqual((after.generation, after.selection_revision), (4, 2))
        self.assertEqual(before.targets, after.targets)

    def test_selection_recompute_retains_read_failure(self):
        item = record("App", "One")
        config = config_for(item)
        store = CountingStore()
        store.failures[("User", "PATH")] = OSError("Unavailable")
        before = scan_all(config, {}, store)
        after = recompute_for_selection(config, {item.key: "B"}, before)
        self.assertIsNone(after.schemes[("App", "One")].status)
        self.assertEqual(after.errors[0].message, "Unavailable")
        self.assertEqual(len(store.reads), 1)

    def test_target_refresh_retries_failed_target_and_deduplicates(self):
        config = config_for(record("App", "One"), record("App", "Two"),
                            record("Other", "One", "X", name="OTHER"))
        store = CountingStore((RegistryState("Path", "User", True, "A", 1),))
        store.failures[("User", "PATH")] = OSError("Unavailable")
        before = scan_all(config, {}, store)
        store.failures.clear()
        path = TargetKey("User", "PATH")
        after = refresh_targets(config, {}, store, before, (path, path), generation=7)
        self.assertIs(after.schemes[("App", "One")].status, SchemeStatus.COMPLETE)
        self.assertIs(after.schemes[("App", "Two")].status, SchemeStatus.COMPLETE)
        self.assertIs(after.schemes[("Other", "One")].status, SchemeStatus.UNCONFIGURED)
        self.assertEqual(len(store.reads), 3)
        self.assertEqual(after.errors, ())
        self.assertEqual(dict(after.read_errors), {})
        self.assertEqual(after.generation, 7)

    def test_target_refresh_failure_discards_stale_success(self):
        config = config_for(record("App", "One"))
        store = CountingStore((RegistryState("Path", "User", True, "A", 1),))
        before = scan_all(config, {}, store)
        store.failures[("User", "PATH")] = OSError("Unavailable")
        path = TargetKey("User", "PATH")
        after = refresh_targets(config, {}, store, before, (path,))
        self.assertNotIn(path, after.targets)
        self.assertIsNone(after.schemes[("App", "One")].status)

    def test_cached_recompute_requires_same_config_digest(self):
        config = config_for(record("App", "One"))
        store = CountingStore()
        before = scan_all(config, {}, store)
        changed = Configuration(config.applications, "different")
        with self.assertRaises(ValueError):
            recompute_for_selection(changed, {}, before)
        with self.assertRaises(ValueError):
            refresh_targets(changed, {}, store, before, ())


if __name__ == "__main__":
    unittest.main()
