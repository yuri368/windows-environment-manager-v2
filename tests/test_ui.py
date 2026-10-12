"""Offscreen UI checks; all service writes use an isolated MemoryStore."""

from __future__ import annotations

import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QLineEdit
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from envmanager.config import load_config, parse_config
from envmanager.models import Calculation, PlanChange, RegistryState, VariableSpec
from envmanager.persistence import StateRepository
from envmanager.selection import SelectionSession
from envmanager.service import EnvironmentService
from envmanager.removal import RemovalRequest, RemovalScope
from envmanager.sharing import ConflictDecision
from envmanager.store import MemoryStore
from envmanager.config_editor_ui import ConfigEditorDialog
from envmanager.ui import (
    FullValueDialog, MainWindow, OrderDialog, PreviewDialog, SharedConflictDialog, VariableCard,
    _local_time, _status_text,
)


class FakeState:
    def snapshot(self):
        return {"last_values": {}, "active": {}, "revision": 0}


class FakeStore:
    def read(self, scope, name):
        return RegistryState(name, scope, False)


class FakeService:
    def __init__(self):
        self.selections = SelectionSession({})
        self.store = FakeStore()
        self.state = FakeState()
        self.reloaded = None
        self.config = parse_config('[App.Default]\nHOME = { default_value = "A", scope = "User", mode = "replace", editable = true }\n')

    def reload(self, config):
        self.reloaded = config
        self.config = config

    def pending(self):
        return []


class UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.temp = Path(temporary.name)

    def real_service(self):
        config_path = Path(__file__).resolve().parents[1] / "env.example.toml"
        store = MemoryStore((
            RegistryState("Path", "User", True, r"C:\Windows;C:\Tools", 1),
            RegistryState("MY_TOOL_LIST", "User", True, "Stable;Beta", 1),
        ))
        service = EnvironmentService(load_config(config_path), store, StateRepository(self.temp / "state"))
        return service, store, config_path

    def test_edit_switches_source_only_after_text_change_and_preserves_draft(self):
        service = FakeService()
        spec = VariableSpec("App", "Default", "HOME", "User", "replace", True, "A")
        card = VariableCard(service, spec)
        self.assertEqual(service.selections.get(spec).source, "default")
        card.editor.setFocus()
        card.editor.setCursorPosition(0)
        self.assertEqual(service.selections.get(spec).source, "default")
        card.editor.setText("B")
        card.editor.textEdited.emit("B")
        self.assertEqual(service.selections.get(spec).source, "custom")
        card.selector.setCurrentIndex(card.selector.findData("default"))
        self.assertEqual(service.selections.get(spec).value, "A")
        card.selector.setCurrentIndex(card.selector.findData("custom"))
        self.assertEqual(service.selections.get(spec).value, "B")
        card.deleteLater()

    def test_sensitive_value_hidden_until_revealed(self):
        service = FakeService()
        spec = VariableSpec("App", "Default", "API_TOKEN", "User", "replace", True, "top-secret")
        card = VariableCard(service, spec)
        self.assertEqual(card.editor.echoMode(), QLineEdit.EchoMode.Password)
        self.assertNotIn("top-secret", card.default_info.text())
        self.assertNotIn("top-secret", card.editor.toolTip())
        self.assertNotIn("top-secret", card.default_info.toolTip())
        card.reveal.click()
        self.assertIn("top-secret", card.default_info.text())
        self.assertIn("top-secret", card.editor.toolTip())
        card.deleteLater()

    def test_long_value_remains_available_without_expanding_card(self):
        service = FakeService()
        long_path = ";".join(f"C:/Tools/Component{i}/bin" for i in range(50))
        spec = VariableSpec("App", "Default", "Path", "User", "replace", False, long_path)
        card = VariableCard(service, spec)
        card.resize(520, card.sizeHint().height())
        card.show()
        self.app.processEvents()
        self.assertEqual(card.default_info.text(), long_path)
        self.assertEqual(card.default_info.toolTip(), long_path)
        self.assertEqual(card.editor.toolTip(), long_path)
        self.assertFalse(card.details.isVisible())
        QTest.mouseClick(card.details_toggle, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertTrue(card.details.isVisible())
        self.assertLessEqual(card.default_info.width(), card.width())
        card.close()
        card.deleteLater()

    def test_sort_dialog_preserves_full_list_and_restores(self):
        before = RegistryState("Path", "User", True, "A;B", 1)
        after = RegistryState("Path", "User", True, "A;B;X", 1)
        calc = Calculation(after, items=("A", "B", "X"), added=("X",), needs_order=True)
        dialog = OrderDialog(PlanChange(before, after, "append", "X", calculation=calc))
        dialog.list.setCurrentRow(2)
        dialog._move_to(0)
        self.assertEqual(dialog.order(), ("X", "A", "B"))
        self.assertIn("X;A;B", dialog.result.text())
        dialog._restore()
        self.assertEqual(dialog.order(), ("A", "B", "X"))
        dialog.deleteLater()

    def test_sort_marker_uses_engine_matching_rule(self):
        before = RegistryState("Path", "User", True, "SS", 1)
        after = RegistryState("Path", "User", True, "SS;ß", 1)
        calc = Calculation(after, items=("SS", "ß"), added=("ß",), needs_order=True)
        dialog = OrderDialog(PlanChange(before, after, "append", "ß", calculation=calc))
        self.assertNotIn("新增", dialog.list.item(0).text())
        self.assertIn("新增", dialog.list.item(1).text())
        dialog.deleteLater()

    def test_config_editor_rejects_invalid_toml_without_writing(self):
        path = self.temp / "env.toml"
        original = '[App.Default]\nHOME = { default_value = "A", scope = "User", mode = "replace", editable = true }\n'
        path.write_text(original, encoding="utf-8")
        service = FakeService()
        dialog = ConfigEditorDialog(path, service, initial_tab="source")
        dialog.source.setPlainText("invalid = [")
        dialog._save()
        self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(path.read_text(encoding="utf-8"), original)
        self.assertIsNone(service.reloaded)
        dialog.deleteLater()

    def test_config_editor_saves_only_on_explicit_action(self):
        path = self.temp / "env.toml"
        original = '[App.Default]\nHOME = { default_value = "A", scope = "User", mode = "replace", editable = true }\n'
        updated = original.replace('default_value = "A"', 'default_value = "B"')
        path.write_text(original, encoding="utf-8")
        service = FakeService()
        dialog = ConfigEditorDialog(path, service, initial_tab="source")
        dialog.source.setPlainText(updated)
        self.assertEqual(path.read_text(encoding="utf-8"), original)
        with patch.object(dialog, "_confirm_diff", return_value=True):
            dialog._save()
        self.assertTrue(dialog.saved)
        self.assertIsNotNone(service.reloaded)
        self.assertEqual(path.read_text(encoding="utf-8"), updated)
        dialog.deleteLater()

    def test_config_editor_restores_service_when_reload_fails(self):
        path = self.temp / "env.toml"
        original = '[App.Default]\nHOME = { default_value = "A", scope = "User", mode = "replace", editable = true }\n'
        path.write_text(original, encoding="utf-8")
        service = FakeService()
        original_config = service.config
        original_selections = service.selections
        dialog = ConfigEditorDialog(path, service, initial_tab="source")
        dialog.source.setPlainText(original.replace('default_value = "A"', 'default_value = "B"'))

        def failing_reload(config):
            service.config = config
            service.selections = SelectionSession({})
            raise RuntimeError("模拟刷新失败")

        with patch.object(service, "reload", side_effect=failing_reload), \
             patch.object(dialog, "_confirm_diff", return_value=True):
            dialog._save()
        self.assertIs(service.config, original_config)
        self.assertIs(service.selections, original_selections)
        self.assertEqual(path.read_text(encoding="utf-8"), original)
        self.assertIn("模拟刷新失败", dialog.error.text())
        dialog.deleteLater()

    def test_multiline_full_value_preserves_exact_characters(self):
        value = "A\r\nB\n C;D "
        dialog = FullValueDialog(value, True, False)
        self.assertEqual(dialog.value(), value)
        dialog.editor.setPlainText(json.dumps(value + "\nE", ensure_ascii=False)[1:-1])
        self.assertEqual(dialog.value(), value + "\nE")
        dialog.deleteLater()

    def test_invalid_custom_input_disables_apply_without_discarding_draft(self):
        service = FakeService()
        window = MainWindow(service, self.temp / "env.toml", demo=True)
        card = window.cards[0]
        card.editor.setText("A\x00B")
        card.editor.textEdited.emit("A\x00B")
        self.assertEqual(card.editor.text(), "A\x00B")
        self.assertEqual(service.selections.get(card.spec).source, "custom")
        self.assertFalse(window.apply_button.isEnabled())
        window.close()
        window.deleteLater()

    def test_main_window_selects_app_and_renders_variable(self):
        service = FakeService()
        window = MainWindow(service, self.temp / "env.toml", demo=True)
        self.assertEqual(window.app_name, "App")
        self.assertEqual(window.option_combo.currentText(), "Default")
        self.assertEqual(len(window.cards), 1)
        self.assertTrue(window.apply_button.isEnabled())
        card = window.cards[0]
        self.assertTrue(card.selector.accessibleName())
        self.assertTrue(card.editor.accessibleName())
        self.assertEqual(card.selector.currentData(), "default")
        self.assertEqual(card.editor.text(), "A")
        self.assertEqual(window.apply_button.parentWidget().objectName(), "footer")
        self.assertIn("A", card.default_info.text())
        self.assertEqual(card.last_info.text(), "尚未应用")
        self.assertIn("变量不存在", card.existing_info.text())
        self.assertEqual(card.existing_type.text(), "—")
        window.resize(850, 560)
        window.show()
        self.app.processEvents()
        self.assertTrue(card.full_value_button.isVisible())
        self.assertTrue(window.apply_button.isVisible())
        self.assertEqual(window.width(), 850)
        self.assertTrue(card.rect().contains(card.selector.geometry()))
        self.assertTrue(card.rect().contains(card.editor.geometry()))
        self.assertTrue(card.rect().contains(card.full_value_button.geometry()))
        self.assertFalse(card.details.isVisible())
        QTest.mouseClick(card.details_toggle, Qt.MouseButton.LeftButton)
        self.assertTrue(card.details.isVisible())
        QTest.mouseClick(card.details_toggle, Qt.MouseButton.LeftButton)
        self.assertFalse(card.details.isVisible())
        window.close()
        window.deleteLater()

    def test_reference_toggle_exposes_absent_empty_and_exact_nonempty_windows_values(self):
        service = FakeService()
        spec = VariableSpec("App", "Default", "HOME", "User", "replace", True, "A")
        for state, text, kind in (
            (RegistryState("HOME", "User", False), "变量不存在", "—"),
            (RegistryState("HOME", "User", True, "", 1), "（空字符串）", "REG_SZ"),
            (RegistryState("HOME", "User", True, "  %HOME%;C:/Tools  ", 2), "  %HOME%;C:/Tools  ", "REG_EXPAND_SZ"),
        ):
            with self.subTest(state=state), patch.object(service.store, "read", return_value=state):
                card = VariableCard(service, spec)
                card.resize(520, card.sizeHint().height())
                card.show()
                self.app.processEvents()
                self.assertFalse(card.details.isVisible())
                self.assertTrue(card.selector.isVisible())
                self.assertTrue(card.editor.isVisible())
                QTest.mouseClick(card.details_toggle, Qt.MouseButton.LeftButton)
                self.app.processEvents()
                self.assertTrue(card.details.isVisible())
                self.assertEqual(card.default_info.text(), "A")
                self.assertEqual(card.last_info.text(), "尚未应用")
                self.assertEqual(card.existing_info.text(), text)
                self.assertEqual(card.existing_type.text(), kind)
                QTest.mouseClick(card.details_toggle, Qt.MouseButton.LeftButton)
                self.assertFalse(card.details.isVisible())
                self.assertEqual(service.selections.get(spec).value, "A")
                card.close()
                card.deleteLater()

    def test_real_service_append_preview_cancel_apply_and_deactivate(self):
        service, store, config_path = self.real_service()
        window = MainWindow(service, config_path, demo=True)
        window._select_app("Python")
        first = service.plan("Python", "Python313")
        self.assertTrue(first.needs_order)
        self.assertTrue(any(change.calculation and change.calculation.items for change in first.changes))
        with patch.object(OrderDialog, "exec", return_value=QDialog.DialogCode.Accepted), \
             patch.object(PreviewDialog, "exec", return_value=QDialog.DialogCode.Rejected):
            window._apply()
        self.assertEqual(store.writes, [])
        self.assertEqual(service.state.snapshot()["active"], {})
        with patch.object(OrderDialog, "exec", return_value=QDialog.DialogCode.Accepted), \
             patch.object(PreviewDialog, "exec", return_value=QDialog.DialogCode.Accepted), \
             patch("envmanager.ui.QMessageBox.information", return_value=None):
            window._apply()
            snapshot = service.state.snapshot()
            path_spec = next(spec for spec in service.config.variables("Python", "Python313") if spec.name == "Path")
            self.assertEqual(snapshot["last_values"][path_spec.key], path_spec.default_value)
            self.assertEqual(snapshot["active"]["Python"]["option"], "Python313")
            self.assertTrue(service.history())
            self.assertFalse(service.pending())
            self.assertTrue(store.read("User", "Path").value.endswith(r"%PYTHON_HOME%;%PYTHON_HOME%\Scripts"))
            window._deactivate()
        self.assertNotIn("Python", service.state.snapshot()["active"])
        self.assertEqual(service.history()[0]["action"], "remove")
        window.close()
        window.deleteLater()

    def test_legacy_deactivate_alias_reviews_unactivated_shared_consumer(self):
        config_path = self.temp / "shared.toml"
        config_path.write_text("""[Java.Default]
Path={default_value='A',scope='User',mode='+append',editable=false}
[Scala.Default]
PATH={default_value='A;B',scope='User',mode='replace',editable=false}
""", encoding="utf-8")
        store = MemoryStore((RegistryState("Path", "User", True, "BASE", 2),))
        service = EnvironmentService(load_config(config_path), store,
                                     StateRepository(self.temp / "state"))
        self.assertTrue(service.execute(service.plan("Java", "Default")).success)
        window = MainWindow(service, config_path, demo=True)
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        window._select_app("Java")
        self.assertNotIn("Scala", service.state.snapshot()["active"])
        with patch.object(SharedConflictDialog, "exec", return_value=QDialog.DialogCode.Accepted) as shared, \
             patch.object(PreviewDialog, "exec", return_value=QDialog.DialogCode.Accepted) as preview, \
             patch("envmanager.ui.QMessageBox.information", return_value=None):
            window._deactivate()
        shared.assert_called_once()
        preview.assert_called_once()
        self.assertEqual(store.read("User", "Path").value, "A;BASE")
        self.assertEqual(service.history()[0]["action"], "remove")
        self.assertEqual(service.history()[0]["metadata"]["metrics"]["kept_shared_count"], 1)

        draft = service.prepare_remove(RemovalRequest(
            RemovalScope.SCHEME, frozenset(("Java",)), {"Java": "Default"}))
        decisions = {draft.shared_conflicts[0].atom.atom_id: ConflictDecision.REMOVE}
        with patch.object(SharedConflictDialog, "exec", return_value=QDialog.DialogCode.Accepted), \
             patch.object(SharedConflictDialog, "decisions", return_value=decisions), \
             patch.object(PreviewDialog, "exec", return_value=QDialog.DialogCode.Rejected):
            window._deactivate()
        self.assertEqual(store.read("User", "Path").value, "A;BASE")
        with patch.object(SharedConflictDialog, "exec", return_value=QDialog.DialogCode.Accepted), \
             patch.object(SharedConflictDialog, "decisions", return_value=decisions), \
             patch.object(PreviewDialog, "exec", return_value=QDialog.DialogCode.Accepted), \
             patch("envmanager.ui.QMessageBox.information", return_value=None):
            window._deactivate()
        self.assertEqual(store.read("User", "Path").value, "BASE")
        self.assertNotIn("Java", service.state.snapshot()["active"])

    def test_hidden_invalid_batch_input_has_visible_reason_and_navigation_then_clears(self):
        service, store, config_path = self.real_service()
        window = MainWindow(service, config_path, demo=True)
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        window._select_app("CustomList")
        card = window.cards[0]
        card.editor.setText("Alpha\x00B")
        card.editor.textEdited.emit("Alpha\x00B")
        for index in range(window.app_list.count()):
            item = window.app_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == "CustomList":
                item.setCheckState(Qt.CheckState.Checked)
                custom_item = item
        window._select_app("Java")
        window.feedback.setText("先前执行成功")
        window.resize(850, 560)
        window.show()
        self.app.processEvents()
        self.assertFalse(window.apply_button.isEnabled())
        self.assertTrue(window.blocking_reason.isVisible())
        self.assertIn("CustomList", window.blocking_reason.text())
        self.assertIn("MY_TOOL_LIST", window.blocking_reason.text())
        self.assertIn("输入无效", window.blocking_reason.text())
        self.assertTrue(window.locate_error_button.isVisible())
        row = window.app_list.itemWidget(custom_item)
        self.assertIn("输入无效", row.meta.toolTip())
        self.assertNotIn("Alpha\x00B", window.blocking_reason.text())
        self.assertNotIn("Alpha\x00B", window.blocking_reason.toolTip())
        self.assertNotIn("Alpha\x00B", row.meta.toolTip())
        self.assertEqual(window.feedback.text(), "先前执行成功")
        window.search.setText("Java")
        QTest.mouseClick(window.locate_error_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertEqual(window.app_name, "CustomList")
        self.assertEqual(window.checked_apps, {"CustomList"})
        self.assertEqual(window.cards[0].editor.text(), "Alpha\x00B")
        self.assertTrue(window.cards[0].editor.hasFocus())
        self.assertTrue(window.scroll.viewport().rect().intersects(
            window.cards[0].geometry().translated(window.card_panel.pos())))
        window.cards[0].editor.setText("Alpha;Beta")
        window.cards[0].editor.textEdited.emit("Alpha;Beta")
        self.assertTrue(window.apply_button.isEnabled())
        self.assertFalse(window.blocking_reason.isVisible())
        self.assertFalse(window.locate_error_button.isVisible())
        self.assertEqual(window.blocking_reason.text(), "")
        self.assertEqual(window.feedback.text(), "先前执行成功")
        for index in range(window.app_list.count()):
            item = window.app_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == "CustomList":
                self.assertNotIn("输入无效", window.app_list.itemWidget(item).meta.toolTip())
        self.assertEqual(store.writes, [])

    def test_pending_banner_and_render_demo_screenshot(self):
        service, _, config_path = self.real_service()
        window = MainWindow(service, config_path, demo=True)
        window._select_app("DualScope")
        window.show()
        self.app.processEvents()
        output = Path(__file__).resolve().parents[1] / "artifacts" / "ui-demo.png"
        output.parent.mkdir(exist_ok=True)
        self.assertTrue(window.grab().save(str(output)))
        self.assertGreater(output.stat().st_size, 1000)
        service.state.save_operation({
            "id": "pending-demo", "created_at": "2026-10-08T00:00:00Z", "app": "Demo", "option": "Test",
            "action": "apply", "status": "prepared", "message": "等待恢复", "changes": [], "attempted": [],
            "snapshot_before": service.state.snapshot(),
        })
        window._refresh_pending()
        self.assertTrue(window.pending_banner.isVisible())
        window.close()
        window.deleteLater()

    def test_utc_time_and_history_status_are_localized(self):
        self.assertEqual(_local_time("2026-10-08T00:00:00Z"), "2026-10-08 08:00:00")
        for status in ("prepared", "writing", "rolling_back", "recovered", "restored"):
            self.assertNotEqual(_status_text(status), status)


if __name__ == "__main__":
    unittest.main()
