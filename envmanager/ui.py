"""Chinese desktop interface. All registry mutations are delegated to the service."""

from __future__ import annotations

import re
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal, QSize, QEvent
from PySide6.QtCore import QUrl
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem, QMainWindow, QMenu, QSizePolicy,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QVBoxLayout, QWidget, QStyledItemDelegate, QStyleOptionViewItem, QStyleOptionComboBox, QStyle, QStylePainter,
    QStackedWidget, QSplitter,
)

from .matching import SchemeStatus, VariableMatchStatus
from .elements import CleanupSettings, effective_elements, normalize_element
from .models import ConflictError, ExecutionPlan, PlanChange, RegistryState, VariableSpec, match_key
from .removal import RemovalKind, RemovalRequest, RemovalScope
from .sharing import ConflictDecision
from .switching import AmbiguousOldSchemeError
from .theme import apply_theme, COLORS
from .config_editor_ui import ConfigEditorDialog
from .icons import icon, app_icon
from .windows_shell import open_environment_settings
from .ui_controls import ActionButton
from .native_workspace_pages import WindowsEnvironmentPage, HistoryPage


def _label(text: str, name: str = "") -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(True)
    return widget


class _DetectionSignals(QObject):
    finished = Signal(int, object, str)


class _DetectionWorker(QRunnable):
    """Run one frozen, read-only detection request outside the UI thread."""

    def __init__(self, token: int, work):
        super().__init__()
        self.token = token
        self.work = work
        self.signals = _DetectionSignals()

    def run(self) -> None:
        try:
            snapshot = self.work()
            error = ""
        except Exception as exc:
            snapshot = None
            error = str(exc)
        self.signals.finished.emit(self.token, snapshot, error)


class ElidingLabel(QLabel):
    """Keep a long heading readable while retaining its exact text in a tooltip."""

    def __init__(self, text: str = "", name: str = ""):
        super().__init__()
        if name:
            self.setObjectName(name)
        self.setWordWrap(False)
        self._full_text = ""
        self.setText(text)

    def setText(self, text: str) -> None:
        self._full_text = text
        self.setToolTip(text)
        self._update_display()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_display()

    def _update_display(self) -> None:
        shown = self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideRight, max(0, self.contentsRect().width())
        )
        if super().text() != shown:
            super().setText(shown)


class ApplicationNameButton(ActionButton):
    """Navigation text elides using the actual row width, with a full tooltip."""

    def __init__(self, app: str, callback):
        super().__init__(app)
        self._full_name = app
        self.setObjectName("manifestName")
        self.setToolTip(f"查看 {app} 的变量")
        self.setAccessibleName(f"查看 {app} 详情")
        self.clicked.connect(callback)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.setText(self.fontMetrics().elidedText(
            self._full_name, Qt.TextElideMode.ElideRight, max(0, self.width() - 4)))


from .scheme_selector import OPTION_STATUS_ROLE, OptionStatusDelegate, OptionComboBox


def _scheme_presentation(scheme) -> tuple[str, str, str, str]:
    """Return human text, semantic tone, progress and explanation."""
    if scheme is None:
        return "… 检测中", "scanning", "", "尚未取得 Windows 检测结果"
    progress = f"{scheme.matched_count}/{scheme.total_count}"
    if not scheme.available:
        detail = "；".join(error.message for error in scheme.errors) or "检测结果不可用"
        return "× 检测错误", "error", progress, detail
    if scheme.status is SchemeStatus.COMPLETE:
        return "✓ 完全匹配", "complete", progress, "方案的全部变量均与 Windows 当前值匹配"
    if scheme.status is SchemeStatus.PARTIAL:
        if scheme.matched_count == 0:
            return "○ 未匹配", "partial", progress, "方案变量与 Windows 当前值均未完整匹配，需要配置"
        return "! 部分匹配", "partial", progress, "方案只有部分变量或列表元素匹配"
    return "○ 尚未配置", "unconfigured", progress, "方案目标变量在 Windows 中均不存在"


def _variable_presentation(result, special: bool = False) -> tuple[str, str, str]:
    if result is None:
        return "… 检测中", "scanning", "尚未取得 Windows 检测结果"
    if not result.available:
        return "× 检测错误", "error", result.error.message if result.error else "检测结果不可用"
    if result.status is VariableMatchStatus.MATCH:
        if special:
            return "✓ 匹配", "complete", "Windows 原始值与当前选定值完全相同"
        if not effective_elements(result.selected_value):
            return "✓ 匹配空值", "complete", "Windows 目标键存在且原始值为空"
        if result.mode == "replace":
            return "✓ 匹配", "complete", "Windows 有效元素与当前选定元素集合完全相同"
        total = len(result.matched_elements) + len(result.missing_elements)
        return f"✓ 匹配 {len(result.matched_elements)}/{total}", "complete", "选定的列表元素均已存在"
    if result.status is VariableMatchStatus.KEY_MISSING:
        return "○ 尚未配置", "unconfigured", "Windows 中不存在此变量"
    if result.status is VariableMatchStatus.VALUE_MISMATCH:
        return "! 不匹配", "partial", ("Windows 原始值与特殊完整值不同，不列入撤销" if special else
                                       "空输入仅匹配存在且原始值为空的键，不列入撤销")
    total = len(result.matched_elements) + len(result.missing_elements)
    return f"! 匹配 {len(result.matched_elements)}/{total}", "partial", "仅实际匹配的配置元素可撤销；配置外元素保留"


def _set_match_label(label: QLabel, text: str, tone: str, tooltip: str) -> None:
    label.setText(text)
    label.setProperty("matching", tone)
    label.setToolTip(tooltip)
    label.setAccessibleDescription(tooltip)
    label.style().unpolish(label)
    label.style().polish(label)


def _activation_badge(label: QLabel, active: str | None) -> None:
    label.setText("已激活" if active else "尚未激活")
    label.setProperty("activation", "active" if active else "inactive")
    label.setToolTip("当前激活方案：" + active if active else "此应用尚未激活方案")
    label.style().unpolish(label)
    label.style().polish(label)


def _button(text: str, callback, name: str = "") -> QPushButton:
    widget = ActionButton(text)
    if name:
        widget.setObjectName(name)
    widget.clicked.connect(callback)
    return widget


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            _clear(item.layout())


def _sensitive(name: str) -> bool:
    return bool(re.search(r"(?:KEY|TOKEN|PASSWORD|SECRET|PASSWD|CREDENTIAL)", name, re.I))


def _value(value: str | None, exists: bool = True, hidden: bool = False) -> str:
    if not exists:
        return "变量不存在"
    if hidden:
        return "••••••••" if value else "（空字符串）"
    if value == "":
        return "（空字符串）"
    return value if value is not None else "变量不存在"


def _registry_type(state: RegistryState) -> str:
    if not state.exists:
        return "—"
    return {1: "REG_SZ", 2: "REG_EXPAND_SZ"}.get(state.registry_type, str(state.registry_type or "未知"))


def _status_text(status: str) -> str:
    return {
        "success": "执行成功", "no_change": "已验证，无需写入", "notification_failed": "已写入，系统通知失败",
        "failed": "执行失败", "conflict": "检测到冲突", "rollback_incomplete": "回滚不完整",
        "rolled_back": "执行失败，已回滚", "state_commit_failed": "状态日志提交失败", "pending": "未完成",
        "prepared": "已准备", "writing": "写入中", "rolling_back": "回滚中",
        "recovered": "已恢复未完成操作", "restored": "已恢复历史状态",
    }.get(status, status)


def _local_time(value: str) -> str:
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=timezone.utc)
        return instant.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return value or ""


def _applications_text(applications, app: str = "", option: str = "") -> str:
    """Format both journal dictionaries and immutable plan pairs."""
    labels = []
    for application in applications or ():
        if isinstance(application, dict):
            name, selected = application.get("app", ""), application.get("option", "")
        else:
            name, selected = application
        labels.append(f"{name} / {selected}")
    return "；".join(labels) if labels else f"{app} / {option}"


VARIABLE_COLUMNS = (124, 112, 112, 180, 112, 132)


def _variable_columns(layout: QGridLayout) -> None:
    """Use one geometry for the header and every independent variable record."""
    layout.setHorizontalSpacing(8)
    for index, width in enumerate(VARIABLE_COLUMNS):
        layout.setColumnMinimumWidth(index, width)
        layout.setColumnStretch(index, 1 if index == 3 else 0)


