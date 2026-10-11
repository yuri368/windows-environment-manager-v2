from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from envmanager.config import load_config, parse_config, save_config
from envmanager.config_editing import ConfigEditingSession, _save_lock
from envmanager.models import Choice, ConfigError, ConflictError, VariableSpec
from envmanager.persistence import StateRepository
from envmanager.service import EnvironmentService
from envmanager.store import MemoryStore

SOURCE = '# 配置注释\n[App.Default]\nHOME = { default_value = \'C:\\Tools\', scope = "User", mode = "replace", editable = true } # 保留注释\n'


class EditingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "env.toml"
        self.path.write_bytes(SOURCE.encode("utf-8"))
        self.session = ConfigEditingSession(self.path)
        self.original = load_config(self.path).variables("App", "Default")[0]
        self.store = MemoryStore()
        self.state = StateRepository(self.root / "state")
        self.service = EnvironmentService(load_config(self.path), self.store, self.state)

    def test_read_and_abandon_never_write_or_change_runtime(self):
        self.assertFalse(self.session.dirty)
        self.assertEqual(self.session.diff(), "")
        self.session.put_record(replace(self.original, default_value="changed"), self.original)
        self.assertTrue(self.session.dirty)
        self.assertEqual(self.path.read_text(encoding="utf-8"), SOURCE)
        self.assertEqual(self.service.config.variables("App", "Default")[0], self.original)
        self.assertEqual(self.store.writes, [])

    def test_commands_undo_redo_and_new_branch(self):
        self.session.put_record(replace(self.original, default_value="B"), self.original)
        modified = self.session.text
        self.assertIn("保留注释", modified)
        self.session.undo()
        self.assertEqual(self.session.text, SOURCE)
        self.assertFalse(self.session.dirty)
        self.assertTrue(self.session.can_redo)
        self.session.redo()
        self.assertEqual(self.session.text, modified)
        self.session.undo()
        self.session.put_record(replace(self.original, default_value="C"), self.original)
        self.assertFalse(self.session.can_redo)

    def test_invalid_command_leaves_text_and_history_unchanged(self):
        with self.assertRaises(ConfigError):
            self.session.put_record(replace(self.original, mode="invalid"), self.original)
        self.assertEqual(self.session.text, SOURCE)
        self.assertFalse(self.session.can_undo)
        self.assertFalse(self.session.can_redo)

    def test_invalid_source_retained_and_blocks_visual_and_save(self):
        invalid = SOURCE + "wrong = ["
        self.session.replace_text(invalid)
        self.assertEqual(self.session.text, invalid)
        self.assertIsNone(self.session.document)
        self.assertTrue(self.session.error)
        with self.assertRaises(ConfigError):
            self.session.put_record(self.original)
        with self.assertRaises(ConfigError):
            self.session.save(self.service)
        self.assertEqual(self.path.read_bytes(), SOURCE.encode("utf-8"))
        self.session.undo()
        self.assertIsNotNone(self.session.document)

    def test_empty_draft_can_add_record_but_cannot_save_empty(self):
        self.session.delete_record(self.original)
        self.assertEqual(self.session.document.groups(), ())
        with self.assertRaises(ConfigError):
            self.session.save(self.service)
        new = VariableSpec("New App", "New.Option", "NEW_HOME", "System", "replace", False, "")
        self.session.put_record(new)
        self.assertEqual(self.session.validate().variables(new.app, new.option), (new,))

    def test_save_validates_refreshes_config_and_keeps_applied_state(self):
        prior = self.state.snapshot()
        self.service.selections.edit(self.original, "runtime-draft")
        updated = replace(self.original, default_value="new", choices=(Choice("empty", ""), Choice("new", "new")))
        self.session.put_record(updated, self.original)
        result = self.session.save(self.service)
        self.assertEqual(load_config(self.path), result)
        self.assertEqual(self.service.config, result)
        self.assertEqual(self.state.snapshot(), prior)
        self.assertEqual(self.store.writes, [])
        self.assertEqual(self.store.notifications, 0)
        self.assertFalse(self.session.dirty)
        self.assertFalse(self.session.can_undo)
        self.assertEqual(self.session.diff(), "")
        self.assertEqual(self.service.selections.get(updated).value, "new")

    def test_same_content_save_refreshes_a_stale_runtime_without_write(self):
        old_service = EnvironmentService(parse_config(SOURCE.replace("C:\\Tools", "old")), self.store, self.state)
        with patch("envmanager.config_editing.save_config") as save:
            self.session.save(old_service)
        save.assert_not_called()
        self.assertEqual(old_service.config, load_config(self.path))

    def test_save_io_failure_restores_config_draft_and_plans(self):
        self.service.selections.edit(self.original, "runtime-draft")
        plan = self.service.plan("App", "Default")
        old_config, old_selections = self.service.config, self.service.selections
        self.session.put_record(replace(self.original, default_value="new"), self.original)
        with patch("envmanager.config_editing.save_config", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.session.save(self.service)
        self.assertIs(self.service.config, old_config)
        self.assertIs(self.service.selections, old_selections)
        self.assertEqual(self.service.selections.get(self.original).value, "runtime-draft")
        self.assertIn(plan.id, self.service._issued_plans)
        self.assertEqual(self.path.read_text(encoding="utf-8"), SOURCE)
        self.assertTrue(self.session.dirty)
        self.assertTrue(self.session.can_undo)

    def test_refresh_failure_never_writes_file(self):
        old_config, old_selections = self.service.config, self.service.selections
        self.session.put_record(replace(self.original, default_value="new"), self.original)

        def failure(config):
            self.service.config = config
            self.service.selections = None
            raise OSError("state unavailable")

        with patch.object(self.service, "reload", side_effect=failure):
            with self.assertRaises(OSError):
                self.session.save(self.service)
        self.assertIs(self.service.config, old_config)
        self.assertIs(self.service.selections, old_selections)
        self.assertEqual(self.path.read_text(encoding="utf-8"), SOURCE)

    def test_external_change_blocks_overwrite_and_retains_draft(self):
        self.session.put_record(replace(self.original, default_value="draft"), self.original)
        external = SOURCE.replace("C:\\Tools", "external")
        self.path.write_bytes(external.encode("utf-8"))
        with self.assertRaises(ConflictError):
            self.session.save(self.service)
        self.assertEqual(self.path.read_text(encoding="utf-8"), external)
        self.assertTrue(self.session.dirty)
        self.assertEqual(self.service.config.variables("App", "Default")[0], self.original)
        self.session.reload_from_disk()
        self.assertFalse(self.session.dirty)
        self.assertEqual(self.session.text, external)

    def test_change_during_runtime_prepare_detected_before_atomic_replace(self):
        self.session.put_record(replace(self.original, default_value="draft"), self.original)
        old_reload = self.service.reload
        old_config, old_selections = self.service.config, self.service.selections
        external = SOURCE.replace("C:\\Tools", "external")

        def external_write(config):
            old_reload(config)
            self.path.write_bytes(external.encode("utf-8"))

        with patch.object(self.service, "reload", side_effect=external_write):
            with self.assertRaises(ConflictError):
                self.session.save(self.service)
        self.assertEqual(self.path.read_text(encoding="utf-8"), external)
        self.assertIs(self.service.config, old_config)
        self.assertIs(self.service.selections, old_selections)

    def test_two_editors_second_save_detects_first_save(self):
        second = ConfigEditingSession(self.path)
        self.session.put_record(replace(self.original, default_value="first"), self.original)
        second.put_record(replace(self.original, default_value="second"), self.original)
        self.session.save(self.service)
        with self.assertRaises(ConflictError):
            second.save(self.service)
        self.assertEqual(load_config(self.path).variables("App", "Default")[0].default_value, "first")

    def test_saving_while_other_editor_lock_held_rejects(self):
        self.session.put_record(replace(self.original, default_value="new"), self.original)
        with _save_lock(self.path):
            with self.assertRaises(ConflictError):
                self.session.save(self.service)
        self.assertEqual(self.path.read_text(encoding="utf-8"), SOURCE)

    def test_source_widget_preserves_crlf_and_mixed_unchanged_lines(self):
        mixed = SOURCE.replace("\n", "\r\n", 2)
        self.session.replace_text(mixed)
        self.session.replace_source_text(mixed.replace("\r\n", "\n"))
        self.assertEqual(self.session.text, mixed)
        self.session.replace_source_text(mixed.replace("\r\n", "\n").replace("C:\\Tools", "D:\\New"))
        self.assertEqual(self.session.text, mixed.replace("C:\\Tools", "D:\\New"))
        self.session.undo()
        self.assertEqual(self.session.text, mixed)

    def test_source_widget_retains_unicode_separators_in_unchanged_values(self):
        special = SOURCE.replace("C:\\Tools", "A\u2028B\u2029C")
        self.session.replace_text(special)
        displayed = special.replace("\u2028", "\n").replace("\u2029", "\n")
        self.session.replace_source_text(displayed)
        self.assertEqual(self.session.text, special)
        self.session.replace_source_text(displayed.replace("# 配置注释", "# 新注释"))
        self.assertEqual(self.session.text, special.replace("# 配置注释", "# 新注释"))

    def test_source_edit_keeps_changed_line_own_mixed_ending(self):
        mixed = SOURCE.replace("# 配置注释\n", "# 配置注释\r\n")
        self.session.replace_text(mixed)
        self.session.replace_source_text(mixed.replace("\r\n", "\n").replace("# 配置注释", "# 已改注释"))
        self.assertEqual(self.session.text, mixed.replace("# 配置注释", "# 已改注释"))

    def test_save_return_error_after_replace_keeps_new_runtime_and_baseline(self):
        self.session.put_record(replace(self.original, default_value="new"), self.original)
        from envmanager.config_editing import save_config as real_save

        def committed_then_failed(*args, **kwargs):
            real_save(*args, **kwargs)
            raise OSError("replace result uncertain")

        with patch("envmanager.config_editing.save_config", side_effect=committed_then_failed):
            config = self.session.save(self.service)
        self.assertEqual(load_config(self.path), config)
        self.assertEqual(self.service.config, config)
        self.assertTrue(self.session.save_warning)
        self.assertFalse(self.session.dirty)
        self.assertFalse(self.session.can_undo)
        self.assertEqual(self.store.writes, [])

    def test_deleted_or_invalid_encoding_disk_does_not_lose_draft(self):
        self.session.put_record(replace(self.original, default_value="draft"), self.original)
        draft = self.session.text
        self.path.unlink()
        with self.assertRaises(ConflictError):
            self.session.save(self.service)
        self.path.write_bytes(b"\xff")
        with self.assertRaises(ConfigError):
            self.session.reload_from_disk()
        self.assertEqual(self.session.text, draft)

    def test_atomic_guard_and_failure_cleanup(self):
        with self.assertRaises(ConflictError):
            save_config(self.path, SOURCE, expected_content=b"wrong baseline")
        self.assertEqual(self.path.read_text(encoding="utf-8"), SOURCE)
        self.assertEqual(list(self.root.glob("*.tmp")), [])
        with patch("envmanager.config.os.replace", side_effect=PermissionError("read only")):
            with self.assertRaises(PermissionError):
                save_config(self.path, SOURCE, expected_content=SOURCE.encode("utf-8"))
        self.assertEqual(list(self.root.glob("*.tmp")), [])
