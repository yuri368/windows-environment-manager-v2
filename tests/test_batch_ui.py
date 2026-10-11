"""Batch desktop behavior, checked offscreen against isolated memory state."""

from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLabel

from envmanager.config import parse_config
from envmanager.models import Calculation, ExecutionPlan, PlanChange, RegistryState
from envmanager.persistence import StateRepository
from envmanager.service import EnvironmentService
from envmanager.store import MemoryStore
from envmanager.ui import HistoryDialog, MainWindow, OldSchemeDialog, OrderDialog, PreviewDialog


CONFIG = '''
[Alpha]
default_option = "Second"
[Alpha.First]
Path = { default_value = "A", scope = "User", mode = "append", editable = true }
[Alpha.Second]
Path = { default_value = "AA", scope = "User", mode = "append", editable = true }
[Beta.Default]
Path = { default_value = "B", scope = "User", mode = "append", editable = true }
'''


class UiDraftService(EnvironmentService):
    """Control selected options so these tests isolate UI state from persistence."""

    def __init__(self, config, store, state):
        super().__init__(config, store, state)
        self.drafts = {app: config.default_option(app) for app in config.apps()}
        self.draft_changes = []

    def selected_option(self, app):
        return self.drafts[app]

    def select_option(self, app, option):
        self.drafts[app] = option
        self.draft_changes.append((app, option))

    def initialization_warning(self, app):
        return None


class BatchUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.temp = Path(directory.name)
        self.service = UiDraftService(parse_config(CONFIG), MemoryStore(), StateRepository(self.temp / "state"))
        self.window = MainWindow(self.service, self.temp / "env.toml", demo=True)
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.close)

    def check(self, app):
        for index in range(self.window.app_list.count()):
            item = self.window.app_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == app:
                item.setCheckState(Qt.CheckState.Checked)
                return
        self.fail(f"Missing app: {app}")

    def plan(self, name="Path", sensitive=False):
        before = RegistryState(name, "User", True, "Original", 1)
        after = RegistryState(name, "User", True, "Original;AA;B", 1)
        inputs = ("secret-alpha", "secret-beta") if sensitive else ("AA", "B")
        contributions = tuple({"app": app, "option": option, "record_key": f"{app}-key", "mode": "append", "selected_value": value}
                              for app, option, value in (("Alpha", "Second", inputs[0]), ("Beta", "Default", inputs[1])))
        calculation = Calculation(after, items=("Original", "AA", "B"), added=("AA", "B"), needs_order=True)
        change = PlanChange(before, after, "append", "misleading-combined-input", "aggregate-path", calculation, contributions)
        return ExecutionPlan("test-batch", "", "", "apply", (change,), self.service.config.digest, 0,
                             applications=(("Alpha", "Second"), ("Beta", "Default")))

    def test_default_and_runtime_option_drafts_survive_app_navigation(self):
        self.assertEqual(self.window.option_combo.currentText(), "Second")
        self.assertIn("默认方案", self.window.active_label.text())
        self.window.option_combo.setCurrentText("First")
        self.window._select_app("Beta")
        self.window._select_app("Alpha")
        self.assertEqual(self.window.option_combo.currentText(), "First")
        self.assertEqual(self.service.draft_changes, [("Alpha", "First")])

    def test_filter_and_detail_navigation_preserve_checked_apps(self):
        self.check("Beta")
        self.check("Alpha")
        self.window.search.setText("Beta")
        self.assertEqual(self.window.app_list.count(), 1)
        self.assertEqual(self.window.app_list.item(0).checkState(), Qt.CheckState.Checked)
        self.window._select_app("Beta")
        self.assertEqual(self.window.checked_apps, {"Alpha", "Beta"})
        self.assertIn("2 个应用", self.window.batch_summary.text())
        self.assertIn("预览", self.window.apply_button.text())
        self.window.search.clear()
        self.assertTrue(all(self.window.app_list.item(i).checkState() == Qt.CheckState.Checked for i in range(2)))
        self.window.clear_selection_button.click()
        self.assertEqual(self.window.checked_apps, set())
        self.assertEqual(self.window.apply_button.text(), "预览并应用")

    def test_all_selection_includes_filtered_apps_and_invalid_hidden_input_blocks_apply(self):
        self.window.search.setText("Beta")
        self.window.select_all_button.click()
        self.assertEqual(self.window.checked_apps, {"Alpha", "Beta"})
        spec = self.service.config.variables("Alpha", "Second")[0]
        self.service.selections.edit(spec, "A\x00B")
        self.window._select_app("Beta")
        self.assertFalse(self.window.apply_button.isEnabled())
        self.assertEqual(self.service.selections.get(spec).value, "A\x00B")
        self.window.clear_selection_button.click()
        self.assertTrue(self.window.apply_button.isEnabled())

    def test_batch_routes_in_configuration_order_and_sorts_shared_target_once(self):
        self.check("Beta")
        self.check("Alpha")
        original = self.plan()
        confirmed = replace(original, changes=(replace(original.changes[0], calculation=replace(original.changes[0].calculation, needs_order=False)),))
        calls = []

        def planning(app_options, orders=None):
            calls.append((app_options, orders))
            return confirmed if orders else original

        # Legacy adapters still expose plan_batch only; retain that compatibility
        # while Issue #2 tests exercise the new production two-stage entrypoint.
        with patch.object(self.service, "prepare_batch_scheme_switch", None, create=True), \
             patch.object(self.service, "plan_batch", side_effect=planning, create=True), \
             patch.object(OrderDialog, "exec", return_value=QDialog.DialogCode.Accepted) as sorting, \
             patch.object(PreviewDialog, "exec", return_value=QDialog.DialogCode.Rejected):
            self.window._apply()
        self.assertEqual(sorting.call_count, 1)
        self.assertEqual(list(calls[0][0]), ["Alpha", "Beta"])
        self.assertEqual(calls[0][0], {"Alpha": "Second", "Beta": "Default"})
        self.assertEqual(calls[1][1], {"aggregate-path": ("Original", "AA", "B")})
        self.assertEqual(self.service.store.writes, [])
        self.assertEqual(self.service.state.snapshot()["active"], {})

    def test_preview_lists_each_application_input_and_masks_sensitive_contributions(self):
        plan = self.plan("API_TOKEN_LIST", sensitive=True)
        dialog = PreviewDialog(plan)
        self.addCleanup(dialog.deleteLater)
        labels = "\n".join(label.text() for label in dialog.findChildren(QLabel))
        self.assertIn("Alpha / Second", labels)
        self.assertIn("Beta / Default", labels)
        self.assertNotIn("secret-alpha", labels)
        self.assertNotIn("misleading-combined-input", labels)
        dialog.show_sensitive.setChecked(True)
        labels = [label.text() for label in dialog.findChildren(QLabel)]
        self.assertIn("secret-alpha", labels)
        self.assertIn("secret-beta", labels)
        self.assertNotIn("misleading-combined-input", labels)
        self.assertEqual(dialog.changes_layout.count(), 1)

    def test_conflicted_batch_previews_without_sorting_or_writing(self):
        self.check("Alpha")
        self.check("Beta")
        plan = replace(self.plan(), conflicts=("另一变量存在冲突，请重新预览",))
        self.assertTrue(plan.needs_order)
        previewed = []

        def reject_conflict(dialog):
            previewed.append(dialog.plan)
            self.assertFalse(dialog.buttons.button(dialog.buttons.StandardButton.Ok).isEnabled())
            self.assertIn("冲突", "\n".join(label.text() for label in dialog.findChildren(QLabel)))
            return QDialog.DialogCode.Rejected

        with patch.object(self.service, "prepare_batch_scheme_switch", None, create=True), \
             patch.object(self.service, "plan_batch", return_value=plan, create=True) as planning, \
             patch.object(OrderDialog, "exec") as sorting, \
             patch.object(PreviewDialog, "exec", new=reject_conflict):
            self.window._apply()
        sorting.assert_not_called()
        planning.assert_called_once()
        self.assertEqual(previewed, [plan])
        self.assertEqual(self.service.store.writes, [])
        self.assertEqual(self.service.state.snapshot()["active"], {})

    def test_history_shows_batch_applications_and_legacy_operation(self):
        entries = [{"id": "batch", "created_at": "2026-10-10T00:00:00Z", "applications": [
            {"app": "Alpha", "option": "Second"}, {"app": "Beta", "option": "Default"}], "status": "success"},
            {"id": "old", "app": "Legacy", "option": "Old", "status": "success"}]
        with patch.object(self.service, "history", return_value=entries):
            dialog = HistoryDialog(self.service, lambda _factory: None)
        self.addCleanup(dialog.deleteLater)
        self.assertIn("Alpha / Second；Beta / Default", dialog.list.item(0).text())
        self.assertIn("Legacy / Old", dialog.list.item(1).text())

    def test_checked_sidebar_and_footer_fit_small_window(self):
        self.window._check_all(True)
        self.window.resize(850, 560)
        self.window.show()
        self.app.processEvents()
        footer = self.window.apply_button.parentWidget()
        self.assertTrue(footer.rect().contains(self.window.apply_button.geometry()))
        self.assertTrue(footer.rect().contains(self.window.deactivate_button.geometry()))
        self.assertTrue(self.window.apply_button.isVisible())
        self.assertEqual(self.window.width(), 850)
        output = Path(__file__).resolve().parents[1] / "artifacts" / "ui-batch-small.png"
        self.assertTrue(self.window.grab().save(str(output)))

    def test_manifest_checkbox_navigation_and_keyboard_space_are_independent(self):
        self.window.show()
        self.app.processEvents()
        beta = self.window.app_list.item(1)
        row = self.window.app_list.itemWidget(beta)
        QTest.mouseClick(row.check, Qt.MouseButton.LeftButton)
        self.assertEqual(self.window.app_name, "Alpha")
        self.assertEqual(beta.checkState(), Qt.CheckState.Checked)
        self.assertEqual(self.window.checked_apps, {"Beta"})
        QTest.mouseClick(row.name_button, Qt.MouseButton.LeftButton)
        self.assertEqual(self.window.app_name, "Beta")
        self.assertEqual(self.window.checked_apps, {"Beta"})
        self.window.app_list.setFocus()
        QTest.keyClick(self.window.app_list, Qt.Key.Key_Space)
        self.assertEqual(beta.checkState(), Qt.CheckState.Unchecked)
        self.assertFalse(row.check.isChecked())
        self.assertEqual(self.window.checked_apps, set())

    def test_manifest_option_switch_updates_batch_and_preserves_unapplied_input(self):
        card = self.window.cards[0]
        card.editor.setText("UnappliedAA")
        card.editor.textEdited.emit("UnappliedAA")
        self.check("Alpha")
        self.check("Beta")
        self.window._select_app("Beta")
        alpha = self.window.app_list.item(0)
        row = self.window.app_list.itemWidget(alpha)
        row.option_combo.setCurrentText("First")
        self.assertEqual(self.window.app_name, "Beta")
        self.assertEqual(self.service.selected_option("Alpha"), "First")
        self.assertIn("Alpha / First", self.window.batch_summary.toolTip())
        row.option_combo.setCurrentText("Second")
        row.name_button.click()
        self.assertEqual(self.window.cards[0].editor.text(), "UnappliedAA")
        self.assertEqual(self.window.cards[0].selector.currentData(), "custom")
        self.assertEqual(self.service.store.writes, [])

    def test_search_empty_state_preserves_checked_batch(self):
        self.check("Alpha")
        self.window.show()
        self.window.search.setText("No matching application")
        self.app.processEvents()
        self.assertEqual(self.window.app_list.count(), 0)
        self.assertTrue(self.window.search_empty.isVisible())
        self.assertEqual(self.window.checked_apps, {"Alpha"})
        self.assertTrue(self.window.apply_button.isEnabled())
        self.assertIn("Alpha / Second", self.window.batch_summary.toolTip())


