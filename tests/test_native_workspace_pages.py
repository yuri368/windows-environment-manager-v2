"""Navigation, complete read-only inventory, native popup and history preview."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QPixmap, QPainter
from PySide6.QtWidgets import QApplication, QStyledItemDelegate, QStyleOptionViewItem, QStyle
from PySide6.QtTest import QTest
from envmanager.config import parse_config
from envmanager.models import RegistryState
from envmanager.persistence import StateRepository
from envmanager.service import EnvironmentService
from envmanager.store import MemoryStore
from envmanager.ui import MainWindow, OPTION_STATUS_ROLE, OptionComboBox


class NativeWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.source = '[App.One]\nHOME={default_value="A",scope="User",mode="replace",editable=true}\n'
        self.path = self.folder / "env.toml"
        self.path.write_text(self.source, encoding="utf-8")
        self.store = MemoryStore((RegistryState("HOME", "User", True, "A", 1),
                                  RegistryState("OUTSIDE_CONFIG", "System", True, "%HOME%;B", 2),
                                  RegistryState("EMPTY", "User", True, "", 1),
                                  RegistryState("API_KEY", "User", True, "secret-value", 1)))
        self.service = EnvironmentService(parse_config(self.source), self.store, StateRepository(self.folder / "state"))
        self.window = MainWindow(self.service, self.path, demo=True)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def table_text(self):
        table = self.window.windows_page.table
        return [[table.item(row, col).text() for col in range(table.columnCount())]
                for row in range(table.rowCount())]

    def test_navigation_inventory_scope_and_exact_value_view_are_read_only(self):
        self.window.windows_button.click()
        self.assertEqual(self.window.workspace_stack.currentIndex(), 1)
        self.assertFalse(self.window.footer_panel.isVisible())
        rows = self.table_text()
        self.assertEqual(len(rows), 4)
        self.assertIn(["系统", "OUTSIDE_CONFIG", "存在", "REG_EXPAND_SZ", "%HOME%;B"], rows)
        self.assertIn(["用户", "EMPTY", "存在", "REG_SZ", "（空字符串）"], rows)
        self.assertNotIn("secret-value", str(rows))
        page = self.window.windows_page
        page.scope.setCurrentIndex(page.scope.findData("System"))
        self.assertEqual(len(self.table_text()), 1)
        page.table.selectRow(0)
        with patch.object(page, "show_value") as show:
            page.full_value.click()
        show.assert_called_once_with("%HOME%;B", False)
        page.scope.setCurrentIndex(0)
        page.search.setText("OUTSIDE")
        self.assertEqual(len(self.table_text()), 1)
        self.window.workbench_button.click()
        self.assertTrue(self.window.footer_panel.isVisible())
        self.assertEqual(self.store.writes, [])
        self.assertEqual(self.store.notifications, 0)
        self.assertEqual(self.path.read_text(encoding="utf-8"), self.source)

    def test_sidebar_scheme_controls_follow_the_visible_row_after_initial_resize(self):
        # Successful activation rebuilds rows before the window receives its first
        # real size. The default QListView layout can retain a 640px virtual row.
        self.assertTrue(self.service.execute(self.service.plan("App", "One")).success)
        window = MainWindow(self.service, self.path, demo=True)
        try:
            for width in (1366, 1024):
                window.resize(width, 768)
                window.show()
                QTest.qWait(30)
                self.app.processEvents()
                item = window.app_list.currentItem()
                row = window.app_list.itemWidget(item)
                combo = row.option_combo
                bottom_right = combo.mapTo(window.app_list.viewport(), combo.rect().bottomRight())
                self.assertLess(bottom_right.x(), window.app_list.viewport().width(), (width, row.geometry().getRect(), row.minimumSizeHint().toTuple(), combo.geometry().getRect(), window.app_list.resizeMode()))
                status_corner = row.match_label.mapTo(window.app_list.viewport(), row.match_label.rect().bottomRight())
                self.assertTrue(row.match_label.isVisible())
                self.assertLess(status_corner.x(), window.app_list.viewport().width())
                self.assertIn("1/1", row.match_label.text())
                combo.showPopup()
                self.app.processEvents()
                self.assertLess(combo.view().window().width(), 400)
                combo.hidePopup()
            for index, button in enumerate(window.nav_buttons):
                for _ in range(2):
                    button.click()
                    self.assertEqual(window.workspace_stack.currentIndex(), index)
                    self.assertTrue(button.isChecked())
                    self.assertTrue(button.property("active"))
            self.assertFalse(window.windows_page.table.tabKeyNavigation())
            self.assertFalse(window.history_page.table.tabKeyNavigation())
        finally:
            window.close()
            window.deleteLater()
            self.app.processEvents()

    def test_inventory_keeps_available_scope_and_shows_other_scope_error(self):
        read = self.service.list_environment
        def broken(scope):
            if scope == "System":
                raise OSError("拒绝访问系统环境")
            return read(scope)
        with patch.object(self.service, "list_environment", side_effect=broken):
            self.window.windows_button.click()
        self.assertEqual(len(self.table_text()), 3)
        self.assertIn("系统环境读取失败", self.window.windows_page.description.text())
        self.assertIn("拒绝访问", self.window.windows_page.empty.text())
        self.assertEqual(self.store.writes, [])

    def test_history_selection_uses_record_id_and_existing_preview_chain(self):
        self.service.state.save_operation({"id": "record-1", "created_at": "2026-10-12T00:00:00+00:00",
            "app": "App", "option": "One", "action": "apply", "status": "success", "message": "已验证"})
        self.window.history_button.click()
        page = self.window.history_page
        self.assertEqual(page.table.rowCount(), 1)
        self.assertFalse(page.restore_button.isEnabled())
        page.table.selectRow(0)
        self.assertTrue(page.restore_button.isEnabled())
        with patch.object(self.service, "plan_restore", return_value="existing-plan") as factory, \
                patch.object(page, "preview", side_effect=lambda call: self.assertEqual(call(), "existing-plan")) as preview:
            page.restore_button.click()
        factory.assert_called_once_with("record-1")
        preview.assert_called_once()
        self.assertEqual(self.store.writes, [])

    def test_comparison_follows_focused_record_and_row_details_stay_independent(self):
        self.assertTrue(self.window.comparison_panel.isVisible())
        card = self.window.cards[0]
        self.assertIn("HOME", self.window.comparison_title.text())
        self.assertEqual(self.window.comparison_actual._full_text, "A")
        card.details_toggle.click()
        self.assertTrue(card.details.isVisible())
        self.window.windows_compare_button.click()
        self.assertTrue(card.details.isVisible())
        self.assertFalse(self.window.comparison_panel.isVisible())
        self.assertEqual(self.store.writes, [])

    def test_popup_has_whole_row_paint_scroll_keyboard_and_full_tooltips(self):
        combo = OptionComboBox(self.window)
        combo.move(20, 80)
        combo.resize(190, 32)
        combo.addItems(["One", "Two", "Three", "Four", "Five", "长方案名" * 70])
        combo.setOptionStatus(0, None, default=True, active=True,
                              presentation=("完全匹配", "complete", "1/1", "检测说明"))
        combo.setOptionStatus(1, None, presentation=("部分匹配", "partial", "0/1", "仍保留方案名称"))
        combo.show()
        combo.showPopup()
        self.app.processEvents()
        popup = combo.view().window()
        self.assertTrue(combo.screen().availableGeometry().contains(popup.frameGeometry()))
        self.assertGreater(combo.view().verticalScrollBar().maximum(), 0)
        self.assertLessEqual(combo.view().viewport().height(), sum(combo.view().sizeHintForRow(i) for i in range(3)) + 4)
        self.assertIn("默认方案", combo.itemData(0, Qt.ItemDataRole.ToolTipRole))
        self.assertIn("当前激活方案", combo.itemData(0, Qt.ItemDataRole.ToolTipRole))
        option = QStyleOptionViewItem()
        option.rect = combo.view().visualRect(combo.model().index(0, 0))
        option.font = combo.font()
        option.fontMetrics = combo.fontMetrics()
        option.state = QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_HasFocus
        pixmap = QPixmap(max(1, option.rect.width()), max(1, option.rect.height()))
        painter = QPainter(pixmap)
        with patch.object(QStyledItemDelegate, "paint", side_effect=AssertionError("native fragment paint")):
            combo.view().itemDelegate().paint(painter, option, combo.model().index(0, 0))
        painter.end()
        QTest.keyClick(combo.view(), Qt.Key.Key_Down)
        QTest.keyClick(combo.view(), Qt.Key.Key_Return)
        self.assertEqual(combo.currentText(), "Two")
        self.assertEqual(combo.currentData(Qt.ItemDataRole.UserRole), "Two")
        combo.hidePopup()
        combo.deleteLater()
        self.assertEqual(self.store.writes, [])


if __name__ == "__main__":
    unittest.main()
