"""vNext UI contracts against isolated configuration and memory registry."""

from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from dataclasses import replace
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QDialog, QLabel

from envmanager.config import parse_config
from envmanager.config_editor_ui import ConfigEditorDialog
from envmanager.elements import CleanupSettings
from envmanager.persistence import StateRepository
from envmanager.removal import RemovalRequest, RemovalScope
from envmanager.service import EnvironmentService
from envmanager.sharing import ConflictDecision
from envmanager.store import MemoryStore
from envmanager.ui import FullValueDialog, MainWindow, PreviewDialog, SharedConflictDialog
from tests.test_detection_service import raw


def record(app, name, value, *, special=False, mode="replace", scope="User"):
    import json
    return (f"[{app}.Default]\n{name} = {{default_value={json.dumps(value)},"
            f"scope=\"{scope}\",mode=\"{mode}\",editable=true,special={str(special).lower()}}}\n")


class VNextUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def service(self, text, states=()):
        self.path = self.root / "env.toml"
        self.path.write_text(text, encoding="utf-8")
        return EnvironmentService(parse_config(text), MemoryStore(states),
                                  StateRepository(self.root / "state"))

    def window(self, service):
        window = MainWindow(service, self.path, demo=True)
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        return window

    def test_t20_shared_elements_share_one_target_group_and_decide_independently(self):
        service = self.service(record("App", "Path", "A;B") +
                               record("Protected", "PATH", "A;B"), [raw("Path", "A;B")])
        draft = service.prepare_remove(RemovalRequest(
            RemovalScope.SCHEME, frozenset(("App",)), {"App": "Default"}))
        dialog = SharedConflictDialog(draft.shared_conflicts)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(len(dialog.target_groups), 1)
        self.assertEqual(len(dialog.decision_boxes), 2)
        self.assertEqual(set(dialog.decisions().values()), {"keep"})
        dialog._set_all(ConflictDecision.REMOVE)
        first = next(iter(dialog.decision_boxes.values()))
        first.setCurrentIndex(first.findData("keep"))
        self.assertEqual(list(dialog.decisions().values()).count("keep"), 1)
        self.assertEqual(list(dialog.decisions().values()).count("remove"), 1)

    def test_special_conflict_has_explicit_whole_value_choices(self):
        service = self.service(record("App", "RAW", "  A;B  ", special=True) +
                               record("Protected", "RAW", "  A;B  ", special=True),
                               [raw("RAW", "  A;B  ")])
        draft = service.prepare_remove(RemovalRequest(
            RemovalScope.SCHEME, frozenset(("App",)), {"App": "Default"}))
        dialog = SharedConflictDialog(draft.shared_conflicts)
        self.addCleanup(dialog.deleteLater)
        box = next(iter(dialog.decision_boxes.values()))
        self.assertEqual([box.itemText(i) for i in range(box.count())],
                         ["保留完整值", "撤销整键"])
        self.assertTrue(any("特殊完整值" in label.text() for label in dialog.findChildren(QLabel)))

    def test_partial_ordinary_replace_detail_offers_actual_elements(self):
        service = self.service(record("App", "LIST", "A;B"), [raw("LIST", " A ;C")])
        card = self.window(service).cards[0]
        details, removal, removable = card._match_details()
        self.assertTrue(removable)
        self.assertIn("已匹配：A", details)
        self.assertIn("缺失：B", details)
        self.assertIn("额外：C", details)
        self.assertEqual(removal, "A")

    def test_empty_selection_detail_allows_explicit_key_removal(self):
        service = self.service(record("App", "EMPTY", "", mode="+append"), [raw("EMPTY", "")])
        details, removal, removable = self.window(service).cards[0]._match_details()
        self.assertTrue(removable)
        self.assertIn("空值", details)
        self.assertIn("删除", removal)

    def test_effective_empty_selection_enables_variable_removal_and_delete_preview(self):
        service = self.service(record("App", "EMPTY", " ;\t ", mode="+append"), [raw("EMPTY", "")])
        window = self.window(service)
        card = window.cards[0]
        self.assertIn("匹配空值", card.match_label.text())
        details, removal, removable = card._match_details()
        self.assertTrue(removable)
        self.assertTrue(card.remove_button.isEnabled())
        self.assertIn("空值", details)
        self.assertIn("删除", removal)
        previewed = []

        def preview(dialog):
            previewed.append(dialog.plan)
            return QDialog.DialogCode.Rejected

        with patch.object(PreviewDialog, "exec", new=preview):
            card.remove_button.click()
        self.assertEqual(len(previewed), 1)
        self.assertTrue(previewed[0].changes[0].before.exists)
        self.assertFalse(previewed[0].changes[0].after.exists)
        self.assertEqual(service.store.writes, [])

    def test_critical_user_variable_deletion_preview_shows_risk(self):
        service = self.service(record("App", "Path", "A"), [raw("Path", "A")])
        draft = service.prepare_remove(RemovalRequest(
            RemovalScope.SCHEME, frozenset(("App",)), {"App": "Default"}))
        preview = PreviewDialog(service.finalize_remove(draft.id))
        self.addCleanup(preview.deleteLater)
        labels = "\n".join(label.text() for label in preview.findChildren(QLabel))
        self.assertIn("将删除关键环境变量", labels)

    def test_restore_and_recover_system_deletions_show_explicit_risk_and_permission(self):
        service = self.service(record("App", "CUSTOM_SYSTEM", "A", scope="System"))
        applied = service.plan("App", "Default")
        self.assertTrue(service.execute(applied).success)
        restored = service.plan_restore(applied.id)
        for action in ("restore", "recover"):
            with self.subTest(action=action):
                preview = PreviewDialog(replace(restored, action=action))
                self.addCleanup(preview.deleteLater)
                labels = "\n".join(label.text() for label in preview.findChildren(QLabel))
                self.assertIn("将删除整个环境变量键", labels)
                self.assertIn("系统作用域删除需要管理员权限", labels)
        self.assertEqual(len(service.store.writes), 1)

    def test_critical_deletion_risk_is_visible_for_restore_and_recover_actions(self):
        service = self.service(record("App", "Path", "A"))
        applied = service.plan("App", "Default")
        self.assertTrue(service.execute(applied).success)
        restored = service.plan_restore(applied.id)
        for action in ("restore", "recover"):
            with self.subTest(action=action):
                preview = PreviewDialog(replace(restored, action=action))
                self.addCleanup(preview.deleteLater)
                labels = "\n".join(label.text() for label in preview.findChildren(QLabel))
                self.assertIn("将删除关键环境变量", labels)

    def test_full_value_dialog_preserves_unicode_separators_and_normal_lf(self):
        values = ("A\u2028B", "A\u2029B", "A\r\nB\u2028C\u2029D", " A\nB;C\t ")
        for value in values:
            with self.subTest(value=repr(value)):
                dialog = FullValueDialog(value, True, False)
                self.addCleanup(dialog.deleteLater)
                self.assertEqual(dialog.value(), value)
                if "\u2028" in value:
                    self.assertIn("\\u2028", dialog.editor.toPlainText())
                if "\u2029" in value:
                    self.assertIn("\\u2029", dialog.editor.toPlainText())
                if value == " A\nB;C\t ":
                    self.assertFalse(dialog.escaped)
                dialog._accept()
                self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
                self.assertEqual(dialog.value(), value)

    def test_main_full_value_entry_keeps_special_selected_raw_string(self):
        value = "  A;A\u2028B\r\nC\u2029D\t "
        service = self.service(record("App", "RAW", value, special=True))
        window = self.window(service)
        card = window.cards[0]
        before_source = service.selections.get(card.spec).source
        before_revision = service.selections.revision
        with patch.object(FullValueDialog, "exec", return_value=QDialog.DialogCode.Accepted):
            card.full_value_button.click()
        after = service.selections.get(card.spec)
        self.assertEqual(after.value, value)
        self.assertEqual(after.source, before_source)
        self.assertEqual(service.selections.revision, before_revision)
        self.assertEqual(self.path.read_text(encoding="utf-8"), record("App", "RAW", value, special=True))
        self.assertEqual(service.store.writes, [])

    def test_special_editor_roundtrip_and_incompatible_mode_retains_draft(self):
        service = self.service(record("App", "RAW", "  A;A;B\t ", special=True))
        editor = ConfigEditorDialog(self.path, service)
        self.addCleanup(editor.deleteLater)
        self.assertTrue(editor.special_check.isChecked())
        self.assertFalse(editor._form_pending())
        self.assertEqual(editor.default_editor.exact_text(), "  A;A;B\t ")
        original = editor.session.text
        editor.mode_combo.setCurrentIndex(editor.mode_combo.findData("+append"))
        self.assertFalse(editor._commit_form())
        self.assertEqual(editor.session.text, original)
        self.assertEqual(editor.mode_combo.currentData(), "+append")
        self.assertTrue(editor.special_check.isChecked())
        self.assertEqual(service.store.writes, [])

    def test_advanced_cleanup_is_collapsed_persistent_and_never_writes_registry(self):
        from envmanager.ui import CleanupSettingsDialog
        service = self.service(record("App", "LIST", "A"), [raw("LIST", " A ;;A")])
        window = self.window(service)
        seen = []

        def change(dialog):
            seen.append(dialog)
            self.assertTrue(dialog.cleanup_options.isHidden())
            self.assertFalse(dialog.advanced_toggle.isChecked())
            dialog.advanced_toggle.setChecked(True)
            self.assertFalse(dialog.cleanup_options.isHidden())
            for checkbox in dialog.cleanup_checks.values():
                self.assertTrue(checkbox.isChecked())
                checkbox.setChecked(False)
            return QDialog.DialogCode.Accepted

        with patch.object(CleanupSettingsDialog, "exec", new=change):
            window._advanced_settings()
        self.assertEqual(len(seen), 1)
        self.assertEqual(service.cleanup_settings, CleanupSettings(False, False, False))
        self.assertEqual(service.store.writes, [])
        reloaded = StateRepository(self.root / "state")
        self.assertEqual(reloaded.cleanup_settings(),
                         {"trim_whitespace": False, "remove_empty": False, "deduplicate": False})


if __name__ == "__main__":
    unittest.main()