class VariableCard(QFrame):
    changed = Signal()

    def __init__(self, service, spec: VariableSpec, parent=None, *, match_result=None,
                 remove_callback=None, snapshot_managed: bool = False,
                 detection_error: str = ""):
        super().__init__(parent)
        self.setObjectName("compactVariable")
        self.service = service
        self.spec = spec
        self.match_result = match_result
        self.remove_callback = remove_callback
        self.snapshot_managed = snapshot_managed
        self.detection_error = detection_error
        self.hidden = _sensitive(spec.name)
        self._updating = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 0, 8, 0)
        outer.setSpacing(4)

        top = QGridLayout()
        _variable_columns(top)
        top.setContentsMargins(0, 8, 0, 8)
        name = ElidingLabel(spec.name, "name")
        name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        name.setToolTip(spec.name)
        top.addWidget(name, 0, 0)
        scope = "用户" if spec.scope == "User" else "系统"
        mode = {"replace": "替换", "+append": "前置", "-append": "后置", "append": "自定义排序"}[spec.mode]
        metadata = ElidingLabel(f"{scope} · {mode}" + (" · 特殊完整值" if spec.special else ""), "meta")
        metadata.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        metadata.setWordWrap(False)
        top.addWidget(metadata, 0, 1)
        self.match_label = _label("", "matchStatus")
        self.match_label.setWordWrap(False)
        top.addWidget(self.match_label, 0, 4)
        self.remove_button = _button("撤销", self._remove, "quiet")
        self.remove_button.setAccessibleName(f"撤销 {spec.name} 的实际匹配内容")
        self.remove_button.setToolTip("预览撤销实际匹配的" + ("特殊完整值（删除整键）" if spec.special else "普通元素或唯一空值"))
        outer.addLayout(top)

        self.selector = QComboBox()
        self.selector.setFixedWidth(VARIABLE_COLUMNS[2])
        self.selector.setToolTip("选择默认值、上次应用值、候选值或自定义值")
        self.selector.setAccessibleName(f"{spec.name} 值来源")
        self.selector.currentIndexChanged.connect(self._choose)
        top.addWidget(self.selector, 0, 2)
        self.editor = QLineEdit()
        self.editor.setObjectName("code")
        self.editor.setMinimumWidth(40)
        self.editor.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.editor.setPlaceholderText("输入自定义值" if spec.editable else "选择值来源")
        self.editor.setReadOnly(not spec.editable)
        self.editor.setAccessibleName(f"{spec.name} 当前选定值")
        self.editor.textEdited.connect(self._edit)
        top.addWidget(self.editor, 0, 3)
        operations = QHBoxLayout()
        operations.setSpacing(0)
        self.full_value_button = _button("", self._full_value, "iconOnly")
        self.full_value_button.setIcon(icon("expand"))
        self.full_value_button.setToolTip("查看完整原始字符串" + ("并编辑" if spec.editable else ""))
        self.full_value_button.setAccessibleName(f"查看 {spec.name} 完整值")
        operations.addWidget(self.full_value_button)
        operations.addWidget(self.remove_button)
        self.reveal = _button("显示" if self.hidden else "", self._toggle_reveal)
        self.reveal.setProperty("quiet", True)
        self.reveal.setVisible(self.hidden)
        self.reveal.setAccessibleName(f"显示 {spec.name} 敏感值")
        operations.addWidget(self.reveal)
        self.error = _label("", "error")
        self.error.setVisible(False)
        outer.addWidget(self.error)

        self.details_toggle = _button("", self._toggle_details, "iconOnly")
        self.details_toggle.setIcon(icon("chevron_right"))
        self.details_toggle.setToolTip("收起或展开默认、上次应用与 Windows 参考值")
        self.details_toggle.setCheckable(True)
        self.details_toggle.setAccessibleName(f"展开 {spec.name} 参考值")
        operations.addWidget(self.details_toggle)
        top.addLayout(operations, 0, 5)
        self.details = QFrame()
        self.details.setObjectName("variableDetails")
        details_layout = QVBoxLayout(self.details)
        details_layout.setContentsMargins(0, 4, 0, 0)
        details_layout.setSpacing(6)
        self.default_info = _label("", "meta")
        self.last_info = _label("", "meta")
        self.existing_info = _label("", "meta")
        self.matching_info = _label("", "meta")
        self.removal_info = _label("", "meta")
        self.existing_type = _label("", "muted")
        self.existing_type.setWordWrap(False)
        for title, widget in (("匹配详情", self.matching_info),
                              ("可撤销内容", self.removal_info),
                              ("默认值", self.default_info),
                              ("上次应用", self.last_info),
                              ("Windows 当前", self.existing_info)):
            widget.setWordWrap(False)
            widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            row = QHBoxLayout()
            row.setSpacing(10)
            caption = _label(title, "muted")
            caption.setFixedWidth(85)
            caption.setWordWrap(False)
            row.addWidget(caption)
            row.addWidget(widget, 1)
            if widget is self.existing_info:
                row.addWidget(self.existing_type)
            details_layout.addLayout(row)
        outer.addWidget(self.details)
        self.details.hide()
        self.setMinimumWidth(sum(VARIABLE_COLUMNS) + 5 * 8 + 16)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.refresh()

    def set_match_result(self, result) -> None:
        self.match_result = result
        self.refresh()

    def _remove(self) -> None:
        if self.remove_callback is not None:
            self.remove_callback(self.spec)

    def _match_details(self) -> tuple[str, str, bool]:
        result = self.match_result
        if result is None or not result.available:
            message = (result.error.message if result and result.error else
                       self.detection_error or "尚未取得检测结果")
            return message, "无可撤销内容", False
        if self.spec.special:
            if result.status is VariableMatchStatus.MATCH:
                return "原始字符串完全匹配", "完整变量（撤销时删除整个键）", True
            if result.status is VariableMatchStatus.VALUE_MISMATCH:
                return "原始字符串不匹配", "值不匹配，不列入撤销", False
            return "Windows 中不存在此变量", "变量不存在，不列入撤销", False
        if not effective_elements(result.selected_value):
            if result.status is VariableMatchStatus.MATCH:
                return "目标键存在且原始值为唯一空值", "撤销匹配空值（删除变量键）", True
            return "空输入未匹配原始空值状态", "无可撤销匹配空值", False
        matched = "；".join(result.matched_elements) or "无"
        missing = "；".join(result.missing_elements) or "无"
        duplicates = [f"{item} × {count}" for item, count in result.duplicate_counts.items()]
        detail = f"已匹配：{matched}；缺失：{missing}"
        detail += "；额外：" + ("；".join(result.extra_elements) or "无")
        if duplicates:
            detail += "；重复：" + "；".join(duplicates)
        removable = bool(result.matched_elements)
        removal = ("；".join(result.matched_elements) if removable else "无可撤销匹配元素")
        if removable and duplicates:
            removal += "（撤销会移除全部等值副本，区分大小写）"
        return detail, removal, removable

    def _toggle_details(self) -> None:
        expanded = self.details_toggle.isChecked()
        self.details.setVisible(expanded)
        self.details_toggle.setIcon(icon("chevron_down" if expanded else "chevron_right"))
        self.details_toggle.setAccessibleName(("收起" if expanded else "展开") + f" {self.spec.name} 参考值")

    def _toggle_reveal(self) -> None:
        self.hidden = not self.hidden
        self.reveal.setText("显示" if self.hidden else "隐藏")
        self.reveal.setAccessibleName(("显示" if self.hidden else "隐藏") + f" {self.spec.name} 敏感值")
        self.refresh()

    def _choose(self, index: int) -> None:
        if self._updating or index < 0:
            return
        try:
            self.service.selections.choose(self.spec, self.selector.itemData(index))
            self.refresh()
            self.changed.emit()
        except Exception as exc:
            self._show_error(str(exc))

    def _edit(self, text: str) -> None:
        if self._updating:
            return
        try:
            self.service.selections.edit(self.spec, text)
            self.refresh(keep_cursor=True)
            self.changed.emit()
        except Exception as exc:
            self._show_error(str(exc))
            self.changed.emit()

    def _show_error(self, message: str) -> None:
        self.error.setText(message)
        self.error.setVisible(bool(message))

    def _full_value(self) -> None:
        dialog = FullValueDialog(self.service.selections.get(self.spec).value, self.spec.editable,
                                 self.hidden, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and self.spec.editable:
            try:
                self.service.selections.edit(self.spec, dialog.value())
                self.refresh()
                self.changed.emit()
            except Exception as exc:
                self._show_error(str(exc))

    def refresh(self, keep_cursor: bool = False) -> None:
        self._updating = True
        try:
            selection = self.service.selections.get(self.spec)
            sources = self.service.selections.sources(self.spec)
            self.selector.clear()
            for source_id, label, _ in sources:
                self.selector.addItem(label, source_id)
                self.selector.setItemData(self.selector.count() - 1, label, Qt.ItemDataRole.ToolTipRole)
            index = self.selector.findData(selection.source)
            if index >= 0:
                self.selector.setCurrentIndex(index)
            current = self.editor.cursorPosition()
            if self.editor.text() != selection.value:
                self.editor.setText(selection.value)
                if keep_cursor:
                    self.editor.setCursorPosition(min(current, len(selection.value)))
                else:
                    self.editor.setCursorPosition(0)
            self.editor.setToolTip(_value(selection.value, hidden=self.hidden))
            self.editor.setEchoMode(QLineEdit.EchoMode.Password if self.hidden else QLineEdit.EchoMode.Normal)
            self._show_error(selection.error or "")
            if selection.warning:
                self.error.setText((selection.error + "\n" if selection.error else "") + selection.warning)
                self.error.setVisible(True)
            state = (self.service.store.read(self.spec.scope, self.spec.name)
                     if self.match_result is None and not self.snapshot_managed else
                     self.match_result.windows_state if self.match_result is not None else None)
            last_values = self.service.state.snapshot().get("last_values", {})
            self.default_info.setText(_value(self.spec.default_value, hidden=self.hidden))
            last = last_values.get(self.spec.key)
            self.last_info.setText(_value(last, hidden=self.hidden) if self.spec.key in last_values else "尚未应用")
            if state is None:
                self.existing_info.setText(
                    "读取失败" if self.detection_error or self.match_result is not None
                    else "检测中")
                self.existing_type.setText("—")
            else:
                self.existing_info.setText(_value(state.value, state.exists, self.hidden))
                self.existing_type.setText(_registry_type(state))
            match_text, match_tone, match_tip = _variable_presentation(self.match_result, self.spec.special)
            if self.detection_error:
                match_text, match_tone, match_tip = "× 检测错误", "error", self.detection_error
            _set_match_label(self.match_label, match_text, match_tone, match_tip)
            detail, removal, removable = self._match_details()
            self.matching_info.setText(detail)
            self.matching_info.setToolTip(detail)
            self.removal_info.setText(removal)
            self.removal_info.setToolTip(removal)
            self.remove_button.setEnabled(self.remove_callback is not None and removable)
            self.default_info.setToolTip(self.default_info.text())
            self.last_info.setToolTip(self.last_info.text())
            self.existing_info.setToolTip(self.existing_info.text())
        except Exception as exc:
            self._show_error(str(exc))
        finally:
            self._updating = False


class FullValueDialog(QDialog):
    """Displays every character, including newlines, without changing it on view."""

    def __init__(self, value: str, editable: bool, sensitive: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("完整变量值")
        self.resize(690, 380)
        self.original = value
        self.editable = editable
        self.sensitive = sensitive
        self.visible_value = not sensitive
        self.escaped = any(char in value for char in ("\r", "\u2028", "\u2029"))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 22, 24, 22)
        outer.setSpacing(13)
        outer.addWidget(_label("完整变量值", "title"))
        instruction = ("原值含特殊换行符，使用 JSON 转义文本显示和编辑（\\r、\\n、\\u2028、\\u2029、\\\\），以保留原始字符。"
                       if self.escaped else "此窗口保留换行、空格和分号。编辑后需先预览并确认应用。")
        outer.addWidget(_label(instruction, "muted"))
        if sensitive:
            reveal = QCheckBox("显示敏感值")
            reveal.toggled.connect(self._reveal)
            outer.addWidget(reveal)
            self.reveal = reveal
        self.editor = QPlainTextEdit()
        self.editor.setObjectName("code")
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.editor.setPlainText(self._display(value) if self.visible_value else "••••••••")
        self.editor.setReadOnly(not editable or not self.visible_value)
        outer.addWidget(self.editor, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("使用此值" if editable else "关闭")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _display(self, value: str) -> str:
        if not self.escaped:
            return value
        return (json.dumps(value, ensure_ascii=False)[1:-1]
                .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))

    def _accept(self) -> None:
        try:
            self.value()
        except ValueError as exc:
            QMessageBox.warning(self, "转义文本无效", str(exc))
            return
        self.accept()

    def _reveal(self, checked: bool) -> None:
        if self.visible_value:
            try:
                self.original = self.value()
            except ValueError as exc:
                QMessageBox.warning(self, "转义文本无效", str(exc))
                self.reveal.blockSignals(True)
                self.reveal.setChecked(True)
                self.reveal.blockSignals(False)
                return
        self.visible_value = checked
        self.editor.setPlainText(self._display(self.original) if checked else "••••••••")
        self.editor.setReadOnly(not self.editable or not checked)

    def value(self) -> str:
        if not self.visible_value:
            return self.original
        displayed = self.editor.toPlainText()
        if not self.escaped:
            return displayed
        try:
            return json.loads('"' + displayed + '"')
        except json.JSONDecodeError as exc:
            raise ValueError(f"请检查反斜杠和换行转义：{exc.msg}") from exc


class OrderDialog(QDialog):
    def __init__(self, change: PlanChange, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"调整顺序 · {change.after.name} ({change.after.scope})")
        self.resize(660, 510)
        calculation = change.calculation
        self.initial = tuple(calculation.items) if calculation else ()
        self.added = {normalize_element(x) for x in (calculation.added if calculation else ())}
        self.moved = {normalize_element(x) for x in (calculation.moved if calculation else ())}
        self.sensitive = _sensitive(change.after.name)
        self.visible_values = not self.sensitive
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 22, 24, 22)
        outer.setSpacing(13)
        outer.addWidget(_label("调整列表顺序", "title"))
        outer.addWidget(_label(f"{change.after.name}  ·  {change.after.scope}", "subtitle"))
        outer.addWidget(_label("拖动列表项，或使用右侧按钮调整完整顺序。确认排序后才会进入执行预览。", "muted"))
        empty_count = self.initial.count("")
        whitespace_count = sum(bool(value) and not value.strip() for value in self.initial)
        notes = []
        if empty_count:
            notes.append(f"{empty_count} 个空项")
        if whitespace_count:
            notes.append(f"{whitespace_count} 个纯空白项")
        self.existing_notice = _label(
            "读取的原始值中含" + "、".join(notes) + "，已标明并保留。空段对应连续或首尾分号。"
            if notes else "", "muted"
        )
        self.existing_notice.setVisible(bool(notes))
        outer.addWidget(self.existing_notice)
        if self.sensitive:
            reveal = QCheckBox("显示敏感值")
            reveal.toggled.connect(self._reveal)
            outer.addWidget(reveal)
        row = QHBoxLayout()
        self.list = QListWidget()
        self.list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list.model().rowsMoved.connect(self._update_result)
        row.addWidget(self.list, 1)
        controls = QVBoxLayout()
        for title, fn in (("置顶", lambda: self._move_to(0)), ("上移", lambda: self._step(-1)),
                          ("下移", lambda: self._step(1)), ("置底", lambda: self._move_to(self.list.count() - 1)),
                          ("恢复初始", self._restore)):
            controls.addWidget(_button(title, fn))
        controls.addStretch()
        row.addLayout(controls)
        outer.addLayout(row, 1)
        self.result = _label("", "muted")
        self.result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self.result)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确认排序")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self._restore()

    def _item(self, value: str) -> QListWidgetItem:
        if not self.visible_values:
            shown = "••••••••"
        elif value == "":
            shown = "（原始值中的空段）"
        elif not value.strip():
            shown = f"（原始值中的纯空白项 · {len(value)} 个字符）"
        else:
            shown = value
        item = QListWidgetItem(shown + ("   · 新增" if normalize_element(value) in self.added else "   · 移动" if normalize_element(value) in self.moved else ""))
        item.setData(Qt.ItemDataRole.UserRole, value)
        if self.visible_values and not value.strip():
            item.setToolTip("读取时原始值中已存在，排序将保留原文。原始字符串：" + json.dumps(value, ensure_ascii=False))
        return item

    def _reveal(self, checked: bool) -> None:
        self.visible_values = checked
        for index in range(self.list.count()):
            item = self.list.item(index)
            value = item.data(Qt.ItemDataRole.UserRole)
            display = self._item(value)
            item.setText(display.text())
            item.setToolTip(display.toolTip())
        self._update_result()

    def _restore(self) -> None:
        self.list.clear()
        for value in self.initial:
            self.list.addItem(self._item(value))
        self._update_result()

    def _move_to(self, destination: int) -> None:
        index = self.list.currentRow()
        if index < 0 or destination < 0 or destination >= self.list.count():
            return
        item = self.list.takeItem(index)
        self.list.insertItem(destination, item)
        self.list.setCurrentRow(destination)
        self._update_result()

    def _step(self, amount: int) -> None:
        self._move_to(self.list.currentRow() + amount)

    def order(self) -> tuple[str, ...]:
        return tuple(self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count()))

    def _update_result(self, *_args) -> None:
        value = ";".join(self.order())
        self.result.setText("结果预览  ·  " + _value(value, hidden=not self.visible_values))


