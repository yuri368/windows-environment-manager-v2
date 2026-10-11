"""Visual and source editor for one TOML configuration draft."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, QSize, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QPushButton, QScrollArea, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem,
    QSizePolicy, QHeaderView, QListWidget, QListWidgetItem,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .config_editing import ConfigEditingSession
from .highlighting import DiffHighlighter, TomlHighlighter
from .icons import icon
from .models import Choice, ConflictError, VariableSpec
from .theme import COLORS, apply_theme


_MODES = (
    ("替换完整值", "replace"), ("置于列表前端", "+append"),
    ("置于列表末尾", "-append"), ("自定义排序", "append"),
)


def _caption(text: str, name: str = "") -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        label.setObjectName(name)
    label.setWordWrap(True)
    return label


def _button(text: str, callback, name: str = "") -> QPushButton:
    button = QPushButton(text)
    if name:
        button.setObjectName(name)
    button.clicked.connect(callback)
    return button


def _field_label(text: str) -> QLabel:
    label = _caption(text, "meta")
    return label


def _delete_button(text: str, callback) -> QPushButton:
    button = _button(text, callback, "danger")
    button.setStyleSheet("QPushButton#danger { border-color: transparent; }")
    return button


def _escaped_text(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)[1:-1].replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def _choice_summary(value: str) -> str:
    return "（空字符串）" if value == "" else _escaped_text(value) if any(
        char in value for char in ("\r", "\n", "\u2028", "\u2029")) else value


class ExactTextEditor(QWidget):
    """Preserves CR and mixed line endings through an explicit escaped view."""

    changed = Signal()

    def __init__(self, *, height: int = 100, parent=None):
        super().__init__(parent)
        self._setting = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(5)
        self.editor = QPlainTextEdit()
        self.editor.setObjectName("code")
        self.editor.setFixedHeight(height)
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        outer.addWidget(self.editor)
        self.escape = QCheckBox("转义输入")
        self.escape.setToolTip("用 \\r、\\n 等转义文本精确保留换行；切换只改变显示方式。")
        outer.addWidget(self.escape)
        self.editor.textChanged.connect(self._changed)
        self.escape.toggled.connect(self._toggle_escape)

    def _changed(self) -> None:
        if not self._setting:
            self.changed.emit()

    def set_exact_text(self, value: str) -> None:
        self._setting = True
        try:
            escaped = any(char in value for char in ("\r", "\u2028", "\u2029"))
            self.escape.setChecked(escaped)
            self.editor.setPlainText(_escaped_text(value) if escaped else value)
        finally:
            self._setting = False

    def exact_text(self) -> str:
        text = self.editor.toPlainText()
        if not self.escape.isChecked():
            return text
        try:
            return json.loads('"' + text + '"')
        except json.JSONDecodeError as exc:
            raise ValueError(f"转义文本无效：{exc.msg}") from exc

    def _toggle_escape(self, checked: bool) -> None:
        if self._setting:
            return
        try:
            if checked:
                value = self.editor.toPlainText()
                display = _escaped_text(value)
            else:
                display = self.exact_text_from_escaped(self.editor.toPlainText())
                if any(char in display for char in ("\r", "\u2028", "\u2029")):
                    raise ValueError("此值包含特殊换行符，请保持转义模式以避免被文本控件转换")
            self._setting = True
            self.editor.setPlainText(display)
        except ValueError as exc:
            self._setting = True
            self.escape.setChecked(not checked)
            QMessageBox.warning(self, "无法切换显示模式", str(exc))
        finally:
            self._setting = False
        self.changed.emit()

    @staticmethod
    def exact_text_from_escaped(text: str) -> str:
        try:
            return json.loads('"' + text + '"')
        except json.JSONDecodeError as exc:
            raise ValueError(f"转义文本无效：{exc.msg}") from exc


class ExactValueDialog(QDialog):
    def __init__(self, value: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑候选值")
        self.resize(650, 330)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 20)
        outer.setSpacing(14)
        outer.addWidget(_caption("编辑候选值", "section"))
        outer.addWidget(_caption("空字符串是有效候选值；原始空格和换行会原样保存。", "muted"))
        self.value_editor = ExactTextEditor(height=170)
        self.value_editor.set_exact_text(value)
        outer.addWidget(self.value_editor)
        self.error = _caption("", "error")
        outer.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _accept(self) -> None:
        try:
            self.value_editor.exact_text()
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        self.accept()

    def value(self) -> str:
        return self.value_editor.exact_text()


class ConfigEditorDialog(QDialog):
    """One shared session for structured edits, raw text, diff, and final save."""

    def __init__(self, path: Path, service, parent=None, initial_tab: str = "visual"):
        super().__init__(parent)
        self.path = Path(path)
        self.service = service
        self.session = ConfigEditingSession(self.path)
        self.saved = False
        self._syncing = False
        self._source_last_text = ""
        self._tab_index = 0
        self._selection_token = None
        self.navigation_app = ""
        self.navigation_option = ""
        self._original: VariableSpec | None = None
        self._new_form = False
        self._form_active = False
        self._form_base: tuple | None = None
        self.setWindowTitle("配置编辑器 · " + self.path.name)
        self.resize(1180, 800)
        apply_theme(QApplication.instance())

        self.setMinimumSize(760, 600)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())
        self.tabs = QTabWidget()
        self.tabs.setObjectName("editorTabs")
        self.visual = self._build_visual()
        self.tabs.addTab(self.visual, "可视化")
        self.tabs.addTab(self._build_source(), "原始 TOML")
        self.tabs.addTab(self._build_diff(), "保存差异")
        self.tabs.currentChanged.connect(self._switch_tab)
        root.addWidget(self.tabs, 1)
        self.error = _caption("", "error")
        self.error.setContentsMargins(12, 8, 12, 8)
        self.error.setVisible(False)
        root.addWidget(self.error)
        root.addWidget(self._build_footer())
        self._refresh_all()
        if initial_tab == "source":
            self.tabs.setCurrentIndex(1)
        elif initial_tab == "diff":
            self.tabs.setCurrentIndex(2)

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setObjectName("editorHeader")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(12)
        layout.addWidget(_caption("配置编辑器", "compactHeading"))
        file_label = _caption(self.path.name, "contextPath")
        file_label.setToolTip(str(self.path))
        layout.addWidget(file_label)
        layout.addStretch()
        self.dirty_label = _caption("", "editorState")
        layout.addWidget(self.dirty_label)
        self.undo_button = _button("", self._undo, "iconOnly")
        self.undo_button.setIcon(icon("undo", COLORS["muted"], 18))
        self.undo_button.setToolTip("撤销草稿修改")
        self.undo_button.setAccessibleName("撤销草稿修改")
        self.redo_button = _button("", self._redo, "iconOnly")
        self.redo_button.setIcon(icon("redo", COLORS["muted"], 18))
        self.redo_button.setToolTip("重做草稿修改")
        self.redo_button.setAccessibleName("重做草稿修改")
        layout.addWidget(self.undo_button)
        layout.addWidget(self.redo_button)
        return header

    def _build_source(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 8, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(_caption("直接编辑配置文本；语法与变量规则实时校验。", "muted"))
        self.source = QPlainTextEdit()
        self.source.setObjectName("code")
        self.source.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.source.setTabStopDistance(self.source.fontMetrics().horizontalAdvance(" ") * 4)
        self.source_highlighter = TomlHighlighter(self.source.document())
        self.source.textChanged.connect(self._source_changed)
        layout.addWidget(self.source, 1)
        return page

    def _build_diff(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 8, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(_caption("当前草稿相对磁盘文件的变化。保存前需确认差异。", "muted"))
        self.diff_view = QPlainTextEdit()
        self.diff_view.setObjectName("code")
        self.diff_view.setReadOnly(True)
        self.diff_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.diff_highlighter = DiffHighlighter(self.diff_view.document())
        layout.addWidget(self.diff_view, 1)
        return page

    def _build_footer(self) -> QFrame:
        footer = QFrame()
        footer.setObjectName("footer")
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)
        layout.addWidget(_button("校验草稿", self._validate, "quiet"))
        layout.addStretch()
        layout.addWidget(_button("取消", self.reject, "quiet"))
        self.save_button = _button("查看差异并保存", self._save, "primary")
        layout.addWidget(self.save_button)
        return footer

    def _build_visual(self) -> QWidget:
        visual = QWidget()
        row = QHBoxLayout(visual)
        row.setContentsMargins(12, 8, 12, 12)
        row.setSpacing(0)
        self.workspace_splitter = QSplitter()
        self.workspace_splitter.setHandleWidth(12)
        self.workspace_splitter.setChildrenCollapsible(False)
        row.addWidget(self.workspace_splitter)
        self.workspace_splitter.addWidget(self._build_browser())
        self.workspace_splitter.addWidget(self._build_workspace())
        self.workspace_splitter.setSizes([240, 900])
        self.workspace_splitter.setStretchFactor(0, 0)
        self.workspace_splitter.setStretchFactor(1, 1)
        return visual

    def _build_browser(self) -> QFrame:
        browser = QFrame()
        browser.setObjectName("editorBrowser")
        browser.setMinimumWidth(216)
        browser.setMaximumWidth(272)
        layout = QVBoxLayout(browser)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        heading = QHBoxLayout()
        heading.addWidget(_caption("配置", "section"))
        heading.addStretch()
        layout.addLayout(heading)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索全部配置")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setAccessibleName("搜索应用、方案或变量")
        self.search_edit.textChanged.connect(self._filter_navigation)
        layout.addWidget(self.search_edit)

        self.navigation = QWidget()
        nav = QVBoxLayout(self.navigation)
        nav.setContentsMargins(0, 0, 0, 0)
        nav.setSpacing(8)
        app_header = QHBoxLayout()
        app_header.setSpacing(4)
        self.app_overview_button = _button("应用", self._application_overview, "quiet")
        app_header.addWidget(self.app_overview_button)
        app_header.addStretch()
        self.new_app_button = _button("新增", lambda: self._new_group("app"), "quiet")
        self.new_app_button.setAccessibleName("新增应用")
        self.new_app_button.setToolTip("新增应用，填写名称与第一条变量")
        app_header.addWidget(self.new_app_button)
        self.delete_app_button = _delete_button("删除", self._delete_app)
        app_header.addWidget(self.delete_app_button)
        nav.addLayout(app_header)
        self.app_list = QListWidget()
        self.app_list.setObjectName("editorApps")
        self.app_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.app_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.app_list.currentItemChanged.connect(self._navigate_application)
        nav.addWidget(self.app_list)
        option_header = QHBoxLayout()
        option_header.setSpacing(4)
        self.option_overview_button = _button("方案", self._option_overview, "quiet")
        option_header.addWidget(self.option_overview_button)
        option_header.addStretch()
        self.new_option_button = _button("新增", lambda: self._new_group("option"), "quiet")
        self.new_option_button.setAccessibleName("在当前应用中新增方案")
        option_header.addWidget(self.new_option_button)
        self.delete_option_button = _delete_button("删除", self._delete_option)
        option_header.addWidget(self.delete_option_button)
        nav.addLayout(option_header)
        self.nav_option_combo = QComboBox()
        self.nav_option_combo.setMinimumContentsLength(8)
        self.nav_option_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.nav_option_combo.currentIndexChanged.connect(self._navigate_option)
        nav.addWidget(self.nav_option_combo)
        self.set_default_button = _button("设为默认方案", self._set_default_option, "quiet")
        self.set_default_button.setToolTip("先更新到草稿；查看差异并保存后生效")
        option_actions = QHBoxLayout()
        option_actions.setSpacing(4)
        option_actions.addWidget(self.set_default_button)
        option_actions.addStretch()
        nav.addLayout(option_actions)
        variable_header = QHBoxLayout()
        variable_header.setSpacing(4)
        variable_header.addWidget(_field_label("变量"))
        variable_header.addStretch()
        self.new_record_button = _button("新增", self._new_record, "quiet")
        self.new_record_button.setAccessibleName("新增变量")
        variable_header.addWidget(self.new_record_button)
        self.delete_record_button = _delete_button("删除", self._delete_record)
        variable_header.addWidget(self.delete_record_button)
        nav.addLayout(variable_header)
        self.record_list = self._navigation_table("editorRecords", ("变量名", "范围"))
        self.record_list.currentCellChanged.connect(self._navigate_record)
        nav.addWidget(self.record_list, 1)
        self.search_results = self._navigation_table("editorSearchResults", ("变量 / 位置", "范围"))
        self.search_results.currentCellChanged.connect(self._navigate_search_result)
        self.search_results.setVisible(False)
        nav.addWidget(self.search_results, 1)
        self.search_status = _caption("", "muted")
        self.search_status.setVisible(False)
        nav.addWidget(self.search_status)
        layout.addWidget(self.navigation, 1)
        return browser

    @staticmethod
    def _navigation_table(name: str, headers: tuple[str, str]) -> QTableWidget:
        table = QTableWidget(0, 2)
        table.setObjectName(name)
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(30)
        table.setShowGrid(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(1, 48)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return table

    def _build_workspace(self) -> QScrollArea:
        panel = QScrollArea()
        self.record_scroll = panel
        panel.setObjectName("editorScroll")
        panel.setWidgetResizable(True)
        panel.setFrameShape(QFrame.Shape.NoFrame)
        panel.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QFrame()
        content.setObjectName("editorWorkspace")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)
        self.form_heading = _caption("配置概览", "compactHeading")
        self.form_context = _caption("", "contextPath")
        layout.addWidget(self.form_heading)
        layout.addWidget(self.form_context)
        self.empty_state = self._build_overview()
        layout.addWidget(self.empty_state, 1)
        self.form = QWidget()
        self.form_layout = QGridLayout(self.form)
        self.form_layout.setContentsMargins(0, 0, 0, 0)
        self.form_layout.setHorizontalSpacing(12)
        self.form_layout.setVerticalSpacing(12)
        self.value_pane = self._build_value_pane()
        self.inspector = self._build_inspector()
        self._compact_form = None
        self._reflow_form()
        layout.addWidget(self.form)
        self.form_error = _caption("", "error")
        self.form_error.setVisible(False)
        layout.addWidget(self.form_error)
        self._form_error_layout = layout
        self._record_layout = layout
        record_actions = QHBoxLayout()
        self.form_state = _caption("", "muted")
        record_actions.addWidget(self.form_state, 1)
        self.update_button = _button("更新此变量", self._commit_form, "secondary")
        self.update_button.setToolTip("将当前变量更新到配置草稿；尚不保存文件")
        record_actions.addWidget(self.update_button)
        layout.insertLayout(2, record_actions)
        layout.addStretch(1)
        panel.setWidget(content)
        return panel

    def _build_overview(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.overview_summary = _caption("", "muted")
        summary_row = QHBoxLayout()
        summary_row.setSpacing(12)
        summary_row.addWidget(self.overview_summary, 1)
        # ui imports this editor; reuse its measured/eliding selector only after
        # both modules have finished defining their classes.
        from .ui import OptionComboBox
        self.overview_option_combo = OptionComboBox()
        self.overview_option_combo.setMinimumWidth(100)
        self.overview_option_combo.setMaximumWidth(220)
        self.overview_option_combo.setMinimumContentsLength(12)
        self.overview_option_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.overview_option_combo.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.overview_option_combo.setAccessibleName("选择概览方案")
        self.overview_option_combo.currentIndexChanged.connect(self._navigate_overview_option)
        summary_row.addWidget(self.overview_option_combo)
        layout.addLayout(summary_row)
        self.overview = QTreeWidget()
        self.overview.setObjectName("configOverview")
        self.overview.setHeaderLabels(("变量 / 方案", "作用域", "默认值"))
        self.overview.setRootIsDecorated(False)
        self.overview.setUniformRowHeights(True)
        self.overview.setColumnWidth(0, 200)
        self.overview.setColumnWidth(1, 90)
        self.overview.header().setStretchLastSection(True)
        self.overview.itemActivated.connect(self._open_overview_record)
        self.overview.itemClicked.connect(self._open_overview_record)
        layout.addWidget(self.overview, 1)
        return page

    def _build_value_pane(self) -> QFrame:
        pane = QFrame()
        pane.setObjectName("editorValuePane")
        pane.setMinimumWidth(0)
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(_caption("默认值", "section"))
        self.default_editor = ExactTextEditor(height=72)
        self.default_editor.changed.connect(self._form_changed)
        layout.addWidget(self.default_editor)
        self.default_editor.setToolTip("追加模式用分号分隔列表；原始空格和变量引用原样保留。")
        choices_heading = QHBoxLayout()
        choices_heading.setContentsMargins(0, 8, 0, 0)
        choices_heading.addWidget(_caption("候选值", "section"))
        choices_heading.addStretch()
        add = _button("添加", self._add_choice, "quiet")
        add.setIcon(icon("plus", COLORS["accent"], 16))
        choices_heading.addWidget(add)
        layout.addLayout(choices_heading)
        self.choices_empty = _caption("暂无候选", "muted")
        layout.addWidget(self.choices_empty)
        self.choices = QTableWidget(0, 2)
        self.choices.setObjectName("choicesTable")
        self.choices.setHorizontalHeaderLabels(("显示名称", "实际值"))
        self.choices.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.choices.setColumnWidth(0, 140)
        self.choices.horizontalHeader().setStretchLastSection(True)
        self.choices.verticalHeader().hide()
        self.choices.verticalHeader().setDefaultSectionSize(30)
        self.choices.setAlternatingRowColors(True)
        self.choices.setShowGrid(False)
        self.choices.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.choices.model().rowsInserted.connect(self._resize_choices)
        self.choices.model().rowsRemoved.connect(self._resize_choices)
        self._resize_choices()
        self.choices.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.choices.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.choices.itemChanged.connect(self._form_changed)
        self.choices.cellDoubleClicked.connect(self._edit_choice_cell)
        layout.addWidget(self.choices)
        self.choice_actions = QWidget()
        actions = QHBoxLayout(self.choice_actions)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.addWidget(_button("编辑选中值", lambda: self._edit_choice_value(), "quiet"))
        actions.addWidget(_button("删除选中项", self._delete_choice, "quiet"))
        actions.addStretch()
        layout.addWidget(self.choice_actions)
        self._resize_choices()
        return pane

    def _build_inspector(self) -> QFrame:
        inspector = QFrame()
        inspector.setObjectName("editorInspector")
        inspector.setMinimumWidth(0)
        layout = QVBoxLayout(inspector)
        layout.setContentsMargins(8, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(_caption("属性", "meta"))
        self.app_combo = QComboBox()
        self.option_combo = QComboBox()
        for combo in (self.app_combo, self.option_combo):
            combo.setEditable(True)
            combo.setMinimumWidth(0)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(8)
            combo.currentTextChanged.connect(self._form_changed)
            combo.lineEdit().textEdited.connect(self._form_changed)
        self.app_combo.currentTextChanged.connect(self._update_options)
        self.name_edit = QLineEdit()
        self.name_edit.textChanged.connect(self._form_changed)
        self.scope_combo = QComboBox()
        self.scope_combo.addItem("用户（User）", "User")
        self.scope_combo.addItem("系统（System）", "System")
        self.scope_combo.currentIndexChanged.connect(self._form_changed)
        self.mode_combo = QComboBox()
        for label, mode in _MODES:
            self.mode_combo.addItem(label, mode)
        self.mode_combo.currentIndexChanged.connect(self._form_changed)
        self._inspector_fields = {}
        for text, widget in (("应用", self.app_combo), ("方案", self.option_combo),
                             ("变量名", self.name_edit), ("作用域", self.scope_combo),
                             ("操作模式", self.mode_combo)):
            field = QVBoxLayout()
            field.setSpacing(4)
            field.addWidget(_field_label(text))
            field.addWidget(widget)
            self._inspector_fields[widget] = field
            layout.addLayout(field)
        self.editable_check = QCheckBox("允许自定义输入")
        self.editable_check.toggled.connect(self._form_changed)
        layout.addWidget(self.editable_check)
        self.app_combo.setToolTip("可输入新应用名称，将当前变量移动到该应用。")
        self.option_combo.setToolTip("可输入新方案名称，将当前变量移动到该方案。")
        layout.addStretch()
        return inspector

    def _reflow_form(self) -> None:
        compact = self.width() < 1020
        if compact == self._compact_form:
            return
        self._compact_form = compact
        self.form_layout.removeWidget(self.value_pane)
        self.form_layout.removeWidget(self.inspector)
        self.form_layout.addWidget(self.value_pane, 0, 0)
        self.form_layout.addWidget(self.inspector, 1 if compact else 0, 0 if compact else 1)
        self.form_layout.setColumnStretch(0, 1)
        self.form_layout.setColumnStretch(1, 0)
        self.inspector.setMaximumWidth(16777215 if compact else 244)
        self.inspector.setMinimumWidth(0 if compact else 220)
        self.form_layout.setRowStretch(0, 0)
        self.form_layout.setRowStretch(1, 0)
        self.form_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.form_layout.setAlignment(self.value_pane, Qt.AlignmentFlag.AlignTop)
        self.form_layout.setAlignment(self.inspector, Qt.AlignmentFlag.AlignTop)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "form_layout"):
            self._reflow_form()

    def _all_records(self) -> tuple[VariableSpec, ...]:
        doc = self.session.document
        return tuple(spec for app, option in doc.groups() for spec in doc.records(app, option)) if doc else ()

    def _filter_navigation(self, text: str = "") -> None:
        query = text.casefold()
        was_syncing = self._syncing
        self._syncing = True
        try:
            self.search_results.setRowCount(0)
            for spec in self._all_records():
                if not query or query not in f"{spec.app} {spec.option} {spec.name} {spec.scope}".casefold():
                    continue
                row = self.search_results.rowCount()
                self.search_results.insertRow(row)
                name = QTableWidgetItem(f"{spec.name}\n{spec.app} / {spec.option}")
                name.setData(Qt.ItemDataRole.UserRole, spec)
                name.setToolTip(f"{spec.app} / {spec.option} / {spec.name}")
                self.search_results.setItem(row, 0, name)
                self.search_results.setItem(row, 1, QTableWidgetItem("用户" if spec.scope == "User" else "系统"))
                self.search_results.setRowHeight(row, 44)
                if self._selection_token and self._selection_token[0] == "record" and self._selection_token[1].key == spec.key:
                    self.search_results.setCurrentCell(row, 0)
            count = self.search_results.rowCount()
            self.record_list.setVisible(not bool(query))
            self.search_results.setVisible(bool(query))
            self.search_status.setVisible(bool(query))
            self.search_status.setText(f"{count} 条匹配变量" if count else "未找到匹配配置")
        finally:
            self._syncing = was_syncing
        if hasattr(self, "delete_record_button"):
            self._update_navigation_actions()

    def _resize_choices(self, *_args) -> None:
        header = max(26, self.choices.horizontalHeader().sizeHint().height())
        rows = self.choices.rowCount()
        self.choices.setFixedHeight(header + min(6, rows) * 30 + 4)
        self.choices.setVisible(rows > 0)
        self.choices_empty.setVisible(rows == 0)
        if hasattr(self, "choice_actions"):
            self.choice_actions.setVisible(rows > 0)

    def _show_overview(self) -> None:
        token = self.selected_token()
        doc = self.session.document
        self.overview.clear()
        if doc is None:
            return
        if token and token[0] == "option":
            app, option = token[1], token[2]
            groups = [(app, option)]
            self.form_heading.setText(option)
            self.form_context.setText(app + " / " + option)
        elif token and token[0] == "app":
            app = token[1]
            groups = [(a, o) for a, o in doc.groups() if a == app]
            self.form_heading.setText(app)
            self.form_context.setText(f"默认方案：{doc.default_option(app)}")
        else:
            groups = list(doc.groups())
            self.form_heading.setText("配置概览")
            self.form_context.setText(self.path.name)
        count = 0
        for app, option in groups:
            for spec in doc.records(app, option):
                item = QTreeWidgetItem([f"{spec.name} / {option}", spec.scope, _choice_summary(spec.default_value)])
                item.setData(0, Qt.ItemDataRole.UserRole, ("record", spec))
                item.setToolTip(2, _choice_summary(spec.default_value))
                self.overview.addTopLevelItem(item)
                count += 1
        self.overview_summary.setText(f"{len(groups)} 个方案 · {count} 条变量。选择变量进入编辑。" if count else
                                      "尚无变量。新增变量时可同时创建应用和方案。")

    def _open_overview_record(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        token = item.data(0, Qt.ItemDataRole.UserRole)
        if token and token[0] == "record":
            self.select_record(token[1])

    def _sync_overview_options(self) -> None:
        doc = self.session.document
        options = [option for app, option in doc.groups() if app == self.navigation_app] if doc else []
        combo = self.overview_option_combo
        combo.clear()
        combo.addItem("全部方案" if options else "暂无方案", None)
        for option in options:
            label = option + (" · 默认" if doc.default_option(self.navigation_app) == option else "")
            combo.addItem(label, option)
            combo.setItemData(combo.count() - 1, label, Qt.ItemDataRole.ToolTipRole)
        token = self.selected_token()
        selected = self.navigation_option if token and token[0] in ("option", "record") else None
        combo.setCurrentIndex(max(0, combo.findData(selected)))
        combo.setEnabled(bool(options))

    def _navigate_overview_option(self, _index: int) -> None:
        if self._syncing or not self.overview_option_combo.isEnabled():
            return
        option = self.overview_option_combo.currentData()
        if option is None:
            self.select_application(self.navigation_app)
        else:
            self.select_option(option)

    def _new_group(self, kind: str) -> None:
        if not self._new_record():
            return
        if kind == "app":
            self.app_combo.setCurrentText("")
            self.option_combo.setCurrentText("Default")
            self.app_combo.setFocus()
            self.form_heading.setText("新增应用")
        else:
            self.option_combo.setCurrentText("")
            self.option_combo.setFocus()
            self.form_heading.setText("新增方案")
        self.form_context.setText("填写名称与第一条变量")

    def selected_token(self):
        return self._selection_token

    def select_application(self, app: str) -> bool:
        doc = self.session.document
        if doc is None or app not in {a for a, _ in doc.groups()}:
            return False
        return self._select_token(("app", app))

    def select_option(self, option: str) -> bool:
        doc = self.session.document
        if doc is None or (self.navigation_app, option) not in doc.groups():
            return False
        return self._select_token(("option", self.navigation_app, option))

    def select_record(self, spec_or_key: VariableSpec | str) -> bool:
        key = spec_or_key.key if isinstance(spec_or_key, VariableSpec) else spec_or_key
        spec = next((spec for spec in self._all_records() if spec.key == key), None)
        return self._select_token(("record", spec)) if spec is not None else False

    def _select_token(self, token) -> bool:
        # Selecting the current record must not reload or discard its pending text.
        if (token == self._selection_token and not self._new_form):
            return True
        if not self._guard_form():
            self._render_navigation()
            return False
        doc = self.session.document
        groups = doc.groups() if doc else ()
        if ((token[0] == "app" and token[1] not in {app for app, _ in groups}) or
                (token[0] == "option" and (token[1], token[2]) not in groups)):
            self._render_navigation()
            return False
        if token[0] == "record":
            spec = next((spec for spec in self._all_records() if spec.key == token[1].key), None)
            if spec is None:
                self._render_navigation()
                return False
            token = ("record", spec)
        self._set_selection(token)
        return True

    def _set_selection(self, token) -> None:
        self._selection_token = token
        doc = self.session.document
        if token and token[0] == "record":
            self.navigation_app, self.navigation_option = token[1].app, token[1].option
        elif token and token[0] == "option":
            self.navigation_app, self.navigation_option = token[1], token[2]
        elif token and token[0] == "app":
            previous_app = self.navigation_app
            self.navigation_app = token[1]
            options = [o for a, o in doc.groups() if a == self.navigation_app] if doc else []
            if previous_app != self.navigation_app or self.navigation_option not in options:
                self.navigation_option = doc.default_option(self.navigation_app) if options else ""
        else:
            self.navigation_app = self.navigation_option = ""
        self._render_navigation()
        if token and token[0] == "record":
            self._load_spec(token[1])
        else:
            self._clear_form()
        self._update_status()

    def _render_navigation(self) -> None:
        was_syncing = self._syncing
        self._syncing = True
        try:
            doc = self.session.document
            groups = doc.groups() if doc else ()
            apps = list(dict.fromkeys(a for a, _ in groups))
            self.app_list.clear()
            for app in apps:
                item = QListWidgetItem(app)
                item.setSizeHint(QSize(0, 30))
                item.setData(Qt.ItemDataRole.UserRole, app)
                self.app_list.addItem(item)
                if app == self.navigation_app:
                    self.app_list.setCurrentItem(item)
            self.app_list.setMinimumHeight(34)
            self.app_list.setMaximumHeight(max(1, min(6, len(apps))) * 30 + 4)
            self.nav_option_combo.clear()
            for app, option in groups:
                if app == self.navigation_app:
                    label = option + (" · 默认" if doc.default_option(app) == option else "")
                    self.nav_option_combo.addItem(label, option)
            self.nav_option_combo.setCurrentIndex(self.nav_option_combo.findData(self.navigation_option))
            self._sync_overview_options()
            self.record_list.setRowCount(0)
            if doc and (self.navigation_app, self.navigation_option) in groups:
                for spec in doc.records(self.navigation_app, self.navigation_option):
                    row = self.record_list.rowCount()
                    self.record_list.insertRow(row)
                    item = QTableWidgetItem(spec.name)
                    item.setData(Qt.ItemDataRole.UserRole, spec)
                    item.setToolTip(spec.name)
                    self.record_list.setItem(row, 0, item)
                    scope = QTableWidgetItem("用户" if spec.scope == "User" else "系统")
                    scope.setToolTip(spec.scope)
                    scope.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    self.record_list.setItem(row, 1, scope)
                    if self._selection_token and self._selection_token[0] == "record" and self._selection_token[1].key == spec.key:
                        self.record_list.setCurrentCell(row, 0)
            self._filter_navigation(self.search_edit.text())
        finally:
            self._syncing = was_syncing

    def _navigate_application(self, current: QListWidgetItem | None, _previous=None) -> None:
        if not self._syncing and current is not None:
            self.select_application(current.data(Qt.ItemDataRole.UserRole))

    def _navigate_option(self, _index: int) -> None:
        if not self._syncing:
            self.select_option(self.nav_option_combo.currentData())

    def _navigate_record(self, row: int, _column: int, *_previous) -> None:
        if not self._syncing and row >= 0:
            self.select_record(self.record_list.item(row, 0).data(Qt.ItemDataRole.UserRole))

    def _navigate_search_result(self, row: int, _column: int, *_previous) -> None:
        if not self._syncing and row >= 0:
            self.select_record(self.search_results.item(row, 0).data(Qt.ItemDataRole.UserRole))

    def _application_overview(self) -> None:
        self.select_application(self.navigation_app)

    def _option_overview(self) -> None:
        self.select_option(self.navigation_option)

    def _refresh_navigation(self, select_key: str | None = None) -> None:
        records = self._all_records()
        doc = self.session.document
        previous = self._selection_token
        key = select_key or (previous[1].key if previous and previous[0] == "record" else None)
        spec = next((spec for spec in records if spec.key == key), None)
        if spec is not None:
            token = ("record", spec)
        elif previous and previous[0] == "app" and doc and previous[1] in {a for a, _ in doc.groups()}:
            token = previous
        elif previous and previous[0] == "option" and doc and (previous[1], previous[2]) in doc.groups():
            token = previous
        else:
            spec = next((spec for spec in records if spec.app == self.navigation_app and spec.option == self.navigation_option), None)
            spec = spec or (records[0] if records else None)
            token = ("record", spec) if spec else None
        self._set_selection(token)

    def _clear_form(self) -> None:
        self._syncing = True
        self._original = None
        self._new_form = False
        self._form_active = False
        self.form.setEnabled(False)
        self.form.setVisible(False)
        self.empty_state.setVisible(True)
        self._show_overview()
        self.form_error.setVisible(False)
        self._syncing = False

    def _load_spec(self, spec: VariableSpec) -> None:
        self._syncing = True
        self._original = spec
        self._new_form = False
        self._form_active = True
        self.form.setEnabled(True)
        self.form.setVisible(True)
        self.empty_state.setVisible(False)
        self.form_heading.setText(spec.name)
        self.form_context.setText(f"{spec.app} / {spec.option}")
        self._fill_form(spec.app, spec.option, spec.name, spec.scope, spec.mode,
                        spec.editable, spec.default_value, spec.choices)
        self._resize_choices()
        self._form_base = self._form_signature()
        self.form_error.setVisible(False)
        self._syncing = False

    def _fill_form(self, app: str, option: str, name: str, scope: str, mode: str,
                   editable: bool, default: str, choices: tuple[Choice, ...]) -> None:
        self.app_combo.clear()
        self.app_combo.addItems(sorted({a for a, _ in (self.session.document.groups() if self.session.document else ())}))
        self.app_combo.setCurrentText(app)
        self._update_options(app)
        self.option_combo.setCurrentText(option)
        self.name_edit.setText(name)
        self.scope_combo.setCurrentIndex(self.scope_combo.findData(scope))
        self.mode_combo.setCurrentIndex(self.mode_combo.findData(mode))
        self.editable_check.setChecked(editable)
        self.default_editor.set_exact_text(default)
        self.choices.setRowCount(0)
        for choice in choices:
            self._append_choice(choice.label, choice.value)

    def _update_options(self, app: str) -> None:
        current = self.option_combo.currentText()
        self.option_combo.blockSignals(True)
        self.option_combo.clear()
        if self.session.document:
            self.option_combo.addItems([option for group_app, option in self.session.document.groups() if group_app == app])
        self.option_combo.setCurrentText(current)
        self.option_combo.blockSignals(False)
        self._form_changed()

    def _form_signature(self) -> tuple:
        try:
            default = self.default_editor.exact_text()
        except ValueError:
            default = ("invalid", self.default_editor.editor.toPlainText())
        return (self.app_combo.currentText(), self.option_combo.currentText(), self.name_edit.text(),
                self.scope_combo.currentData(), self.mode_combo.currentData(), self.editable_check.isChecked(),
                default, tuple(self._choices_data()))

    def _choices_data(self) -> list[tuple[str, str]]:
        result = []
        for row in range(self.choices.rowCount()):
            label = self.choices.item(row, 0).text() if self.choices.item(row, 0) else ""
            value = self.choices.item(row, 1).data(Qt.ItemDataRole.UserRole) if self.choices.item(row, 1) else ""
            result.append((label, value))
        return result

    def _form_changed(self, *_args) -> None:
        if not self._syncing and self._form_active:
            self.form_error.setVisible(False)
            self._update_status()

    def _form_pending(self) -> bool:
        return self._form_active and (self._new_form or self._form_signature() != self._form_base)

    def _ask_form_action(self) -> str:
        box = QMessageBox(self)
        box.setWindowTitle("表单尚未更新")
        box.setText("当前表单尚未更新到草稿。如何处理？")
        update = box.addButton("更新到草稿", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("放弃表单修改", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("继续编辑", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return "update" if box.clickedButton() is update else "discard" if box.clickedButton() is discard else "cancel"

    def _guard_form(self) -> bool:
        if not self._form_pending():
            return True
        action = self._ask_form_action()
        if action == "cancel":
            return False
        if action == "discard":
            if self._original is not None:
                self._load_spec(self._original)
            else:
                self._clear_form()
            return True
        return self._commit_form()

    def _form_spec(self) -> VariableSpec:
        app, option, name = self.app_combo.currentText(), self.option_combo.currentText(), self.name_edit.text()
        if not app or not option or not name:
            raise ValueError("应用、方案和变量名均不能为空")
        return VariableSpec(app, option, name, self.scope_combo.currentData(), self.mode_combo.currentData(),
                            self.editable_check.isChecked(), self.default_editor.exact_text(),
                            tuple(Choice(label, value) for label, value in self._choices_data()))

    def _commit_form(self) -> bool:
        if not self._form_active:
            return True
        try:
            spec = self._form_spec()
            self.session.put_record(spec, original=self._original)
        except Exception as exc:
            self._present_form_error(str(exc))
            return False
        self._original = spec
        self._new_form = False
        self._refresh_all(select_key=spec.key)
        return True

    def _present_form_error(self, message: str) -> None:
        """Keep the reason and the field to repair together, including narrow layouts."""
        target = None
        if not self.app_combo.currentText():
            target = self.app_combo
        elif not self.option_combo.currentText():
            target = self.option_combo
        elif not self.name_edit.text() or any(char in self.name_edit.text() for char in ("=", "\x00")):
            target = self.name_edit
        elif "default_value" in message or "转义文本无效" in message:
            target = self.default_editor.editor
        elif ".choices" in message:
            target = self.choices
        elif ".scope" in message:
            target = self.scope_combo
        elif ".mode" in message:
            target = self.mode_combo

        self._form_error_layout.removeWidget(self.form_error)
        if target in self._inspector_fields:
            owner = self._inspector_fields[target]
            position = owner.count()
        elif target is self.default_editor.editor:
            owner = self.value_pane.layout()
            position = owner.indexOf(self.default_editor) + 1
        elif target is self.choices:
            owner = self.value_pane.layout()
            position = owner.indexOf(self.choices) + 1
        else:
            owner = self._record_layout
            position = owner.count() - 2
        owner.insertWidget(position, self.form_error)
        self._form_error_layout = owner
        self.form_error.setText(message)
        self.form_error.setVisible(True)

        # A hidden error label changes the scroll content's height when shown.
        # Wait until Qt has placed it before revealing it and its repair control.
        def reveal_error():
            if not self.form_error.isVisible():
                return
            self._record_layout.activate()
            self.form.layout().activate()
            self.value_pane.layout().activate()
            self.inspector.layout().activate()
            owner.activate()
            if target is not None:
                target.setFocus(Qt.FocusReason.OtherFocusReason)
                self.record_scroll.ensureWidgetVisible(target, 12, 12)
            self.record_scroll.ensureWidgetVisible(self.form_error, 12, 12)

        QTimer.singleShot(0, reveal_error)

    def _new_record(self) -> bool:
        if not self._guard_form() or self.session.document is None:
            return False
        token = self.selected_token()
        app = token[1].app if token and token[0] == "record" else token[1] if token else ""
        option = token[2] if token and token[0] == "option" else token[1].option if token and token[0] == "record" else ""
        if token and token[0] == "app":
            option = self.session.document.default_option(app) or ""
        self._syncing = True
        self._original = None
        self._new_form = True
        self._form_active = True
        self.form.setEnabled(True)
        self.form.setVisible(True)
        self.empty_state.setVisible(False)
        self.form_heading.setText("新增变量")
        self.form_context.setText(f"{app} / {option}" if app else "新应用 / 新方案")
        self._fill_form(app, option, "", "User", "replace", False, "", ())
        self._form_base = self._form_signature()
        self._syncing = False
        self.name_edit.setFocus()
        self._form_changed()
        return True

    def _update_navigation_actions(self) -> None:
        token = self.selected_token()
        kind = token[0] if token else None
        doc = self.session.document
        groups = doc.groups() if doc else ()
        app_exists = self.navigation_app in {app for app, _ in groups}
        option_exists = (self.navigation_app, self.navigation_option) in groups
        self.new_record_button.setEnabled(doc is not None)
        self.new_app_button.setEnabled(doc is not None)
        self.new_option_button.setEnabled(app_exists)
        self.app_overview_button.setEnabled(app_exists)
        self.option_overview_button.setEnabled(option_exists)
        self.overview_option_combo.setEnabled(app_exists)
        self.app_overview_button.setAccessibleName(
            f"查看应用 {self.navigation_app} 概览" if app_exists else "应用概览")
        self.app_overview_button.setToolTip(
            f"查看应用 {self.navigation_app} 的全部方案和变量" if app_exists else "请选择应用")
        self.option_overview_button.setAccessibleName(
            f"查看方案 {self.navigation_app} / {self.navigation_option} 概览" if option_exists else "方案概览")
        self.option_overview_button.setToolTip(
            f"查看方案 {self.navigation_app} / {self.navigation_option} 的全部变量" if option_exists else "请选择方案")
        self.delete_record_button.setEnabled(doc is not None and kind == "record" and not self._new_form)
        self.delete_option_button.setEnabled(option_exists)
        self.delete_app_button.setEnabled(app_exists)
        self.delete_app_button.setAccessibleName(f"删除应用 {self.navigation_app}" if app_exists else "删除应用")
        self.delete_app_button.setToolTip(f"从草稿删除应用 {self.navigation_app} 及全部方案" if app_exists else "请选择应用")
        self.delete_option_button.setAccessibleName(
            f"删除方案 {self.navigation_app} / {self.navigation_option}" if option_exists else "删除方案")
        self.delete_option_button.setToolTip(
            f"从草稿删除方案 {self.navigation_app} / {self.navigation_option}" if option_exists else "请选择方案")
        self.new_option_button.setAccessibleName(
            f"在应用 {self.navigation_app} 中新增方案" if app_exists else "新增方案")
        self.new_option_button.setToolTip(
            f"在应用 {self.navigation_app} 中新增方案，填写名称与第一条变量" if app_exists else "请选择应用")
        self.new_record_button.setToolTip(
            f"在 {self.navigation_app} / {self.navigation_option} 中新增变量" if option_exists else "新增变量")
        if kind == "record" and not self._new_form:
            spec = token[1]
            self.delete_record_button.setAccessibleName(f"删除变量 {spec.name} ({spec.scope})")
            self.delete_record_button.setToolTip(f"从草稿删除 {spec.app} / {spec.option} / {spec.name} ({spec.scope})")
        else:
            self.delete_record_button.setAccessibleName("删除变量")
            self.delete_record_button.setToolTip("新建变量尚未更新到草稿" if self._new_form else "请选择要删除的变量")
        group = ((self.navigation_app, self.navigation_option)
                 if self.navigation_app and self.navigation_option else None)
        is_default = (group is not None and self.session.document is not None and
                      self.session.document.default_option(group[0]) == group[1])
        self.set_default_button.setEnabled(option_exists and not is_default)
        self.set_default_button.setText("当前默认方案" if is_default else "设为默认方案")

    def _set_default_option(self) -> None:
        if not self._guard_form() or self.session.document is None:
            return
        token = self.selected_token()
        if not self.navigation_app or not self.navigation_option:
            return
        app, option = self.navigation_app, self.navigation_option
        try:
            self.session.set_default_option(app, option)
            self._refresh_all(select_key=token[1].key if token and token[0] == "record" else None)
        except Exception as exc:
            self._show_error(str(exc))

    def _default_removal_note(self, app: str, option: str) -> str:
        doc = self.session.document
        if doc is None or doc.default_option(app) != option:
            return ""
        remaining = [name for group_app, name in doc.groups() if group_app == app and name != option]
        return (f"\n删除后，将 {remaining[0]} 设为该应用的默认方案。" if remaining else
                "\n这是该应用最后一个方案，应用及默认方案设置将一并删除。")

    def _append_choice(self, label: str, value: str) -> None:
        row = self.choices.rowCount()
        self.choices.insertRow(row)
        self.choices.setItem(row, 0, QTableWidgetItem(label))
        cell = QTableWidgetItem(_choice_summary(value))
        cell.setData(Qt.ItemDataRole.UserRole, value)
        cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.choices.setItem(row, 1, cell)

    def _add_choice(self) -> None:
        self._append_choice("", "")
        self.choices.setCurrentCell(self.choices.rowCount() - 1, 0)
        self.choices.editItem(self.choices.currentItem())
        self._form_changed()

    def _edit_choice_cell(self, row: int, column: int) -> None:
        if column == 1:
            self._edit_choice_value(row)

    def _edit_choice_value(self, row: int | None = None) -> None:
        if row is None:
            row = self.choices.currentRow()
        if row < 0:
            return
        cell = self.choices.item(row, 1)
        dialog = ExactValueDialog(cell.data(Qt.ItemDataRole.UserRole), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        value = dialog.value()
        cell.setData(Qt.ItemDataRole.UserRole, value)
        cell.setText(_choice_summary(value))
        self._form_changed()

    def _delete_choice(self) -> None:
        row = self.choices.currentRow()
        if row >= 0:
            self.choices.removeRow(row)
            self._form_changed()

    def _confirm_delete(self, description: str) -> bool:
        return QMessageBox.question(self, "删除配置", f"从配置草稿中删除 {description}？\nWindows 变量不会被删除。",
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def _delete_record(self) -> None:
        if self._new_form or self.session.document is None:
            return
        if not self._guard_form():
            return
        token = self.selected_token()
        if not token or token[0] != "record":
            return
        spec = token[1]
        note = (self._default_removal_note(spec.app, spec.option)
                if len(self.session.document.records(spec.app, spec.option)) == 1 else "")
        if not self._confirm_delete(f"{spec.app} / {spec.option} / {spec.name} ({spec.scope})" + note):
            return
        try:
            self.session.delete_record(spec)
            self._refresh_all()
        except Exception as exc:
            self._show_error(str(exc))

    def _delete_option(self) -> None:
        app, option = self.navigation_app, self.navigation_option
        if not self._guard_form():
            return
        doc = self.session.document
        if doc is None or (app, option) not in doc.groups():
            return
        if not self._confirm_delete(f"方案 {app} / {option}" + self._default_removal_note(app, option)):
            return
        try:
            self.session.delete_group(app, option)
            self._refresh_all()
        except Exception as exc:
            self._show_error(str(exc))

    def _delete_app(self) -> None:
        app = self.navigation_app
        if not self._guard_form():
            return
        doc = self.session.document
        if doc is None or app not in {a for a, _ in doc.groups()}:
            return
        if not self._confirm_delete(f"应用 {app}"):
            return
        try:
            self.session.delete_group(app)
            self._refresh_all()
        except Exception as exc:
            self._show_error(str(exc))

    def _source_changed(self) -> None:
        if self._syncing:
            return
        text = self.source.toPlainText()
        if text == self._source_last_text:
            return
        self._source_last_text = text
        try:
            self.session.replace_source_text(text)
        except Exception as exc:
            self._show_error(str(exc))
        self._update_status()

    def _switch_tab(self, index: int) -> None:
        if self._syncing:
            return
        if not self._guard_form() or (index == 0 and self.session.document is None):
            if index == 0 and self.session.document is None:
                self._show_error("原始 TOML 当前无效，修正后才能返回可视化编辑。" + (self.session.error or ""))
            self._syncing = True
            self.tabs.setCurrentIndex(self._tab_index)
            self._syncing = False
            return
        self._tab_index = index
        if index == 0:
            self._refresh_navigation()
        elif index == 1:
            self._sync_source()
        else:
            self.diff_view.setPlainText(self.session.diff() or "（无差异）")
        self._update_status()

    def _sync_source(self) -> None:
        self._syncing = True
        self.source.setPlainText(self.session.text)
        self._source_last_text = self.source.toPlainText()
        self._syncing = False

    def _refresh_all(self, select_key: str | None = None) -> None:
        self._sync_source()
        self._refresh_navigation(select_key)
        self.diff_view.setPlainText(self.session.diff() or "（无差异）")
        self._update_status()

    def _show_error(self, message: str) -> None:
        self.error.setText(message)
        self.error.setVisible(bool(message))

    def _update_status(self) -> None:
        self._show_error(self.session.error or "")
        self.dirty_label.setText("变量尚未更新" if self._form_pending() else
                                 "草稿尚未保存" if self.session.dirty else "已与文件同步")
        self.form_state.setText("当前修改尚未进入草稿" if self._form_pending() else
                                "配置草稿有未保存修改" if self._form_active and self.session.dirty else
                                "" if self._form_active else "")
        self.update_button.setVisible(self._form_active)
        self.undo_button.setEnabled(self.session.can_undo)
        self.redo_button.setEnabled(self.session.can_redo)
        self.update_button.setEnabled(self._form_active and self._form_pending())
        self.save_button.setEnabled((self.session.dirty or self._form_pending()) and self.session.document is not None
                                    and self.session.error is None)
        self.tabs.setTabEnabled(0, self.session.document is not None)
        self._update_navigation_actions()

    def _undo(self) -> None:
        if not self._guard_form():
            return
        self.session.undo()
        self._refresh_all()

    def _redo(self) -> None:
        if not self._guard_form():
            return
        self.session.redo()
        self._refresh_all()

    def _validate(self) -> bool:
        if not self._guard_form():
            return False
        try:
            self.session.validate()
        except Exception as exc:
            self._show_error(str(exc))
            return False
        self._show_error("")
        QMessageBox.information(self, "校验通过", "当前配置草稿符合 TOML 和变量规则。")
        return True

    def _confirm_diff(self, diff: str) -> bool:
        dialog = QDialog(self)
        dialog.setWindowTitle("确认保存差异")
        dialog.resize(800, 570)
        layout = QVBoxLayout(dialog)
        layout.addWidget(_caption("请确认以下配置文件变更。保存只修改 TOML 文件，不执行环境变量操作。", "muted"))
        preview = QPlainTextEdit()
        preview.setObjectName("code")
        preview.setReadOnly(True)
        preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        dialog.highlighter = DiffHighlighter(preview.document())
        preview.setPlainText(diff or "（无差异）")
        layout.addWidget(preview, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("确认保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("返回编辑")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        return dialog.exec() == QDialog.DialogCode.Accepted

    def _conflict(self, message: str) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("配置文件已在外部改变")
        box.setText(message)
        box.setInformativeText("当前草稿尚未覆盖磁盘。可以重新加载磁盘版本，或保留草稿继续检查。")
        reload_button = box.addButton("重新加载磁盘", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("保留草稿", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is reload_button:
            try:
                self.session.reload_from_disk()
                self._refresh_all()
            except Exception as exc:
                self._show_error(str(exc))

    def _save(self) -> bool:
        if not self._guard_form():
            return False
        try:
            self.session.validate()
            diff = self.session.diff()
        except Exception as exc:
            self._show_error(str(exc))
            return False
        self.diff_view.setPlainText(diff or "（无差异）")
        if not self._confirm_diff(diff):
            return False
        try:
            self.session.save(self.service)
        except ConflictError as exc:
            self._conflict(str(exc))
            return False
        except Exception as exc:
            self._show_error(str(exc))
            return False
        self.saved = True
        warning = getattr(self.session, "save_warning", None)
        if warning:
            QMessageBox.warning(self, "配置已保存，刷新提示", warning)
        self.accept()
        return True

    def _ask_close_action(self) -> str:
        box = QMessageBox(self)
        box.setWindowTitle("配置草稿尚未保存")
        box.setText("要保存配置草稿吗？")
        save = box.addButton("查看差异并保存", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("放弃草稿", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("继续编辑", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return "save" if box.clickedButton() is save else "discard" if box.clickedButton() is discard else "cancel"

    def reject(self) -> None:
        if not self._guard_form():
            return
        if self.session.dirty:
            action = self._ask_close_action()
            if action == "cancel":
                return
            if action == "save":
                self._save()
                return
        super().reject()

    def closeEvent(self, event) -> None:
        if not self._guard_form():
            event.ignore()
            return
        if self.session.dirty:
            action = self._ask_close_action()
            if action == "cancel":
                event.ignore()
                return
            if action == "save":
                if self._save():
                    event.accept()
                else:
                    event.ignore()
                return
        event.accept()
