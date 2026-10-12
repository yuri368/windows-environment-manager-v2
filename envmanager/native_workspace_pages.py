"""Read-only native Windows inventory and service-backed history workspaces."""
import re
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFrame, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QTableWidget, QTableWidgetItem, QVBoxLayout)
from .ui_controls import ActionButton


def _label(text, name="muted"):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setObjectName(name)
    return widget


def _table(headers):
    table = QTableWidget(0, len(headers))
    table.setObjectName("nativeTable")
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().hide()
    table.verticalHeader().setDefaultSectionSize(42)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setTabKeyNavigation(False)
    table.setWordWrap(False)
    table.setShowGrid(False)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    table.horizontalHeader().setStretchLastSection(True)
    return table


class WindowsEnvironmentPage(QFrame):
    def __init__(self, system_settings, show_value, parent=None):
        super().__init__(parent)
        self.setObjectName("nativePage")
        self.show_value = show_value
        self.records = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(12)
        heading = QHBoxLayout()
        heading.addWidget(_label("Windows 环境", "compactHeading"), 1)
        self.count = _label("")
        heading.addWidget(self.count)
        self.system_settings_button = ActionButton("打开系统环境变量")
        self.system_settings_button.clicked.connect(system_settings)
        heading.addWidget(self.system_settings_button)
        outer.addLayout(heading)
        self.description = _label("当前配置涉及的 Windows 目标 · 只读检测快照")
        self.description.setWordWrap(True)
        outer.addWidget(self.description)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索变量名称或当前值…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._render)
        filters.addWidget(self.search, 1)
        self.scope = QComboBox()
        self.scope.addItem("全部作用域", "all")
        self.scope.addItem("用户", "User")
        self.scope.addItem("系统", "System")
        self.scope.currentIndexChanged.connect(self._render)
        filters.addWidget(self.scope)
        outer.addLayout(filters)
        self.table = _table(("作用域", "变量名称", "状态", "注册表类型", "Windows 当前值"))
        self.table.itemActivated.connect(self._show_value)
        outer.addWidget(self.table, 1)
        actions = QHBoxLayout()
        self.empty = _label("")
        actions.addWidget(self.empty, 1)
        self.full_value = ActionButton("查看完整值")
        self.full_value.clicked.connect(self._show_value)
        self.table.itemSelectionChanged.connect(lambda: self.full_value.setEnabled(self.table.currentRow() >= 0))
        actions.addWidget(self.full_value)
        outer.addLayout(actions)

    def refresh(self, snapshot, error="", *, records=None, description=None):
        self.records = []
        if records is not None:
            self.records = [(state.scope, state.name, state, "") for state in records]
        elif snapshot is not None:
            self.records = [(target.scope, state.name, state, "") for target, state in snapshot.targets.items()]
            self.records += [(target.scope, target.normalized_name, None, problem.message)
                             for target, problem in snapshot.read_errors.items()]
        self.description.setText(description or
            ("当前配置涉及的 Windows 目标 · 只读检测快照" +
             (f" · 检测时间 {snapshot.scanned_at.astimezone().strftime('%H:%M:%S')}" if snapshot else "")))
        self._error = error
        self._render()

    def _render(self):
        needle = self.search.text().casefold()
        scope = self.scope.currentData()
        self.table.setRowCount(0)
        for record in sorted(self.records, key=lambda r: (r[0], r[1].casefold())):
            target_scope, name, state, error = record
            sensitive = bool(re.search(r"(?:KEY|TOKEN|PASSWORD|SECRET|PASSWD|CREDENTIAL)", name, re.I))
            actual = state.value if state is not None and state.exists else ""
            if scope not in ("all", target_scope) or (needle and needle not in name.casefold()
                                                      and (sensitive or needle not in actual.casefold())):
                continue
            exists = state is not None and state.exists
            status = "读取失败" if error else "存在" if exists else "不存在"
            reg_type = "—" if not exists else {1: "REG_SZ", 2: "REG_EXPAND_SZ"}.get(state.registry_type, str(state.registry_type))
            value = error if error else "（不存在）" if not exists else "（敏感值已隐藏）" if sensitive else actual or "（空字符串）"
            row = self.table.rowCount()
            self.table.insertRow(row)
            for column, text in enumerate(("用户" if target_scope == "User" else "系统", name, status, reg_type, value)):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                item.setData(Qt.ItemDataRole.UserRole, (state, sensitive))
                self.table.setItem(row, column, item)
        self.count.setText(f"{self.table.rowCount()} 项变量")
        self.empty.setText(getattr(self, "_error", "") or ("没有符合筛选的变量。" if not self.table.rowCount()
                                                        else "双击记录或按 Enter 查看完整原始值。"))
        self.full_value.setEnabled(self.table.currentRow() >= 0)

    def _show_value(self, *_):
        row = self.table.currentRow()
        if row < 0:
            return
        state, sensitive = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if state is not None and state.exists:
            self.show_value(state.value, sensitive)