class SharedConflictDialog(QDialog):
    """Collect one explicit keep/remove decision for each real shared object."""

    def __init__(self, conflicts, ordinary_count: int = 0, parent=None):
        super().__init__(parent)
        self.conflicts = tuple(conflicts)
        self.decision_boxes: dict[str, QComboBox] = {}
        self.target_groups: dict[tuple[str, str], QFrame] = {}
        self._sensitive_rows = []
        self.setWindowTitle("共享环境变量冲突")
        self.resize(760, 560)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(10)
        outer.addWidget(_label("共享环境变量冲突", "title"))
        outer.addWidget(_label(
            f"发现 {len(self.conflicts)} 个共享对象。默认保留；其他 {ordinary_count} 个无共享对象仍按原计划撤销。",
            "muted"))
        tools = QHBoxLayout()
        self.show_sensitive = QCheckBox("显示敏感值")
        self.show_sensitive.setVisible(any(_sensitive(conflict.atom.target[1])
                                          for conflict in self.conflicts))
        self.show_sensitive.toggled.connect(self._refresh_sensitive_values)
        tools.addWidget(self.show_sensitive)
        tools.addStretch(1)
        tools.addWidget(_button("全部保留共享项", lambda: self._set_all(ConflictDecision.KEEP), "quiet"))
        remove_all = _button("全部撤销共享项", lambda: self._set_all(ConflictDecision.REMOVE), "danger")
        remove_all.setToolTip("仅改变本窗口中的共享冲突对象")
        tools.addWidget(remove_all)
        outer.addLayout(tools)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        rows = QVBoxLayout(body)
        rows.setContentsMargins(2, 2, 2, 2)
        rows.setSpacing(8)
        rows.setAlignment(Qt.AlignmentFlag.AlignTop)
        for conflict in self.conflicts:
            atom = conflict.atom
            target = f"{atom.target[0]} / {atom.target[1]}"
            group_key = (atom.target[0], match_key(atom.target[1]))
            if group_key not in self.target_groups:
                group = QFrame()
                group.setObjectName("conflictItem")
                grouped = QVBoxLayout(group)
                grouped.setContentsMargins(12, 10, 12, 10)
                grouped.setSpacing(8)
                kind = "特殊完整值" if atom.kind is RemovalKind.WHOLE_KEY else "普通变量"
                group_title = ElidingLabel(f"{target} · {kind}", "name")
                group_title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                grouped.addWidget(group_title)
                self.target_groups[group_key] = group
                rows.addWidget(group)
            grouped = self.target_groups[group_key].layout()
            sources = "；".join(dict.fromkeys(f"{source.app} / {source.option}"
                                             for source in atom.sources))
            consumers = "；".join(dict.fromkeys(f"{consumer.app} / {consumer.option}"
                                                for consumer in conflict.consumers))
            item = QWidget()
            item.setObjectName("conflictRow")
            layout = QVBoxLayout(item)
            layout.setContentsMargins(0, 4, 0, 4)
            layout.setSpacing(5)
            heading = QHBoxLayout()
            kind = ("特殊完整值" if atom.kind is RemovalKind.WHOLE_KEY else
                    "存在且为空的状态" if atom.kind is RemovalKind.EMPTY_VALUE else "元素")
            title = ElidingLabel(kind, "muted")
            title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            heading.addWidget(title, 1)
            decision = QComboBox()
            decision.setObjectName("conflictDecision")
            decision.addItem("保留完整值" if atom.kind is RemovalKind.WHOLE_KEY else "保留", ConflictDecision.KEEP.value)
            decision.addItem("撤销整键" if atom.kind is RemovalKind.WHOLE_KEY else "撤销", ConflictDecision.REMOVE.value)
            decision.setCurrentIndex(0)
            sensitive = _sensitive(atom.target[1])
            shown_value = _value(atom.display_value, hidden=sensitive)
            decision.setAccessibleName(f"{target} {shown_value} 的共享冲突决定")
            decision.setAccessibleDescription(f"共享方：{consumers}；当前决定：保留")
            decision.setToolTip("保留不会移除此对象；撤销会影响下列共享方案")
            decision.currentIndexChanged.connect(self._update_summary)
            decision.currentTextChanged.connect(
                lambda text, box=decision, shared=consumers:
                box.setAccessibleDescription(f"共享方：{shared}；当前决定：{text}"))
            self.decision_boxes[atom.atom_id] = decision
            heading.addWidget(decision)
            layout.addLayout(heading)
            value_prefix = ("完整原始值：" if atom.kind is RemovalKind.WHOLE_KEY else
                            "空值状态：" if atom.kind is RemovalKind.EMPTY_VALUE else "元素：")
            value = _label(value_prefix + shown_value, "code")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(value)
            layout.addWidget(_label("撤销来源：" + sources, "meta"))
            layout.addWidget(_label("共享方：" + consumers, "meta"))
            risks = "；".join(dict.fromkeys(consumer.reason for consumer in conflict.consumers))
            if atom.occurrence_count > 1:
                risks += ("；" if risks else "") + f"Windows 中出现 {atom.occurrence_count} 次，撤销会移除全部匹配副本"
            risk = _label("影响：" + ("撤销会影响上述共享方案的已匹配配置" if sensitive else risks), "error")
            risk.setVisible(bool(risks))
            layout.addWidget(risk)
            if sensitive:
                self._sensitive_rows.append((value, value_prefix, atom.display_value,
                                             risk, risks, decision, target))
            grouped.addWidget(item)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        self.summary = _label("", "selectionSummary")
        outer.addWidget(self.summary)
        outer.addWidget(_label("无共享项不会显示在此窗口，但仍会进入最终完整预览。", "muted"))
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("继续预览")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setAutoDefault(False)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消整个操作")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        outer.addWidget(self.buttons)
        self._update_summary()

    def _refresh_sensitive_values(self, *_args) -> None:
        hidden = not self.show_sensitive.isChecked()
        for label, prefix, raw, risk, explanation, decision, target in self._sensitive_rows:
            shown = _value(raw, hidden=hidden)
            label.setText(prefix + shown)
            risk.setText("影响：" + ("撤销会影响上述共享方案的已匹配配置" if hidden else explanation))
            decision.setAccessibleName(f"{target} {shown} 的共享冲突决定")

    def _set_all(self, decision: ConflictDecision) -> None:
        for box in self.decision_boxes.values():
            box.setCurrentIndex(box.findData(decision.value))
        self._update_summary()

    def _update_summary(self, *_args) -> None:
        kept = sum(box.currentData() == ConflictDecision.KEEP.value
                   for box in self.decision_boxes.values())
        removed = len(self.decision_boxes) - kept
        self.summary.setText(f"共享项目：保留 {kept} · 选择撤销 {removed}")

    def decisions(self) -> dict[str, str]:
        return {atom_id: box.currentData() for atom_id, box in self.decision_boxes.items()}


