"""Chinese desktop interface. All registry mutations are delegated to the service."""

from __future__ import annotations

import re
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem, QMainWindow, QMenu, QSizePolicy,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QVBoxLayout, QWidget, QStyledItemDelegate, QStyleOptionViewItem, QStyleOptionComboBox, QStyle, QStylePainter,
)

from .models import ExecutionPlan, PlanChange, RegistryState, VariableSpec, match_key
from .theme import apply_theme, COLORS
from .config_editor_ui import ConfigEditorDialog
from .icons import icon, app_icon
from .windows_shell import open_environment_settings


def _label(text: str, name: str = "") -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(True)
    return widget


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


class OptionComboBox(QComboBox):
    """Measure styled label space and keep the independent option popup readable."""

    def __init__(self, parent=None):
        super().__init__(parent)
        view = QListView(self)
        view.setObjectName("optionPopup")
        # The native combo menu delegate has a different row-height calculation
        # from the styled list view. Use one delegate for layout and rendering.
        view.setItemDelegate(QStyledItemDelegate(view))
        view.setTextElideMode(Qt.TextElideMode.ElideNone)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setView(view)
        self.setMaxVisibleItems(10)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.currentTextChanged.connect(self.setToolTip)

    def _label_width(self, index: int) -> int:
        width = self.fontMetrics().horizontalAdvance(self.itemText(index))
        if not self.itemIcon(index).isNull():
            width += self.iconSize().width() + 4
        return width + 4

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        option.rect.setSize(hint)
        field = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox, option,
            QStyle.SubControl.SC_ComboBoxEditField, self,
        )
        label = max((self._label_width(index) for index in range(self.count())), default=0)
        return QSize(hint.width() + max(0, label - field.width()), hint.height())

    def minimumSizeHint(self) -> QSize:
        return QSize(80, self.sizeHint().height())

    def displayedText(self) -> str:
        """Expose the actual closed label for accessibility/geometry verification."""
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        field = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox, option,
            QStyle.SubControl.SC_ComboBoxEditField, self,
        )
        available = field.width() - 4
        if not option.currentIcon.isNull():
            available -= option.iconSize.width() + 4
        return self.fontMetrics().elidedText(
            self.currentText(), Qt.TextElideMode.ElideRight, max(0, available)
        )

    def paintEvent(self, event) -> None:
        painter = QStylePainter(self)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        option.currentText = self.displayedText()
        painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, option)

    def showPopup(self) -> None:
        view = self.view()
        view.ensurePolished()
        # Font/DPI and icons must be measured after the view receives its style.
        rows = min(self.count(), self.maxVisibleItems())
        row_heights = [view.sizeHintForRow(index) for index in range(self.count())]
        height = sum(sorted(row_heights, reverse=True)[:rows])
        width = max((view.sizeHintForColumn(0), self.width()), default=self.width())
        screen = self.screen().availableGeometry()
        view.setMinimumWidth(min(width + 2 * view.frameWidth() + 8, screen.width()))
        super().showPopup()
        if not view.isVisible():
            return
        popup = view.window()
        chrome = popup.height() - view.viewport().height()
        popup.resize(min(popup.width(), screen.width()), min(height + chrome, screen.height()))
        # Expanding the list can put a popup anchored near a screen edge outside
        # the available area. Keep the complete popup reachable.
        popup.move(
            max(screen.left(), min(popup.x(), screen.right() - popup.width() + 1)),
            max(screen.top(), min(popup.y(), screen.bottom() - popup.height() + 1)),
        )


def _activation_badge(label: QLabel, active: str | None) -> None:
    label.setText("已激活" if active else "尚未激活")
    label.setProperty("activation", "active" if active else "inactive")
    label.setToolTip("当前激活方案：" + active if active else "此应用尚未激活方案")
    label.style().unpolish(label)
    label.style().polish(label)


def _button(text: str, callback, name: str = "") -> QPushButton:
    widget = QPushButton(text)
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


