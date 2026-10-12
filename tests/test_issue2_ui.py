"""Issue #2 matching, removal-conflict and scheme-switch UI behavior."""

from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLabel

from envmanager.persistence import StateRepository
from envmanager.models import OperationResult
from envmanager.removal import RemovalRequest, RemovalScope
from envmanager.service import EnvironmentService
from envmanager.sharing import ConflictDecision
from envmanager.store import MemoryStore
from envmanager.ui import (
    OPTION_STATUS_ROLE,
    ApplicationRow,
    MainWindow,
    OldSchemeDialog,
    OrderDialog,
    PreviewDialog,
    SharedConflictDialog,
)
from tests.test_detection_service import config, raw


class Issue2UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        warning = patch("envmanager.ui.QMessageBox.warning", return_value=None)
        information = patch("envmanager.ui.QMessageBox.information", return_value=None)
        warning.start()
        information.start()
        self.addCleanup(warning.stop)
        self.addCleanup(information.stop)

    def make_window(self, configuration, states=()):
        store = MemoryStore(states)
        service = EnvironmentService(
            configuration, store, StateRepository(self.root / "state"))
        window = MainWindow(service, self.root / "env.toml", demo=True)
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        return window, service, store

    @staticmethod
    def row(window: MainWindow, app: str) -> ApplicationRow:
        for index in range(window.app_list.count()):
            item = window.app_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == app:
                return window.app_list.itemWidget(item)
        raise AssertionError(f"missing application row: {app}")

    @staticmethod
    def check(window: MainWindow, app: str) -> None:
        for index in range(window.app_list.count()):
            item = window.app_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == app:
                item.setCheckState(Qt.CheckState.Checked)
                return
        raise AssertionError(f"missing application: {app}")

    def test_global_detection_populates_rows_and_popup_without_writes(self):
        configuration = config(
            ("App", "Full", [("HOME", "ok", "replace", "User"),
                               ("Path", "A", "+append", "User")]),
            ("App", "Partial", [("HOME", "wrong", "replace", "User"),
                                  ("Path", "A", "+append", "User")]),
            ("App", "Missing", [("OTHER", "x", "replace", "User")]),
        )
        window, _, store = self.make_window(
            configuration, [raw("HOME", "ok"), raw("Path", "BASE;A")])
        app_row = self.row(window, "App")
        self.assertEqual("2/2", app_row.match_label.text())
        self.assertIn("完全匹配 2/2", app_row.match_label.toolTip())
        self.assertEqual(window.option_combo.currentText(), "Full")
        self.assertEqual(window.option_combo.currentData(Qt.ItemDataRole.UserRole), "Full")
        statuses = {}
        for index in range(window.option_combo.count()):
            statuses[window.option_combo.itemText(index)] = window.option_combo.itemData(
                index, OPTION_STATUS_ROLE)["display"]
        self.assertIn("完全匹配", statuses["Full"])
        self.assertIn("部分匹配", statuses["Partial"])
        self.assertIn("尚未配置", statuses["Missing"])
        self.assertEqual(store.writes, [])

    def test_activation_history_and_live_match_status_are_independent(self):
        configuration = config(
            ("App", "One", [("HOME", "one", "replace", "User")]),
            ("App", "Two", [("HOME", "two", "replace", "User")]),
        )
        window, service, store = self.make_window(configuration, [raw("HOME", "two")])
        snapshot = service.state.snapshot()
        snapshot["active"]["App"] = {"option": "One", "effects": []}
        operation = {"id": "seed", "created_at": "2000", "app": "App", "option": "One",
                     "action": "apply", "status": "success", "changes": []}
        service.state.save_operation(operation)
        service.state.commit(snapshot["revision"], snapshot, operation)
        window._render_variables()
        self.assertIn("已激活", window.activation_badge.text())
        self.assertIn("未匹配 0/1", window.match_status.text())
        window.option_combo.setCurrentText("Two")
        self.assertIn("完全匹配", window.match_status.text())
        self.assertIn("One", window.active_label.text())
        self.assertEqual(store.writes, [])

    def test_variable_partial_details_enable_only_actual_element_removal(self):
        configuration = config(
            ("App", "Default", [("Path", "A;MISSING", "+append", "User")]))
        window, _, store = self.make_window(
            configuration, [raw("Path", "BASE;A; A ")])
        card = window.cards[0]
        self.assertIn("匹配 1/2", card.match_label.text())
        self.assertTrue(card.remove_button.isEnabled())
        self.assertIn("已匹配：A", card.matching_info.text())
        self.assertIn("缺失：MISSING", card.matching_info.text())
        self.assertIn("× 2", card.matching_info.text())
        self.assertIn("全部等值副本，区分大小写", card.removal_info.text())
        self.assertEqual(store.writes, [])

    def test_five_visible_entrypoints_build_exact_removal_scopes(self):
        configuration = config(
            ("App", "One", [("A", "a", "replace", "User")]),
            ("App", "Two", [("B", "b", "replace", "User")]),
            ("Other", "One", [("C", "c", "replace", "User")]),
        )
        window, _, _ = self.make_window(
            configuration, [raw("A", "a"), raw("B", "b"), raw("C", "c")])
        captured = []
        with patch.object(window, "_run_removal", side_effect=captured.append):
            window._remove_variable(window.cards[0].spec)
            window._remove_scheme()
            window._remove_application_all()
            self.check(window, "App")
            self.check(window, "Other")
            window._remove_batch_current()
            window._remove_batch_all()
        self.assertEqual([request.scope for request in captured], [
            RemovalScope.VARIABLE, RemovalScope.SCHEME, RemovalScope.APPLICATION_ALL,
            RemovalScope.BATCH_CURRENT, RemovalScope.BATCH_ALL,
        ])
        self.assertEqual(captured[0].variable_keys, (window.cards[0].spec.key,))
        self.assertEqual(captured[1].selected_options, {"App": "One"})
        self.assertEqual(captured[2].selected_apps, frozenset(("App",)))
        self.assertEqual(captured[3].selected_apps, frozenset(("App", "Other")))
        self.assertEqual(captured[3].selected_options, {"App": "One", "Other": "One"})
        self.assertEqual(captured[4].selected_options, {})

    def test_shared_dialog_has_independent_rows_and_bulk_only_changes_conflicts(self):
        configuration = config(
            ("App", "Default", [("Path", "A;B;ORDINARY", "+append", "User")]),
            ("OtherA", "One", [("Path", "A", "+append", "User")]),
            ("OtherB", "One", [("Path", "B", "+append", "User")]),
        )
        window, service, _ = self.make_window(
            configuration, [raw("Path", "BASE;A;B;ORDINARY")])
        draft = service.prepare_remove(RemovalRequest(
            RemovalScope.SCHEME, frozenset(("App",)), {"App": "Default"}))
        dialog = SharedConflictDialog(
            draft.shared_conflicts, len(draft.ordinary_atoms), window)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(len(dialog.decision_boxes), 2)
        labels = "\n".join(label.text() for label in dialog.findChildren(QLabel))
        self.assertIn("元素：A", labels)
        self.assertIn("元素：B", labels)
        self.assertNotIn("元素：ORDINARY", labels)
        self.assertTrue(all(value == "keep" for value in dialog.decisions().values()))
        dialog._set_all(ConflictDecision.REMOVE)
        first = next(iter(dialog.decision_boxes.values()))
        first.setCurrentIndex(first.findData("keep"))
        decisions = dialog.decisions()
        self.assertEqual(tuple(decisions.values()).count("keep"), 1)
        self.assertEqual(tuple(decisions.values()).count("remove"), 1)

    def test_no_shared_conflict_skips_conflict_dialog_and_opens_full_preview(self):
        configuration = config(
            ("App", "Default", [("Path", "ORDINARY", "+append", "User")]))
        window, _, store = self.make_window(
            configuration, [raw("Path", "BASE;ORDINARY")])
        previewed = []

        def reject_preview(dialog):
            previewed.append(dialog.plan)
            return QDialog.DialogCode.Rejected

        with patch.object(SharedConflictDialog, "exec") as conflicts, \
             patch.object(PreviewDialog, "exec", new=reject_preview):
            window._remove_scheme()
        conflicts.assert_not_called()
        self.assertEqual(len(previewed), 1)
        self.assertEqual(previewed[0].action, "remove")
        self.assertEqual(previewed[0].changes[0].after.value, "BASE")
        self.assertEqual(store.writes, [])

    def test_conflict_review_lists_only_shared_but_preview_keeps_ordinary_change(self):
        configuration = config(
            ("App", "Default", [("Path", "SHARED;ORDINARY", "+append", "User")]),
            ("Protected", "Any", [("Path", "SHARED", "+append", "User")]),
        )
        window, _, store = self.make_window(
            configuration, [raw("Path", "BASE;SHARED;ORDINARY")])
        reviewed, previewed, errors = [], [], []

        def allow_shared_remove(dialog):
            reviewed.append(dialog)
            labels = "\n".join(label.text() for label in dialog.findChildren(QLabel))
            self.assertIn("元素：SHARED", labels)
            self.assertNotIn("元素：ORDINARY", labels)
            dialog._set_all(ConflictDecision.REMOVE)
            return QDialog.DialogCode.Accepted

        def reject_preview(dialog):
            previewed.append(dialog.plan)
            return QDialog.DialogCode.Rejected

        with patch.object(SharedConflictDialog, "exec", new=allow_shared_remove), \
             patch.object(PreviewDialog, "exec", new=reject_preview), \
             patch.object(window, "_show_operation_error", side_effect=errors.append):
            window._remove_scheme()
        self.assertEqual(errors, [])
        self.assertEqual(len(reviewed), 1)
        self.assertEqual(len(previewed), 1)
        self.assertEqual(previewed[0].changes[0].after.value, "BASE")
        self.assertEqual(set(previewed[0].metadata["decisions"].values()), {"remove"})
        self.assertEqual(len(previewed[0].metadata["ordinary_atoms"]), 1)
        final_dialog = PreviewDialog(previewed[0])
        self.addCleanup(final_dialog.deleteLater)
        final_labels = "\n".join(label.text() for label in final_dialog.findChildren(QLabel))
        self.assertIn("共享保护范围", final_labels)
        self.assertIn("Protected（Any）", final_labels)
        self.assertIn("撤销 · User / PATH · SHARED", final_labels)
        self.assertIn("撤销列表元素：SHARED；ORDINARY", final_labels)
        self.assertEqual(store.writes, [])

    def test_cancel_shared_review_never_opens_preview_or_writes(self):
        configuration = config(
            ("App", "Default", [("Path", "SHARED", "+append", "User")]),
            ("Protected", "Any", [("Path", "SHARED", "+append", "User")]),
        )
        window, _, store = self.make_window(
            configuration, [raw("Path", "BASE;SHARED")])
        with patch.object(SharedConflictDialog, "exec", return_value=QDialog.DialogCode.Rejected), \
             patch.object(PreviewDialog, "exec") as preview:
            window._remove_scheme()
        preview.assert_not_called()
        self.assertEqual(store.writes, [])

    def test_confirmed_scheme_removal_executes_through_preview_and_refreshes_match(self):
        configuration = config(
            ("App", "Default", [("Path", "REMOVE", "+append", "User")]))
        window, service, store = self.make_window(
            configuration, [raw("Path", "BASE;REMOVE")])
        with patch.object(PreviewDialog, "exec", return_value=QDialog.DialogCode.Accepted):
            window._remove_scheme()
        self.assertEqual(store.read("User", "Path").value, "BASE")
        self.assertEqual(len(store.writes), 1)
        self.assertIn("未匹配 0/1", window.match_status.text())
        self.assertEqual(service.state.history()[0]["action"], "remove")

    def test_all_keep_shared_still_executes_ordinary_removal(self):
        configuration = config(
            ("App", "Default", [("Path", "SHARED;ORDINARY", "+append", "User")]),
            ("Protected", "Any", [("Path", "SHARED", "+append", "User")]),
        )
        window, _, store = self.make_window(
            configuration, [raw("Path", "BASE;SHARED;ORDINARY")])
        with patch.object(SharedConflictDialog, "exec",
                          return_value=QDialog.DialogCode.Accepted), \
             patch.object(PreviewDialog, "exec", return_value=QDialog.DialogCode.Accepted):
            window._remove_scheme()
        self.assertEqual(store.read("User", "Path").value, "BASE;SHARED")
        self.assertEqual(len(store.writes), 1)

    def test_ambiguous_switch_requires_old_scheme_choice_before_preview(self):
        configuration = config(
            ("App", "A", [("HOME", "old", "replace", "User")]),
            ("App", "B", [("HOME", "old", "replace", "User")]),
            ("App", "New", [("HOME", "new", "replace", "User")]),
        )
        window, _, store = self.make_window(configuration, [raw("HOME", "old")])
        window.option_combo.setCurrentText("New")
        previewed = []

        def choose_b(dialog: OldSchemeDialog):
            for index in range(dialog.list.count()):
                item = dialog.list.item(index)
                if item.data(Qt.ItemDataRole.UserRole) == "B":
                    item.setCheckState(Qt.CheckState.Checked)
            return QDialog.DialogCode.Accepted

        def reject_preview(dialog: PreviewDialog):
            previewed.append(dialog.plan)
            return QDialog.DialogCode.Rejected

        with patch.object(OldSchemeDialog, "exec", new=choose_b), \
             patch.object(PreviewDialog, "exec", new=reject_preview):
            window._apply()
        self.assertEqual(len(previewed), 1)
        self.assertEqual(previewed[0].action, "switch")
        self.assertEqual(previewed[0].metadata["old_options"], ["B"])
        final_dialog = PreviewDialog(previewed[0])
        self.addCleanup(final_dialog.deleteLater)
        labels = "\n".join(label.text() for label in final_dialog.findChildren(QLabel))
        self.assertIn("切换：App / B  →  App / New", labels)
        self.assertIn("旧方案实际撤销：old", labels)
        self.assertIn("将替换完整变量值", labels)
        self.assertNotIn("撤销列表元素：old", labels)
        self.assertEqual(store.writes, [])

    def test_partial_old_scheme_requires_explicit_choice_and_skips_mismatched_value(self):
        configuration = config(
            ("App", "Old", [("HOME", "old", "replace", "User"),
                              ("Path", "OLD", "+append", "User")]),
            ("App", "New", [("HOME", "new", "replace", "User"),
                              ("Path", "NEW", "+append", "User")]),
        )
        window, _, store = self.make_window(
            configuration, [raw("HOME", "external"), raw("Path", "BASE;OLD")])
        window.option_combo.setCurrentText("New")
        previewed = []

        def choose_old(dialog):
            self.assertFalse(dialog.required)
            labels = "\n".join(label.text() for label in dialog.findChildren(QLabel))
            self.assertIn("准备应用：App / New", labels)
            self.assertIn("确认前不会写入环境变量", labels)
            item = dialog.list.item(0)
            self.assertEqual(item.data(Qt.ItemDataRole.UserRole), "Old")
            self.assertIn("部分匹配", item.text())
            item.setCheckState(Qt.CheckState.Checked)
            return QDialog.DialogCode.Accepted

        def capture(dialog):
            previewed.append(dialog.plan)
            return QDialog.DialogCode.Rejected

        with patch.object(OldSchemeDialog, "exec", new=choose_old), \
             patch.object(PreviewDialog, "exec", new=capture):
            window._apply()
        plan = previewed[0]
        self.assertEqual(plan.metadata["old_options"], ["Old"])
        path = next(change for change in plan.changes if change.before.name == "Path")
        self.assertEqual(path.after.value, "NEW;BASE")
        preview = PreviewDialog(plan)
        self.addCleanup(preview.deleteLater)
        labels = "\n".join(label.text() for label in preview.findChildren(QLabel))
        self.assertIn("Windows 列表中没有该元素", labels)
        self.assertNotIn("value_mismatch", labels)
        self.assertEqual(store.writes, [])

    def test_zero_match_old_path_goes_directly_to_preview_in_single_and_batch_apply(self):
        configuration = config(
            ("Git", "PortableGit", [("Path", "NEW_CMD", "+append", "User")]),
            ("Git", "GitPortable", [("Path", "OLD_CMD", "+append", "User")]),
            ("Editor", "Default", [("Path", "EDITOR", "+append", "User")]),
        )
        window, service, store = self.make_window(configuration, [raw("Path", "NEW_CMD;EDITOR;BASE")])
        window.option_combo.setCurrentText("PortableGit")
        initial_state = service.state.snapshot()
        for batch in (False, True):
            with self.subTest(batch=batch):
                if batch:
                    self.check(window, "Git")
                    self.check(window, "Editor")
                with patch.object(OldSchemeDialog, "exec") as old_dialog, \
                     patch.object(PreviewDialog, "exec", return_value=QDialog.DialogCode.Rejected) as preview:
                    window._apply()
                old_dialog.assert_not_called()
                preview.assert_called_once()
                self.assertEqual(store.writes, [])
                self.assertEqual(service.state.snapshot(), initial_state)

    def test_old_scheme_dialog_wraps_long_names_and_scrolls_many_candidates(self):
        dialog = OldSchemeDialog("应用名称中文测试-" * 6,
                                [(f"旧方案-{i}-" + "很长名称" * 5, "partial") for i in range(20)],
                                required=False, new_option="long-portable-scheme-name-" * 5)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.close)
        dialog.show()
        self.application.processEvents()
        self.assertEqual(dialog.list.count(), 20)
        self.assertGreater(dialog.list.verticalScrollBar().maximum(), 0)
        for label in dialog.findChildren(QLabel):
            if label.text():
                self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))
        dialog.list.scrollToItem(dialog.list.item(19))
        self.application.processEvents()
        last = dialog.list.visualItemRect(dialog.list.item(19))
        self.assertFalse(last.intersected(dialog.list.viewport().rect()).isEmpty())

    def test_execute_result_reports_metrics_and_distinguishes_recovery_outcomes(self):
        configuration = config(("App", "Default", [("HOME", "ok", "replace", "User")]))
        window, service, store = self.make_window(configuration, [raw("HOME", "ok")])
        plan = service.plan("App", "Default")
        cases = (
            ("success", "执行成功", False),
            ("no_change", "已验证，无需写入", False),
            ("notification_failed", "已写入，系统通知失败", True),
            ("rolled_back", "执行失败，已回滚", True),
            ("rollback_incomplete", "回滚不完整", True),
        )
        for status, expected_title, warning in cases:
            with self.subTest(status=status):
                result = OperationResult(status, "测试结果", "id", changed_count=0 if status == "no_change" else 2,
                                         removed_element_count=0 if status == "no_change" else 3,
                                         kept_shared_count=1, skipped_count=4,
                                         errors=("User / HOME 待修复",) if status == "rollback_incomplete" else (),
                                         notification_error="broadcast failed" if status == "notification_failed" else None)
                with patch.object(PreviewDialog, "exec", return_value=QDialog.DialogCode.Accepted), \
                     patch.object(service, "execute", return_value=result), \
                     patch("envmanager.ui.QMessageBox.warning") as warn, \
                     patch("envmanager.ui.QMessageBox.information") as info:
                    window._execute_plan(plan)
                called = warn if warning else info
                self.assertEqual(called.call_args.args[1], expected_title)
                self.assertIn(expected_title, window.feedback.text())
                if result.success:
                    message = called.call_args.args[2]
                    self.assertIn(f"实际写入 {result.changed_count} 个注册表目标", message)
                    self.assertIn(f"移除 {result.removed_element_count} 个列表元素/副本", message)
                    self.assertIn("保留 1 个共享对象", message)
                    self.assertIn("跳过 4 项", message)
                if status == "rollback_incomplete":
                    self.assertIn("User / HOME 待修复", called.call_args.args[2])
        self.assertEqual(store.writes, [])

    def test_switch_order_is_reprepared_before_shared_review_and_final_preview(self):
        configuration = config(
            ("App", "Old", [("Path", "OLD", "+append", "User")]),
            ("App", "New", [("Path", "NEW", "append", "User")]),
            ("Protected", "Any", [("Path", "OLD", "+append", "User")]),
        )
        window, service, store = self.make_window(configuration, [raw("Path", "BASE;OLD")])
        window.option_combo.setCurrentText("New")
        events = []
        previewed = []
        original_prepare = service.prepare_scheme_switch

        def prepare(*args, **kwargs):
            events.append("prepare_with_order" if kwargs.get("orders") else "prepare")
            return original_prepare(*args, **kwargs)

        def order(dialog):
            events.append("order")
            return QDialog.DialogCode.Accepted

        def shared(dialog):
            events.append("shared")
            self.assertEqual(len(dialog.conflicts), 1)
            dialog._set_all(ConflictDecision.REMOVE)
            return QDialog.DialogCode.Accepted

        def preview(dialog):
            events.append("preview")
            previewed.append(dialog.plan)
            return QDialog.DialogCode.Rejected

        with patch.object(service, "prepare_scheme_switch", side_effect=prepare), \
             patch.object(OrderDialog, "exec", new=order), \
             patch.object(OrderDialog, "order", return_value=("NEW", "BASE")), \
             patch.object(SharedConflictDialog, "exec", new=shared), \
             patch.object(PreviewDialog, "exec", new=preview):
            window._apply()
        self.assertEqual(events, ["prepare", "order", "prepare_with_order", "shared", "preview"])
        self.assertFalse(previewed[0].needs_order)
        self.assertEqual(previewed[0].changes[0].after.value, "NEW;BASE")
        self.assertEqual(store.writes, [])

    def test_switch_preview_counts_whole_key_deletion(self):
        configuration = config(
            ("App", "Old", [("OLD_HOME", "old", "replace", "User")]),
            ("App", "New", [("NEW_HOME", "new", "replace", "User")]),
        )
        window, service, _ = self.make_window(configuration, [raw("OLD_HOME", "old")])
        window.option_combo.setCurrentText("New")
        draft = service.prepare_scheme_switch("App", "New")
        plan = service.finalize_scheme_switch(draft.id)
        preview = PreviewDialog(plan)
        self.addCleanup(preview.deleteLater)
        labels = "\n".join(label.text() for label in preview.findChildren(QLabel))
        self.assertIn("删除 1 个完整变量", labels)

    def test_switch_shared_keep_requests_complete_order_again_in_single_and_checked_routes(self):
        configuration = config(
            ("App", "Old", [("Path", "OLD", "+append", "User")]),
            ("App", "New", [("Path", "NEW", "append", "User")]),
            ("Protected", "Any", [("Path", "OLD", "+append", "User")]),
        )
        for checked in (False, True):
            with self.subTest(checked=checked):
                window, service, store = self.make_window(configuration, [raw("Path", "BASE;OLD")])
                window.option_combo.setCurrentText("New")
                if checked:
                    self.check(window, "App")
                events, previewed = [], []
                api = "prepare_batch_scheme_switch" if checked else "prepare_scheme_switch"
                original_prepare = getattr(service, api)

                def prepare(*args, **kwargs):
                    events.append("prepare_with_order" if kwargs.get("orders") else "prepare")
                    return original_prepare(*args, **kwargs)

                def order(dialog):
                    events.append("order")
                    return QDialog.DialogCode.Accepted

                def shared(dialog):
                    events.append("shared")
                    self.assertEqual(len(dialog.conflicts), 1)
                    self.assertTrue(all(value == "keep" for value in dialog.decisions().values()))
                    return QDialog.DialogCode.Accepted

                def confirmed_order(dialog):
                    # OrderDialog also reads order() while updating its own
                    # preview; tie the answer to this dialog's complete list.
                    items = {dialog.list.item(index).text() for index in range(dialog.list.count())}
                    return ("BASE", "OLD", "NEW") if "OLD" in items else ("NEW", "BASE")

                def preview(dialog):
                    events.append("preview")
                    previewed.append(dialog.plan)
                    return QDialog.DialogCode.Rejected

                with patch.object(service, api, side_effect=prepare), \
                     patch.object(OrderDialog, "exec", new=order), \
                     patch.object(OrderDialog, "order", new=confirmed_order), \
                     patch.object(SharedConflictDialog, "exec", new=shared), \
                     patch.object(PreviewDialog, "exec", new=preview):
                    window._apply()
                self.assertEqual(events, ["prepare", "order", "prepare_with_order", "shared",
                                          "order", "prepare_with_order", "shared", "preview"])
                self.assertFalse(previewed[0].needs_order)
                self.assertEqual(previewed[0].changes[0].after.value, "BASE;OLD;NEW")
                self.assertEqual(store.writes, [])

    def test_shared_sensitive_value_requires_reveal_without_changing_decision(self):
        configuration = config(
            ("App", "Default", [("SECRET_TOKEN", "top-secret", "replace", "User")]),
            ("Protected", "Any", [("SECRET_TOKEN", "top-secret", "replace", "User")]),
        )
        _, service, _ = self.make_window(configuration, [raw("SECRET_TOKEN", "top-secret")])
        draft = service.prepare_remove(RemovalRequest(
            RemovalScope.SCHEME, frozenset(("App",)), {"App": "Default"}))
        dialog = SharedConflictDialog(draft.shared_conflicts)
        self.addCleanup(dialog.deleteLater)
        labels = lambda: "\n".join(label.text() for label in dialog.findChildren(QLabel))
        self.assertNotIn("top-secret", labels())
        before = dialog.decisions()
        box = next(iter(dialog.decision_boxes.values()))
        self.assertIn("Protected / Any", box.accessibleDescription())
        dialog.show_sensitive.setChecked(True)
        self.assertIn("top-secret", labels())
        self.assertEqual(dialog.decisions(), before)

    def test_checked_single_application_switches_manually_configured_old_scheme(self):
        configuration = config(
            ("App", "Old", [("Path", "OLD", "+append", "User")]),
            ("App", "New", [("Path", "NEW", "+append", "User")]),
        )
        window, service, store = self.make_window(configuration, [raw("Path", "BASE;OLD")])
        window.option_combo.setCurrentText("New")
        self.check(window, "App")
        with patch.object(PreviewDialog, "exec", return_value=QDialog.DialogCode.Accepted):
            window._apply()
        self.assertEqual(store.read("User", "Path").value, "NEW;BASE")
        self.assertEqual(len(store.writes), 1)
        self.assertEqual(service.state.history()[0]["action"], "switch")

    def test_checked_applications_share_one_net_switch_plan_and_one_write(self):
        configuration = config(
            ("App", "Old", [("Path", "OLD_A", "+append", "User")]),
            ("App", "New", [("Path", "NEW_A", "+append", "User")]),
            ("Other", "Old", [("Path", "OLD_B", "+append", "User")]),
            ("Other", "New", [("Path", "NEW_B", "+append", "User")]),
        )
        window, service, store = self.make_window(
            configuration, [raw("Path", "BASE;OLD_A;OLD_B")])
        service.select_option("Other", "New")
        window.option_combo.setCurrentText("New")
        self.check(window, "App")
        self.check(window, "Other")
        previewed = []

        def confirm(dialog):
            previewed.append(dialog.plan)
            return QDialog.DialogCode.Accepted

        with patch.object(PreviewDialog, "exec", new=confirm):
            window._apply()
        plan = previewed[0]
        self.assertEqual(plan.applications, (("App", "New"), ("Other", "New")))
        self.assertEqual(len(plan.changes), 1)
        self.assertEqual(store.read("User", "Path").value, "NEW_A;NEW_B;BASE")
        self.assertEqual(len(store.writes), 1)
        self.assertEqual({item["app"] for item in plan.metadata["switches"]}, {"App", "Other"})
        preview = PreviewDialog(plan)
        self.addCleanup(preview.deleteLater)
        labels = "\n".join(label.text() for label in preview.findChildren(QLabel))
        self.assertIn("切换：App / Old  →  App / New", labels)
        self.assertIn("切换：Other / Old  →  Other / New", labels)

    def test_checked_batch_resolves_ambiguous_and_partial_old_choices_per_application(self):
        configuration = config(
            ("App", "A", [("Path", "OLD_A", "+append", "User")]),
            ("App", "B", [("Path", "OLD_A", "+append", "User")]),
            ("App", "New", [("Path", "NEW_A", "+append", "User")]),
            ("Other", "Old", [("HOME", "old", "replace", "User"),
                                ("Path", "OLD_B", "+append", "User")]),
            ("Other", "New", [("Path", "NEW_B", "+append", "User")]),
        )
        window, service, store = self.make_window(
            configuration, [raw("Path", "BASE;OLD_A;OLD_B"), raw("HOME", "external")])
        service.select_option("Other", "New")
        window.option_combo.setCurrentText("New")
        self.check(window, "App")
        self.check(window, "Other")
        choices, previewed = [], []

        def choose(dialog):
            choices.append(dialog.required)
            dialog.list.item(0).setCheckState(Qt.CheckState.Checked)
            return QDialog.DialogCode.Accepted

        def preview(dialog):
            previewed.append(dialog.plan)
            return QDialog.DialogCode.Rejected

        with patch.object(OldSchemeDialog, "exec", new=choose), \
             patch.object(PreviewDialog, "exec", new=preview):
            window._apply()
        self.assertEqual(choices, [True, False])
        old = {item["app"]: item["old_options"] for item in previewed[0].metadata["switches"]}
        self.assertEqual(old, {"App": ["A"], "Other": ["Old"]})
        self.assertEqual(store.writes, [])

    def test_production_startup_scan_is_nonblocking_and_preserves_live_drafts(self):
        configuration = config(("App", "Default", [("HOME", "default", "replace", "User")]))
        store = MemoryStore([raw("HOME", "edited")])
        service = EnvironmentService(configuration, store, StateRepository(self.root / "async-state"))
        release = threading.Event()
        original = service.run_detection_scan
        reads = []
        original_read = store.read

        def record_read(scope, name):
            reads.append(threading.current_thread())
            return original_read(scope, name)

        def delayed(request):
            if not release.wait(5):
                raise RuntimeError("worker did not receive test release")
            return original(request)

        with patch.object(service, "run_detection_scan", side_effect=delayed), \
             patch.object(store, "read", side_effect=record_read):
            window = MainWindow(service, self.root / "env.toml", demo=False)
            self.addCleanup(window.deleteLater)
            self.addCleanup(window.close)
            try:
                self.assertTrue(window._scanning)
                self.assertIn("检测中", window.match_status.text())
                self.assertFalse(window.apply_button.isEnabled())
                self.assertEqual(reads, [])
                processed = []
                QTimer.singleShot(0, lambda: processed.append(True))
                self.application.processEvents()
                self.assertEqual(processed, [True])
                card = window.cards[0]
                card._edit("edited")
                self.check(window, "App")
                self.assertEqual(reads, [])
            finally:
                release.set()
            for _ in range(100):
                if not window._scanning:
                    break
                QTest.qWait(20)
            self.assertFalse(window._scanning)
            self.assertIn("完全匹配", window.match_status.text())
            self.assertEqual(window.cards[0].editor.text(), "edited")
            self.assertEqual(window.checked_apps, {"App"})
            self.assertTrue(window.apply_button.isEnabled())
            self.assertTrue(window.deactivate_button.isEnabled())
            self.assertEqual(len(reads), 1)
            self.assertIsNot(reads[0], threading.current_thread())
            self.assertEqual(store.writes, [])


if __name__ == "__main__":
    unittest.main()