class CleanupSettingsDialog(QDialog):
    """Keep write-cleanup controls together and folded until deliberately opened."""

    def __init__(self, settings: CleanupSettings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("高级设置")
        self.resize(480, 230)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(12)
        outer.addWidget(_label("高级设置", "title"))
        outer.addWidget(_label("普通值整理只作用于本次执行涉及的目标，实际结果在最终预览中确认。", "muted"))
        self.advanced_toggle = QPushButton("普通值写入自动整理")
        self.advanced_toggle.setObjectName("quiet")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setIcon(icon("chevron_right"))
        outer.addWidget(self.advanced_toggle)
        self.cleanup_options = QWidget()
        options = QVBoxLayout(self.cleanup_options)
        options.setContentsMargins(8, 0, 8, 0)
        options.setSpacing(8)
        self.cleanup_checks = {}
        for name, title in (("trim_whitespace", "清理元素首尾的空格和制表符"),
                            ("remove_empty", "清理无效空元素"),
                            ("deduplicate", "按大小写敏感规则去重")):
            checkbox = QCheckBox(title)
            checkbox.setChecked(getattr(settings, name))
            self.cleanup_checks[name] = checkbox
            options.addWidget(checkbox)
        options.addWidget(_label("检测始终使用统一元素规则。特殊完整值、历史恢复和失败回滚不进行整理。", "meta"))
        outer.addWidget(self.cleanup_options)
        self.cleanup_options.hide()
        self.advanced_toggle.toggled.connect(self.cleanup_options.setVisible)
        self.advanced_toggle.toggled.connect(lambda expanded: self.advanced_toggle.setIcon(
            icon("chevron_down" if expanded else "chevron_right")))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存设置")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def settings(self) -> CleanupSettings:
        return CleanupSettings(**{name: box.isChecked() for name, box in self.cleanup_checks.items()})


class OldSchemeDialog(QDialog):
    """Make ambiguous or partial old-scheme removal an explicit user choice."""

    def __init__(self, app: str, options, *, required: bool, parent=None):
        super().__init__(parent)
        self.required = required
        self.setWindowTitle("选择待撤销旧方案")
        self.resize(520, 390)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(10)
        outer.addWidget(_label("选择待撤销旧方案", "title"))
        explanation = ("检测到多个完全匹配方案，请明确选择本次作为旧方案撤销的范围。"
                       if required else
                       "检测到部分匹配方案。仅勾选后才会撤销其中实际匹配的内容；不勾选可只应用新方案。")
        outer.addWidget(_label(f"{app}：{explanation}", "muted"))
        self.list = QListWidget()
        self.list.setAccessibleName(f"{app} 待撤销旧方案候选")
        for option, status in options:
            text = f"{option}  ·  {'完全匹配' if status == 'complete' else '部分匹配'}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, option)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.list.addItem(item)
        self.list.itemChanged.connect(self._update_accept)
        outer.addWidget(self.list, 1)
        self.validation = _label("", "error")
        outer.addWidget(self.validation)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("继续")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        outer.addWidget(self.buttons)
        self._update_accept()

    def selected_options(self) -> tuple[str, ...]:
        return tuple(self.list.item(index).data(Qt.ItemDataRole.UserRole)
                     for index in range(self.list.count())
                     if self.list.item(index).checkState() == Qt.CheckState.Checked)

    def _update_accept(self, *_args) -> None:
        valid = bool(self.selected_options()) or not self.required
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(valid)
        self.validation.setText("请至少选择一个旧方案。" if self.required and not valid else "")


class PreviewDialog(QDialog):
    def __init__(self, plan: ExecutionPlan, parent=None):
        super().__init__(parent)
        self.plan = plan
        self.setWindowTitle("确认变更预览")
        self.resize(760, 610)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 22, 24, 22)
        outer.setSpacing(12)
        outer.addWidget(_label({"apply": "应用方案", "batch": "应用所选方案", "batch_apply": "应用所选方案", "switch": "切换方案", "remove": "撤销环境变量", "deactivate": "停用方案",
                                "restore": "历史恢复", "recover": "未完成操作恢复"}.get(plan.action, plan.action), "title"))
        outer.addWidget(_label(_applications_text(getattr(plan, "applications", ()), plan.app, plan.option)
                               + f"   ·   {len(plan.changes)} 项变量", "muted"))
        metadata = getattr(plan, "metadata", {}) or {}
        request = metadata.get("request", {})
        scope = request.get("scope")
        if scope:
            scope_text = {
                "variable": "单变量", "scheme": "单方案", "application_all": "应用全部方案",
                "batch_current": "批量 A · 当前方案", "batch_all": "批量 B · 所有方案",
                "scheme_switch": "方案切换", "batch_scheme_switch": "勾选应用统一切换",
            }.get(scope, scope)
            outer.addWidget(_label("操作范围：" + scope_text, "selectionSummary"))
        metrics = metadata.get("metrics", {})
        if metrics:
            parts = [f"预计修改 {sum(change.changed for change in plan.changes)} 个注册表目标"]
            removed_key_count = metrics.get(
                "removed_key_count", metrics.get("removed_whole_key_count", 0))
            if removed_key_count:
                parts.append(f"删除 {removed_key_count} 个完整变量")
            if metrics.get("removed_element_count"):
                parts.append(f"移除 {metrics['removed_element_count']} 个列表元素/副本")
            if metrics.get("kept_shared_count"):
                parts.append(f"保留 {metrics['kept_shared_count']} 个共享对象")
            if metrics.get("skipped_count"):
                parts.append(f"跳过 {metrics['skipped_count']} 项")
            outer.addWidget(_label(" · ".join(parts), "muted"))
        self.show_sensitive = QCheckBox("显示敏感值")
        self.show_sensitive.toggled.connect(self._populate)
        outer.addWidget(self.show_sensitive)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        self.changes_layout = QVBoxLayout(body)
        self.changes_layout.setContentsMargins(2, 2, 2, 2)
        self.changes_layout.setSpacing(10)
        self.changes_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        if plan.warnings:
            outer.addWidget(_label("提示：" + "；".join(plan.warnings), "muted"))
        if plan.conflicts:
            outer.addWidget(_label("冲突：" + "；".join(plan.conflicts) + "。请取消并重新预览。", "error"))
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "确认执行撤销" if plan.action == "remove" else
            "确认切换" if plan.action == "switch" else "确认执行")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(plan.can_execute)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        outer.addWidget(self.buttons)
        self._populate()

    @staticmethod
    def _summary_panel(title: str):
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(5)
        layout.addWidget(_label(title, "section"))
        return panel, layout

    def _populate_metadata(self) -> None:
        metadata = getattr(self.plan, "metadata", {}) or {}
        atoms = {atom.get("atom_id"): atom for atom in metadata.get("atoms", ())}

        if self.plan.action == "switch":
            panel, layout = self._summary_panel("方案切换范围")
            switches = metadata.get("switches") or [{
                "app": self.plan.app,
                "old_options": metadata.get("old_options", ()),
                "new_option": metadata.get("new_option", self.plan.option),
            }]
            for switch in switches:
                app = switch["app"]
                old_options = switch.get("old_options", ())
                new_option = switch["new_option"]
                before = "、".join(f"{app} / {option}" for option in old_options)
                layout.addWidget(_label(
                    f"切换：{before}  →  {app} / {new_option}"
                    if before else f"应用：{app} / {new_option}（未选择旧方案撤销）",
                    "selectionSummary"))
            removed = tuple(metadata.get("removed_atoms", ()))
            removed_set = set(removed)
            for heading, identifiers, tone in (
                ("旧方案实际撤销", removed, "error"),
                ("共同需要或按决定保留",
                 tuple(identifier for identifier in atoms if identifier not in removed_set), "muted"),
            ):
                values = []
                for identifier in identifiers:
                    if identifier not in atoms:
                        continue
                    atom = atoms[identifier]
                    target = atom.get("target", ("", ""))
                    hidden = _sensitive(target[1]) and not self.show_sensitive.isChecked()
                    values.append(_value(atom.get("display_value", ""), hidden=hidden))
                layout.addWidget(_label(
                    f"{heading}：" + ("；".join(values) if values else "无"), tone))
            self.changes_layout.addWidget(panel)

        protected = metadata.get("protected_applications", ())
        if protected:
            panel, layout = self._summary_panel("共享保护范围")
            labels = [f"{item['app']}（{'、'.join(item.get('options', ())) or '无方案'}）"
                      for item in protected]
            layout.addWidget(_label("未勾选应用的所有方案：" + "；".join(labels), "meta"))
            self.changes_layout.addWidget(panel)

        shared = metadata.get("shared_conflicts", ())
        if shared:
            panel, layout = self._summary_panel("共享对象决定")
            for item in shared:
                atom = atoms.get(item.get("atom_id"), {})
                target = atom.get("target", ("", ""))
                name = target[1] if len(target) > 1 else ""
                hidden = _sensitive(name) and not self.show_sensitive.isChecked()
                value = _value(atom.get("display_value", ""), hidden=hidden)
                decision = "保留" if item.get("decision") == "keep" else "撤销"
                consumers = "；".join(dict.fromkeys(
                    f"{consumer['app']} / {consumer['option']}"
                    for consumer in item.get("consumers", ())))
                scope = target[0] if target else ""
                summary = f"{decision} · {scope} / {name} · {value}"
                if consumers:
                    summary += " · 共享方：" + consumers
                layout.addWidget(_label(summary,
                                        "muted" if decision == "保留" else "error"))
            self.changes_layout.addWidget(panel)

        skipped = metadata.get("skipped_items", ())
        if skipped:
            panel, layout = self._summary_panel("未修改与跳过")
            reason_text = {
                "key_missing": "Windows 中变量不存在",
                "value_mismatch": "Windows 当前值与方案不匹配",
                "element_missing": "Windows 列表中没有该元素",
                "empty_input": "方案输入为空，没有可撤销元素",
            }
            for item in skipped:
                source = item.get("source", {})
                target = item.get("target", ("", ""))
                origin = f"{source.get('app', '')} / {source.get('option', '')}"
                name = target[1] if len(target) > 1 else ""
                hidden = _sensitive(name) and not self.show_sensitive.isChecked()
                value = _value(item.get("display_value", ""), hidden=hidden)
                layout.addWidget(_label(
                    f"{origin} · {target[0]} / {name} · {value}："
                    f"{reason_text.get(item.get('reason'), item.get('reason', '未修改'))}",
                    "meta"))
            self.changes_layout.addWidget(panel)

    def _populate(self) -> None:
        _clear(self.changes_layout)
        self._populate_metadata()
        for change in self.plan.changes:
            box = QFrame()
            box.setObjectName("card")
            layout = QVBoxLayout(box)
            layout.setContentsMargins(18, 15, 18, 16)
            layout.setSpacing(11)

            heading = QHBoxLayout()
            heading.setSpacing(7)
            name = _label(change.before.name, "name")
            name.setWordWrap(False)
            name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            name.setToolTip(change.before.name)
            heading.addWidget(name, 1)
            for text, tone, original in (
                ("用户" if change.before.scope == "User" else "系统", "neutral", change.before.scope),
                ({"replace": "替换", "+append": "前置", "-append": "后置", "append": "自定义排序", "batch_append": "组合追加", "remove": "撤销"}.get(change.mode, change.mode), "accent", change.mode),
            ):
                badge = _label(text, "badge")
                badge.setProperty("tone", tone)
                badge.setWordWrap(False)
                badge.setToolTip(original)
                heading.addWidget(badge)
            if not change.changed:
                unchanged = _label("无需写入", "badge")
                unchanged.setProperty("tone", "warm")
                unchanged.setWordWrap(False)
                heading.addWidget(unchanged)
            layout.addLayout(heading)

            hidden = _sensitive(change.before.name) and not self.show_sensitive.isChecked()
            contributions = getattr(change, "contributions", ())
            if contributions:
                layout.addWidget(_label(
                    "撤销对象与共享决定" if change.mode == "remove" else "本次选定输入", "muted"))
                for contribution in contributions:
                    if contribution.get("kind") == "cleanup":
                        before_cleanup = _value(contribution.get("before_value", ""), hidden=hidden)
                        after_cleanup = _value(contribution.get("after_value", ""), hidden=hidden)
                        layout.addWidget(_label("自动整理：" + before_cleanup + " → " + after_cleanup, "muted"))
                        continue
                    if "sources" in contribution:
                        decision = contribution.get("decision", "remove")
                        kind = ("特殊完整值" if contribution.get("kind") == "whole_key" else
                                "唯一空值状态" if contribution.get("kind") == "empty_value" else "元素")
                        action = "保留" if decision == "keep" else "撤销"
                        display = _value(contribution.get("display_value", ""), hidden=hidden)
                        item = _label(f"{action} {kind}：{display}",
                                      "muted" if decision == "keep" else "error")
                        item.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                        layout.addWidget(item)
                        sources = "；".join(dict.fromkeys(
                            f"{source['app']} / {source['option']}"
                            for source in contribution.get("sources", ())))
                        if sources:
                            layout.addWidget(_label("撤销来源：" + sources, "meta"))
                        consumers = "；".join(dict.fromkeys(
                            f"{consumer['app']} / {consumer['option']}"
                            for consumer in contribution.get("consumers", ())))
                        if consumers:
                            layout.addWidget(_label("共享影响：" + consumers, "error"))
                        count = contribution.get("occurrence_count", 1)
                        if count > 1:
                            layout.addWidget(_label(f"Windows 中有 {count} 个匹配副本", "muted"))
                        continue
                    mode = {"replace": "替换", "+append": "前置", "-append": "后置", "append": "自定义排序", "remove": "撤销"}.get(contribution["mode"], contribution["mode"])
                    origin = _label(f"{contribution['app']} / {contribution['option']}  ·  {mode}", "meta")
                    origin.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                    origin.setToolTip(origin.text())
                    layout.addWidget(origin)
                    raw = _value(contribution["selected_value"], hidden=hidden)
                    input_value = _label(raw)
                    input_value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                    input_value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                    input_value.setToolTip(raw)
                    layout.addWidget(input_value)
            values = QGridLayout()
            values.setContentsMargins(0, 0, 0, 0)
            values.setHorizontalSpacing(12)
            values.setVerticalSpacing(8)
            values.setColumnMinimumWidth(0, 106)
            values.setColumnStretch(1, 1)

            for row, title, raw, kind, color in (
                (0, "本次选定输入", _value(change.selected_value, hidden=hidden), "", ""),
                (1, "Windows 原始状态", _value(change.before.value, change.before.exists, hidden), _registry_type(change.before), ""),
                (3, "预计写入结果", _value(change.after.value, change.after.exists, hidden), _registry_type(change.after), "good"),
            ):
                if row == 0 and contributions:
                    continue
                caption = _label(title, color or "muted")
                caption.setWordWrap(False)
                values.addWidget(caption, row, 0, Qt.AlignmentFlag.AlignTop)
                value = _label(raw, color or "")
                value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                value.setToolTip(raw)
                values.addWidget(value, row, 1)
                if kind:
                    type_label = _label(kind, "meta")
                    type_label.setWordWrap(False)
                    values.addWidget(type_label, row, 2, Qt.AlignmentFlag.AlignTop)
            divider = QFrame()
            divider.setFrameShape(QFrame.Shape.HLine)
            divider.setObjectName("divider")
            values.addWidget(divider, 2, 0, 1, 3)
            layout.addLayout(values)
            if change.before.exists and not change.after.exists:
                layout.addWidget(_label("将删除整个环境变量键", "error"))
                if change.before.scope == "System":
                    layout.addWidget(_label("系统作用域删除需要管理员权限，并可能影响其他用户和系统程序。", "error"))
                if match_key(change.before.name) in {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP"}:
                    layout.addWidget(_label("将删除关键环境变量；请检查依赖此变量的程序和系统功能。", "error"))
            elif self.plan.action in {"remove", "switch"} and change.before.exists:
                if self.plan.action == "switch" and change.mode == "replace":
                    if change.changed:
                        layout.addWidget(_label("将替换完整变量值", "muted"))
                else:
                    after_keys = set(effective_elements(change.after.value))
                    removed = []
                    seen = set()
                    for item in change.before.value.split(";"):
                        key = normalize_element(item)
                        if key and key not in after_keys and key not in seen:
                            seen.add(key)
                            removed.append(item)
                    if removed:
                        removed_text = "；".join(_value(item, hidden=hidden) for item in removed)
                        layout.addWidget(_label("撤销列表元素：" + removed_text, "error"))
            calc = change.calculation
            if calc:
                effects = []
                if calc.added:
                    effects.append("新增 " + str(len(calc.added)))
                if calc.moved:
                    effects.append("移动 " + str(len(calc.moved)))
                if calc.deduplicated:
                    effects.append("匹配去重 " + str(len(calc.deduplicated)))
                if effects:
                    layout.addWidget(_label("列表变化  ·  " + "，".join(effects), "muted"))
                if calc.warnings:
                    layout.addWidget(_label("提示  ·  " + "；".join(calc.warnings), "muted"))
            self.changes_layout.addWidget(box)
        if not self.plan.changes:
            self.changes_layout.addWidget(_label("本次没有变量变更。", "muted"))


class HistoryDialog(QDialog):
    def __init__(self, service, preview_callback, parent=None):
        super().__init__(parent)
        self.service = service
        self.preview_callback = preview_callback
        self.setWindowTitle("操作历史")
        self.resize(680, 490)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 22, 24, 22)
        outer.setSpacing(12)
        outer.addWidget(_label("操作历史", "title"))
        outer.addWidget(_label("历史恢复会先生成预览，并检查后续变更。", "muted"))
        self.list = QListWidget()
        outer.addWidget(self.list, 1)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(_button("关闭", self.accept))
        row.addWidget(_button("预览恢复", self._restore, "primary"))
        outer.addLayout(row)
        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        for entry in self.service.history():
            applications = _applications_text(entry.get("applications"), entry.get("app", ""), entry.get("option", ""))
            label = f"{_local_time(entry.get('created_at', ''))}  ·  {applications}  ·  {_status_text(entry.get('status', ''))}"
            item = QListWidgetItem(label + ("\n" + entry.get("message", "") if entry.get("message") else ""))
            item.setToolTip(item.text())
            item.setData(Qt.ItemDataRole.UserRole, entry["id"])
            self.list.addItem(item)

    def _restore(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        self.preview_callback(lambda: self.service.plan_restore(item.data(Qt.ItemDataRole.UserRole)))
        self.refresh()


class ManifestDelegate(QStyledItemDelegate):
    """Retain native item selection/Space handling, draw one visible checkbox."""

    def paint(self, painter, option, index) -> None:
        clean = QStyleOptionViewItem(option)
        self.initStyleOption(clean, index)
        clean.features &= ~QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
        clean.text = ""
        style = clean.widget.style() if clean.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, clean, painter, clean.widget)

    def editorEvent(self, event, model, option, index) -> bool:
        # The visible QCheckBox owns pointer input. There is no hidden second hit area.
        return False


class ApplicationList(QListWidget):
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.doItemsLayout()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space and self.currentItem() is not None:
            item = self.currentItem()
            item.setCheckState(Qt.CheckState.Unchecked if item.checkState() == Qt.CheckState.Checked
                               else Qt.CheckState.Checked)
            event.accept()
            return
        super().keyPressEvent(event)


class ApplicationRow(QFrame):
    """A batch entry: selection, detail navigation and option are separate actions."""

    def __init__(self, window, item, app: str):
        super().__init__()
        self.setObjectName("manifestRow")
        self.item = item
        self.app_name = app
        self.window = window
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(6)
        self.check = QCheckBox()
        self.check.setAccessibleName(f"批量选择 {app}")
        self.check.setChecked(item.checkState() == Qt.CheckState.Checked)
        self.check.toggled.connect(lambda checked: item.setCheckState(
            Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked))
        layout.addWidget(self.check)
        mark = QLabel()
        mark.setPixmap(app_icon(app, 22).pixmap(22, 22))
        mark.setFixedSize(22, 22)
        mark.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(mark)
        body = QVBoxLayout()
        body.setSpacing(0)
        top = QHBoxLayout()
        top.setSpacing(4)
        self.name_button = ApplicationNameButton(app, lambda: window._navigate_app(app))
        self.name_button.setProperty("quiet", True)
        self.name_button.setToolTip(f"查看 {app} 的变量")
        self.name_button.setAccessibleName(f"查看 {app} 详情")
        self.name_button.setMinimumWidth(0)
        self.name_button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        top.addWidget(self.name_button, 1)
        self.match_label = _label("", "matchStatus")
        self.match_label.setWordWrap(False)
        self.match_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        top.addWidget(self.match_label)
        body.addLayout(top)
        status = QHBoxLayout()
        status.setSpacing(4)
        self.option_combo = OptionComboBox(compact=True)
        self.option_combo.setObjectName("manifestOption")
        self.option_combo.setAccessibleName(f"{app} 批量方案")
        self.option_combo.setMinimumWidth(80)
        self.option_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.option_combo.setFixedHeight(self.option_combo.minimumSizeHint().height())
        self.option_combo.addItems(window.service.config.options(app))
        window._update_option_combo(self.option_combo, app)
        self.option_combo.setCurrentText(window._selected_option(app))
        self.option_combo.currentTextChanged.connect(lambda option: window._manifest_option_changed(app, option))
        status.addWidget(self.option_combo)
        self.activation_badge = _label("", "activationBadge")
        self.activation_badge.setWordWrap(False)
        self.activation_badge.setParent(self)
        self.activation_badge.hide()
        self.meta = ElidingLabel("", "manifestMeta")
        self.meta.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.meta.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        status.addWidget(self.meta)
        body.addLayout(status)
        layout.addLayout(body, 1)
        self.refresh(window)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.window._navigate_app(self.app_name)
            event.accept()
            return
        super().mousePressEvent(event)

    def refresh(self, window) -> None:
        self.check.blockSignals(True)
        self.check.setChecked(self.item.checkState() == Qt.CheckState.Checked)
        self.check.blockSignals(False)
        selected = window._selected_option(self.app_name)
        window._update_option_combo(self.option_combo, self.app_name)
        self.option_combo.blockSignals(True)
        self.option_combo.setCurrentText(selected)
        self.option_combo.blockSignals(False)
        self.option_combo._current_tooltip()
        activation = window.service.state.snapshot().get("active", {}).get(self.app_name)
        active = activation.get("option") if isinstance(activation, dict) else activation
        specs = window.service.config.variables(self.app_name, selected)
        count = len(specs)
        invalid = sum(bool(window.service.selections.get(spec).error) for spec in specs)
        match_text, match_tone, progress, match_tip = window._scheme_ui(self.app_name, selected)
        _set_match_label(self.match_label, "错误" if match_tone == "error" else progress or "…", match_tone,
                         f"{match_text} {progress}\n{match_tip}".strip())
        _activation_badge(self.activation_badge, active)
        status = f"{invalid} 处输入无效 · " if invalid else ""
        status += f"当前激活 {active} · " if active else "尚未激活 · "
        self.meta.setText("输入无效" if invalid else {"complete": "已匹配", "partial": "需配置", "unconfigured": "需配置", "error": "检测错误", "scanning": "未读取"}.get(match_tone, "未读取"))
        self.meta.setToolTip(status + f"{count} 项变量；准备方案：{selected}")
        self.meta.setVisible(bool(invalid))
        self.meta.setObjectName("error" if invalid else "manifestMeta")
        self.meta.style().unpolish(self.meta)
        self.meta.style().polish(self.meta)
        self.setProperty("active", self.app_name == window.app_name)
        self.style().unpolish(self)
        self.style().polish(self)


