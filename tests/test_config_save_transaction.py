"""Configuration save faults use only temporary files and MemoryStore."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from envmanager.config import load_config, save_config
from envmanager.config_editing import ConfigEditingSession, _save_lock
from envmanager.models import ConflictError, RegistryState
from envmanager.persistence import StateRepository
from envmanager.removal import RemovalRequest, RemovalScope
from envmanager.service import EnvironmentService
from envmanager.store import MemoryStore


SOURCE = '''[App.Old]
HOME = { default_value = "old", scope = "User", mode = "replace", editable = true, choices = [{label = "choice", value = "candidate"}] }
[App.New]
HOME = { default_value = "new", scope = "User", mode = "replace", editable = true }
'''


class ConfigurationSaveTransactionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "env.toml"
        self.path.write_bytes(SOURCE.encode("utf-8"))
        self.state = StateRepository(self.root / "state")
        self.store = MemoryStore((RegistryState("HOME", "User", True, "new", 1),))
        self.service = EnvironmentService(load_config(self.path), self.store, self.state)
        self.session = ConfigEditingSession(self.path)
        self.old = self.service.config.variables("App", "Old")[0]
        self.new = self.service.config.variables("App", "New")[0]
        self.service.select_option("App", "New")
        self.service.selections.edit(self.old, "old-custom-draft")
        self.service.selections.choose(self.old, "choice:0")
        self.service.selections.edit(self.new, "new-custom-draft")
        self.service.selections.choose(self.new, "default")
        self.plan = self.service.plan("App", "New")
        self.switch = self.service.prepare_scheme_switch("App", "New")
        self.removal = self.service.prepare_remove(
            RemovalRequest(RemovalScope.SCHEME, frozenset(("App",))))
        self.service._initialization_warnings["App"] = "existing initialization warning"
        self.before = self.runtime()
        self.history_before = self.state.history()
        self.state_before = self.state.snapshot()

    def runtime(self):
        service = self.service
        return {
            "config": service.config,
            "selections": deepcopy(vars(service.selections)),
            "options": dict(service._options),
            "warnings": dict(service._initialization_warnings),
            "option_revision": service._option_revision,
            "detection": service._detection,
            "generation": service._detection_generation,
            "plans": dict(service._issued_plans),
            "removal_drafts": dict(service._removal_drafts),
            "removal_snapshots": deepcopy(service._removal_snapshots),
            "switch_drafts": dict(service._switch_drafts),
        }

    def assert_no_writes(self):
        self.assertEqual(self.store.writes, [])
        self.assertEqual(self.store.notifications, 0)
        self.assertEqual(self.state.snapshot(), self.state_before)
        self.assertEqual(self.state.history(), self.history_before)

    def test_delete_selected_scheme_save_failure_restores_entire_runtime(self):
        self.session.delete_group("App", "New")
        draft = self.session.text
        with patch("envmanager.config_editing.save_config", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.session.save(self.service)
        self.assertEqual(self.service.selected_option("App"), "New")
        self.assertEqual(self.runtime(), self.before)
        self.assertEqual(self.path.read_bytes(), SOURCE.encode("utf-8"))
        self.assertEqual(self.session.text, draft)
        self.assertTrue(self.session.dirty)
        self.assertTrue(self.session.can_undo)
        # The same frozen drafts remain valid; validation does not execute them.
        self.service.finalize_remove(self.removal.id)
        self.service.finalize_scheme_switch(self.switch.id)
        self.assert_no_writes()

    def test_rename_and_add_scheme_save_failure_preserves_sources_and_custom_drafts(self):
        for modified in (
            SOURCE.replace("[App.New]", "[App.Renamed]"),
            SOURCE + '\n[App.Extra]\nEXTRA = { default_value = "extra", scope = "User", mode = "replace", editable = true }\n',
        ):
            with self.subTest(modified=modified):
                self.session.replace_text(modified)
                with patch("envmanager.config_editing.save_config", side_effect=PermissionError("read only")):
                    with self.assertRaises(PermissionError):
                        self.session.save(self.service)
                self.assertEqual(self.runtime(), self.before)
                self.assertEqual(self.service.selections.get(self.old).source, "choice:0")
                self.assertEqual(self.service.selections.get(self.old).custom_draft, "old-custom-draft")
                self.assertEqual(self.service.selections.get(self.new).custom_draft, "new-custom-draft")
                self.assertTrue(self.session.dirty)
                self.assert_no_writes()

    def test_reload_mid_initialization_failure_is_atomic_without_editor(self):
        self.session.delete_group("App", "New")
        initialize = self.service._initialize_options

        def failed_initialization(snapshot):
            initialize(snapshot)
            self.service._initialization_warnings.clear()
            self.service._detection_generation += 10
            raise OSError("state unavailable")

        with patch.object(self.service, "_initialize_options", side_effect=failed_initialization):
            with self.assertRaises(OSError):
                self.service.reload(self.session.validate())
        self.assertEqual(self.runtime(), self.before)
        self.assertEqual(self.path.read_bytes(), SOURCE.encode("utf-8"))
        self.assert_no_writes()

    def test_editor_reload_mid_failure_never_calls_writer_and_preserves_redo(self):
        self.session.delete_group("App", "New")
        self.session.replace_text(self.session.text + "# later edit\n")
        self.session.undo()
        draft = self.session.text
        initialize = self.service._initialize_options

        def failure(snapshot):
            initialize(snapshot)
            raise OSError("initialization failed")

        with patch.object(self.service, "_initialize_options", side_effect=failure), \
                patch("envmanager.config_editing.save_config") as writer:
            with self.assertRaises(OSError):
                self.session.save(self.service)
        writer.assert_not_called()
        self.assertEqual(self.runtime(), self.before)
        self.assertEqual(self.session.text, draft)
        self.assertTrue(self.session.can_undo)
        self.assertTrue(self.session.can_redo)
        self.assert_no_writes()

    def test_success_discards_frozen_old_caches_and_recalculates_option(self):
        self.session.delete_group("App", "New")
        result = self.session.save(self.service)
        self.assertEqual(load_config(self.path), result)
        self.assertEqual(self.service.selected_option("App"), "Old")
        self.assertEqual(self.service._option_revision, self.before["option_revision"] + 1)
        self.assertIsNone(self.service._detection)
        for name in ("_issued_plans", "_removal_drafts", "_removal_snapshots", "_switch_drafts"):
            self.assertEqual(getattr(self.service, name), {})
        with self.assertRaises(ConflictError):
            self.service.execute(self.plan)
        self.assertFalse(self.session.dirty)
        self.assertFalse(self.session.can_undo)
        self.assertFalse(self.session.can_redo)
        self.assert_no_writes()

    def test_replace_committed_then_error_keeps_new_runtime_and_caches_invalid(self):
        self.session.delete_group("App", "New")

        def committed_then_error(*args, **kwargs):
            save_config(*args, **kwargs)
            raise OSError("returned after replace")

        with patch("envmanager.config_editing.save_config", side_effect=committed_then_error):
            config = self.session.save(self.service)
        self.assertEqual(load_config(self.path), config)
        self.assertEqual(self.service.config, config)
        self.assertEqual(self.service.selected_option("App"), "Old")
        self.assertIsNone(self.service._detection)
        self.assertEqual(self.service._issued_plans, {})
        self.assertEqual(self.service._removal_drafts, {})
        self.assertEqual(self.service._removal_snapshots, {})
        self.assertEqual(self.service._switch_drafts, {})
        self.assertTrue(self.session.save_warning)
        self.assertFalse(self.session.dirty)
        self.assertFalse(self.session.can_undo)
        self.assert_no_writes()

    def test_unreadable_save_result_blocks_old_and_new_operations_until_reload(self):
        self.session.delete_group("App", "New")
        baseline = self.path.read_bytes()
        draft = self.session.text

        def committed_then_error(*args, **kwargs):
            save_config(*args, **kwargs)
            raise OSError("returned after replace")

        # The initial baseline read succeeds; the verification read cannot tell
        # whether replacement occurred. A real replace is performed in between.
        with patch.object(Path, "read_bytes", side_effect=[baseline, baseline, OSError("unreadable")]), \
                patch("envmanager.config_editing.save_config", side_effect=committed_then_error):
            with self.assertRaisesRegex(ConflictError, "无法核实"):
                self.session.save(self.service)
        self.assertEqual(self.runtime(), self.before)
        self.assertTrue(self.service.configuration_save_error)
        self.assertEqual(self.session.text, draft)
        self.assertTrue(self.session.dirty)
        self.assertTrue(self.session.can_undo)
        for operation in (
            lambda: self.service.execute(self.plan),
            lambda: self.service.plan("App", "New"),
            lambda: self.service.finalize_remove(self.removal.id),
            lambda: self.service.finalize_scheme_switch(self.switch.id),
        ):
            with self.subTest(operation=operation), self.assertRaisesRegex(ConflictError, "无法核实"):
                operation()
        self.assert_no_writes()
        # Reading the editor alone must not unlock a still stale service.
        self.session.reload_from_disk()
        self.assertTrue(self.service.configuration_save_error)
        self.service.reload(load_config(self.path))
        self.assertIsNone(self.service.configuration_save_error)
        self.assertEqual(self.service.selected_option("App"), "Old")
        self.assertEqual(self.service._issued_plans, {})
        self.service.plan("App", "Old")
        self.assert_no_writes()

    def test_unexpected_disk_content_also_blocks_until_reload(self):
        self.session.delete_group("App", "New")
        external = SOURCE.replace('default_value = "old"', 'default_value = "external"')

        def uncertain_writer(*args, **kwargs):
            self.path.write_bytes(external.encode("utf-8"))
            raise OSError("unknown replacement result")

        with patch("envmanager.config_editing.save_config", side_effect=uncertain_writer):
            with self.assertRaisesRegex(ConflictError, "无法核实"):
                self.session.save(self.service)
        self.assertEqual(self.runtime(), self.before)
        self.assertEqual(self.path.read_bytes(), external.encode("utf-8"))
        self.assertTrue(self.service.configuration_save_error)
        self.assertTrue(self.session.dirty)
        self.assert_no_writes()

    def test_failed_recovery_reload_preserves_safety_gate(self):
        self.service.block_configuration_operations("保存结果无法核实，请重新加载")
        initialize = self.service._initialize_options

        def failure(snapshot):
            initialize(snapshot)
            raise OSError("recovery unavailable")

        with patch.object(self.service, "_initialize_options", side_effect=failure):
            with self.assertRaises(OSError):
                self.service.reload(load_config(self.path))
        self.assertTrue(self.service.configuration_save_error)
        self.assertEqual(self.runtime(), self.before)
        with self.assertRaisesRegex(ConflictError, "无法核实"):
            self.service.execute(self.plan)
        self.assert_no_writes()

    def test_external_baseline_change_preserves_full_runtime_and_undo(self):
        self.session.delete_group("App", "New")
        draft = self.session.text
        external = SOURCE + "# externally edited\n"
        self.path.write_bytes(external.encode("utf-8"))
        with patch("envmanager.config_editing.save_config") as writer:
            with self.assertRaisesRegex(ConflictError, "其他程序修改"):
                self.session.save(self.service)
        writer.assert_not_called()
        self.assertEqual(self.runtime(), self.before)
        self.assertEqual(self.session.text, draft)
        self.assertTrue(self.session.dirty)
        self.assertTrue(self.session.can_undo)
        self.assertEqual(self.path.read_bytes(), external.encode("utf-8"))
        self.assert_no_writes()

    def test_editor_lock_refusal_preserves_full_runtime_and_draft(self):
        self.session.delete_group("App", "New")
        draft = self.session.text
        with _save_lock(self.path), patch("envmanager.config_editing.save_config") as writer:
            with self.assertRaisesRegex(ConflictError, "另一个配置编辑器"):
                self.session.save(self.service)
        writer.assert_not_called()
        self.assertEqual(self.runtime(), self.before)
        self.assertEqual(self.session.text, draft)
        self.assertTrue(self.session.dirty)
        self.assertTrue(self.session.can_undo)
        self.assertEqual(self.path.read_bytes(), SOURCE.encode("utf-8"))
        self.assert_no_writes()

    def test_external_change_after_runtime_prepare_is_not_overwritten(self):
        self.session.delete_group("App", "New")
        external = SOURCE + "# external race\n"
        reload_runtime = self.service.reload

        def external_write(config):
            reload_runtime(config)
            self.path.write_bytes(external.encode("utf-8"))

        with patch.object(self.service, "reload", side_effect=external_write):
            with self.assertRaises(ConflictError):
                self.session.save(self.service)
        self.assertEqual(self.path.read_bytes(), external.encode("utf-8"))
        self.assertEqual(self.runtime(), self.before)
        self.assertTrue(self.session.dirty)
        self.assertTrue(self.session.can_undo)
        self.assertTrue(self.service.configuration_save_error)
        self.assert_no_writes()