class VariableCard(QFrame):
    changed = Signal()

    def __init__(self, service, spec: VariableSpec, parent=None):
        super().__init__(parent)
        self.setObjectName("compactVariable")
        self.service = service
        self.spec = spec
        self.hidden = _sensitive(spec.name)
        self._updating = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(8)
        name = ElidingLabel(spec.name, "name")
        name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        name.setToolTip(spec.name)
        top.addWidget(name, 1)
        scope = "用户" if spec.scope == "User" else "系统"
        mode = {"replace": "替换", "+append": "前置", "-append": "后置", "append": "自定义排序"}[spec.mode]
        metadata = _label(f"{scope} · {mode}", "meta")
        metadata.setWordWrap(False)
        top.addWidget(metadata)
        outer.addLayout(top)

        selector_row = QHBoxLayout()
        selector_row.setSpacing(8)
        self.selector = QComboBox()
        self.selector.setFixedWidth(116)
        self.selector.setToolTip("选择默认值、上次应用值、候选值或自定义值")
        self.selector.setAccessibleName(f"{spec.name} 值来源")
        self.selector.currentIndexChanged.connect(self._choose)
        selector_row.addWidget(self.selector)
        self.editor = QLineEdit()
        self.editor.setMinimumWidth(40)
        self.editor.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.editor.setPlaceholderText("输入自定义值" if spec.editable else "选择值来源")
        self.editor.setReadOnly(not spec.editable)
        self.editor.setAccessibleName(f"{spec.name} 当前选定值")
        self.editor.textEdited.connect(self._edit)
        selector_row.addWidget(self.editor, 1)
        self.full_value_button = _button("", self._full_value, "iconOnly")
        self.full_value_button.setIcon(icon("expand"))
        self.full_value_button.setToolTip("查看完整原始字符串" + ("并编辑" if spec.editable else ""))
        self.full_value_button.setAccessibleName(f"查看 {spec.name} 完整值")
        selector_row.addWidget(self.full_value_button)
        self.reveal = _button("显示" if self.hidden else "", self._toggle_reveal)
        self.reveal.setProperty("quiet", True)
        self.reveal.setVisible(self.hidden)
        self.reveal.setAccessibleName(f"显示 {spec.name} 敏感值")
        selector_row.addWidget(self.reveal)
        outer.addLayout(selector_row)
        self.error = _label("", "error")
        self.error.setVisible(False)
        outer.addWidget(self.error)

        self.details_toggle = _button("", self._toggle_details, "iconOnly")
        self.details_toggle.setIcon(icon("chevron_right"))
        self.details_toggle.setToolTip("收起或展开默认、上次应用与 Windows 参考值")
        self.details_toggle.setCheckable(True)
        self.details_toggle.setAccessibleName(f"展开 {spec.name} 参考值")
        top.addWidget(self.details_toggle)
        self.details = QFrame()
        self.details.setObjectName("variableDetails")
        details_layout = QVBoxLayout(self.details)
        details_layout.setContentsMargins(0, 4, 0, 0)
        details_layout.setSpacing(6)
        self.default_info = _label("", "meta")
        self.last_info = _label("", "meta")
        self.existing_info = _label("", "meta")
        self.existing_type = _label("", "muted")
        self.existing_type.setWordWrap(False)
        for title, widget in (("默认值", self.default_info),
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
        self.refresh()

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
            state = self.service.store.read(self.spec.scope, self.spec.name)
            last_values = self.service.state.snapshot().get("last_values", {})
            self.default_info.setText(_value(self.spec.default_value, hidden=self.hidden))
            last = last_values.get(self.spec.key)
            self.last_info.setText(_value(last, hidden=self.hidden) if self.spec.key in last_values else "尚未应用")
            self.existing_info.setText(_value(state.value, state.exists, self.hidden))
            self.existing_type.setText(_registry_type(state))
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
        self.escaped = "\r" in value
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 22, 24, 22)
        outer.setSpacing(13)
        outer.addWidget(_label("完整变量值", "title"))
        instruction = ("原值含回车符，使用 JSON 转义文本显示和编辑（\\r、\\n、\\\\），以保留原始字符。"
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
        return json.dumps(value, ensure_ascii=False)[1:-1] if self.escaped else value

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
        self.added = {match_key(x) for x in (calculation.added if calculation else ())}
        self.moved = {match_key(x) for x in (calculation.moved if calculation else ())}
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
        item = QListWidgetItem(shown + ("   · 新增" if match_key(value) in self.added else "   · 移动" if match_key(value) in self.moved else ""))
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


class PreviewDialog(QDialog):
    def __init__(self, plan: ExecutionPlan, parent=None):
        super().__init__(parent)
        self.plan = plan
        self.setWindowTitle("确认变更预览")
        self.resize(760, 610)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 22, 24, 22)
        outer.setSpacing(12)
        outer.addWidget(_label({"apply": "应用方案", "batch": "应用所选方案", "batch_apply": "应用所选方案", "switch": "切换方案", "deactivate": "停用方案",
                                "restore": "历史恢复", "recover": "未完成操作恢复"}.get(plan.action, plan.action), "title"))
        outer.addWidget(_label(_applications_text(getattr(plan, "applications", ()), plan.app, plan.option)
                               + f"   ·   {len(plan.changes)} 项变量", "muted"))
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
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确认执行")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(plan.can_execute)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        outer.addWidget(self.buttons)
        self._populate()

    def _populate(self) -> None:
        _clear(self.changes_layout)
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
                ({"replace": "替换", "+append": "前置", "-append": "后置", "append": "自定义排序", "batch_append": "组合追加"}.get(change.mode, change.mode), "accent", change.mode),
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
                layout.addWidget(_label("本次选定输入", "muted"))
                for contribution in contributions:
                    mode = {"replace": "替换", "+append": "前置", "-append": "后置", "append": "自定义排序"}.get(contribution["mode"], contribution["mode"])
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
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)
        self.check = QCheckBox()
        self.check.setAccessibleName(f"批量选择 {app}")
        self.check.setChecked(item.checkState() == Qt.CheckState.Checked)
        self.check.toggled.connect(lambda checked: item.setCheckState(
            Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked))
        layout.addWidget(self.check)
        body = QVBoxLayout()
        body.setSpacing(0)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.name_button = _button(app, lambda: window._navigate_app(app), "manifestName")
        self.name_button.setProperty("quiet", True)
        self.name_button.setToolTip(f"查看 {app} 的变量")
        self.name_button.setAccessibleName(f"查看 {app} 详情")
        self.name_button.setMinimumWidth(0)
        self.name_button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        top.addWidget(self.name_button, 1)
        self.option_combo = OptionComboBox()
        self.option_combo.setObjectName("manifestOption")
        self.option_combo.setAccessibleName(f"{app} 批量方案")
        self.option_combo.setFixedWidth(116)
        self.option_combo.addItems(window.service.config.options(app))
        self.option_combo.setCurrentText(window._selected_option(app))
        self.option_combo.setToolTip(self.option_combo.currentText())
        self.option_combo.currentTextChanged.connect(lambda option: window._manifest_option_changed(app, option))
        top.addWidget(self.option_combo)
        body.addLayout(top)
        status = QHBoxLayout()
        status.setSpacing(6)
        self.activation_badge = _label("", "activationBadge")
        self.activation_badge.setWordWrap(False)
        status.addWidget(self.activation_badge)
        self.meta = ElidingLabel("", "manifestMeta")
        self.meta.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        status.addWidget(self.meta, 1)
        body.addLayout(status)
        layout.addLayout(body, 1)
        self.refresh(window)

    def refresh(self, window) -> None:
        self.check.blockSignals(True)
        self.check.setChecked(self.item.checkState() == Qt.CheckState.Checked)
        self.check.blockSignals(False)
        selected = window._selected_option(self.app_name)
        self.option_combo.blockSignals(True)
        self.option_combo.setCurrentText(selected)
        self.option_combo.setToolTip(selected)
        self.option_combo.blockSignals(False)
        activation = window.service.state.snapshot().get("active", {}).get(self.app_name)
        active = activation.get("option") if isinstance(activation, dict) else activation
        specs = window.service.config.variables(self.app_name, selected)
        count = len(specs)
        invalid = sum(bool(window.service.selections.get(spec).error) for spec in specs)
        _activation_badge(self.activation_badge, active)
        status = f"{invalid} 处输入无效 · " if invalid else ""
        status += f"{active} · " if active else ""
        self.meta.setText(status + f"{count} 项变量")
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
        self.setWindowTitle("环境变量方案管理器" + (" · 演示模式" if demo else ""))
        self.resize(1190, 790)
        self.setMinimumSize(850, 560)
        apply_theme(QApplication.instance())

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_toolbar())
        workspace = QHBoxLayout()
        workspace.setContentsMargins(12, 12, 12, 12)
        workspace.setSpacing(12)
        workspace.addWidget(self._build_manifest())
        workspace.addWidget(self._build_inspector(), 1)
        root.addLayout(workspace, 1)
        root.addWidget(self._build_footer())
        self._render_apps()
        apps = service.config.apps()
        if apps:
            self._select_app(apps[0])
        self._refresh_pending()

    def _build_toolbar(self) -> QFrame:
        toolbar = QFrame()
        toolbar.setObjectName("workspaceHeader")
        bar = QHBoxLayout(toolbar)
        bar.setContentsMargins(12, 8, 12, 8)
        bar.setSpacing(8)
        brand = _label("环境方案", "appTitle")
        brand.setWordWrap(False)
        bar.addWidget(brand)
        self.breadcrumb = ElidingLabel("批量工作台", "muted")
        self.breadcrumb.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        bar.addWidget(self.breadcrumb, 1)
        self.mode_label = _label("演示模式" if self.demo else "Windows 环境", "muted")
        self.mode_label.setWordWrap(False)
        bar.addWidget(self.mode_label)
        self.system_settings_button = _button("环境变量", self._open_system_settings, "quiet")
        self.system_settings_button.setIcon(icon("sliders"))
        self.system_settings_button.setToolTip("打开 Windows 系统的环境变量设置")
        self.system_settings_button.setAccessibleName("打开 Windows 环境变量设置")
        bar.addWidget(self.system_settings_button)
        for text, symbol, callback in (("历史", "history", self._history), ("刷新", "refresh", self._reload)):
            button = _button(text, callback, "quiet")
            button.setIcon(icon(symbol))
            bar.addWidget(button)
        config = QPushButton("配置")
        config.setObjectName("quiet")
        config.setIcon(icon("code"))
        menu = QMenu(config)
        menu.addAction("可视化编辑", self._visual_config)
        menu.addAction("编辑原始 TOML", self._edit_config)
        menu.addSeparator()
        menu.addAction("打开配置文件", self._open_config)
        config.setMenu(menu)
        bar.addWidget(config)
        return toolbar

    def _build_manifest(self) -> QFrame:
        manifest = QFrame()
        manifest.setObjectName("manifest")
        manifest.setFixedWidth(300)
        side = QVBoxLayout(manifest)
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(8)
        heading = QHBoxLayout()
        heading.addWidget(_label("应用", "section"), 1)
        self.manifest_count = _label("", "selectionSummary")
        heading.addWidget(self.manifest_count)
        self.select_all_button = _button("全选", lambda: self._check_all(True), "quiet")
        self.clear_selection_button = _button("清空", lambda: self._check_all(False), "quiet")
        self.select_all_button.setToolTip("勾选所有应用，包含搜索中隐藏的应用")
        self.clear_selection_button.setToolTip("取消所有应用的勾选")
        heading.addWidget(self.select_all_button)
        heading.addWidget(self.clear_selection_button)
        side.addLayout(heading)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索应用")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icon("search", COLORS["muted"], 18), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setAccessibleName("搜索批量应用清单")
        self.search.textChanged.connect(self._render_apps)
        side.addWidget(self.search)
        self.app_list = ApplicationList()
        self.app_list.setObjectName("manifestList")
        self.app_list.setItemDelegate(ManifestDelegate(self.app_list))
        self.app_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.app_list.setAccessibleName("应用清单；空格勾选，名称查看详情，方案下拉切换")
        self.app_list.currentItemChanged.connect(lambda item, previous: self._select_app(item.data(Qt.ItemDataRole.UserRole)) if item else None)
        self.app_list.itemChanged.connect(self._check_app)
        side.addWidget(self.app_list, 1)
        self.search_empty = _label("没有匹配的应用，试试其他名称。", "muted")
        self.search_empty.setVisible(False)
        side.addWidget(self.search_empty)
        return manifest

    def _build_inspector(self) -> QFrame:
        detail = QFrame()
        detail.setObjectName("detailPane")
        layout = QVBoxLayout(detail)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        header = QFrame()
        header.setObjectName("inspectorHeader")
        context = QVBoxLayout(header)
        context.setContentsMargins(8, 8, 8, 8)
        context.setSpacing(4)
        heading = QHBoxLayout()
        heading.setSpacing(8)
        self.app_mark = QLabel()
        self.app_mark.setFixedSize(20, 20)
        heading.addWidget(self.app_mark)
        self.title = ElidingLabel("选择应用", "section")
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        heading.addWidget(self.title, 1)
        self.variable_count = _label("", "muted")
        self.variable_count.setWordWrap(False)
        heading.addWidget(self.variable_count)
        self.option_row = heading
        self.option_combo = OptionComboBox()
        self.option_combo.setObjectName("detailOption")
        self.option_combo.setMinimumWidth(80)
        self.option_combo.setMaximumWidth(280)
        self.option_combo.setAccessibleName("当前详情应用方案")
        self.option_combo.currentIndexChanged.connect(self._option_changed)
        heading.addWidget(self.option_combo)
        context.addLayout(heading)
        status = QHBoxLayout()
        status.setSpacing(6)
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
        pending.addWidget(_button("检查恢复", self._pending, "quiet"))
        layout.addWidget(self.pending_banner)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.card_panel = QWidget()
        self.card_layout = QVBoxLayout(self.card_panel)
        self.card_layout.setContentsMargins(0, 0, 0, 0)
        self.card_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.card_layout.setSpacing(4)
        self.scroll.setWidget(self.card_panel)
        layout.addWidget(self.scroll, 1)
        return detail

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
        self.batch_summary = ElidingLabel("", "selectionSummary")
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
        self.feedback = _label("", "muted")
        self.feedback.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        feedback.addWidget(self.feedback)
        actions.addWidget(self.feedback_panel, 1)
        self.deactivate_button = _button("停用当前", self._deactivate, "quiet")
        self.deactivate_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.deactivate_button.setToolTip("预览停用详情中当前应用的激活方案")
        actions.addWidget(self.deactivate_button)
        self.apply_button = _button("预览并应用", self._apply, "primary")
        self.apply_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.apply_button.setMinimumWidth(150)
        self.apply_button.setIcon(icon("arrow_right", COLORS["surface"], 18))
        actions.addWidget(self.apply_button)
        return footer

    def _render_apps(self) -> None:
        self.checked_apps.intersection_update(self.service.config.apps())
        self.app_list.blockSignals(True)
        self.app_list.clear()
        needle = self.search.text().casefold()
        for app in self.service.config.apps():
            if needle not in app.casefold():
                continue
            item = QListWidgetItem(app)
            item.setData(Qt.ItemDataRole.UserRole, app)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if app in self.checked_apps else Qt.CheckState.Unchecked)
            item.setToolTip(app + "\n勾选以批量应用；点击名称查看和修改方案")
            item.setSizeHint(QSize(280, 56))
            self.app_list.addItem(item)
            self.app_list.setItemWidget(item, ApplicationRow(self, item, app))
            if app == self.app_name:
                self.app_list.setCurrentItem(item)
        self.app_list.blockSignals(False)
        self.search_empty.setVisible(self.app_list.count() == 0)
        self._update_apply_availability()

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
        if app == self.app_name:
            self.option_combo.blockSignals(True)
            self.option_combo.setCurrentText(option)
            self.option_combo.blockSignals(False)
            self._render_variables()
        self._refresh_manifest_rows()
        self._update_apply_availability()

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
        self._render_variables()
        self._refresh_manifest_rows()

    def _select_app(self, app: str) -> None:
        self.app_name = app
        self.title.setText(app)
        self.title.setToolTip(app)
        self.breadcrumb.setText("批量工作台")
        self.app_mark.setPixmap(app_icon(app, 18).pixmap(18, 18))
        self.option_combo.blockSignals(True)
        self.option_combo.clear()
        if not app:
            self.option_combo.blockSignals(False)
            self._refresh_manifest_rows()
            self._render_variables()
            return
        self.option_combo.addItems(self.service.config.options(app))
        default = self.service.config.default_option(app) if hasattr(self.service.config, "default_option") else None
        for index in range(self.option_combo.count()):
            option = self.option_combo.itemText(index)
            self.option_combo.setItemData(index, option + ("（默认方案）" if option == default else ""), Qt.ItemDataRole.ToolTipRole)
            if option == default:
                self.option_combo.setItemIcon(index, icon("check", COLORS["accent"], 16))
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
        _clear(self.card_layout)
        self.cards = []
        if not self.app_name or not self.option_combo.currentText():
            self.variable_count.setText("")
            _activation_badge(self.activation_badge, None)
            self.active_label.setText("当前激活：无")
            self.deactivate_button.setEnabled(False)
            self._update_apply_availability()
            return
        option = self.option_combo.currentText()
        self.option_combo.setToolTip(option)
        activation = self.service.state.snapshot().get("active", {}).get(self.app_name)
        active = activation.get("option") if isinstance(activation, dict) else activation
        _activation_badge(self.activation_badge, active)
        self.active_label.setText("当前激活方案 " + active if active else "")
        if hasattr(self.service.config, "default_option"):
            separator = " · " if active else ""
            self.active_label.setText(self.active_label.text() + separator + "默认方案 " + self.service.config.default_option(self.app_name))
        if hasattr(self.service, "initialization_warning"):
            warning = self.service.initialization_warning(self.app_name)
            if warning:
                self.active_label.setText(self.active_label.text() + "\n" + warning)
        self.active_label.setToolTip(self.active_label.text())
        self.deactivate_button.setEnabled(bool(active))
        for spec in self.service.config.variables(self.app_name, option):
            card = VariableCard(self.service, spec)
            card.changed.connect(self._update_apply_availability)
            self.cards.append(card)
            self.card_layout.addWidget(card)
        previous = self.option_combo
        for card in self.cards:
            QWidget.setTabOrder(previous, card.selector)
            QWidget.setTabOrder(card.selector, card.editor)
            QWidget.setTabOrder(card.editor, card.full_value_button)
            previous = card.full_value_button
            if card.hidden:
                QWidget.setTabOrder(previous, card.reveal)
                previous = card.reveal
        QWidget.setTabOrder(previous, self.deactivate_button)
        QWidget.setTabOrder(self.deactivate_button, self.apply_button)
        self.variable_count.setText(str(len(self.cards)) + " 项")
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
            self.batch_summary.setText("未勾选应用 · 应用当前方案")
        self.manifest_count.setText(f"已选 {len(selected)} / {len(self.service.config.apps())}")
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
        self.blocking_reason.setVisible(bool(self._invalid_specs))
        self.locate_error_button.setVisible(bool(self._invalid_specs))
        self.apply_button.setEnabled(bool(specs) and not self._invalid_specs)
        self._refresh_manifest_rows()

    def _locate_invalid_input(self) -> None:
        if not self._invalid_specs:
            return
        spec = self._invalid_specs[0]
        visible_apps = {self.app_list.item(index).data(Qt.ItemDataRole.UserRole)
                        for index in range(self.app_list.count())}
        if spec.app not in visible_apps:
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
        if selected:
            self._preview(lambda orders=None: self.service.plan_batch(selected, orders))
        else:
            self._preview(lambda orders=None: self.service.plan(self.app_name, self.option_combo.currentText(), orders))

    def _deactivate(self) -> None:
        self._preview(lambda: self.service.plan_deactivate(self.app_name))

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
            if PreviewDialog(plan, self).exec() != QDialog.DialogCode.Accepted:
                return
            result = self.service.execute(plan)
            self.feedback.setText(_status_text(result.status) + "  ·  " + result.message)
            if not result.success or result.notification_error or result.errors:
                message = result.message
                if result.errors:
                    message += "\n" + "\n".join(result.errors)
                if result.notification_error:
                    message += "\n系统通知：" + result.notification_error
                QMessageBox.warning(self, _status_text(result.status), message)
            else:
                QMessageBox.information(self, _status_text(result.status), result.message)
            self._render_variables()
            self._refresh_pending()
        except Exception as exc:
            self.feedback.setText(str(exc))
            QMessageBox.warning(self, "无法执行", str(exc) + "\n请刷新并重新预览。")

    def _history(self) -> None:
        HistoryDialog(self.service, self._preview, self).exec()
        self._render_variables()

    def _edit_config(self) -> None:
        self._show_config_editor("source")

    def _visual_config(self) -> None:
        self._show_config_editor("visual")

    def _show_config_editor(self, initial_tab: str) -> None:
        try:
            dialog = ConfigEditorDialog(self.config_path, self.service, self, initial_tab=initial_tab)
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.saved:
                self._render_apps()
                apps = self.service.config.apps()
                self._select_app(self.app_name if self.app_name in apps else (apps[0] if apps else ""))
                self._refresh_pending()
                self.feedback.setText("配置已校验并保存")
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
        try:
            from .config import load_config
            self.service.reload(load_config(self.config_path))
            apps = self.service.config.apps()
            self._render_apps()
            self._select_app(self.app_name if self.app_name in apps else (apps[0] if apps else ""))
            self._refresh_pending()
            self.feedback.setText("配置与当前状态已刷新")
        except Exception as exc:
            QMessageBox.warning(self, "刷新失败", str(exc))