class MainWindow(QMainWindow):
    def __init__(self, service, config_path: Path, demo: bool = False):
        super().__init__()
        self.service = service
        self.config_path = Path(config_path)
        self.demo = demo
        self.app_name = ""
        self.checked_apps: set[str] = set()
        self._option_drafts: dict[str, str] = {}
        self.cards: list[VariableCard] = []
        self._invalid_specs: list[VariableSpec] = []
        self._detection = None
        self._detection_error = ""
        self._scanning = False
        self._scan_token = 0
        self._scan_workers = {}
        self._thread_pool = QThreadPool(self)
        self.setWindowTitle("环境变量方案管理器" + (" · 演示模式" if demo else ""))
        self.resize(1366, 768)
        self.setMinimumSize(850, 560)
        self._startup_geometry_fitted = False
        apply_theme(QApplication.instance())

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_toolbar())
        self.workspace_stack = QStackedWidget()
        self.workbench_page = QWidget()
        workspace = QHBoxLayout(self.workbench_page)
        workspace.setContentsMargins(0, 0, 0, 0)
        workspace.setSpacing(0)
        self.workspace_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.workspace_splitter.setChildrenCollapsible(False)
        self.manifest_panel = self._build_manifest()
        self.workspace_splitter.addWidget(self.manifest_panel)
        self.workspace_splitter.addWidget(self._build_inspector())
        self.workspace_splitter.setSizes([260, 1100])
        workspace.addWidget(self.workspace_splitter)
        self.workspace_stack.addWidget(self.workbench_page)
        self.windows_page = WindowsEnvironmentPage(self._open_system_settings, self._show_windows_value)
        self.workspace_stack.addWidget(self.windows_page)
        self.history_page = HistoryPage(service, self._preview, _local_time, _applications_text, _status_text)
        self.workspace_stack.addWidget(self.history_page)
        root.addWidget(self.workspace_stack, 1)
        self.footer_panel = self._build_footer()
        root.addWidget(self.footer_panel)
        self._scan_detection(force=True)
        self._render_apps()
        apps = service.config.apps()
        if apps:
            self._select_app(apps[0])
        self._refresh_pending()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if (not self._startup_geometry_fitted
                and QApplication.instance().platformName() not in {"offscreen", "minimal"}):
            self._startup_geometry_fitted = True
            # The native frame margins are available after the first show.
            QTimer.singleShot(0, self._fit_startup_geometry)

    def _fit_startup_geometry(self, available=None) -> None:
        if available is None:
            screen = self.screen()
            if screen is None:
                return
            available = screen.availableGeometry()
        area = available.adjusted(8, 8, -8, -8)
        frame = self.frameGeometry()
        extra_width = max(0, frame.width() - self.width())
        extra_height = max(0, frame.height() - self.height())
        width_limit = max(1, area.width() - extra_width)
        height_limit = max(1, area.height() - extra_height)
        self.setMinimumSize(min(self.minimumWidth(), width_limit),
                            min(self.minimumHeight(), height_limit))
        self.resize(min(self.width(), width_limit), min(self.height(), height_limit))
        frame = self.frameGeometry()
        self.move(area.left() + (area.width() - frame.width()) // 2,
                  area.top() + (area.height() - frame.height()) // 2)

    def _scan_detection(self, *, force: bool) -> None:
        if not hasattr(self.service, "detection_snapshot"):
            self._detection = None
            self._detection_error = ""
            return
        if force and not self.demo and hasattr(self.service, "prepare_detection_scan"):
            self._begin_detection_scan()
            return
        if self._scanning:
            return
        try:
            self._detection = self.service.detection_snapshot(force=force)
            self._detection_error = ""
        except Exception as exc:
            self._detection = None
            self._detection_error = str(exc)
            if hasattr(self, "feedback"):
                self.feedback.setText("检测失败：" + str(exc))

    def _begin_detection_scan(self) -> None:
        if self._scanning:
            return
        try:
            request = self.service.prepare_detection_scan()
        except Exception as exc:
            self._detection = None
            self._detection_error = str(exc)
            self.feedback.setText("检测失败：" + str(exc))
            self._refresh_detection_views()
            return
        self._scan_token += 1
        token = self._scan_token
        self._scanning = True
        self._detection = None
        self._detection_error = ""
        self.scan_progress.setVisible(True)
        self.feedback.setText("正在只读检测所有应用、方案与变量…")
        self._update_scan_controls()
        self._refresh_detection_views()
        worker = _DetectionWorker(
            token, lambda: self.service.run_detection_scan(request))
        self._scan_workers[token] = worker
        worker.signals.finished.connect(self._finish_detection_scan)
        self._thread_pool.start(worker)

    def _finish_detection_scan(self, token: int, snapshot, error: str) -> None:
        self._scan_workers.pop(token, None)
        if token != self._scan_token:
            return
        try:
            if error:
                raise RuntimeError(error)
            self._detection = self.service.accept_detection_scan(snapshot)
            self._detection_error = ""
        except Exception as exc:
            self._detection = None
            self._detection_error = str(exc)
        self._scanning = False
        self.scan_progress.setVisible(False)
        self._update_scan_controls()
        self._refresh_detection_views()
        if self._detection_error:
            self.feedback.setText("检测失败：" + self._detection_error + "；可点击刷新重试。")
        else:
            schemes = len(self._detection.schemes) if self._detection else 0
            errors = len(self._detection.errors) if self._detection else 0
            self.feedback.setText(
                f"检测完成：{schemes} 个方案" + (f" · {errors} 项异常" if errors else " · 无异常"))

    def _update_scan_controls(self) -> None:
        for name in ("history_button", "refresh_button", "config_button", "pending_button"):
            button = getattr(self, name, None)
            if button is not None:
                button.setEnabled(not self._scanning)
        if hasattr(self, "apply_button"):
            self._update_apply_availability()

    def _refresh_detection_views(self) -> None:
        if not hasattr(self, "app_list"):
            return
        self._refresh_manifest_rows()
        for card in self.cards:
            card.match_result = self._variable_match(card.spec)
            card.detection_error = self._detection_error
            card.refresh(keep_cursor=card.editor.hasFocus())
        if self.app_name and self.option_combo.currentText():
            option = self.option_combo.currentText()
            self._update_option_combo(self.option_combo, self.app_name)
            text, tone, progress, tip = self._scheme_ui(self.app_name, option)
            _set_match_label(self.match_status, f"{text} {progress}".strip(), tone, tip)
        self._update_apply_availability()
        self._filter_variable_rows()
        self._refresh_active_app_filter()
        self._refresh_filter_buttons()
        self._refresh_comparison()

    def _scheme_match(self, app: str, option: str):
        if self._detection is None:
            return None
        return self._detection.schemes.get((app, option))

    def _scheme_ui(self, app: str, option: str) -> tuple[str, str, str, str]:
        if self._detection_error:
            return "× 检测错误", "error", "", self._detection_error
        return _scheme_presentation(self._scheme_match(app, option))

    def _variable_match(self, spec: VariableSpec):
        scheme = self._scheme_match(spec.app, spec.option)
        if scheme is None:
            return None
        return next((result for result in scheme.variables if result.spec_key == spec.key), None)

    def _update_option_combo(self, combo: OptionComboBox, app: str) -> None:
        default = (self.service.config.default_option(app)
                   if hasattr(self.service.config, "default_option") else None)
        for index in range(combo.count()):
            option = combo.itemText(index)
            activation = self.service.state.snapshot().get("active", {}).get(app)
            active = activation.get("option") if isinstance(activation, dict) else activation
            combo.setOptionStatus(index, self._scheme_match(app, option), default=option == default,
                                  active=option == active, presentation=self._scheme_ui(app, option))
        combo.view().update()
        combo.updateGeometry()

    def _build_toolbar(self) -> QFrame:
        toolbar = QFrame()
        toolbar.setObjectName("workspaceHeader")
        bar = QHBoxLayout(toolbar)
        bar.setContentsMargins(12, 8, 12, 8)
        bar.setSpacing(8)
        brand = _label("环境方案", "appTitle")
        brand.setWordWrap(False)
        bar.addWidget(brand)
        self.breadcrumb = ElidingLabel("工作台", "workspaceTab")
        self.breadcrumb.setParent(toolbar)
        self.breadcrumb.hide()
        self.nav_buttons = []
        for index, title in enumerate(("工作台", "Windows 环境", "历史")):
            button = _button(title, lambda checked=False, page=index: self._show_workspace(page), "navTab")
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.setProperty("active", index == 0)
            bar.addWidget(button)
            self.nav_buttons.append(button)
        self.workbench_button, self.windows_button, self.history_button = self.nav_buttons
        bar.addStretch(1)
        self.mode_label = _label("演示模式" if self.demo else "Windows 环境", "muted")
        self.mode_label.setParent(toolbar)
        self.mode_label.hide()
        self.system_settings_button = _button("环境变量", self._open_system_settings, "quiet")
        self.system_settings_button.setParent(toolbar)
        self.system_settings_button.hide()
        self.refresh_button = _button("刷新检测", self._reload, "quiet")
        self.refresh_button.setIcon(icon("refresh"))
        bar.addWidget(self.refresh_button)
        self.config_button = ActionButton("配置编辑器")
        self.config_button.setObjectName("quiet")
        self.config_button.setIcon(icon("code"))
        menu = QMenu(self.config_button)
        menu.addAction("可视化编辑", self._visual_config)
        menu.addAction("编辑原始 TOML", self._edit_config)
        menu.addSeparator()
        menu.addAction("打开配置文件", self._open_config)
        menu.addSeparator()
        menu.addAction("高级设置", self._advanced_settings)
        self.config_button.setMenu(menu)
        bar.addWidget(self.config_button)
        return toolbar

    def _advanced_settings(self) -> None:
        dialog = CleanupSettingsDialog(self.service.cleanup_settings, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.service.set_cleanup_settings(dialog.settings())
        except Exception as exc:
            QMessageBox.warning(self, "设置未保存", str(exc))
            return
        self.feedback.setText("高级设置已保存；新的预览将按当前整理选项计算。")

    def _show_workspace(self, index: int) -> None:
        self.workspace_stack.setCurrentIndex(index)
        for number, button in enumerate(self.nav_buttons):
            button.setChecked(number == index)
            button.setProperty("active", number == index)
            button.style().unpolish(button)
            button.style().polish(button)
        self.footer_panel.setVisible(index == 0)
        if index == 1:
            self._refresh_windows_page()
        elif index == 2:
            self.history_page.refresh()

    def _refresh_windows_page(self) -> None:
        if hasattr(self.service, "list_environment"):
            records, errors = [], []
            for scope in ("User", "System"):
                try:
                    records.extend(self.service.list_environment(scope))
                except Exception as exc:
                    errors.append(("用户" if scope == "User" else "系统") + "环境读取失败：" + str(exc))
            self.windows_page.refresh(None, "；".join(errors), records=records,
                description=("演示存储中的全部变量 · 只读" if self.demo else "Windows 用户与系统环境的全部变量 · 只读") +
                            (" · " + "；".join(errors) if errors else ""))
        else:
            self.windows_page.refresh(self._detection, self._detection_error)

    def _show_windows_value(self, value: str, sensitive: bool) -> None:
        FullValueDialog(value, False, sensitive, self).exec()

    def _refresh_filter_buttons(self, *_args) -> None:
        if not hasattr(self, "filter_buttons"):
            return
        complete = sum(bool((scheme := self._scheme_match(app, self._selected_option(app)))
                            and scheme.available and scheme.status is SchemeStatus.COMPLETE
                            and not self._detection_error) for app in self.service.config.apps())
        total = len(self.service.config.apps())
        for index, (button, title, count) in enumerate(zip(self.filter_buttons,
                ("全部", "已匹配", "需配置"), (total, complete, total - complete))):
            button.setText(f"{title} {count}")
            button.setChecked(index == self.app_filter.currentIndex())

    def _build_comparison(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("comparisonPanel")
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        header = QFrame()
        header.setObjectName("comparisonHeader")
        row = QHBoxLayout(header)
        row.setContentsMargins(12, 8, 12, 8)
        self.comparison_title = _label("Windows 值对照", "section")
        row.addWidget(self.comparison_title, 1)
        row.addWidget(_label("只读检测快照", "muted"))
        outer.addWidget(header)
        contents = QGridLayout()
        contents.setContentsMargins(12, 10, 12, 10)
        contents.setHorizontalSpacing(20)
        contents.setVerticalSpacing(6)
        contents.setColumnStretch(0, 1)
        contents.setColumnStretch(1, 1)
        contents.addWidget(_label("当前选定值", "muted"), 0, 0)
        contents.addWidget(_label("Windows 当前值", "muted"), 0, 1)
        self.comparison_selected = ElidingLabel("", "comparisonValue")
        self.comparison_actual = ElidingLabel("", "comparisonValue")
        self.comparison_meta = ElidingLabel("", "meta")
        self.comparison_result = ElidingLabel("", "meta")
        for widget in (self.comparison_selected, self.comparison_actual, self.comparison_meta, self.comparison_result):
            widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        contents.addWidget(self.comparison_selected, 1, 0)
        contents.addWidget(self.comparison_actual, 1, 1)
        contents.addWidget(self.comparison_meta, 2, 0)
        contents.addWidget(self.comparison_result, 2, 1)
        outer.addLayout(contents)
        panel.hide()
        self._comparison_card = None
        return panel

    def _refresh_comparison(self) -> None:
        card = getattr(self, "_comparison_card", None)
        if card is None or card not in self.cards:
            self.comparison_panel.hide()
            return
        spec = card.spec
        self.comparison_title.setText(f"Windows 值对照 · {spec.name}")
        self.comparison_selected.setText(_value(self.service.selections.get(spec).value, hidden=card.hidden))
        result = card.match_result
        state = result.windows_state if result is not None else None
        self.comparison_actual.setText(_value(state.value, state.exists, card.hidden) if state is not None
                                       else self._detection_error or "尚未取得 Windows 当前值")
        source = card.selector.currentText()
        self.comparison_meta.setText(("用户" if spec.scope == "User" else "系统") + " · " +
            {"replace": "替换", "+append": "前置", "-append": "后置", "append": "自定义排序"}[spec.mode] + " · " + source)
        self.comparison_result.setText(card.match_label.text() + " · " + (_registry_type(state) if state else "—"))
        for record in self.cards:
            record.setProperty("inspected", record is card)
            record.style().unpolish(record)
            record.style().polish(record)

    def eventFilter(self, watched, event) -> bool:
        if event.type() in (QEvent.Type.FocusIn, QEvent.Type.MouseButtonPress):
            for card in self.cards:
                if watched in (card, card.editor, card.selector):
                    self._comparison_card = card
                    self._refresh_comparison()
                    break
        return super().eventFilter(watched, event)

    def _build_manifest(self) -> QFrame:
        manifest = QFrame()
        manifest.setObjectName("manifest")
        manifest.setMinimumWidth(248)
        manifest.setMaximumWidth(320)
        side = QVBoxLayout(manifest)
        side.setContentsMargins(12, 12, 8, 8)
        side.setSpacing(6)
        heading = QHBoxLayout()
        heading.addWidget(_label("应用", "section"), 1)
        self.manifest_count = _label("", "selectionSummary")
        heading.addWidget(self.manifest_count)
        self.select_all_button = _button("全选", lambda: self._check_all(True), "quiet")
        self.clear_selection_button = _button("清空", lambda: self._check_all(False), "quiet")
        self.select_all_button.setToolTip("勾选所有应用，包含搜索中隐藏的应用")
        self.clear_selection_button.setToolTip("取消所有应用的勾选")
        side.addLayout(heading)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索应用或方案…")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icon("search", COLORS["muted"], 18), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setAccessibleName("搜索批量应用清单")
        self.search.textChanged.connect(self._render_apps)
        side.addWidget(self.search)
        self.app_filter = QComboBox()
        self.app_filter.setObjectName("manifestFilter")
        self.app_filter.addItem("全部应用", "all")
        self.app_filter.addItem("完全匹配", "complete")
        self.app_filter.addItem("需配置", "needs")
        self.app_filter.setToolTip("按每个应用准备使用的方案筛选；检测失败与未读取不会列为完全匹配")
        self.app_filter.setAccessibleName("按准备方案的检测状态筛选应用")
        self.app_filter.currentIndexChanged.connect(self._render_apps)
        self.app_filter.setParent(manifest)
        self.app_filter.hide()
        filters = QHBoxLayout()
        filters.setSpacing(2)
        self.filter_buttons = []
        for index, text in enumerate(("全部", "已匹配", "需配置")):
            button = _button(text, lambda checked=False, item=index: self.app_filter.setCurrentIndex(item), "filterButton")
            button.setCheckable(True)
            filters.addWidget(button, 1)
            self.filter_buttons.append(button)
        self.app_filter.currentIndexChanged.connect(self._refresh_filter_buttons)
        self._refresh_filter_buttons()
        side.addLayout(filters)
        selection = QHBoxLayout()
        selection.addWidget(_label("批量选择", "muted"), 1)
        selection.addWidget(self.select_all_button)
        selection.addWidget(self.clear_selection_button)
        side.addLayout(selection)
        self.app_list = ApplicationList()
        self.app_list.setResizeMode(ApplicationList.ResizeMode.Adjust)
        self.app_list.setObjectName("manifestList")
        self.app_list.setItemDelegate(ManifestDelegate(self.app_list))
        self.app_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.app_list.setAccessibleName("应用清单；空格勾选，名称查看详情，第二行方案摘要可切换")
        self.app_list.currentItemChanged.connect(lambda item, previous: self._select_app(item.data(Qt.ItemDataRole.UserRole)) if item else None)
        self.app_list.itemChanged.connect(self._check_app)
        side.addWidget(self.app_list, 1)
        self.search_empty = _label("没有匹配的应用，试试其他名称。", "muted")
        self.search_empty.setVisible(False)
        side.addWidget(self.search_empty)
        side.addWidget(_label("勾选用于批量操作；点击应用查看详情。\n准备方案与输入只保留在运行草稿中。", "manifestHint"))
        return manifest

    def _build_inspector(self) -> QFrame:
        detail = QFrame()
        detail.setObjectName("detailPane")
        layout = QVBoxLayout(detail)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header = QFrame()
        header.setObjectName("inspectorHeader")
        context = QVBoxLayout(header)
        context.setContentsMargins(20, 12, 20, 12)
        context.setSpacing(4)
        self.workspace_crumb = ElidingLabel("应用", "contextPath")
        self.workspace_crumb.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        context.addWidget(self.workspace_crumb)
        heading = QHBoxLayout()
        heading.setSpacing(8)
        self.app_mark = QLabel()
        self.app_mark.setFixedSize(20, 20)
        heading.addWidget(self.app_mark)
        self.title = ElidingLabel("选择应用", "compactHeading")
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        heading.addWidget(self.title, 1)
        self.option_row = heading
        preparation = QVBoxLayout()
        preparation.setSpacing(3)
        preparation.addWidget(_label("准备使用的方案", "muted"))
        self.option_combo = OptionComboBox()
        self.option_combo.setObjectName("detailOption")
        self.option_combo.setMinimumWidth(150)
        self.option_combo.setMaximumWidth(190)
        self.option_combo.setFixedHeight(self.option_combo.minimumSizeHint().height())
        self.option_combo.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.option_combo.setAccessibleName("准备使用的方案；不会改变当前激活方案")
        self.option_combo.currentIndexChanged.connect(self._option_changed)
        preparation.addWidget(self.option_combo)
        heading.addLayout(preparation)
        context.addLayout(heading)
        status = QHBoxLayout()
        status.setSpacing(6)
        self.match_status = _label("", "matchStatus")
        self.match_status.setWordWrap(False)
        status.addWidget(self.match_status, 0, Qt.AlignmentFlag.AlignTop)
        self.activation_badge = _label("", "activationBadge")
        self.activation_badge.setWordWrap(False)
        status.addWidget(self.activation_badge, 0, Qt.AlignmentFlag.AlignTop)
        self.active_label = _label("", "muted")
        self.active_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        status.addWidget(self.active_label, 1)
        context.addLayout(status)
        self.subtitle = _label("", "muted")
        self.subtitle.setParent(header)
        self.subtitle.hide()
        layout.addWidget(header)
        self.pending_banner = QFrame()
        self.pending_banner.setObjectName("banner")
        pending = QHBoxLayout(self.pending_banner)
        pending.setContentsMargins(12, 8, 12, 8)
        self.pending_text = _label("")
        pending.addWidget(self.pending_text, 1)
        self.pending_button = _button("检查恢复", self._pending, "quiet")
        pending.addWidget(self.pending_button)
        layout.addWidget(self.pending_banner)
        variables_heading = QHBoxLayout()
        variables_heading.setContentsMargins(20, 12, 20, 8)
        variables_heading.setSpacing(8)
        variables_heading.addWidget(_label("环境变量", "section"))
        self.variable_count = _label("", "muted")
        self.variable_count.setWordWrap(False)
        variables_heading.addWidget(self.variable_count)
        variables_heading.addStretch(1)
        self.mismatch_only = QCheckBox("仅看未匹配")
        self.mismatch_only.setToolTip("显示准备方案中未完全匹配、检测失败或尚未读取的变量")
        self.mismatch_only.toggled.connect(self._filter_variable_rows)
        variables_heading.addWidget(self.mismatch_only)
        self.windows_compare_button = _button("展开 Windows 对照", self._toggle_windows_comparison, "quiet")
        self.windows_compare_button.setCheckable(True)
        self.windows_compare_button.setChecked(True)
        self.windows_compare_button.setText("收起 Windows 对照")
        variables_heading.addWidget(self.windows_compare_button)
        layout.addLayout(variables_heading)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.card_panel = QWidget()
        self.card_layout = QVBoxLayout(self.card_panel)
        self.card_layout.setContentsMargins(20, 0, 20, 12)
        self.card_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.card_layout.setSpacing(0)
        self.table_frame = QFrame()
        self.table_frame.setObjectName("variableTable")
        self.table_frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.table_layout = QVBoxLayout(self.table_frame)
        self.table_layout.setContentsMargins(0, 0, 0, 0)
        self.table_layout.setSpacing(0)
        self.card_layout.addWidget(self.table_frame)
        self.comparison_panel = self._build_comparison()
        self.comparison_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.card_layout.addWidget(self.comparison_panel)
        self.card_layout.addStretch(1)
        self.scroll.setWidget(self.card_panel)
        layout.addWidget(self.scroll, 1)
        return detail

    def _toggle_windows_comparison(self) -> None:
        expanded = self.windows_compare_button.isChecked()
        self.windows_compare_button.setText("收起 Windows 对照" if expanded else "展开 Windows 对照")
        self.comparison_panel.setVisible(expanded)
        self._refresh_comparison()

    def _filter_variable_rows(self) -> None:
        visible = 0
        for card in self.cards:
            result = card.match_result
            show = (not self.mismatch_only.isChecked() or result is None
                    or not result.available or result.status is not VariableMatchStatus.MATCH)
            card.setVisible(show)
            visible += int(show)
        inspected = getattr(self, "_comparison_card", None)
        if inspected is not None and inspected.isHidden():
            self._comparison_card = next((card for card in self.cards if not card.isHidden()), None)
            self._refresh_comparison()
        self.comparison_panel.setVisible(bool(self._comparison_card) and self.windows_compare_button.isChecked())
        if getattr(self, "variable_empty", None) is not None:
            self.variable_empty.setVisible(visible == 0)
            self.variable_empty.setText("当前方案没有未匹配的变量。" if self.cards else "当前方案没有变量。")
        self.variable_count.setText(f"{len(self.cards)} 项" + (f" · 显示 {visible} 项" if self.mismatch_only.isChecked() else ""))

    def _build_footer(self) -> QFrame:
        footer = QFrame()
        footer.setObjectName("footer")
        actions = QHBoxLayout(footer)
        actions.setContentsMargins(12, 8, 12, 8)
        actions.setSpacing(8)
        self.feedback_panel = QWidget()
        self.feedback_panel.setObjectName("footerFeedback")
        self.feedback_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.feedback_panel.setMinimumWidth(0)
        feedback = QVBoxLayout(self.feedback_panel)
        feedback.setContentsMargins(0, 0, 0, 0)
        feedback.setSpacing(4)
        self.batch_summary = ElidingLabel("", "footerScope")
        self.batch_summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        feedback.addWidget(self.batch_summary)
        blocking = QHBoxLayout()
        blocking.setSpacing(8)
        self.blocking_reason = _label("", "error")
        self.blocking_reason.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.blocking_reason.setAccessibleName("应用被阻止的原因")
        blocking.addWidget(self.blocking_reason, 1)
        self.locate_error_button = _button("定位输入", self._locate_invalid_input, "quiet")
        self.locate_error_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.locate_error_button.setToolTip("查看第一个无效输入并定位到变量")
        blocking.addWidget(self.locate_error_button)
        feedback.addLayout(blocking)
        self.feedback = _label("演示模式 · 所有操作均先预览" if self.demo else "确认预览后执行 Windows 变更", "muted")
        self.feedback.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        progress_row = QHBoxLayout()
        progress_row.setSpacing(8)
        progress_row.addWidget(self.feedback, 1)
        self.scan_progress = QProgressBar()
        self.scan_progress.setObjectName("scanProgress")
        self.scan_progress.setRange(0, 0)
        self.scan_progress.setTextVisible(False)
        self.scan_progress.setFixedSize(72, 6)
        self.scan_progress.setAccessibleName("正在检测 Windows 环境变量")
        self.scan_progress.setVisible(False)
        progress_row.addWidget(self.scan_progress)
        feedback.addLayout(progress_row)
        actions.addWidget(self.feedback_panel, 1)
        self.deactivate_button = ActionButton("撤销配置")
        self.deactivate_button.setObjectName("quiet")
        self.deactivate_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.deactivate_button.setToolTip("预览撤销当前应用正在浏览的方案；不依赖管理器应用历史")
        removal_menu = QMenu(self.deactivate_button)
        self.current_remove_action = removal_menu.addAction("撤销当前准备方案", self._remove_scheme)
        self.application_remove_action = removal_menu.addAction("撤销当前应用的所有方案", self._remove_application_all)
        removal_menu.addSeparator()
        self.batch_current_action = removal_menu.addAction("撤销已勾选应用的准备方案", self._remove_batch_current)
        self.batch_all_action = removal_menu.addAction("撤销已勾选应用的所有方案", self._remove_batch_all)
        self.deactivate_button.setMenu(removal_menu)
        actions.addWidget(self.deactivate_button)
        self.remove_more_button = ActionButton("应用撤销")
        self.remove_more_button.setObjectName("quiet")
        self.remove_more_button.setToolTip("撤销当前应用所有方案中 Windows 实际匹配的内容")
        application_menu = QMenu(self.remove_more_button)
        application_menu.addAction("撤销此应用所有方案", self._remove_application_all)
        self.remove_more_button.setMenu(application_menu)
        self.remove_more_button.setParent(footer)
        self.remove_more_button.hide()
        self.batch_remove_button = ActionButton("批量撤销")
        self.batch_remove_button.setObjectName("quiet")
        self.batch_remove_button.setToolTip("请先勾选应用，再选择当前方案或所有方案范围")
        batch_menu = QMenu(self.batch_remove_button)
        batch_menu.addAction(
            "A  撤销已勾选应用的当前方案", self._remove_batch_current)
        batch_menu.addAction(
            "B  撤销已勾选应用的所有方案", self._remove_batch_all)
        self.batch_remove_button.setMenu(batch_menu)
        self.batch_remove_button.setParent(footer)
        self.batch_remove_button.hide()
        self.apply_button = _button("预览并应用", self._apply, "primary")
        self.apply_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.apply_button.setMinimumWidth(130)
        self.apply_button.setIcon(icon("arrow_right", COLORS["surface"], 18))
        actions.addWidget(self.apply_button)
        return footer

    def _render_apps(self) -> None:
        self.checked_apps.intersection_update(self.service.config.apps())
        self.app_list.blockSignals(True)
        self.app_list.clear()
        needle = self.search.text().casefold()
        filter_mode = self.app_filter.currentData()
        for app in self.service.config.apps():
            if needle not in app.casefold() and not any(needle in option.casefold() for option in self.service.config.options(app)):
                continue
            scheme = self._scheme_match(app, self._selected_option(app))
            complete = (not self._detection_error and scheme is not None and scheme.available
                        and scheme.status is SchemeStatus.COMPLETE)
            if filter_mode == "complete" and not complete:
                continue
            if filter_mode == "needs" and complete:
                continue
            item = QListWidgetItem(app)
            item.setData(Qt.ItemDataRole.UserRole, app)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if app in self.checked_apps else Qt.CheckState.Unchecked)
            item.setToolTip(app + "\n勾选以批量应用；点击名称查看和修改方案")
            item.setSizeHint(QSize(0, 48))
            self.app_list.addItem(item)
            self.app_list.setItemWidget(item, ApplicationRow(self, item, app))
            if app == self.app_name:
                self.app_list.setCurrentItem(item)
        self.app_list.blockSignals(False)
        self.search_empty.setVisible(self.app_list.count() == 0)
        self._update_apply_availability()

    def _refresh_active_app_filter(self) -> None:
        if self.app_filter.currentData() != "all":
            self._render_apps()

    def _refresh_manifest_rows(self) -> None:
        for index in range(self.app_list.count()):
            row = self.app_list.itemWidget(self.app_list.item(index))
            if isinstance(row, ApplicationRow):
                row.refresh(self)

    def _navigate_app(self, app: str) -> None:
        self._select_app(app)
        self.scroll.verticalScrollBar().setValue(0)

    def _manifest_option_changed(self, app: str, option: str) -> None:
        if not option:
            return
        if hasattr(self.service, "select_option"):
            self.service.select_option(app, option)
        else:
            self._option_drafts[app] = option
        self._scan_detection(force=False)
        if app == self.app_name:
            self.option_combo.blockSignals(True)
            self.option_combo.setCurrentText(option)
            self.option_combo.blockSignals(False)
            self._render_variables()
        self._refresh_manifest_rows()
        self._update_apply_availability()
        self._refresh_active_app_filter()

    def _check_app(self, item: QListWidgetItem) -> None:
        app = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            self.checked_apps.add(app)
        else:
            self.checked_apps.discard(app)
        row = self.app_list.itemWidget(item)
        if isinstance(row, ApplicationRow):
            row.refresh(self)
        self._update_apply_availability()

    def _check_all(self, checked: bool) -> None:
        self.checked_apps = set(self.service.config.apps()) if checked else set()
        self._render_apps()
        self._update_apply_availability()

    def _selected_option(self, app: str) -> str:
        if hasattr(self.service, "selected_option"):
            return self.service.selected_option(app)
        options = self.service.config.options(app)
        selected = self._option_drafts.get(app)
        if selected not in options:
            activation = self.service.state.snapshot().get("active", {}).get(app)
            selected = activation.get("option") if isinstance(activation, dict) else activation
            default = self.service.config.default_option(app) if hasattr(self.service.config, "default_option") else options[0]
            selected = selected if selected in options else default
            self._option_drafts[app] = selected
        return selected

    def _option_changed(self, *_args) -> None:
        option = self.option_combo.currentText()
        if self.app_name and option:
            if hasattr(self.service, "select_option"):
                self.service.select_option(self.app_name, option)
            else:
                self._option_drafts[self.app_name] = option
        self._scan_detection(force=False)
        self._render_variables()
        self._refresh_manifest_rows()
        self._refresh_active_app_filter()

    def _select_app(self, app: str) -> None:
        self.app_name = app
        self.title.setText(app)
        self.title.setToolTip(app)
        self.breadcrumb.setText("工作台")
        self.workspace_crumb.setText(f"应用 / {app}" if app else "应用")
        self.app_mark.setPixmap(app_icon(app, 18).pixmap(18, 18))
        self.option_combo.blockSignals(True)
        self.option_combo.clear()
        if not app:
            self.option_combo.blockSignals(False)
            self._refresh_manifest_rows()
            self._render_variables()
            return
        self.option_combo.addItems(self.service.config.options(app))
        self._update_option_combo(self.option_combo, app)
        self.option_combo.setCurrentText(self._selected_option(app))
        self.option_combo.blockSignals(False)
        self.app_list.blockSignals(True)
        for index in range(self.app_list.count()):
            item = self.app_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == app:
                self.app_list.setCurrentItem(item)
                break
        self.app_list.blockSignals(False)
        self._refresh_manifest_rows()
        self._render_variables()

    def _render_variables(self, *_args) -> None:
        _clear(self.table_layout)
        self.cards = []
        self.variable_empty = None
        if not self.app_name or not self.option_combo.currentText():
            self.variable_count.setText("")
            _set_match_label(self.match_status, "", "unconfigured", "")
            _activation_badge(self.activation_badge, None)
            self.active_label.setText("当前激活：无")
            self.deactivate_button.setEnabled(False)
            self.remove_more_button.setEnabled(False)
            self._update_apply_availability()
            return
        option = self.option_combo.currentText()
        table_header = QFrame()
        table_header.setObjectName("variableTableHeader")
        columns = QGridLayout(table_header)
        columns.setContentsMargins(8, 6, 8, 6)
        _variable_columns(columns)
        for index, text in enumerate(("变量名称", "作用域 / 模式", "值来源", "当前选定值", "检测", "操作")):
            label = _label(text, "muted")
            label.setWordWrap(False)
            columns.addWidget(label, 0, index)
        self.table_layout.addWidget(table_header)
        self._update_option_combo(self.option_combo, self.app_name)
        current_index = self.option_combo.currentIndex()
        self.option_combo.setToolTip(self.option_combo.itemData(
            current_index, Qt.ItemDataRole.ToolTipRole) or option)
        match_text, match_tone, progress, match_tip = self._scheme_ui(self.app_name, option)
        _set_match_label(self.match_status, f"{match_text} {progress}".strip(), match_tone, match_tip)
        activation = self.service.state.snapshot().get("active", {}).get(self.app_name)
        active = activation.get("option") if isinstance(activation, dict) else activation
        _activation_badge(self.activation_badge, active)
        self.active_label.setText("当前激活方案 " + active if active else "当前激活方案：无")
        if hasattr(self.service.config, "default_option"):
            separator = " · "
            self.active_label.setText(self.active_label.text() + separator + "默认方案 " + self.service.config.default_option(self.app_name))
        if hasattr(self.service, "initialization_warning"):
            warning = self.service.initialization_warning(self.app_name)
            if warning:
                self.active_label.setText(self.active_label.text() + "\n" + warning)
        self.active_label.setToolTip(self.active_label.text())
        self.deactivate_button.setEnabled(not self._scanning)
        self.remove_more_button.setEnabled(not self._scanning)
        for spec in self.service.config.variables(self.app_name, option):
            card = VariableCard(self.service, spec, match_result=self._variable_match(spec),
                                remove_callback=self._remove_variable,
                                snapshot_managed=hasattr(self.service, "detection_snapshot"),
                                detection_error=self._detection_error)
            card.changed.connect(self._variable_changed)
            self.cards.append(card)
            self.table_layout.addWidget(card)
            card.installEventFilter(self)
            card.editor.installEventFilter(self)
            card.selector.installEventFilter(self)

        self.variable_empty = _label("", "muted")
        self.table_layout.addWidget(self.variable_empty)
        self._comparison_card = self.cards[0] if self.cards else None
        self.comparison_panel.setVisible(bool(self.cards) and self.windows_compare_button.isChecked())
        self._refresh_comparison()
        self._refresh_filter_buttons()
        previous = self.option_combo
        for card in self.cards:
            QWidget.setTabOrder(previous, card.selector)
            QWidget.setTabOrder(card.selector, card.editor)
            QWidget.setTabOrder(card.editor, card.full_value_button)
            previous = card.full_value_button
            QWidget.setTabOrder(previous, card.remove_button)
            previous = card.remove_button
            if card.hidden:
                QWidget.setTabOrder(previous, card.reveal)
                previous = card.reveal
        QWidget.setTabOrder(previous, self.deactivate_button)
        QWidget.setTabOrder(self.deactivate_button, self.apply_button)
        self._filter_variable_rows()
        self._refresh_manifest_rows()
        self._update_apply_availability()

    def _update_apply_availability(self) -> None:
        selected = [app for app in self.service.config.apps() if app in self.checked_apps]
        if selected:
            specs = [spec for app in selected for spec in self.service.config.variables(app, self._selected_option(app))]
            self.apply_button.setText("预览所选应用")
            self.batch_summary.setText(f"已选 {len(selected)} 个应用 · {len(specs)} 项变量 · " + _applications_text([(app, self._selected_option(app)) for app in selected]))
            self.batch_summary.setToolTip(_applications_text([(app, self._selected_option(app)) for app in selected]))
        else:
            specs = [card.spec for card in self.cards]
            self.apply_button.setText("预览并应用")
            self.batch_summary.setText("单应用 · " + _applications_text([(self.app_name, self.option_combo.currentText())]) if self.app_name else "请选择应用")
        self.manifest_count.setText(f"已选 {len(selected)} / {len(self.service.config.apps())}")
        save_error = getattr(self.service, "configuration_save_error", "")
        can_remove_batch = bool(selected) and not self._scanning and not save_error
        self.batch_remove_button.setEnabled(can_remove_batch)
        self.batch_current_action.setEnabled(can_remove_batch)
        self.batch_all_action.setEnabled(can_remove_batch)
        self.batch_remove_button.setToolTip(
            f"已勾选 {len(selected)} 个应用；选择批量 A（当前方案）或 B（所有方案）"
            if selected else "请先在左侧勾选至少一个应用")
        self._invalid_specs = [spec for spec in specs if self.service.selections.get(spec).error]
        if self._invalid_specs:
            first = self._invalid_specs[0]
            reason = f"{first.app} / {first.name}（{first.scope}）输入无效"
            if len(self._invalid_specs) > 1:
                reason += f" · 共 {len(self._invalid_specs)} 处"
            self.blocking_reason.setText(reason)
            self.blocking_reason.setToolTip("\n".join(
                f"{spec.app} / {spec.name}（{spec.scope}）输入无效" for spec in self._invalid_specs))
        else:
            self.blocking_reason.clear()
            self.blocking_reason.setToolTip("")
        if save_error:
            self.blocking_reason.setText("配置文件与运行状态未同步；请重新加载配置。")
            self.blocking_reason.setToolTip(str(save_error))
        self.blocking_reason.setVisible(bool(self._invalid_specs) or bool(save_error))
        self.locate_error_button.setVisible(bool(self._invalid_specs))
        self.apply_button.setEnabled(
            bool(specs) and not self._invalid_specs and not self._scanning and not save_error)
        can_remove_current = bool(self.app_name and self.option_combo.currentText()) and not self._scanning and not save_error
        self.deactivate_button.setEnabled(can_remove_current or can_remove_batch)
        self.remove_more_button.setEnabled(can_remove_current)
        self.current_remove_action.setEnabled(can_remove_current)
        self.application_remove_action.setEnabled(can_remove_current)
        for card in self.cards:
            card.remove_button.setEnabled(not save_error and not self._scanning
                                          and card.remove_callback is not None and card._match_details()[2])
        self._refresh_manifest_rows()

    def _variable_changed(self) -> None:
        """Recompute match labels from the cached Windows snapshot without writing."""
        self._scan_detection(force=False)
        for card in self.cards:
            card.match_result = self._variable_match(card.spec)
            card.refresh(keep_cursor=card.editor.hasFocus())
        if self.app_name and self.option_combo.currentText():
            option = self.option_combo.currentText()
            self._update_option_combo(self.option_combo, self.app_name)
            text, tone, progress, tip = self._scheme_ui(self.app_name, option)
            _set_match_label(self.match_status, f"{text} {progress}".strip(), tone, tip)
        self._update_apply_availability()
        self._filter_variable_rows()
        self._refresh_active_app_filter()
        self._refresh_filter_buttons()
        self._refresh_comparison()

    def _locate_invalid_input(self) -> None:
        if not self._invalid_specs:
            return
        spec = self._invalid_specs[0]
        visible_apps = {self.app_list.item(index).data(Qt.ItemDataRole.UserRole)
                        for index in range(self.app_list.count())}
        if spec.app not in visible_apps:
            self.app_filter.setCurrentIndex(0)
            self.search.clear()
        self._navigate_app(spec.app)
        for card in self.cards:
            if card.spec.key == spec.key:
                self.scroll.ensureWidgetVisible(card)
                card.editor.setFocus(Qt.FocusReason.OtherFocusReason)
                break

    def _refresh_pending(self) -> None:
        try:
            pending = self.service.pending()
        except Exception as exc:
            self.feedback.setText(str(exc))
            pending = []
        self.pending_banner.setVisible(bool(pending))
        self.pending_text.setText(f"检测到 {len(pending)} 项未完成操作。请检查并预览恢复。")

    def _pending(self) -> None:
        pending = self.service.pending()
        if not pending:
            self._refresh_pending()
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("未完成操作")
        dialog.resize(610, 400)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)
        layout.addWidget(_label("未完成操作", "title"))
        layout.addWidget(_label("逐项预览可安全恢复的变更。冲突项会保留当前数据。", "muted"))
        items = QListWidget()
        layout.addWidget(items, 1)
        for entry in pending:
            applications = _applications_text(entry.get("applications"), entry.get("app", ""), entry.get("option", ""))
            item = QListWidgetItem(f"{_local_time(entry.get('created_at', ''))}  ·  {applications}  ·  {_status_text(entry.get('status', ''))}\n{entry.get('message', '')}")
            item.setToolTip(item.text())
            item.setData(Qt.ItemDataRole.UserRole, entry["id"])
            items.addItem(item)
        layout.addWidget(_button("预览恢复选中操作", lambda: self._preview(lambda: self.service.plan_recover(items.currentItem().data(Qt.ItemDataRole.UserRole))) if items.currentItem() else None, "primary"))
        dialog.exec()
        self._refresh_pending()

    def _apply(self) -> None:
        selected = {app: self._selected_option(app) for app in self.service.config.apps() if app in self.checked_apps}
        if selected and callable(getattr(self.service, "prepare_batch_scheme_switch", None)):
            self._switch_selected_schemes(selected)
        elif selected:
            self._preview(lambda orders=None: self.service.plan_batch(selected, orders))
        elif hasattr(self.service, "prepare_scheme_switch"):
            self._switch_current_scheme()
        else:
            self._preview(lambda orders=None: self.service.plan(self.app_name, self.option_combo.currentText(), orders))

    def _remove_variable(self, spec: VariableSpec) -> None:
        request = RemovalRequest(RemovalScope.VARIABLE, frozenset((spec.app,)),
                                 {spec.app: spec.option}, (spec.key,))
        self._run_removal(request)

    def _remove_scheme(self) -> None:
        if not self.app_name or not self.option_combo.currentText():
            return
        request = RemovalRequest(RemovalScope.SCHEME, frozenset((self.app_name,)),
                                 {self.app_name: self.option_combo.currentText()})
        self._run_removal(request)

    def _remove_application_all(self) -> None:
        if not self.app_name:
            return
        self._run_removal(RemovalRequest(
            RemovalScope.APPLICATION_ALL, frozenset((self.app_name,))))

    def _remove_batch_current(self) -> None:
        if not self.checked_apps:
            return
        options = {app: self._selected_option(app) for app in self.service.config.apps()
                   if app in self.checked_apps}
        self._run_removal(RemovalRequest(
            RemovalScope.BATCH_CURRENT, frozenset(self.checked_apps), options))

    def _remove_batch_all(self) -> None:
        if not self.checked_apps:
            return
        self._run_removal(RemovalRequest(
            RemovalScope.BATCH_ALL, frozenset(self.checked_apps)))

    def _run_removal(self, request: RemovalRequest) -> None:
        try:
            draft = self.service.prepare_remove(request)
            if not draft.candidates.atoms:
                skipped = len(draft.skipped)
                message = "当前范围没有与 Windows 实际值匹配的可撤销内容。"
                if skipped:
                    message += f" 已跳过 {skipped} 项缺失或不匹配内容。"
                self.feedback.setText(message)
                QMessageBox.information(self, "无可撤销内容", message)
                return
            decisions = {}
            if draft.shared_conflicts:
                dialog = SharedConflictDialog(
                    draft.shared_conflicts, len(draft.ordinary_atoms), self)
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                decisions = dialog.decisions()
            plan = self.service.finalize_remove(draft.id, decisions)
            self._execute_plan(plan)
        except Exception as exc:
            self._show_operation_error(exc)

    def _choose_old_schemes(self, app: str, options, *, required: bool):
        dialog = OldSchemeDialog(app, options, required=required, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.selected_options()

    def _collect_orders(self, changes) -> dict[str, tuple[str, ...]] | None:
        orders = {}
        for change in changes:
            if not change.calculation or not change.calculation.needs_order:
                continue
            dialog = OrderDialog(change, self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return None
            orders[change.record_key] = dialog.order()
        return orders

    def _switch_current_scheme(self) -> None:
        app, new_option = self.app_name, self.option_combo.currentText()
        if not app or not new_option:
            return
        try:
            try:
                draft = self.service.prepare_scheme_switch(app, new_option)
            except AmbiguousOldSchemeError as exc:
                options = tuple((option, "complete") for option in exc.candidates.complete)
                old_options = self._choose_old_schemes(app, options, required=True)
                if old_options is None:
                    return
                draft = self.service.prepare_scheme_switch(app, new_option, old_options=old_options)

            if not draft.old_options and draft.candidates.partial:
                options = tuple((option, "partial") for option in draft.candidates.partial)
                old_options = self._choose_old_schemes(app, options, required=False)
                if old_options is None:
                    return
                if old_options:
                    draft = self.service.prepare_scheme_switch(
                        app, new_option, old_options=old_options)

            self._review_switch_draft(
                draft,
                lambda current, orders: self.service.prepare_scheme_switch(
                    app, new_option, old_options=current.old_options, orders=orders),
                self.service.finalize_scheme_switch,
            )
        except Exception as exc:
            self._show_operation_error(exc)

    def _switch_selected_schemes(self, selected: dict[str, str]) -> None:
        try:
            choices = {}
            # Resolve each application's ambiguity independently, then compose
            # the whole batch once. Never execute per-application switch plans.
            while True:
                try:
                    draft = self.service.prepare_batch_scheme_switch(
                        selected, old_options=choices or None)
                except AmbiguousOldSchemeError as exc:
                    app = exc.app
                    options = tuple((option, "complete") for option in exc.candidates.complete)
                    chosen = self._choose_old_schemes(app, options, required=True)
                    if chosen is None:
                        return
                    choices[app] = chosen
                    continue
                changed = False
                for app, candidates in draft.candidates_by_app.items():
                    if app in choices or draft.old_options_by_app[app] or not candidates.partial:
                        continue
                    options = tuple((option, "partial") for option in candidates.partial)
                    chosen = self._choose_old_schemes(app, options, required=False)
                    if chosen is None:
                        return
                    choices[app] = chosen
                    changed = True
                if not changed:
                    break
            self._review_switch_draft(
                draft,
                lambda current, orders: self.service.prepare_batch_scheme_switch(
                    selected, old_options=dict(current.old_options_by_app), orders=orders),
                self.service.finalize_batch_scheme_switch,
            )
        except Exception as exc:
            self._show_operation_error(exc)

    def _review_switch_draft(self, draft, prepare_again, finalize) -> None:
        # Ordering can change net shared impacts. Re-prepare before asking for
        # conflict decisions; consent is never carried into a different draft.
        for _attempt in range(2):
            if draft.needs_order:
                orders = self._collect_orders(draft.changes)
                if orders is None:
                    return
                draft = prepare_again(draft, orders)
            decisions = {}
            if draft.shared_conflicts:
                conflict_dialog = SharedConflictDialog(
                    draft.shared_conflicts, len(draft.ordinary_atoms), self)
                if conflict_dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                decisions = conflict_dialog.decisions()
            plan = finalize(draft.id, decisions)
            if not plan.needs_order:
                self._execute_plan(plan)
                return
            orders = self._collect_orders(plan.changes)
            if orders is None:
                return
            draft = prepare_again(draft, orders)
        raise ConflictError("最终顺序仍未确认，请重新预览")

    def _deactivate(self) -> None:
        """Compatibility alias for the visible scheme-removal workflow."""
        self._remove_scheme()

    def _preview(self, plan_factory) -> None:
        try:
            plan = plan_factory()
            if plan.needs_order and not plan.conflicts:
                orders = {}
                for change in plan.changes:
                    if not change.calculation or not change.calculation.needs_order:
                        continue
                    dialog = OrderDialog(change, self)
                    if dialog.exec() != QDialog.DialogCode.Accepted:
                        return
                    orders[change.record_key] = dialog.order()
                plan = plan_factory(orders)
            self._execute_plan(plan)
        except Exception as exc:
            self._show_operation_error(exc)

    def _execute_plan(self, plan: ExecutionPlan) -> bool:
        if self._scanning:
            self.feedback.setText("检测尚未完成，请等待检测结果后重新预览。")
            return False
        if PreviewDialog(plan, self).exec() != QDialog.DialogCode.Accepted:
            return False
        result = self.service.execute(plan)
        status_text = _status_text(result.status)
        metrics_text = (
            f"实际写入 {result.changed_count} 个注册表目标 · "
            f"移除 {result.removed_element_count} 个列表元素/副本 · "
            f"保留 {result.kept_shared_count} 个共享对象 · "
            f"跳过 {result.skipped_count} 项")
        feedback = status_text + "  ·  " + result.message
        if result.success:
            feedback += "  ·  " + metrics_text
        self.feedback.setText(feedback)
        if not result.success or result.notification_error or result.errors:
            message = result.message
            if result.errors:
                message += "\n" + "\n".join(result.errors)
            if result.notification_error:
                message += "\n系统通知：" + result.notification_error
            if result.success:
                message += "\n" + metrics_text
            QMessageBox.warning(self, _status_text(result.status), message)
        else:
            QMessageBox.information(self, status_text, result.message + "\n" + metrics_text)
        self._scan_detection(force=False)
        current_app = self.app_name
        self._render_apps()
        if current_app:
            self._select_app(current_app)
        self._refresh_pending()
        return result.success

    def _show_operation_error(self, exc: Exception) -> None:
        self.feedback.setText(str(exc))
        QMessageBox.warning(self, "无法执行", str(exc) + "\n请刷新并重新预览。")

    def _history(self) -> None:
        self._show_workspace(2)

    def _edit_config(self) -> None:
        self._show_config_editor("source")

    def _visual_config(self) -> None:
        self._show_config_editor("visual")

    def _show_config_editor(self, initial_tab: str) -> None:
        before_digest = self.service.config.digest
        try:
            dialog = ConfigEditorDialog(self.config_path, self.service, self, initial_tab=initial_tab)
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
            if (accepted and dialog.saved) or self.service.config.digest != before_digest:
                self._scan_detection(force=True)
                self._render_apps()
                apps = self.service.config.apps()
                self._select_app(self.app_name if self.app_name in apps else (apps[0] if apps else ""))
                self._refresh_pending()
                self.feedback.setText(("配置已校验并保存" if dialog.saved else "已重新加载配置") + ("，正在检测…" if self._scanning else ""))
            self._update_apply_availability()
        except Exception as exc:
            QMessageBox.warning(self, "配置编辑器无法打开", str(exc))

    def _open_config(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.config_path.resolve())))

    def _open_system_settings(self) -> None:
        try:
            open_environment_settings()
            self.feedback.setText("已请求打开 Windows 环境变量设置；修改后点击刷新。")
        except OSError as exc:
            QMessageBox.warning(self, "无法打开环境变量设置", str(exc))

    def _reload(self) -> None:
        if self._scanning:
            return
        try:
            from .config import load_config
            self.service.reload(load_config(self.config_path))
            self._scan_detection(force=True)
            apps = self.service.config.apps()
            self._render_apps()
            self._select_app(self.app_name if self.app_name in apps else (apps[0] if apps else ""))
            self._refresh_pending()
            self.feedback.setText("配置已重载，正在检测…" if self._scanning else "配置与当前状态已刷新")
            if self.workspace_stack.currentIndex() == 1:
                self._refresh_windows_page()
            elif self.workspace_stack.currentIndex() == 2:
                self.history_page.refresh()
        except Exception as exc:
            QMessageBox.warning(self, "刷新失败", str(exc))
