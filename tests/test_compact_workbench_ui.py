"""Compact workbench interactions against isolated, read-only Windows snapshots."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from envmanager.config import parse_config
from envmanager.models import RegistryState
from envmanager.persistence import StateRepository
from envmanager.service import EnvironmentService
from envmanager.store import MemoryStore
from envmanager.ui import MainWindow


CONFIG = '''
[Alpha.One]
HOME = { default_value = "A", scope = "User", mode = "replace", editable = true }
[Alpha.Two]
HOME = { default_value = "B", scope = "User", mode = "replace", editable = true }
[Beta.Default]
Path = [
  { default_value = "UserBin", scope = "User", mode = "+append", editable = true },
  { default_value = "SystemBin", scope = "System", mode = "-append", editable = false },
]
'''


class CompactWorkbenchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "env.toml"
        self.path.write_text(CONFIG, encoding="utf-8")
        self.store = MemoryStore((RegistryState("HOME", "User", True, "A", 1),
                                  RegistryState("Path", "User", True, "UserBin", 2)))
        self.service = EnvironmentService(parse_config(CONFIG), self.store,
                                          StateRepository(Path(directory.name) / "state"))
        self.window = MainWindow(self.service, self.path, demo=True)
        self.window.show()
        self.app.processEvents()
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.close)

    def visible_apps(self):
        return [self.window.app_list.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.window.app_list.count())]

    def test_filter_uses_prepared_option_and_preserves_batch_and_detail(self):
        self.window._check_all(True)
        self.window.app_filter.setCurrentIndex(self.window.app_filter.findData("complete"))
        self.assertEqual(self.visible_apps(), ["Alpha"])
        self.window.option_combo.setCurrentText("Two")
        self.assertEqual(self.visible_apps(), [])
        self.assertEqual(self.window.app_name, "Alpha")
        self.assertIn("未匹配 0/1", self.window.match_status.text())
        self.assertEqual(self.window.checked_apps, {"Alpha", "Beta"})
        self.assertIn("Alpha / Two", self.window.batch_summary.toolTip())
        self.window.app_filter.setCurrentIndex(self.window.app_filter.findData("needs"))
        self.assertEqual(self.visible_apps(), ["Alpha", "Beta"])
        self.window.search.setText("Two")
        self.assertEqual(self.visible_apps(), ["Alpha"])
        self.assertEqual(self.service.state.snapshot()["active"], {})
        self.assertEqual(self.store.writes, [])

    def test_focused_enter_navigates_and_opens_preview_once_without_writes(self):
        row = self.window.app_list.itemWidget(self.window.app_list.item(1))
        row.name_button.setFocus()
        self.app.processEvents()
        QTest.keyClick(row.name_button, Qt.Key.Key_Return)
        self.assertEqual(self.window.app_name, "Beta")
        self.window._navigate_app("Alpha")
        self.window.apply_button.setFocus()
        self.app.processEvents()
        with patch.object(self.window, "_choose_old_schemes", return_value=()), \
                patch.object(self.window, "_execute_plan") as preview:
            QTest.keyClick(self.window.apply_button, Qt.Key.Key_Return)
        preview.assert_called_once()
        self.assertEqual(self.store.writes, [])
        self.assertEqual(self.store.notifications, 0)

    def test_read_error_and_unread_are_never_complete(self):
        self.window._detection = None
        self.window._detection_error = "无法读取用户环境"
        self.window._refresh_detection_views()
        self.window.app_filter.setCurrentIndex(self.window.app_filter.findData("complete"))
        self.assertEqual(self.visible_apps(), [])
        self.window.app_filter.setCurrentIndex(self.window.app_filter.findData("needs"))
        self.assertEqual(self.visible_apps(), ["Alpha", "Beta"])
        self.assertIn("检测错误", self.window.match_status.text())
        row = self.window.app_list.itemWidget(self.window.app_list.item(0))
        self.assertEqual(row.match_label.text(), "错误")

    def test_dual_scope_filter_keeps_records_and_source_drafts_independent(self):
        self.window._select_app("Beta")
        user, system = self.window.cards
        self.window.mismatch_only.setChecked(True)
        self.assertTrue(user.isHidden())
        self.assertFalse(system.isHidden())
        user.editor.setText("UserDraft")
        user.editor.textEdited.emit("UserDraft")
        self.assertFalse(user.isHidden())
        self.assertFalse(system.isHidden())
        self.assertEqual(user.selector.currentData(), "custom")
        self.assertEqual(system.selector.currentData(), "default")
        self.assertTrue(system.editor.isReadOnly())
        self.window.windows_compare_button.click()
        self.assertTrue(all(card.details.isVisible() for card in self.window.cards))
        self.window.windows_compare_button.click()
        self.assertTrue(all(card.details.isHidden() for card in self.window.cards))
        self.assertEqual(self.store.writes, [])

    def test_row_background_navigation_does_not_toggle_batch_check(self):
        item = self.window.app_list.item(1)
        row = self.window.app_list.itemWidget(item)
        QTest.mouseClick(row, Qt.MouseButton.LeftButton)
        self.assertEqual(self.window.app_name, "Beta")
        self.assertEqual(self.window.checked_apps, set())
        QTest.mouseClick(row.check, Qt.MouseButton.LeftButton)
        self.assertEqual(self.window.checked_apps, {"Beta"})
        self.assertEqual(self.window.app_name, "Beta")

    def test_single_and_batch_scope_are_explicit_and_all_removal_ranges_exist(self):
        self.window._select_app("Beta")
        self.assertIn("单应用", self.window.batch_summary.text())
        self.assertIn("Beta / Default", self.window.batch_summary.toolTip())
        self.window._check_all(True)
        self.assertIn("2 个应用", self.window.batch_summary.text())
        menu = [action for action in self.window.deactivate_button.menu().actions() if not action.isSeparator()]
        self.assertEqual(len(menu), 4)
        for action in (self.window.current_remove_action, self.window.application_remove_action,
                       self.window.batch_current_action, self.window.batch_all_action):
            self.assertTrue(action.isEnabled())
        self.assertEqual(self.store.writes, [])

    def test_configuration_save_failure_blocks_apply_and_every_removal_scope(self):
        self.window._check_all(True)
        self.assertTrue(self.window.cards[0].remove_button.isEnabled())
        self.service.block_configuration_operations("备份恢复失败；配置文件需要重新加载")
        self.window._update_apply_availability()
        self.assertFalse(self.window.apply_button.isEnabled())
        self.assertFalse(self.window.deactivate_button.isEnabled())
        self.assertFalse(self.window.cards[0].remove_button.isEnabled())
        self.assertTrue(self.window.blocking_reason.isVisible())
        self.assertIn("重新加载", self.window.blocking_reason.text())
        self.assertIn("备份恢复失败", self.window.blocking_reason.toolTip())
        self.service.reload(parse_config(CONFIG))
        self.window._scan_detection(force=True)
        self.window._render_variables()
        self.assertTrue(self.window.apply_button.isEnabled())
        self.assertTrue(self.window.cards[0].remove_button.isEnabled())
        self.assertFalse(self.window.blocking_reason.isVisible())

    def test_cancelled_editor_reload_refreshes_runtime_cards_when_digest_changed(self):
        changed = CONFIG.replace('default_value = "A"', 'default_value = "Changed"')

        def close_after_reload(dialog):
            self.service.reload(parse_config(changed))
            return QDialog.DialogCode.Rejected

        with patch("envmanager.ui.ConfigEditorDialog.exec", new=close_after_reload):
            self.window._visual_config()
        self.assertEqual(self.window.cards[0].editor.text(), "Changed")
        self.assertEqual(self.window.cards[0].spec.default_value, "Changed")
        self.assertEqual(self.store.writes, [])

    def test_compact_rows_scroll_and_fixed_footer_remain_reachable(self):
        for width, height in ((850, 560), (1024, 768), (1366, 768), (1536, 864)):
            with self.subTest(size=(width, height)):
                self.window.resize(width, height)
                self.app.processEvents()
                row = self.window.app_list.itemWidget(self.window.app_list.item(0))
                self.assertGreaterEqual(row.height(), 44)
                self.assertLessEqual(row.height(), 50)
                manifest = self.window.app_list.parentWidget()
                self.assertGreaterEqual(manifest.width(), 248)
                self.assertLessEqual(manifest.width(), 270)
                footer = self.window.apply_button.parentWidget()
                for button in (self.window.apply_button, self.window.deactivate_button):
                    self.assertTrue(footer.rect().contains(button.geometry()))
                if width <= 1024:
                    bar = self.window.scroll.horizontalScrollBar()
                    self.assertGreater(bar.maximum(), 0)
                    bar.setValue(bar.maximum())
                    self.app.processEvents()
                    button = self.window.cards[0].remove_button
                    location = button.mapTo(self.window.scroll.viewport(), button.rect().center())
                    self.assertTrue(self.window.scroll.viewport().rect().contains(location))


if __name__ == "__main__":
    unittest.main()