REAL_CONFIG = '''
[Alpha]
default_option = "Second"
[Alpha.First]
Path = { default_value = "A", scope = "User", mode = "+append", editable = true }
[Alpha.Second]
Path = { default_value = "AA", scope = "User", mode = "+append", editable = true }
[Beta.Default]
Path = { default_value = "B", scope = "User", mode = "-append", editable = true, choices = [{ label = "候选 B", value = "PickedB" }] }
[Gamma.Default]
Path = { default_value = "C", scope = "User", mode = "append", editable = false }
'''


class RealBatchUiTests(unittest.TestCase):
    """Exercise the actual UI/service/persistence contract without registry writes."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.temp = Path(directory.name)
        self.config = parse_config(REAL_CONFIG)
        self.store = MemoryStore((RegistryState("Path", "User", True, "Original;Keep", 1),))
        self.repository = StateRepository(self.temp / "state")
        self.service = EnvironmentService(self.config, self.store, self.repository)
        self.window = self.open_window(self.service)

    def open_window(self, service):
        window = MainWindow(service, self.temp / "env.toml", demo=True)
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        return window

    @staticmethod
    def check(window, app):
        for index in range(window.app_list.count()):
            item = window.app_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == app:
                item.setCheckState(Qt.CheckState.Checked)
                return
        raise AssertionError(f"Missing application: {app}")

    def approved_apply(self, window, sorting=None):
        def apply_without_old_removal(dialog):
            # Existing scenarios apply fresh requirements. The new matching
            # workflow can disclose 0/N partial alternatives; explicitly choose
            # the dialog's "only apply" path rather than dismissing the review.
            self.assertFalse(dialog.required)
            self.assertEqual(dialog.selected_options(), ())
            return QDialog.DialogCode.Accepted

        with patch.object(PreviewDialog, "exec", return_value=QDialog.DialogCode.Accepted), \
             patch.object(OldSchemeDialog, "exec", new=apply_without_old_removal), \
             patch("envmanager.ui.QMessageBox.information", return_value=None), \
             patch("envmanager.ui.QMessageBox.warning", return_value=None) as warning:
            if sorting is None:
                window._apply()
            else:
                orders = []

                def confirmed_order(dialog):
                    orders.append(dialog)
                    return sorting(dialog)

                with patch.object(OrderDialog, "exec", new=confirmed_order):
                    window._apply()
                self.assertEqual(len(orders), 1)
        warning.assert_not_called()

    def test_batch_summary_and_content_sized_actions_remain_visible_at_both_window_sizes(self):
        self.window._check_all(True)
        for width, height in ((850, 560), (1280, 800)):
            with self.subTest(width=width):
                self.window.resize(width, height)
                self.window.show()
                self.app.processEvents()
                summary = self.window.batch_summary
                apply = self.window.apply_button
                footer = apply.parentWidget()
                self.assertTrue(summary.isVisible())
                self.assertGreater(summary.width(), 200)
                self.assertIn("已选 3 个应用", summary.text())
                self.assertIn("Alpha", summary.text())
                for app, option in (("Alpha", "Second"), ("Beta", "Default"), ("Gamma", "Default")):
                    self.assertIn(f"{app} / {option}", summary.toolTip())
                self.assertTrue(apply.isVisible())
                self.assertLess(apply.width(), 220)
                self.assertTrue(footer.rect().contains(apply.geometry()))
                self.assertTrue(footer.rect().contains(self.window.deactivate_button.geometry()))
                self.assertTrue(self.window.rect().contains(
                    apply.rect().translated(apply.mapTo(self.window, apply.rect().topLeft()))))

    def test_real_shared_directions_then_mixed_append_sort_once_and_execute_once(self):
        self.check(self.window, "Beta")
        self.check(self.window, "Alpha")
        self.assertEqual(self.window.option_combo.currentText(), "Second")
        plan = self.service.plan_batch(["Alpha", "Beta"])
        self.assertEqual(len(plan.changes), 1)
        self.assertEqual(plan.changes[0].mode, "batch_append")
        preview = PreviewDialog(plan)
        self.addCleanup(preview.deleteLater)
        self.assertIn("组合追加", [label.text() for label in preview.findChildren(QLabel)])
        self.approved_apply(self.window)
        self.assertEqual(self.store.read("User", "Path").value, "AA;Original;Keep;B")
        self.assertEqual(len(self.store.writes), 1)
        self.assertEqual(set(self.repository.snapshot()["active"]), {"Alpha", "Beta"})

        self.check(self.window, "Gamma")
        mixed = self.service.plan_batch(["Alpha", "Beta", "Gamma"])
        self.assertEqual(len(mixed.changes), 1)
        self.assertTrue(mixed.needs_order)
        self.assertEqual(len(mixed.changes[0].contributions), 3)

        def confirm_order(dialog):
            dialog.list.setCurrentRow(dialog.list.count() - 1)
            dialog._move_to(0)
            return QDialog.DialogCode.Accepted

        self.approved_apply(self.window, confirm_order)
        self.assertEqual(self.store.read("User", "Path").value, "C;AA;Original;Keep;B")
        self.assertEqual(len(self.store.writes), 2)
        self.assertEqual(set(self.repository.snapshot()["active"]), {"Alpha", "Beta", "Gamma"})
        self.assertEqual(len(self.service.history()[0]["applications"]), 3)
        self.assertFalse(self.service.pending())
        self.window.resize(850, 560)
        self.window.show()
        self.app.processEvents()
        output = Path(__file__).resolve().parents[1] / "artifacts" / "ui-batch-real.png"
        output.parent.mkdir(exist_ok=True)
        self.assertTrue(self.window.grab().save(str(output)))

    def test_real_success_sources_restart_and_newer_running_drafts_take_priority(self):
        self.window.option_combo.setCurrentText("First")
        alpha_card = self.window.cards[0]
        alpha_card.editor.setText("UserA")
        alpha_card.editor.textEdited.emit("UserA")
        self.window._select_app("Beta")
        beta_card = self.window.cards[0]
        beta_card.selector.setCurrentIndex(beta_card.selector.findData("choice:0"))
        self.check(self.window, "Beta")
        self.check(self.window, "Alpha")
        self.approved_apply(self.window)
        self.assertEqual(self.store.read("User", "Path").value, "UserA;Original;Keep;PickedB")

        self.window._select_app("Alpha")
        self.assertEqual(self.window.option_combo.currentText(), "First")
        self.assertEqual(self.window.cards[0].selector.currentData(), "custom")
        self.window.cards[0].editor.setText("UnappliedA")
        self.window.cards[0].editor.textEdited.emit("UnappliedA")
        self.window.option_combo.setCurrentText("Second")
        self.window._select_app("Beta")
        self.window.cards[0].selector.setCurrentIndex(self.window.cards[0].selector.findData("default"))
        self.window._select_app("Alpha")
        self.assertEqual(self.window.option_combo.currentText(), "Second")
        self.window.option_combo.setCurrentText("First")
        self.assertEqual(self.window.cards[0].editor.text(), "UnappliedA")
        self.assertEqual(self.window.cards[0].selector.currentData(), "custom")
        draft_plan = self.service.plan_batch(["Alpha", "Beta"])
        self.assertEqual([entry["selected_value"] for entry in draft_plan.changes[0].contributions], ["UnappliedA", "B"])

        restarted = EnvironmentService(self.config, self.store, StateRepository(self.temp / "state"))
        resumed = self.open_window(restarted)
        self.assertEqual(resumed.option_combo.currentText(), "First")
        self.assertEqual(resumed.cards[0].editor.text(), "UserA")
        self.assertEqual(resumed.cards[0].selector.currentData(), "custom")
        resumed._select_app("Beta")
        self.assertEqual(resumed.cards[0].editor.text(), "PickedB")
        self.assertEqual(resumed.cards[0].selector.currentData(), "choice:0")
        self.assertEqual(resumed.cards[0].selector.currentText(), "候选 B")
        self.assertEqual(len(self.store.writes), 1)


if __name__ == "__main__":
    unittest.main()
