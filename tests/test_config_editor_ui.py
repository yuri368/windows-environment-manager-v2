"""Offscreen visual TOML editor tests with isolated disk and memory registry."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import QPoint, QTimer, Qt

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QMenu, QMessageBox, QPushButton

from envmanager.config import load_config
from envmanager.config_editor_ui import ConfigEditorDialog, ExactTextEditor, ExactValueDialog
from envmanager.persistence import StateRepository
from envmanager.service import EnvironmentService
from envmanager.store import MemoryStore
from envmanager.ui import MainWindow


BASE = (
    "# 请保留注释\n"
    "[App.One]\n"
    "HOME = { default_value = 'C:\\Tools\\One', scope = \"User\", mode = \"replace\", editable = true }\n"
    "LIST = { default_value = 'A;B', scope = \"User\", mode = \"append\", editable = false }\n"
    "\n[App.Two]\n"
    "HOME = { default_value = 'C:\\Tools\\Two', scope = \"User\", mode = \"replace\", editable = true }\n"
)


class ConfigEditorUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.temp = Path(temporary.name)
        self.path = self.temp / "env.toml"
        self.path.write_text(BASE, encoding="utf-8", newline="")
        self.store = MemoryStore()
        self.service = EnvironmentService(load_config(self.path), self.store,
                                          StateRepository(self.temp / "state"))

    def dialog(self, initial_tab="visual"):
        dialog = ConfigEditorDialog(self.path, self.service, initial_tab=initial_tab)
        self.addCleanup(dialog.deleteLater)
        return dialog

    @staticmethod
    def record_spec(dialog, app_index=0, option_index=0, record_index=0):
        groups = dialog.session.document.groups()
        apps = list(dict.fromkeys(app for app, _ in groups))
        app = apps[app_index]
        options = [option for group_app, option in groups if group_app == app]
        return dialog.session.document.records(app, options[option_index])[record_index]

    def test_initial_record_is_ready_without_changing_draft(self):
        dialog = self.dialog()
        self.assertEqual(dialog.name_edit.text(), "HOME")
        self.assertTrue(dialog._form_active)
        self.assertFalse(dialog._form_pending())
        self.assertFalse(dialog.session.dirty)
        self.assertFalse(dialog.session.can_undo)
        self.assertEqual(dialog.default_editor.exact_text(), r"C:\Tools\One")
        self.assertEqual(self.store.writes, [])

    def test_search_filters_global_records_and_keeps_pending_record(self):
        dialog = self.dialog()
        first = self.record_spec(dialog)
        dialog.name_edit.setText("Unsaved HOME")
        dialog.search_edit.setText("list")
        self.assertEqual(dialog.search_results.rowCount(), 1)
        self.assertEqual(dialog.search_results.item(0, 0).data(Qt.ItemDataRole.UserRole).name, "LIST")
        self.assertEqual(dialog.selected_token()[1].key, first.key)
        self.assertEqual(dialog.name_edit.text(), "Unsaved HOME")
        self.assertFalse(dialog.session.dirty)
        dialog.search_edit.setText("nothing matches")
        self.assertIn("未找到", dialog.search_status.text())
        dialog.search_edit.clear()
        self.assertEqual(dialog.record_list.rowCount(), 2)
        self.assertEqual(dialog.selected_token()[1].key, first.key)
        self.assertEqual(dialog.name_edit.text(), "Unsaved HOME")

    def test_app_and_option_overviews_open_records(self):
        dialog = self.dialog()
        dialog.select_application("App")
        self.assertFalse(dialog._form_active)
        self.assertEqual(dialog.overview.topLevelItemCount(), 3)
        self.assertIn("2 个方案", dialog.overview_summary.text())
        dialog.select_option("Two")
        self.assertEqual(dialog.overview.topLevelItemCount(), 1)
        self.assertEqual(dialog.form_heading.text(), "Two")
        dialog._open_overview_record(dialog.overview.topLevelItem(0))
        self.assertTrue(dialog._form_active)
        self.assertEqual(dialog.option_combo.currentText(), "Two")
        self.assertFalse(dialog.session.dirty)

    def test_overview_selector_navigates_both_directions_and_all_options(self):
        dialog = self.dialog()
        dialog.show()
        dialog.select_application("App")
        self.app.processEvents()
        combo = dialog.overview_option_combo
        self.assertTrue(combo.isVisible())
        self.assertEqual(combo.itemText(0), "全部方案")
        self.assertIn("默认", combo.itemText(combo.findData("One")))
        self.assertIsNone(combo.currentData())
        combo.setCurrentIndex(combo.findData("Two"))
        self.assertEqual(dialog.selected_token(), ("option", "App", "Two"))
        self.assertEqual(dialog.navigation_option, "Two")
        self.assertEqual(dialog.nav_option_combo.currentData(), "Two")
        self.assertEqual(dialog.overview.topLevelItemCount(), 1)
        self.assertFalse(dialog._form_active)
        dialog.nav_option_combo.setCurrentIndex(dialog.nav_option_combo.findData("One"))
        self.assertEqual(combo.currentData(), "One")
        self.assertEqual(dialog.selected_token(), ("option", "App", "One"))
        combo.setCurrentIndex(0)
        self.assertEqual(dialog.selected_token(), ("app", "App"))
        self.assertEqual(dialog.overview.topLevelItemCount(), 3)
        self.assertEqual(dialog.navigation_option, "One")
        self.assertIsNone(combo.currentData())
        self.assertFalse(dialog._form_active)
        dialog._open_overview_record(dialog.overview.topLevelItem(0))
        self.assertTrue(dialog._form_active)
        self.assertFalse(combo.isVisible())
        self.assertEqual(self.path.read_text(encoding="utf-8"), BASE)
        self.assertFalse(dialog.session.dirty)
        self.assertEqual(self.store.writes, [])
        dialog.hide()

    def test_overview_selector_rebuilds_for_other_application_and_single_option(self):
        extra = ('\n[Other.Solo]\n'
                 'HOME = [{ default_value = "user", scope = "User", mode = "replace", editable = true },'
                 '{ default_value = "system", scope = "System", mode = "replace", editable = true }]\n')
        self.path.write_text(BASE + extra, encoding="utf-8")
        dialog = self.dialog()
        dialog.show()
        dialog.select_application("App")
        dialog.overview_option_combo.setCurrentIndex(dialog.overview_option_combo.findData("Two"))
        dialog.app_list.setCurrentRow(1)
        self.app.processEvents()
        combo = dialog.overview_option_combo
        self.assertEqual(combo.count(), 2)
        self.assertTrue(combo.isVisible())
        self.assertTrue(combo.isEnabled())
        self.assertIsNone(combo.currentData())
        combo.setCurrentIndex(combo.findData("Solo"))
        self.assertEqual(dialog.selected_token(), ("option", "Other", "Solo"))
        self.assertEqual(dialog.nav_option_combo.currentData(), "Solo")
        scopes = {dialog.overview.topLevelItem(i).text(1) for i in range(2)}
        self.assertEqual(scopes, {"User", "System"})
        dialog._set_selection(None)
        self.assertFalse(combo.isEnabled())
        self.assertEqual(combo.currentText(), "暂无方案")
        self.assertFalse(dialog.session.dirty)
        self.assertEqual(self.path.read_text(encoding="utf-8"), BASE + extra)
        self.assertEqual(self.store.writes, [])
        dialog.hide()

    def test_overview_selector_cancel_keeps_pending_record_and_selection(self):
        dialog = self.dialog()
        first = dialog.selected_token()[1]
        dialog.name_edit.setText("pending name")
        with patch.object(dialog, "_ask_form_action", return_value="cancel") as guard:
            dialog.overview_option_combo.setCurrentIndex(dialog.overview_option_combo.findData("Two"))
        guard.assert_called_once()
        self.assertEqual(dialog.selected_token()[1].key, first.key)
        self.assertEqual(dialog.overview_option_combo.currentData(), "One")
        self.assertEqual(dialog.nav_option_combo.currentData(), "One")
        self.assertEqual(dialog.name_edit.text(), "pending name")
        self.assertFalse(dialog.session.dirty)
        self.assertEqual(self.path.read_text(encoding="utf-8"), BASE)
        self.assertEqual(self.store.writes, [])

    def test_overview_selector_long_name_elides_and_fits_narrow_window(self):
        name = "这是一个用于校验方案选择器完整名称与省略显示的很长方案名称" * 2
        long_config = BASE.replace("App.Two", f'App."{name}"')
        self.path.write_text(long_config, encoding="utf-8")
        dialog = self.dialog()
        dialog.resize(850, 600)
        dialog.show()
        dialog.select_application("App")
        self.app.processEvents()
        combo = dialog.overview_option_combo
        combo.setCurrentIndex(combo.findData(name))
        self.app.processEvents()
        self.assertEqual((dialog.width(), dialog.height()), (850, 600))
        self.assertEqual(combo.currentData(), name)
        self.assertEqual(combo.toolTip(), name)
        self.assertIn("…", combo.displayedText())
        self.assertLessEqual(combo.width(), 220)
        self.assertGreaterEqual(combo.width(), 100)
        origin = combo.mapTo(dialog.record_scroll.viewport(), QPoint(0, 0))
        viewport = dialog.record_scroll.viewport()
        self.assertGreaterEqual(origin.x(), 0)
        self.assertLessEqual(origin.x() + combo.width(), viewport.width())
        self.assertGreaterEqual(origin.y(), 0)
        self.assertLessEqual(origin.y() + combo.height(), viewport.height())
        self.assertEqual(dialog.record_scroll.horizontalScrollBar().maximum(), 0)
        combo.showPopup()
        self.app.processEvents()
        self.assertEqual(combo.view().textElideMode(), Qt.TextElideMode.ElideNone)
        self.assertEqual(combo.itemText(combo.findData(name)), name)
        self.assertEqual(combo.itemData(combo.findData(name), Qt.ItemDataRole.ToolTipRole), name)
        combo.hidePopup()
        self.assertFalse(dialog.session.dirty)
        self.assertEqual(self.path.read_text(encoding="utf-8"), long_config)
        self.assertEqual(self.store.writes, [])
        dialog.hide()

    def test_direct_create_buttons_honor_context_and_pending_guard(self):
        dialog = self.dialog()
        dialog.select_application("App")
        dialog._new_record()
        self.assertEqual(dialog.app_combo.currentText(), "App")
        self.assertEqual(dialog.option_combo.currentText(), "One")
        dialog.name_edit.setText("keep me")
        with patch.object(dialog, "_ask_form_action", return_value="cancel"):
            dialog.new_app_button.click()
        self.assertEqual(dialog.app_combo.currentText(), "App")
        self.assertEqual(dialog.name_edit.text(), "keep me")
        self.assertFalse(dialog.session.dirty)
        with patch.object(dialog, "_ask_form_action", return_value="discard"):
            dialog.new_app_button.click()
        self.assertEqual(dialog.app_combo.currentText(), "")
        self.assertEqual(dialog.option_combo.currentText(), "Default")
        self.assertFalse(hasattr(dialog, "more_button"))
        self.assertEqual(dialog.findChildren(QMenu), [])
        self.assertFalse(dialog.delete_record_button.isEnabled())
        self.assertEqual(self.store.writes, [])

    def test_actual_create_buttons_add_application_and_option_to_draft(self):
        dialog = self.dialog()
        dialog.new_app_button.click()
        dialog.app_combo.setCurrentText("Added App")
        dialog.name_edit.setText("NEW_HOME")
        dialog.default_editor.editor.setPlainText("first")
        dialog.update_button.click()
        self.assertIn(("Added App", "Default"), dialog.session.document.groups())
        dialog.new_option_button.click()
        self.assertEqual(dialog.app_combo.currentText(), "Added App")
        self.assertEqual(dialog.option_combo.currentText(), "")
        dialog.option_combo.setCurrentText("Second")
        dialog.name_edit.setText("SECOND_HOME")
        dialog.default_editor.editor.setPlainText("second")
        dialog.update_button.click()
        self.assertIn(("Added App", "Second"), dialog.session.document.groups())
        self.assertEqual(self.path.read_text(encoding="utf-8"), BASE)
        self.assertEqual(self.store.writes, [])

    def test_delete_buttons_use_current_navigation_and_variable_scope(self):
        extra = ('\n[Other.Default]\n'
                 'HOME = [ { default_value = "other", scope = "User", mode = "replace", editable = true },'
                 '{ default_value = "system", scope = "System", mode = "replace", editable = true } ]\n')
        self.path.write_text(BASE + extra, encoding="utf-8")
        dialog = self.dialog()
        dialog.search_edit.setText("other")
        dialog.search_results.setCurrentCell(1, 0)
        self.assertEqual(dialog.navigation_app, "Other")
        with patch.object(dialog, "_confirm_delete", return_value=True) as confirm:
            dialog.delete_record_button.click()
        self.assertIn("Other / Default / HOME (System)", confirm.call_args.args[0])
        remaining = dialog.session.document.records("Other", "Default")
        self.assertEqual([spec.scope for spec in remaining], ["User"])
        dialog.select_application("App")
        dialog.select_option("Two")
        with patch.object(dialog, "_confirm_delete", return_value=True) as confirm:
            dialog.delete_option_button.click()
        self.assertIn("方案 App / Two", confirm.call_args.args[0])
        self.assertIn(("Other", "Default"), dialog.session.document.groups())
        dialog.select_application("Other")
        with patch.object(dialog, "_confirm_delete", return_value=True) as confirm:
            dialog.delete_app_button.click()
        self.assertEqual(confirm.call_args.args[0], "应用 Other")
        self.assertEqual(dialog.session.document.groups(), (("App", "One"),))
        self.assertEqual(self.path.read_text(encoding="utf-8"), BASE + extra)
        self.assertEqual(self.store.writes, [])

    def test_pending_form_cancel_blocks_each_delete_button(self):
        for attribute in ("delete_record_button", "delete_option_button", "delete_app_button"):
            with self.subTest(button=attribute):
                dialog = self.dialog()
                dialog.name_edit.setText("pending name")
                with patch.object(dialog, "_ask_form_action", return_value="cancel") as guard, \
                     patch.object(dialog, "_confirm_delete", return_value=True) as confirm:
                    getattr(dialog, attribute).click()
                guard.assert_called_once()
                confirm.assert_not_called()
                self.assertEqual(dialog.session.text, BASE)
                self.assertEqual(dialog.name_edit.text(), "pending name")
                self.assertEqual(self.store.writes, [])

    def test_group_delete_keeps_clicked_scope_when_guard_moves_variable(self):
        for attribute, expected in (("delete_option_button", "方案 App / One"),
                                    ("delete_app_button", "应用 App")):
            with self.subTest(button=attribute):
                dialog = self.dialog()
                dialog.app_combo.setCurrentText("Moved App")
                dialog.option_combo.setCurrentText("Fresh")
                with patch.object(dialog, "_ask_form_action", return_value="update"), \
                     patch.object(dialog, "_confirm_delete", return_value=True) as confirm:
                    getattr(dialog, attribute).click()
                self.assertIn(expected, confirm.call_args.args[0])
                self.assertIn(("Moved App", "Fresh"), dialog.session.document.groups())
                self.assertEqual(self.path.read_text(encoding="utf-8"), BASE)
                self.assertEqual(self.store.writes, [])

    def test_direct_actions_stay_visible_and_fit_minimum_sidebar(self):
        extra = "".join(f'\n[Other{index}.Default]\n'
                        'HOME = { default_value = "other", scope = "User", mode = "replace", editable = false }\n'
                        for index in range(7))
        self.path.write_text(BASE + extra, encoding="utf-8")
        dialog = self.dialog()
        controls = (dialog.new_app_button, dialog.new_option_button, dialog.new_record_button, dialog.delete_app_button,
                    dialog.delete_option_button, dialog.delete_record_button,
                    dialog.app_overview_button, dialog.option_overview_button, dialog.set_default_button)
        for width, height in ((850, 600), (850, 680), (1280, 680)):
            with self.subTest(width=width, height=height):
                dialog.resize(width, height)
                dialog.show()
                dialog.workspace_splitter.setSizes([216, width - 252])
                self.app.processEvents()
                self.assertEqual((dialog.width(), dialog.height()), (width, height))
                browser = dialog.workspace_splitter.widget(0)
                self.assertEqual(browser.width(), 216)
                app_list_origin = dialog.app_list.mapTo(browser, QPoint(0, 0))
                option_header_origin = dialog.option_overview_button.mapTo(browser, QPoint(0, 0))
                self.assertLessEqual(app_list_origin.y() + dialog.app_list.height(), option_header_origin.y())
                for button in controls:
                    self.assertTrue(button.isVisible(), button.text())
                    parent = button.parentWidget()
                    origin = button.mapTo(parent, QPoint(0, 0))
                    self.assertGreaterEqual(origin.x(), 0)
                    self.assertLessEqual(origin.x() + button.width(), parent.width(), button.text())
                    self.assertGreaterEqual(button.width(), button.sizeHint().width(), button.text())
                    browser_origin = button.mapTo(browser, QPoint(0, 0))
                    self.assertGreaterEqual(browser_origin.y(), 0)
                    self.assertLessEqual(browser_origin.y() + button.height(), browser.height(), button.text())
                for new_button, delete_button in ((dialog.new_app_button, dialog.delete_app_button),
                                                 (dialog.new_option_button, dialog.delete_option_button),
                                                 (dialog.new_record_button, dialog.delete_record_button)):
                    new_origin = new_button.mapTo(browser, QPoint(0, 0))
                    delete_origin = delete_button.mapTo(browser, QPoint(0, 0))
                    self.assertEqual(new_button.text(), "新增")
                    self.assertEqual(delete_button.text(), "删除")
                    self.assertEqual(new_origin.y(), delete_origin.y())
                    self.assertLessEqual(delete_origin.x() - new_origin.x() - new_button.width(), 4)
                    self.assertIn("新增", new_button.accessibleName())
                    self.assertIn("删除", delete_button.accessibleName())
                dialog.search_edit.setText("nothing matches")
                self.app.processEvents()
                self.assertEqual((dialog.width(), dialog.height()), (width, height))
                self.assertTrue(dialog.new_app_button.isVisible())
                self.assertTrue(dialog.delete_app_button.isVisible())
                self.assertTrue(dialog.delete_option_button.isVisible())
                self.assertTrue(dialog.delete_record_button.isVisible())
                app_list_origin = dialog.app_list.mapTo(browser, QPoint(0, 0))
                option_header_origin = dialog.option_overview_button.mapTo(browser, QPoint(0, 0))
                self.assertLessEqual(app_list_origin.y() + dialog.app_list.height(), option_header_origin.y())
                dialog.search_edit.clear()
        dialog.hide()
        self.assertEqual(self.path.read_text(encoding="utf-8"), BASE + extra)
        self.assertEqual(self.store.writes, [])

    def test_direct_action_availability_for_new_empty_and_invalid_source(self):
        dialog = self.dialog()
        dialog.new_record_button.click()
        self.assertFalse(dialog.delete_record_button.isEnabled())
        with patch.object(dialog, "_confirm_delete") as confirm:
            dialog.delete_record_button.click()
            dialog._delete_record()
        confirm.assert_not_called()
        self.assertFalse(dialog.delete_record_button.isHidden())
        with patch.object(dialog, "_ask_form_action", return_value="discard"):
            dialog.select_application("App")
        self.assertFalse(dialog.delete_record_button.isHidden())
        self.assertFalse(dialog.delete_record_button.isEnabled())
        dialog.select_option("One")
        self.assertFalse(dialog.delete_record_button.isHidden())
        self.assertFalse(dialog.delete_record_button.isEnabled())
        dialog._set_selection(None)
        self.assertTrue(dialog.new_app_button.isEnabled())
        self.assertFalse(dialog.new_option_button.isEnabled())
        self.assertFalse(dialog.delete_app_button.isEnabled())
        self.assertFalse(dialog.delete_option_button.isEnabled())
        self.assertFalse(dialog.delete_record_button.isEnabled())
        dialog.tabs.setCurrentIndex(1)
        dialog.source.setPlainText("[broken")
        for button in (dialog.new_app_button, dialog.new_option_button, dialog.delete_app_button,
                       dialog.delete_option_button, dialog.delete_record_button):
            self.assertFalse(button.isEnabled())
        self.assertEqual(self.path.read_text(encoding="utf-8"), BASE)
        self.assertEqual(self.store.writes, [])

    def test_paired_actions_remain_findable_after_deleting_last_application(self):
        dialog = self.dialog()
        with patch.object(dialog, "_confirm_delete", return_value=True):
            dialog.delete_app_button.click()
        self.assertEqual(dialog.session.document.groups(), ())
        self.assertTrue(dialog.new_app_button.isEnabled())
        for button in (dialog.new_option_button, dialog.delete_app_button,
                       dialog.delete_option_button, dialog.delete_record_button):
            self.assertFalse(button.isEnabled())
            self.assertFalse(button.isHidden())
        self.assertEqual(self.path.read_text(encoding="utf-8"), BASE)
        self.assertEqual(self.store.writes, [])

    def test_workspace_reflows_without_horizontal_overflow(self):
        dialog = self.dialog()
        dialog.show()
        self.app.processEvents()
        self.assertFalse(dialog._compact_form)
        self.assertGreater(dialog.inspector.geometry().left(), dialog.value_pane.geometry().left())
        self.assertTrue(dialog.default_editor.isVisible())
        self.assertTrue(dialog.choices_empty.isVisible())
        self.assertFalse(dialog.choices.isVisible())
        self.assertTrue(dialog.update_button.isVisible())
        dialog.resize(820, 680)
        self.app.processEvents()
        self.assertTrue(dialog._compact_form)
        self.assertGreater(dialog.inspector.geometry().top(), dialog.value_pane.geometry().top())
        panel = dialog.workspace_splitter.widget(1)
        self.assertEqual(panel.horizontalScrollBar().maximum(), 0)
        self.assertLessEqual(panel.widget().width(), panel.viewport().width())
        dialog.hide()

    def test_pending_and_updated_draft_have_distinct_state_text(self):
        dialog = self.dialog()
        dialog.default_editor.editor.setPlainText("changed")
        self.assertIn("尚未进入草稿", dialog.form_state.text())
        self.assertIn("尚未更新", dialog.dirty_label.text())
        dialog.update_button.click()
        self.assertIn("未保存", dialog.form_state.text())
        self.assertIn("尚未保存", dialog.dirty_label.text())
        self.assertFalse(dialog.update_button.isEnabled())
        self.assertEqual(self.path.read_text(encoding="utf-8"), BASE)

    def test_narrow_invalid_record_reveals_error_and_repair_field(self):
        for width, error_kind in ((820, "name"), (850, "default")):
            with self.subTest(width=width, error_kind=error_kind):
                dialog = self.dialog()
                dialog.resize(width, 680)
                dialog.show()
                self.app.processEvents()
                if error_kind == "name":
                    dialog.name_edit.setText("BAD=NAME")
                    repair = dialog.name_edit
                    expected = "BAD=NAME"
                else:
                    dialog.mode_combo.setCurrentIndex(dialog.mode_combo.findData("append"))
                    dialog.default_editor.editor.setPlainText("A\x00B")
                    repair = dialog.default_editor.editor
                    expected = "A\x00B"
                dialog.record_scroll.verticalScrollBar().setValue(0)
                self.assertFalse(dialog._commit_form())
                self.app.processEvents()
                self.app.processEvents()
                viewport = dialog.record_scroll.viewport()
                error_origin = dialog.form_error.mapTo(viewport, QPoint(0, 0))
                repair_origin = repair.mapTo(viewport, QPoint(0, 0))
                self.assertTrue(dialog.form_error.isVisible())
                self.assertGreaterEqual(error_origin.y(), 0)
                self.assertLessEqual(error_origin.y() + dialog.form_error.height(), viewport.height())
                self.assertGreaterEqual(repair_origin.y(), 0)
                self.assertLessEqual(repair_origin.y() + repair.height(), viewport.height())
                self.assertIs(dialog.focusWidget(), repair)
                if error_kind == "name":
                    self.assertEqual(dialog.name_edit.text(), expected)
                else:
                    self.assertEqual(dialog.default_editor.exact_text(), expected)
                self.assertTrue(dialog._form_pending())
                self.assertFalse(dialog.session.dirty)
                self.assertEqual(self.path.read_text(encoding="utf-8"), BASE)
                self.assertEqual(self.store.writes, [])
                dialog.hide()

    def test_flat_navigation_switches_app_and_option_with_pending_guard(self):
        extra = ('\n[Other.Default]\n'
                 'OTHER_HOME = { default_value = "other", scope = "System", mode = "replace", editable = false }\n')
        self.path.write_text(BASE + extra, encoding="utf-8")
        dialog = self.dialog()
        self.assertFalse(hasattr(dialog, "tree"))
        self.assertEqual(dialog.app_list.count(), 2)
        self.assertEqual(dialog.record_list.item(0, 0).text(), "HOME")
        self.assertEqual(dialog.record_list.item(0, 1).text(), "用户")
        first = dialog.selected_token()[1]
        dialog.name_edit.setText("pending edit")
        with patch.object(dialog, "_ask_form_action", return_value="cancel"):
            dialog.app_list.setCurrentRow(1)
        self.assertEqual(dialog.app_list.currentItem().text(), "App")
        self.assertEqual(dialog.navigation_app, "App")
        self.assertEqual(dialog.selected_token()[1].key, first.key)
        self.assertEqual(dialog.name_edit.text(), "pending edit")
        with patch.object(dialog, "_ask_form_action", return_value="cancel"):
            dialog.nav_option_combo.setCurrentIndex(dialog.nav_option_combo.findData("Two"))
        self.assertEqual(dialog.navigation_option, "One")
        self.assertEqual(dialog.nav_option_combo.currentData(), "One")
        self.assertEqual(dialog.name_edit.text(), "pending edit")
        with patch.object(dialog, "_ask_form_action", return_value="discard"):
            dialog.app_list.setCurrentRow(1)
        self.assertEqual(dialog.navigation_app, "Other")
        self.assertEqual(dialog.navigation_option, "Default")
        self.assertFalse(dialog._form_active)
        self.assertEqual(dialog.record_list.rowCount(), 1)
        self.assertEqual(dialog.record_list.item(0, 0).text(), "OTHER_HOME")
        self.assertEqual(dialog.record_list.item(0, 1).text(), "系统")
        dialog.record_list.setCurrentCell(0, 0)
        self.assertTrue(dialog._form_active)
        self.assertEqual(dialog.name_edit.text(), "OTHER_HOME")
        self.assertFalse(dialog.session.dirty)
        self.assertEqual(self.path.read_text(encoding="utf-8"), BASE + extra)
        self.assertEqual(self.store.writes, [])

    def test_global_search_selects_cross_app_result_with_guard(self):
        extra = ('\n[Other.Default]\n'
                 'OTHER_HOME = { default_value = "other", scope = "System", mode = "replace", editable = false }\n')
        self.path.write_text(BASE + extra, encoding="utf-8")
        dialog = self.dialog()
        dialog.search_edit.setText("other")
        self.assertEqual(dialog.search_results.rowCount(), 1)
        self.assertIn("Other / Default", dialog.search_results.item(0, 0).text())
        dialog.default_editor.editor.setPlainText("pending value")
        with patch.object(dialog, "_ask_form_action", return_value="cancel"):
            dialog.search_results.setCurrentCell(0, 0)
        self.assertEqual(dialog.navigation_app, "App")
        self.assertEqual(dialog.default_editor.exact_text(), "pending value")
        with patch.object(dialog, "_ask_form_action", return_value="update"):
            dialog.search_results.setCurrentCell(0, 0)
        self.assertEqual(dialog.navigation_app, "Other")
        self.assertEqual(dialog.navigation_option, "Default")
        self.assertEqual(dialog.name_edit.text(), "OTHER_HOME")
        self.assertIn("pending value", dialog.session.text)
        dialog.search_edit.clear()
        self.assertEqual(dialog.app_list.currentItem().text(), "Other")
        self.assertEqual(dialog.record_list.currentRow(), 0)
        self.assertEqual(self.path.read_text(encoding="utf-8"), BASE + extra)
        self.assertEqual(self.store.writes, [])

    def test_default_and_candidate_controls_are_compact_and_top_aligned(self):
        dialog = self.dialog()
        dialog.show()
        self.app.processEvents()
        self.assertEqual(dialog.default_editor.editor.height(), 72)
        self.assertFalse(dialog.choices.isVisible())
        self.assertTrue(dialog.choices_empty.isVisible())
        dialog._append_choice("One", "value one")
        dialog._append_choice("Two", "value two")
        self.app.processEvents()
        self.assertTrue(dialog.choices.isVisible())
        self.assertFalse(dialog.choices_empty.isVisible())
        self.assertLessEqual(dialog.choices.height(), 96)
        self.assertLess(dialog.value_pane.geometry().top(), 12)
        self.assertLess(dialog.inspector.geometry().top(), 12)
        self.assertLess(dialog.value_pane.height(), 320)
        self.assertLess(dialog.save_button.width(), 240)
        self.assertLess(dialog.update_button.width(), 180)
        self.assertGreater(dialog.save_button.geometry().left(), dialog.width() // 2)
        dialog.hide()

    def test_record_form_updates_draft_without_disk_or_registry_write(self):
        dialog = self.dialog()
        self.assertEqual(dialog.app_list.count(), 1)
        self.assertEqual(dialog.record_list.item(0, 0).text(), "HOME")
        self.assertEqual(dialog.record_list.item(0, 1).text(), "用户")
        dialog.select_record(self.record_spec(dialog))
        dialog.default_editor.set_exact_text(r"C:\Tools\Changed")
        dialog.mode_combo.setCurrentIndex(dialog.mode_combo.findData("+append"))
        dialog.editable_check.setChecked(False)
        self.assertTrue(dialog._commit_form())
        updated = next(spec for spec in dialog.session.document.records("App", "One") if spec.name == "HOME")
        self.assertEqual(updated.default_value, r"C:\Tools\Changed")
        self.assertEqual(updated.mode, "+append")
        self.assertFalse(updated.editable)
        self.assertIn("# 请保留注释", dialog.session.text)
        self.assertEqual(self.path.read_text(encoding="utf-8"), BASE)
        self.assertEqual(self.store.writes, [])

    def test_default_option_button_only_changes_draft_until_explicit_save(self):
        dialog = self.dialog()
        self.assertIn("默认", dialog.nav_option_combo.itemText(0))
        dialog.select_option("Two")
        self.assertTrue(dialog.set_default_button.isEnabled())
        dialog.set_default_button.click()
        self.assertEqual(dialog.session.document.default_option("App"), "Two")
        self.assertIn("默认", dialog.nav_option_combo.itemText(1))
        self.assertFalse(dialog.set_default_button.isEnabled())
        self.assertEqual(self.path.read_text(encoding="utf-8"), BASE)
        self.assertEqual(self.store.writes, [])
        dialog._undo()
        self.assertEqual(dialog.session.document.default_option("App"), "One")
        dialog._redo()
        self.assertEqual(dialog.session.document.default_option("App"), "Two")
        with patch.object(dialog, "_confirm_diff", return_value=True):
            self.assertTrue(dialog._save())
        self.assertEqual(load_config(self.path).default_option("App"), "Two")
        self.assertEqual(self.store.writes, [])

    def test_delete_default_option_confirmation_describes_fallback(self):
        dialog = self.dialog()
        dialog.select_option("One")
        with patch.object(dialog, "_confirm_delete", return_value=True) as confirm:
            dialog._delete_option()
        self.assertIn("将 Two 设为该应用的默认方案", confirm.call_args.args[0])
        self.assertEqual(dialog.session.document.default_option("App"), "Two")
        self.assertEqual(self.path.read_text(encoding="utf-8"), BASE)

    def test_new_record_choice_empty_value_and_dual_scope(self):
        dialog = self.dialog()
        dialog.select_record(self.record_spec(dialog))
        dialog._new_record()
        self.assertEqual(dialog.app_combo.currentText(), "App")
        self.assertEqual(dialog.option_combo.currentText(), "One")
        dialog.name_edit.setText("HOME")
        dialog.scope_combo.setCurrentIndex(dialog.scope_combo.findData("System"))
        dialog.default_editor.set_exact_text("D:/New")
        dialog._add_choice()
        dialog.choices.item(0, 0).setText("空值")
        self.assertTrue(dialog._commit_form())
        pair = tuple(spec for spec in dialog.session.document.records("App", "One") if spec.name == "HOME")
        self.assertEqual({spec.scope for spec in pair}, {"User", "System"})
        self.assertEqual(pair[1].choices[0].value, "")
        self.assertEqual(self.store.writes, [])

    def test_new_named_group_and_rename_move_record(self):
        dialog = self.dialog()
        dialog.select_record(self.record_spec(dialog, record_index=1))
        dialog.app_combo.setCurrentText("New App")
        dialog.option_combo.setCurrentText("Fresh")
        dialog.name_edit.setText("MY_PATH")
        self.assertTrue(dialog._commit_form())
        moved = dialog.session.document.records("New App", "Fresh")
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0].name, "MY_PATH")
        self.assertTrue(all(spec.name != "LIST" for spec in dialog.session.document.records("App", "One")))
        self.assertEqual(self.path.read_text(encoding="utf-8"), BASE)

    def test_delete_record_and_group_are_draft_only(self):
        dialog = self.dialog()
        dialog.select_record(self.record_spec(dialog, record_index=1))
        with patch("envmanager.config_editor_ui.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            dialog._delete_record()
            self.assertEqual([spec.name for spec in dialog.session.document.records("App", "One")], ["HOME"])
            dialog.select_record(self.record_spec(dialog))
            dialog._delete_option()
            self.assertEqual(dialog.session.document.groups(), (("App", "Two"),))
            dialog.select_application("App")
            dialog._delete_app()
        self.assertEqual(dialog.session.document.groups(), ())
        self.assertFalse(dialog.save_button.isEnabled())
        self.assertEqual(self.path.read_text(encoding="utf-8"), BASE)
        self.assertEqual(self.store.writes, [])

    def test_navigation_cancel_preserves_invalid_form_and_update_failure(self):
        dialog = self.dialog()
        first = self.record_spec(dialog)
        second = self.record_spec(dialog, record_index=1)
        dialog.select_record(first)
        dialog.name_edit.setText("bad=name")
        with patch.object(dialog, "_ask_form_action", return_value="cancel"):
            dialog.select_record(second)
        self.assertEqual(dialog.selected_token()[1].key, first.key)
        self.assertEqual(dialog.name_edit.text(), "bad=name")
        with patch.object(dialog, "_ask_form_action", return_value="update"):
            dialog.select_record(second)
        self.assertEqual(dialog.name_edit.text(), "bad=name")
        self.assertTrue(dialog.form_error.isVisible() or dialog.form_error.text())
        self.assertEqual(self.path.read_text(encoding="utf-8"), BASE)

    def test_source_invalid_kept_and_visual_blocked(self):
        dialog = self.dialog(initial_tab="source")
        invalid = "[App.One]\nHOME = ["
        dialog.source.setPlainText(invalid)
        self.assertEqual(dialog.session.text, invalid)
        self.assertIsNone(dialog.session.document)
        self.assertFalse(dialog.tabs.isTabEnabled(0))
        self.assertFalse(dialog.save_button.isEnabled())
        self.assertFalse(dialog.overview_option_combo.isEnabled())
        dialog.source.setPlainText(BASE)
        self.assertIsNotNone(dialog.session.document)
        self.assertTrue(dialog.tabs.isTabEnabled(0))
        self.assertEqual(self.path.read_text(encoding="utf-8"), BASE)

    def test_source_small_edit_keeps_windows_line_endings(self):
        windows_text = BASE.replace("\n", "\r\n")
        self.path.write_bytes(windows_text.encode("utf-8"))
        self.service = EnvironmentService(load_config(self.path), self.store,
                                          StateRepository(self.temp / "crlf-state"))
        dialog = self.dialog(initial_tab="source")
        dialog.source.setPlainText(dialog.source.toPlainText().replace("# 请保留注释", "# 已调整注释", 1))
        self.assertIn("# 已调整注释\r\n", dialog.session.text)
        self.assertEqual(dialog.session.text.count("\r\n"), windows_text.count("\r\n"))
        self.assertEqual(dialog.session.text.replace("\r\n", "").count("\n"), 0)
        self.assertEqual(self.path.read_bytes(), windows_text.encode("utf-8"))

    def test_syntax_coloring_preserves_exact_draft_and_undo(self):
        original = "# 🌱 配置草稿\r\n" + BASE.replace("\n", "\r\n")
        self.path.write_bytes(original.encode("utf-8"))
        dialog = self.dialog(initial_tab="source")
        dialog.source_highlighter.rehighlight()
        self.app.processEvents()
        self.assertEqual(dialog.session.text, original)
        self.assertFalse(dialog.session.dirty)
        self.assertFalse(dialog.session.can_undo)

        dialog.source.setPlainText(dialog.source.toPlainText().replace("# 请保留注释", "# 修改注释", 1))
        self.app.processEvents()
        draft = dialog.session.text
        self.assertIn("# 修改注释\r\n", draft)
        dialog.source_highlighter.rehighlight()
        self.app.processEvents()
        self.assertEqual(dialog.session.text, draft)
        dialog._undo()
        self.app.processEvents()
        self.assertEqual(dialog.session.text, original)
        self.assertFalse(dialog.session.dirty)
        dialog._redo()
        self.app.processEvents()
        self.assertEqual(dialog.session.text, draft)
        self.assertEqual(self.path.read_bytes(), original.encode("utf-8"))
        self.assertEqual(self.store.writes, [])

    def test_undo_redo_after_visual_command(self):
        dialog = self.dialog()
        dialog.select_record(self.record_spec(dialog))
        dialog.default_editor.set_exact_text("Changed")
        self.assertTrue(dialog._commit_form())
        self.assertTrue(dialog.session.can_undo)
        dialog._undo()
        self.assertEqual(dialog.session.text, BASE)
        dialog._redo()
        self.assertIn("Changed", dialog.session.text)

    def test_save_confirms_diff_and_refreshes_service_without_registry_write(self):
        dialog = self.dialog()
        dialog.select_record(self.record_spec(dialog))
        dialog.default_editor.set_exact_text("Changed")
        self.assertTrue(dialog._commit_form())
        self.assertIn("Changed", dialog.session.diff())
        with patch.object(dialog, "_confirm_diff", return_value=True) as confirm:
            self.assertTrue(dialog._save())
        confirm.assert_called_once()
        self.assertTrue(dialog.saved)
        self.assertIn("Changed", self.path.read_text(encoding="utf-8"))
        self.assertEqual(self.service.config.variables("App", "One")[0].default_value, "Changed")
        self.assertEqual(self.store.writes, [])

    def test_save_button_commits_pending_form_on_real_click(self):
        dialog = self.dialog()
        dialog.select_record(self.record_spec(dialog))
        dialog.default_editor.editor.setPlainText("Pending change")
        self.assertFalse(dialog.session.dirty)
        self.assertTrue(dialog.save_button.isEnabled())
        with patch.object(dialog, "_ask_form_action", return_value="update") as prompt, \
             patch.object(dialog, "_confirm_diff", return_value=True):
            dialog.save_button.click()
        prompt.assert_called_once()
        self.assertTrue(dialog.saved)
        self.assertIn("Pending change", self.path.read_text(encoding="utf-8"))
        self.assertEqual(self.store.writes, [])

    def test_local_update_button_commits_form_on_real_click(self):
        dialog = self.dialog()
        dialog.show()
        self.app.processEvents()
        dialog.select_record(self.record_spec(dialog))
        dialog.default_editor.editor.setPlainText("Updated from record pane")
        self.assertTrue(dialog.update_button.isVisible())
        self.assertTrue(dialog.update_button.isEnabled())
        dialog.update_button.click()
        self.assertTrue(dialog.session.dirty)
        self.assertFalse(dialog.update_button.isEnabled())
        self.assertIn("Updated from record pane", dialog.session.text)
        dialog.hide()

    def test_save_button_keeps_invalid_pending_form_on_real_click(self):
        dialog = self.dialog()
        dialog.select_record(self.record_spec(dialog))
        dialog.name_edit.setText("bad=name")
        self.assertTrue(dialog.save_button.isEnabled())
        with patch.object(dialog, "_ask_form_action", return_value="update"):
            dialog.save_button.click()
        self.assertFalse(dialog.saved)
        self.assertEqual(dialog.name_edit.text(), "bad=name")
        self.assertEqual(self.path.read_text(encoding="utf-8"), BASE)

    def test_edit_selected_choice_button_uses_selected_row(self):
        dialog = self.dialog()
        dialog.select_record(self.record_spec(dialog))
        dialog._append_choice("first", "value-one")
        dialog._append_choice("second", "value-two")
        dialog.choices.setCurrentCell(1, 0)
        button = next(widget for widget in dialog.findChildren(QPushButton) if widget.text() == "编辑选中值")
        with patch("envmanager.config_editor_ui.ExactValueDialog.exec", return_value=QDialog.DialogCode.Accepted), \
             patch("envmanager.config_editor_ui.ExactValueDialog.value", return_value="changed-two"):
            button.click()
        self.assertEqual(dialog._choices_data(), [("first", "value-one"), ("second", "changed-two")])

    def test_save_warning_reports_after_success_and_accepts(self):
        dialog = self.dialog()
        dialog.select_record(self.record_spec(dialog))
        dialog.default_editor.set_exact_text("Saved with warning")
        self.assertTrue(dialog._commit_form())
        original_save = dialog.session.save

        def save_with_warning(service):
            result = original_save(service)
            dialog.session.save_warning = "刷新后提示"
            return result

        with patch.object(dialog, "_confirm_diff", return_value=True), \
             patch.object(dialog.session, "save", side_effect=save_with_warning), \
             patch("envmanager.config_editor_ui.QMessageBox.warning") as warning:
            self.assertTrue(dialog._save())
        warning.assert_called_once()
        self.assertTrue(dialog.saved)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertIn("Saved with warning", self.path.read_text(encoding="utf-8"))

    def test_main_window_two_config_entries_choose_expected_tab(self):
        window = MainWindow(self.service, self.path, demo=True)
        self.addCleanup(window.deleteLater)
        with patch("envmanager.ui.ConfigEditorDialog") as dialog_class:
            dialog_class.return_value.exec.return_value = QDialog.DialogCode.Rejected
            dialog_class.return_value.saved = False
            window._visual_config()
            window._edit_config()
        self.assertEqual(dialog_class.call_count, 2)
        self.assertEqual(dialog_class.call_args_list[0].kwargs["initial_tab"], "visual")
        self.assertEqual(dialog_class.call_args_list[1].kwargs["initial_tab"], "source")
        self.assertEqual(self.store.writes, [])

    def test_main_window_real_editor_add_modify_delete_save_and_refresh(self):
        window = MainWindow(self.service, self.path, demo=True)
        self.addCleanup(window.deleteLater)
        errors = []
        snapshots = []

        def factory(*args, **kwargs):
            dialog = ConfigEditorDialog(*args, **kwargs)
            self.addCleanup(dialog.deleteLater)

            def edit_and_save():
                try:
                    dialog.select_record(self.record_spec(dialog))
                    dialog.default_editor.set_exact_text("Modified HOME")
                    self.assertTrue(dialog._commit_form())
                    dialog._new_record()
                    dialog.name_edit.setText("NEW_VALUE")
                    dialog.default_editor.set_exact_text("Added value")
                    self.assertTrue(dialog._commit_form())
                    target = next(spec for spec in dialog.session.document.records("App", "One")
                                  if spec.name == "LIST")
                    dialog.select_record(target)
                    with patch.object(dialog, "_confirm_delete", return_value=True):
                        dialog._delete_record()
                    with patch.object(dialog, "_confirm_diff", return_value=True):
                        self.assertTrue(dialog._save())
                    snapshots.append(dialog.saved)
                except BaseException as exc:
                    errors.append(exc)
                    QDialog.reject(dialog)

            QTimer.singleShot(0, edit_and_save)
            return dialog

        with patch("envmanager.ui.ConfigEditorDialog", side_effect=factory):
            window._visual_config()
        if errors:
            raise errors[0]
        self.assertEqual(snapshots, [True])
        records = load_config(self.path).variables("App", "One")
        self.assertEqual({spec.name: spec.default_value for spec in records},
                         {"HOME": "Modified HOME", "NEW_VALUE": "Added value"})
        self.assertEqual({card.spec.name for card in window.cards}, {"HOME", "NEW_VALUE"})
        self.assertIn("配置已校验并保存", window.feedback.text())
        self.assertEqual(self.store.writes, [])
        self.assertEqual(self.service.state.snapshot()["active"], {})

    def test_external_change_blocks_save_and_keeps_draft(self):
        dialog = self.dialog()
        dialog.select_record(self.record_spec(dialog))
        dialog.default_editor.set_exact_text("Changed")
        self.assertTrue(dialog._commit_form())
        external = BASE + "\n# external\n"
        self.path.write_text(external, encoding="utf-8")
        with patch.object(dialog, "_confirm_diff", return_value=True), \
             patch.object(dialog, "_conflict") as conflict:
            self.assertFalse(dialog._save())
        conflict.assert_called_once()
        self.assertFalse(dialog.saved)
        self.assertEqual(self.path.read_text(encoding="utf-8"), external)
        self.assertIn("Changed", dialog.session.text)
        self.assertEqual(self.store.writes, [])

    def test_unsaved_close_cancel_then_discard_never_writes(self):
        dialog = self.dialog()
        dialog.select_record(self.record_spec(dialog))
        dialog.default_editor.set_exact_text("Changed")
        self.assertTrue(dialog._commit_form())
        with patch.object(dialog, "_ask_close_action", return_value="cancel"):
            dialog.reject()
        self.assertFalse(dialog.saved)
        self.assertEqual(self.path.read_text(encoding="utf-8"), BASE)
        with patch.object(dialog, "_ask_close_action", return_value="discard"):
            dialog.reject()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertEqual(self.path.read_text(encoding="utf-8"), BASE)
        self.assertEqual(self.store.writes, [])

    def test_exact_text_editor_preserves_cr_and_unicode_separators(self):
        editor = ExactTextEditor()
        raw = "A\r\nB\u2028C\u2029D"
        editor.set_exact_text(raw)
        self.assertTrue(editor.escape.isChecked())
        self.assertEqual(editor.exact_text(), raw)
        self.assertIn("\\u2028", editor.editor.toPlainText())
        self.assertIn("\\u2029", editor.editor.toPlainText())
        editor.deleteLater()

    def test_candidate_value_dialog_preserves_empty_and_multiline(self):
        empty = ExactValueDialog("")
        self.assertEqual(empty.value(), "")
        empty.deleteLater()
        raw = "A\r\nB\u2028C"
        dialog = ExactValueDialog(raw)
        self.assertEqual(dialog.value(), raw)
        dialog.deleteLater()

    def test_render_editor_screenshot(self):
        config_path = self.path
        service = EnvironmentService(load_config(config_path), MemoryStore(),
                                     StateRepository(self.temp / "screenshot-state"))
        dialog = ConfigEditorDialog(config_path, service)
        self.addCleanup(dialog.deleteLater)
        dialog.select_record(self.record_spec(dialog))
        dialog.show()
        self.app.processEvents()
        output = self.temp / "config-editor.png"
        self.assertTrue(dialog.grab().save(str(output)))
        self.assertGreater(output.stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
