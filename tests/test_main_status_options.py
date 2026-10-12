"""Visible main-window status and styled combo geometry, without registry access."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QPoint, QRect
from PySide6.QtGui import QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionComboBox, QWidget

from envmanager.config import parse_config
from envmanager.persistence import StateRepository
from envmanager.service import EnvironmentService
from envmanager.store import MemoryStore
from envmanager.theme import COLORS
from envmanager.ui import MainWindow, OPTION_STATUS_ROLE, OptionStatusDelegate, OptionComboBox


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
        self.show_window(self.window)

    def show_window(self, window):
        if self.app.platformName() == "windows":
            # Native popup tests need the disposable fixture in front; a
            # visible but occluded window can lose focus and dismiss a popup.
            window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        window.show()
        if self.app.platformName() == "windows":
            window.raise_()
            window.activateWindow()
            QTest.qWait(100)
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

    def reload_options(self, app, names):
        config = parse_config("\n".join(
            f'["{app}"."{name}"]\nHOME = {{ default_value = "{index}", scope = "User", mode = "replace", editable = true }}'
            for index, name in enumerate(names)
        ))
        self.service.reload(config)
        self.window._render_apps()
        self.window._select_app(app)
        self.app.processEvents()

    def assert_bounded_popup(self, combo, view):
        popup = view.window()
        screen = combo.screen().availableGeometry().adjusted(4, 4, -4, -4)
        self.assertTrue(screen.contains(popup.geometry()), (screen, popup.geometry()))
        expected = max(166, combo.width()) if combo.compact else combo.width()
        self.assertEqual(popup.width(), min(expected, screen.width()))
        self.assertEqual(view.horizontalScrollBar().maximum(), 0)

    def test_two_manifest_options_are_both_fully_visible_after_popup_show(self):
        combo = self.row("Java").option_combo
        view = self.popup(combo)
        self.assertEqual(combo.count(), 2)
        for index in range(combo.count()):
            rect = view.visualRect(combo.model().index(index, 0))
            self.assertTrue(view.viewport().rect().contains(rect), (index, rect, view.viewport().rect()))
        self.assertEqual(view.verticalScrollBar().maximum(), 0)
        self.assertEqual(self.store.writes, [])

    def test_both_popup_containers_have_one_surface_and_no_extra_row_space(self):
        for combo in (self.row("Java").option_combo, self.window.option_combo):
            with self.subTest(combo=combo.objectName()):
                view = self.popup(combo)
                popup = view.window()
                # These are the visible bounds, including the real window frame.
                self.assertEqual(popup.frameGeometry(), popup.geometry())
                self.assertEqual(view.geometry(), popup.rect())
                self.assertEqual(view.viewport().height(),
                                 sum(view.sizeHintForRow(i) for i in range(combo.count())))
                self.assertEqual(popup.y(), combo.mapToGlobal(QPoint(0, combo.height() + 4)).y())
                self.assert_bounded_popup(combo, view)
                combo.hidePopup()

    def test_selector_entries_keep_compact_sizes_and_popup_alignment(self):
        self.window.resize(1100, 560)
        self.window.workspace_splitter.setSizes([300, 800])
        self.app.processEvents()
        manifest = self.row("Java").option_combo
        detail = self.window.option_combo
        self.assertEqual(manifest.height(), 24)
        self.assertEqual(detail.size().width(), 166)
        self.assertEqual(detail.height(), 31)
        for combo in (manifest, detail):
            with self.subTest(combo=combo.objectName()):
                if combo is detail:
                    # offscreen commonly exposes an 800px screen while this
                    # application's minimum width is 850px. Put the anchor
                    # inside the work area before testing its right alignment;
                    # screen-edge clamping has its own independent regression.
                    screen = combo.screen().availableGeometry()
                    self.window.move(screen.right() - self.window.width() - 8,
                                     self.window.y())
                    self.app.processEvents()
                self.assertEqual(combo.font().pixelSize(), 12)
                view = self.popup(combo)
                self.assert_bounded_popup(combo, view)
                self.assertEqual(view.sizeHintForRow(0), 30)
                if combo is detail:
                    self.assertEqual(view.window().geometry().right(),
                                     combo.mapToGlobal(QPoint(combo.width() - 1, 0)).x())
                    self.assertEqual(view.window().x(), combo.mapToGlobal(QPoint(0, 0)).x())
                    self.assertEqual(combo._text_rect().left(), 32)
                else:
                    sidebar = QRect(self.window.app_list.mapToGlobal(QPoint(0, 0)),
                                    self.window.app_list.size())
                    self.assertGreaterEqual(sidebar.width(), view.window().width())
                    self.assertTrue(sidebar.contains(view.window().geometry()),
                                    (sidebar, view.window().geometry()))
                combo.hidePopup()
        self.assertEqual(self.store.writes, [])

    def test_popup_keyboard_selection_and_escape_preserve_real_scheme_state(self):
        for combo in (self.row("Java").option_combo, self.window.option_combo):
            self.window.option_combo.setCurrentText("JDK17")
            view = self.popup(combo)
            QTest.keyClick(view, Qt.Key.Key_Down)
            QTest.keyClick(view, Qt.Key.Key_Return)
            self.app.processEvents()
            self.assertFalse(view.isVisible())
            self.assertEqual(self.service.selected_option("Java"), "JDK21")
            view = self.popup(combo)
            QTest.keyClick(view, Qt.Key.Key_Up)
            QTest.keyClick(view, Qt.Key.Key_Escape)
            self.app.processEvents()
            self.assertFalse(view.isVisible())
            self.assertEqual(self.service.selected_option("Java"), "JDK21")
        self.assertEqual(self.store.writes, [])

    def test_popup_near_bottom_right_opens_above_without_clipping(self):
        host = QWidget()
        self.addCleanup(host.deleteLater)
        self.addCleanup(host.close)
        host.resize(210, 60)
        combo = OptionComboBox(host)
        combo.setGeometry(10, 20, 190, 30)
        combo.addItems(["3.9.11", "3.3.3"])
        combo.setOptionStatus(0, None, default=True, active=True,
                              presentation=("完全匹配", "complete", "2/2", ""))
        screen = host.screen().availableGeometry()
        host.move(screen.right() - host.width() - 8, screen.bottom() - host.height() - 8)
        self.show_window(host)
        view = self.popup(combo)
        popup = view.window()
        self.assertTrue(screen.contains(popup.frameGeometry()))
        self.assertLess(popup.geometry().right(), screen.right())
        self.assertEqual(popup.y() + popup.height() + 4, combo.mapToGlobal(QPoint(0, 0)).y())
        for i in range(combo.count()):
            self.assertTrue(view.viewport().rect().contains(view.visualRect(combo.model().index(i, 0))),
                            (view.geometry(), view.viewport().rect(), view.visualRect(combo.model().index(i, 0))))

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

    def test_long_names_elide_without_expanding_popup_and_keep_full_semantics(self):
        names = ["长期支持生产环境默认方案" * 3,
                 "Production_Long_Term_Support_Default_Environment_Profile" * 2]
        self.reload_options("中文应用", names)
        row = self.row("中文应用")
        detail = self.window.option_combo
        for combo in (row.option_combo, detail):
            for index, name in enumerate(names):
                with self.subTest(combo=combo.objectName(), name=name):
                    combo.setCurrentIndex(index)
                    self.app.processEvents()
                    self.assertIn("…", combo.displayedText())
                    self.assertEqual(combo.currentText(), name)
                    self.assertIn(name, combo.toolTip())
                    view = self.popup(combo)
                    self.assertIsInstance(view.itemDelegate(), OptionStatusDelegate)
                    self.assert_bounded_popup(combo, view)
                    self.assertIn(name, combo.itemData(index, Qt.ItemDataRole.ToolTipRole))
                    self.assertIn(name, combo.itemData(index, Qt.ItemDataRole.AccessibleTextRole))
                    rect = view.visualRect(combo.model().index(index, 0))
                    self.assertTrue(view.viewport().rect().contains(rect))
                    self.assertLess(rect.width(), view.fontMetrics().horizontalAdvance(name))
                    combo.hidePopup()
        self.assertEqual(self.window.width(), 850)
        self.assertEqual(self.store.writes, [])

    def test_many_options_have_complete_rows_and_scroll_to_last(self):
        options = [f"方案{index:02d}" for index in range(32)]
        self.reload_options("应用", options)
        for combo in (self.row("应用").option_combo, self.window.option_combo):
            with self.subTest(combo=combo.objectName()):
                combo.setCurrentIndex(0)
                view = self.popup(combo)
                self.assert_bounded_popup(combo, view)
                self.assertGreater(view.verticalScrollBar().maximum(), 0)
                self.assertEqual(view.viewport().height(), sum(view.sizeHintForRow(i) for i in range(3)))
                last = combo.model().index(combo.count() - 1, 0)
                view.scrollTo(last)
                self.app.processEvents()
                self.assertTrue(view.viewport().rect().contains(view.visualRect(last)))
                QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton,
                                 pos=view.visualRect(last).center())
                self.app.processEvents()
                self.assertFalse(view.isVisible())
                self.assertEqual(combo.currentIndex(), 31)
                self.assertEqual(self.service.selected_option("应用"), options[-1])
                # Reopening preserves the selected row and scrolls it into view.
                view = self.popup(combo)
                self.assertTrue(view.viewport().rect().contains(view.visualRect(last)))
                QTest.keyClick(view, Qt.Key.Key_Up)
                QTest.keyClick(view, Qt.Key.Key_Return)
                self.app.processEvents()
                self.assertEqual(self.service.selected_option("应用"), options[-2])
                combo.hidePopup()
        self.assertEqual(self.store.writes, [])

    def test_mouse_selection_and_outside_cancel_preserve_scheme_state(self):
        for combo in (self.row("Java").option_combo, self.window.option_combo):
            with self.subTest(combo=combo.objectName()):
                self.window.option_combo.setCurrentText("JDK17")
                view = self.popup(combo)
                second = combo.model().index(1, 0)
                QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton,
                                 pos=view.visualRect(second).center())
                self.app.processEvents()
                self.assertFalse(view.isVisible())
                self.assertEqual(self.service.selected_option("Java"), "JDK21")
                view = self.popup(combo)
                QTest.keyClick(view, Qt.Key.Key_Up)
                # A popup owns the mouse grab: an outside press is delivered
                # to its container with a position outside the surface.
                popup = view.window()
                outside = popup.mapFromGlobal(self.window.mapToGlobal(QPoint(2, 2)))
                self.assertFalse(popup.rect().contains(outside))
                QTest.mouseClick(popup, Qt.MouseButton.LeftButton, pos=outside)
                self.app.processEvents()
                self.assertFalse(view.isVisible())
                self.assertEqual(self.service.selected_option("Java"), "JDK21")
                self.assertEqual(combo.currentText(), "JDK21")
        self.assertEqual(self.store.writes, [])

    def test_popup_status_keeps_complete_tooltip_and_accessible_semantics(self):
        combo = self.window.option_combo
        presentations = [
            ("完全匹配", "complete", "2/2", "所有变量均匹配"),
            ("部分匹配", "partial", "1/2", "第二个变量未匹配"),
            ("未配置", "unconfigured", "", "请先配置变量"),
            ("检测失败", "error", "", "Windows 检测失败"),
            ("扫描中", "scanning", "", "正在检测 Windows 状态"),
        ]
        for text, tone, progress, detail in presentations:
            with self.subTest(tone=tone):
                combo.setOptionStatus(0, None, default=True, active=True,
                                      presentation=(text, tone, progress, detail))
                index = combo.model().index(0, 0)
                status = index.data(OPTION_STATUS_ROLE)
                self.assertEqual(status["text"], text)
                self.assertEqual(status["progress"], progress)
                for role in (Qt.ItemDataRole.ToolTipRole, Qt.ItemDataRole.AccessibleTextRole):
                    explanation = index.data(role)
                    for expected in (combo.itemText(0), "默认方案", "当前激活方案", text, progress):
                        self.assertIn(expected, explanation)
                self.assertIn(detail, index.data(Qt.ItemDataRole.ToolTipRole))
                view = self.popup(combo)
                self.assert_bounded_popup(combo, view)
                self.assertTrue(view.viewport().rect().contains(view.visualRect(index)))
                combo.hidePopup()
        self.assertEqual(self.store.writes, [])

    def test_status_reflects_actual_active_option_and_keeps_invalid_feedback(self):
        result = self.service.execute(self.service.plan("Python", "Python313"))
        self.assertTrue(result.success)
        self.window._select_app("Python")
        self.window.option_combo.setCurrentText("Python312")
        self.app.processEvents()
        row = self.row("Python")
        self.assertEqual(row.option_combo.toolTip(),
                         row.option_combo.currentData(Qt.ItemDataRole.ToolTipRole))
        self.assertIn("Python312", row.option_combo.toolTip())
        card = self.window.cards[0]
        # Ordinary edge whitespace is valid input in vNext; NUL remains
        # invalid and exercises the same persistent validation feedback.
        card.editor.setText("313\x00invalid")
        card.editor.textEdited.emit("313\x00invalid")
        self.app.processEvents()
        row = self.row("Python")
        for combo in (row.option_combo, self.window.option_combo):
            self.assertEqual(combo.toolTip(), combo.currentData(Qt.ItemDataRole.ToolTipRole))
            self.assertIn("Python312", combo.toolTip())
            self.assertIn(combo.currentData(OPTION_STATUS_ROLE)["display"], combo.toolTip())
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