class HistoryPage(QFrame):
    def __init__(self, service, preview, format_time, format_apps, format_status, parent=None):
        super().__init__(parent)
        self.setObjectName("nativePage")
        self.service, self.preview = service, preview
        self.format_time, self.format_apps, self.format_status = format_time, format_apps, format_status
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(12)
        heading = QHBoxLayout()
        heading.addWidget(_label("操作历史", "compactHeading"), 1)
        self.count = _label("")
        heading.addWidget(self.count)
        outer.addLayout(heading)
        outer.addWidget(_label("真实执行记录 · 恢复前先生成预览，并核对 Windows 当前状态。"))
        self.table = _table(("时间", "应用 / 方案", "操作", "状态", "说明"))
        self.table.itemSelectionChanged.connect(self._selected)
        outer.addWidget(self.table, 1)
        self.detail = _label("")
        self.detail.setWordWrap(True)
        outer.addWidget(self.detail)
        actions = QHBoxLayout()
        actions.addStretch()
        self.restore_button = ActionButton("预览恢复")
        self.restore_button.setObjectName("primary")
        self.restore_button.clicked.connect(self._restore)
        actions.addWidget(self.restore_button)
        outer.addLayout(actions)
        self._selected()

    def refresh(self):
        current = self.table.item(self.table.currentRow(), 0)
        selected_id = current.data(Qt.ItemDataRole.UserRole) if current else None
        self.table.setRowCount(0)
        for entry in self.service.history():
            row = self.table.rowCount()
            self.table.insertRow(row)
            applications = self.format_apps(entry.get("applications"), entry.get("app", ""), entry.get("option", ""))
            action = {"apply": "应用", "switch": "切换方案", "remove": "撤销配置", "restore": "历史恢复",
                      "recover": "恢复操作"}.get(entry.get("action"), entry.get("action", "—"))
            texts = (self.format_time(entry.get("created_at", "")), applications, action,
                     self.format_status(entry.get("status", "")), entry.get("message", ""))
            for col, text in enumerate(texts):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                item.setData(Qt.ItemDataRole.UserRole, entry["id"])
                self.table.setItem(row, col, item)
            if entry["id"] == selected_id:
                self.table.selectRow(row)
        self.count.setText(f"{self.table.rowCount()} 条记录")
        self._selected()

    def _selected(self):
        row = self.table.currentRow()
        self.restore_button.setEnabled(row >= 0)
        self.detail.setText("选择记录以预览恢复。" if row < 0 and self.table.rowCount() else
                            "尚无操作历史。" if row < 0 else
                            " · ".join(self.table.item(row, col).text() for col in range(self.table.columnCount())))

    def _restore(self):
        row = self.table.currentRow()
        if row < 0:
            return
        record_id = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        self.preview(lambda: self.service.plan_restore(record_id))
        self.refresh()
