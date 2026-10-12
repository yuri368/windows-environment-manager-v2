"""Visible main-window status and styled combo geometry, without registry access."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionComboBox

from envmanager.config import parse_config
from envmanager.persistence import StateRepository
from envmanager.service import EnvironmentService
from envmanager.store import MemoryStore
from envmanager.theme import COLORS
from envmanager.ui import MainWindow, OPTION_STATUS_ROLE, OptionStatusDelegate


CONFIG = '''
[Java.JDK17]
JAVA_HOME = { default_value = "17", scope = "User", mode = "replace", editable = true }
[Java.JDK21]
JAVA_HOME = { default_value = "21", scope = "User", mode = "replace", editable = true }
[Python.Python313]
PYTHON_HOME = { default_value = "313", scope = "User", mode = "replace", editable = true }
[Python.Python312]
PYTHON_HOME = { default_value = "312", scope = "User", mode = "replace", editable = true }
'''


class MainStatusOptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.temp = Path(directory.name)
        self.store = MemoryStore()
        self.service = EnvironmentService(parse_config(CONFIG), self.store, StateRepository(self.temp / "state"))
        self.window = MainWindow(self.service, self.temp / "env.toml", demo=True)
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.close)
        self.window.resize(850, 560)
        self.window.show()
        self.app.processEvents()

    def row(self, app):
        for index in range(self.window.app_list.count()):
            item = self.window.app_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == app:
                return self.window.app_list.itemWidget(item)
        self.fail("Missing application: " + app)

    def popup(self, combo):
        combo.showPopup()
        self.addCleanup(combo.hidePopup)
        self.app.processEvents()
        self.assertTrue(combo.view().isVisible())
        return combo.view()

    def test_two_manifest_options_are_both_fully_visible_after_popup_show(self):
        combo = self.row("Java").option_combo
        view = self.popup(combo)
        self.assertEqual(combo.count(), 2)
        for index in range(combo.count()):
            rect = view.visualRect(combo.model().index(index, 0))
            self.assertTrue(view.viewport().rect().contains(rect), (index, rect, view.viewport().rect()))
        self.assertEqual(view.verticalScrollBar().maximum(), 0)
        self.assertEqual(self.store.writes, [])

    def test_detail_uses_plain_text_and_styled_field_width_at_narrow_size(self):
        self.window._select_app("Python")
        self.app.processEvents()
        combo = self.window.option_combo
        self.assertIsInstance(combo.view().itemDelegate(), OptionStatusDelegate)
        self.assertEqual(combo.currentData(Qt.ItemDataRole.UserRole), "Python313")
        self.assertIn("默认方案", combo.toolTip())
        self.assertIsNotNone(combo.currentData(OPTION_STATUS_ROLE))
        option = QStyleOptionComboBox()
        combo.initStyleOption(option)
        field = combo.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox, option,
            QStyle.SubControl.SC_ComboBoxEditField, combo,
        )
        needed = combo.fontMetrics().horizontalAdvance("Python313") + 8
        self.assertGreaterEqual(field.width(), needed)
        self.assertEqual(combo.displayedText(), "Python313")
        self.assertEqual(self.window.width(), 850)
        self.assertTrue(combo.parentWidget().rect().contains(combo.geometry()))
        self.assertTrue(self.window.apply_button.isVisible())

    def test_long_chinese_name_elides_in_manifest_and_is_complete_in_popup(self):
        name = "长期支持生产环境默认方案"
        config = parse_config(f'["中文应用"."{name}"]\nHOME = {{ default_value = "A", scope = "User", mode = "replace", editable = true }}\n')
        self.service.reload(config)
        self.window._render_apps()
        self.window._select_app("中文应用")
        self.app.processEvents()
        row = self.row("中文应用")
        self.assertIn("…", row.option_combo.displayedText())
        self.assertEqual(row.option_combo.currentText(), name)
        self.assertEqual(row.option_combo.toolTip(), name)
        detail = self.window.option_combo
        self.assertIn("…", detail.displayedText())
        self.assertEqual(detail.currentText(), name)
        self.assertIn(name, detail.toolTip())
        self.assertEqual(self.window.width(), 850)
        view = self.popup(row.option_combo)
        self.assertIsInstance(view.itemDelegate(), OptionStatusDelegate)
        self.assertIn(name, row.option_combo.itemData(0, Qt.ItemDataRole.ToolTipRole))
        rect = view.visualRect(row.option_combo.model().index(0, 0))
        self.assertTrue(view.viewport().rect().contains(rect))
        self.assertGreaterEqual(rect.width(), view.fontMetrics().horizontalAdvance(name) + 16)
        self.assertEqual(self.store.writes, [])

    def test_many_options_have_complete_rows_and_scroll_to_last(self):
        options = [f"方案{index:02d}" for index in range(32)]
        config = parse_config("\n".join(
            f'["应用"."{name}"]\nHOME = {{ default_value = "{index}", scope = "User", mode = "replace", editable = true }}'
            for index, name in enumerate(options)
        ))
        self.service.reload(config)
        self.window._render_apps()
        self.window._select_app("应用")
        self.app.processEvents()
        combo = self.row("应用").option_combo
        view = self.popup(combo)
        self.assertGreater(view.verticalScrollBar().maximum(), 0)
        self.assertLess(view.viewport().height(), sum(view.sizeHintForRow(i) for i in range(combo.count())))
        last = combo.model().index(combo.count() - 1, 0)
        view.scrollTo(last)
        self.app.processEvents()
        self.assertTrue(view.viewport().rect().contains(view.visualRect(last)))
        self.assertTrue(combo.screen().availableGeometry().contains(view.window().geometry()))

    def test_status_reflects_actual_active_option_and_keeps_invalid_feedback(self):
        result = self.service.execute(self.service.plan("Python", "Python313"))
        self.assertTrue(result.success)
        self.window._select_app("Python")
        self.window.option_combo.setCurrentText("Python312")
        card = self.window.cards[0]
        # Ordinary edge whitespace is valid input in vNext; NUL remains
        # invalid and exercises the same persistent validation feedback.
        card.editor.setText("313\x00invalid")
        card.editor.textEdited.emit("313\x00invalid")
        self.app.processEvents()
        row = self.row("Python")
        self.assertEqual(row.option_combo.currentText(), "Python312")
        for badge in (row.activation_badge, self.window.activation_badge):
            self.assertEqual(badge.text(), "已激活")
            self.assertEqual(badge.property("activation"), "active")
            self.assertIn("Python313", badge.toolTip())
            self.assertEqual(badge.palette().color(QPalette.ColorRole.WindowText).name(), COLORS["accent"].lower())
        self.assertIn("Python313", self.window.active_label.text())
        self.assertIn("Python313", row.meta.toolTip())
        self.assertIn("输入无效", row.meta.toolTip())
        self.assertEqual(row.meta.objectName(), "error")
        self.assertFalse(self.window.apply_button.isEnabled())
        self.window._select_app("Java")
        self.app.processEvents()
        for badge in (self.row("Java").activation_badge, self.window.activation_badge):
            self.assertEqual(badge.text(), "尚未激活")
            self.assertEqual(badge.property("activation"), "inactive")
            self.assertEqual(badge.palette().color(QPalette.ColorRole.WindowText).name(), COLORS["warning"].lower())


if __name__ == "__main__":
    unittest.main()
