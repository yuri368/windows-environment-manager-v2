"""Pure boundary tests. No WindowsStore, elevation, GUI or host registry writes."""
from __future__ import annotations

import argparse
import ctypes
from pathlib import Path
import tempfile
import unittest

from envmanager.config import parse_config
from envmanager.models import ConflictError, RegistryState
from envmanager.store import MemoryStore
from tools.manual_windows_acceptance import (Evidence, ScopedAcceptanceStore, acceptance_config,
                                             arguments, canonical_test_id, names_for, NativeWindows)

GUID = "cc18e51d-60f0-4f30-b48b-73a71ae1945e"


class AcceptanceBoundaryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.names = names_for(GUID)
        self.evidence = Evidence(self.directory)

    def guarded(self, states=()):
        backend = MemoryStore(states)
        return ScopedAcceptanceStore(backend, self.names, self.evidence), backend

    def raw(self, value="owned"):
        return RegistryState(self.names["DUAL"], "System", True, value, 2)

    def test_guid_and_explicit_name_allowlist(self):
        self.assertEqual(canonical_test_id(GUID), GUID.replace("-", "").upper())
        self.assertTrue(all(name.startswith("ENV_MANAGER_VNEXT_QA_" + GUID.replace("-", "").upper() + "_")
                            for name in self.names.values()))
        for value in ("", "Path", "../../Path", "{ " + GUID + " }", "0" * 32, "z" * 32):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                canonical_test_id(value)
        store, backend = self.guarded()
        for scope, name in (("User", "Path"), ("System", "PATH"), ("Other", self.names["DUAL"]),
                            ("User", self.names["DUAL"] + "_EXTRA")):
            with self.subTest(scope=scope, name=name), self.assertRaises(ValueError):
                store.delete(scope, name)
        self.assertEqual(backend.writes, [])

    def test_collision_prevents_all_writes_and_cleanup_preserves_existing(self):
        original = self.raw("user pre-existing content")
        store, backend = self.guarded((original,))
        with self.assertRaises(ConflictError):
            store.preflight()
        self.assertEqual(store.expected, {})
        with self.assertRaises(ConflictError):
            store.write(self.raw("new"))
        residuals = store.cleanup()
        self.assertEqual(len(residuals), 1)
        self.assertTrue(backend.read(original.scope, original.name).same_content(original))
        self.assertEqual(backend.writes, [])

    def test_cleanup_deletes_only_confirmed_tool_state(self):
        store, backend = self.guarded()
        store.preflight()
        original = self.raw()
        store.write(original)
        self.assertEqual(store.cleanup(), [])
        self.assertFalse(backend.read(original.scope, original.name).exists)
        self.assertTrue((self.directory / "registry-journal.jsonl").is_file())
        self.assertTrue((self.directory / "residuals.json").is_file())

    def test_external_mutation_is_never_reclaimed_or_deleted(self):
        store, backend = self.guarded()
        store.preflight()
        store.write(self.raw())
        external = self.raw("external content")
        backend.write(external)
        with self.assertRaises(ConflictError):
            store.write(self.raw("replacement"))
        self.assertEqual(len(store.cleanup()), 1)
        self.assertTrue(backend.read(external.scope, external.name).same_content(external))

    def test_type_change_is_external_even_if_text_identical(self):
        store, backend = self.guarded()
        store.preflight()
        original = self.raw()
        store.write(original)
        backend.write(RegistryState(original.name, original.scope, True, original.value, 1))
        self.assertEqual(len(store.cleanup()), 1)
        self.assertEqual(backend.read(original.scope, original.name).registry_type, 1)

    def test_after_write_failure_retains_confirmed_state_for_service_rollback(self):
        store, backend = self.guarded()
        store.preflight()
        original = self.raw()
        store.fail_once = (original.scope, original.name)
        with self.assertRaises(OSError):
            store.write(original)
        self.assertTrue(store.expected[(original.scope, original.name)].same_content(original))
        store.delete(original.scope, original.name)
        self.assertFalse(backend.read(original.scope, original.name).exists)

    def test_config_has_only_guid_names_and_independent_both_scopes(self):
        configuration = parse_config(acceptance_config(self.names))
        records = [spec for app in configuration.apps() for option in configuration.options(app)
                   for spec in configuration.variables(app, option)]
        self.assertEqual(len(records), len(self.names) * 2)
        self.assertEqual({spec.name for spec in records}, set(self.names.values()))
        self.assertEqual({spec.scope for spec in records}, {"User", "System"})
        self.assertEqual(sum(spec.special for spec in records), 2)

    def test_arguments_require_existing_exe_absolute_paths_and_fresh_directory(self):
        executable = self.directory / "installed.exe"
        executable.write_bytes(b"pure-test-placeholder")
        work = self.directory / "acceptance"
        valid = ["--work-dir", str(work), "--exe", str(executable), "--test-id", GUID]
        parsed = arguments(valid)
        self.assertEqual(parsed.gui_timeout, 300)
        for modified in (valid + ["--gui-timeout", "0"], valid + ["--gui-timeout", "901"],
                         ["--work-dir", "relative", "--exe", str(executable), "--test-id", GUID],
                         ["--work-dir", str(work), "--exe", str(work / "missing.exe"), "--test-id", GUID]):
            with self.subTest(args=modified), self.assertRaises(SystemExit):
                arguments(modified)
        work.mkdir()
        (work / "prior-result.json").write_text("{}")
        with self.assertRaises(SystemExit):
            arguments(valid)


class NativeDpiBoundaryTests(unittest.TestCase):
    """Mock the native context calls; never enumerate or operate host windows."""
    def native(self):
        class ContextAPI:
            current = 77
            calls = []

            def SetThreadDpiAwarenessContext(self, value):
                value = value.value if isinstance(value, ctypes.c_void_p) else value
                old = self.current
                self.current = value
                self.calls.append(value)
                return old
        native = NativeWindows.__new__(NativeWindows)
        native.u = ContextAPI()
        return native

    def test_physical_context_restores_prior_context_on_success_and_exception(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                native = self.native()
                try:
                    with native.physical_coordinates():
                        self.assertEqual(native.u.current, ctypes.c_void_p(-4).value)
                        if fail:
                            raise RuntimeError("capture failure")
                except RuntimeError:
                    self.assertTrue(fail)
                self.assertEqual(native.u.current, 77)
                self.assertEqual(native.u.calls[-1], 77)

    def test_screenshot_wrapper_keeps_entire_capture_inside_physical_context(self):
        native = self.native()
        observations = []
        def capture(window, path):
            observations.append(native.u.current)
        native._screenshot_physical = capture
        native.screenshot({}, "unused.png")
        self.assertEqual(observations, [ctypes.c_void_p(-4).value])
        self.assertEqual(native.u.current, 77)


if __name__ == "__main__":
    unittest.main()
