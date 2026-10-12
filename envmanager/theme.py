"""Shared semantic tokens and native control styling for both workspaces."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication


COLORS = {
    "canvas": "#F5F3EF",
    "inventory": "#F0F0EB",
    "surface": "#FCFCFA",
    "surface_muted": "#F1F2ED",
    "input": "#FFFFFF",
    "ink": "#262D28",
    "text": "#39443C",
    "secondary": "#606B62",
    "muted": "#666F63",
    "disabled": "#90968D",
    "rule": "#DDE1D7",
    "control_border": "#BFC7BB",
    "control_hover": "#98A692",
    "accent": "#38644D",
    "accent_hover": "#2C513C",
    "accent_pressed": "#254631",
    "selected": "#E5ECE2",
    "hover": "#EBEFE7",
    "focus": "#527A56",
    "danger": "#A44336",
    "danger_surface": "#FAEEE8",
    "warning": "#85612B",
    "warning_surface": "#F8EEDB",
    "warning_rule": "#E3D1AF",
    "scroll": "#BBC4B4",
}

SPACING = {"base": 4, "field": 8, "group": 12, "section": 16, "workspace": 12}
FONT_FAMILIES = ["Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "Segoe UI Symbol"]
TYPE = {
    "dialog_title": {"size": 18, "weight": 500},
    "workspace_title": {"size": 18, "weight": 500},
    "section": {"size": 13, "weight": 500},
    "body": {"size": 13, "weight": 400},
    "meta": {"size": 12, "weight": 400},
}
_ASSETS = (Path(__file__).parent / "assets").resolve().as_posix()


def _resolve_style(source: str) -> str:
    for name, value in COLORS.items():
        source = source.replace("@" + name + "@", value)
    for role, attributes in TYPE.items():
        for attribute, value in attributes.items():
            source = source.replace(f"@{role}_{attribute}@", str(value))
    return source.replace("ASSETS/", _ASSETS + "/")


STYLE = _resolve_style("""
QWidget {
    color: @ink@; font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "Segoe UI Symbol";
    font-size: @body_size@px; selection-background-color: @selected@; selection-color: @ink@;
}
QMainWindow, QDialog { background: @canvas@; }
QLabel { background: transparent; border: none; }
QLabel#title { font-size: @dialog_title_size@px; font-weight: @dialog_title_weight@; }
QLabel#appTitle, QLabel#compactHeading { font-size: @workspace_title_size@px; font-weight: @workspace_title_weight@; }
QLabel#section { font-size: @section_size@px; font-weight: @section_weight@; color: @text@; }
QLabel#name, QLabel#manifestName { font-size: @section_size@px; font-weight: @section_weight@; color: @ink@; }
QLabel#muted, QLabel#subtitle, QLabel#meta, QLabel#manifestMeta, QLabel#contextPath {
    color: @secondary@; font-size: @meta_size@px;
}
QLabel#workspaceCrumb, QLabel#manifestHint, QLabel#footerScope { color: @secondary@; font-size: @meta_size@px; }
QLabel#eyebrow { color: @muted@; font-size: 11px; }
QLabel#selectionSummary, QLabel#editorState { color: @accent@; font-size: 12px; }
QLabel#error { color: @danger@; }
QLabel#good { color: @accent@; }
QLabel#badge { background: @surface_muted@; color: @secondary@; border-radius: 4px; padding: 3px 7px; font-size: 11px; }
QLabel#badge[tone="accent"] { background: @selected@; color: @accent@; }
QLabel#badge[tone="warm"] { background: @warning_surface@; color: @warning@; }
QLabel#activationBadge { border-radius: 3px; padding: 1px 5px; font-size: @meta_size@px; font-weight: 400; }
QLabel#activationBadge[activation="active"] { background: @selected@; color: @accent@; }
QLabel#activationBadge[activation="inactive"] { background: @warning_surface@; color: @warning@; }
QLabel#matchStatus { border-radius: 3px; padding: 1px 5px; font-size: @meta_size@px; font-weight: 500; }
QLabel#matchStatus[matching="complete"] { background: @selected@; color: @accent@; }
QLabel#matchStatus[matching="partial"] { background: @warning_surface@; color: @warning@; }
QLabel#matchStatus[matching="unconfigured"], QLabel#matchStatus[matching="scanning"] {
    background: @surface_muted@; color: @secondary@;
}
QLabel#matchStatus[matching="error"] { background: @danger_surface@; color: @danger@; }
QLabel#code, QLineEdit#code, QPlainTextEdit#code {
    font-family: "Cascadia Mono", "Consolas", "Microsoft YaHei UI"; font-size: 13px;
}
QFrame#workspaceHeader, QFrame#editorHeader { background: @surface@; border: none; border-bottom: 1px solid @rule@; }
QFrame#sidebar, QFrame#manifest, QFrame#editorBrowser { background: @inventory@; border: none; }
QFrame#sidebar { border-right: 1px solid @rule@; }
QFrame#detailPane, QFrame#editorWorkspace, QFrame#editorValuePane { background: @surface@; border: none; }
QFrame#inspectorHeader { background: @surface@; border: none; border-bottom: 1px solid @rule@; }
QFrame#editorInspector { background: transparent; border: none; }
QFrame#variableTableHeader { background: @surface_muted@; border: none; border-bottom: 1px solid @rule@; }
QFrame#card, QFrame#panel { background: @surface@; border: 1px solid @rule@; border-radius: 8px; }
QFrame#compactVariable { background: @surface@; border: none; border-bottom: 1px solid @rule@; border-radius: 0; }
QFrame#variableDetails { background: @surface_muted@; border: none; border-radius: 4px; }
QFrame#conflictItem { background: @surface@; border: 1px solid @rule@; border-radius: 6px; }
QFrame#toolbar { background: @canvas@; border: none; }
QFrame#footer { background: @surface@; border: none; border-top: 1px solid @rule@; }
QFrame#banner { background: @warning_surface@; border: 1px solid @warning_rule@; border-radius: 6px; }
QFrame#divider { background: @rule@; border: none; min-height: 1px; max-height: 1px; }
QProgressBar#scanProgress { background: @surface_muted@; border: none; border-radius: 3px; }
QProgressBar#scanProgress::chunk { background: @accent@; border-radius: 3px; }
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox {
    background: @input@; border: 1px solid @control_border@; border-radius: 5px;
    padding: 5px 8px; color: @text@; placeholder-text-color: @secondary@;
}
QLineEdit:hover, QPlainTextEdit:hover, QComboBox:hover { border-color: @control_hover@; }
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus { border-color: @focus@; }
QLineEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled {
    background: @surface_muted@; color: @disabled@; border-color: @rule@;
}
QLineEdit:read-only { background: @surface_muted@; }
QLineEdit[invalid="true"], QPlainTextEdit[invalid="true"] { border-color: @danger@; }
QComboBox { padding-right: 26px; }
QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: top right; width: 26px; border: none; }
QComboBox::down-arrow { image: url("ASSETS/chevron-down.svg"); width: 12px; height: 12px; }
QComboBox QLineEdit { border: none; padding: 0; background: transparent; }
QComboBox QAbstractItemView {
    border: 1px solid @control_border@; background: @input@;
    selection-background-color: @selected@; selection-color: @ink@; padding: 4px;
}
QListView#optionPopup::item { padding: 4px 8px; }
QPushButton, QToolButton {
    background: @input@; border: 1px solid @control_border@; border-radius: 5px;
    padding: 5px 10px; font-weight: 400;
}
QPushButton:hover, QToolButton:hover { background: @hover@; border-color: @control_hover@; }
QPushButton:pressed, QToolButton:pressed { background: @selected@; }
QPushButton:focus, QToolButton:focus { border-color: @focus@; }
QPushButton:disabled, QToolButton:disabled { color: @disabled@; background: @surface_muted@; border-color: @rule@; }
QPushButton#primary { background: @accent@; color: @input@; border-color: @accent@; font-weight: 500; }
QPushButton#primary:hover { background: @accent_hover@; border-color: @accent_hover@; }
QPushButton#primary:pressed { background: @accent_pressed@; }
QPushButton#primary:disabled { color: @disabled@; background: @rule@; border-color: @rule@; }
QPushButton#secondary, QPushButton[secondary="true"] { background: @hover@; border-color: @rule@; color: @accent@; }
QPushButton#secondary:hover, QPushButton[secondary="true"]:hover { background: @selected@; }
QPushButton#secondary:disabled, QPushButton[secondary="true"]:disabled { background: @surface_muted@; color: @disabled@; border-color: @rule@; }
QPushButton#quiet, QPushButton[quiet="true"], QToolButton#quiet, QPushButton#iconOnly, QToolButton#iconOnly {
    background: transparent; border: 1px solid transparent;
}
QPushButton#quiet:hover, QPushButton[quiet="true"]:hover, QToolButton#quiet:hover, QPushButton#iconOnly:hover, QToolButton#iconOnly:hover { background: @hover@; }
QPushButton#quiet:focus, QPushButton[quiet="true"]:focus, QToolButton#quiet:focus, QPushButton#iconOnly:focus, QToolButton#iconOnly:focus { border-color: @focus@; }
QPushButton#iconOnly, QToolButton#iconOnly { padding: 5px; }
QPushButton#danger { background: transparent; border-color: @rule@; color: @danger@; }
QPushButton#danger:hover { background: @danger_surface@; border-color: @danger@; }
QPushButton#danger:disabled { color: @disabled@; border-color: @rule@; }
QPushButton#nav { text-align: left; background: transparent; border: 1px solid transparent; padding: 8px 12px; color: @secondary@; }
QPushButton#nav:hover { background: @hover@; }
QPushButton#nav[active="true"] { background: @surface@; color: @accent@; font-weight: 600; }
QPushButton#workspaceTab { background: transparent; border: none; border-bottom: 2px solid @accent@; border-radius: 0; color: @accent@; padding: 5px 8px; }
QPushButton#workspaceTab:focus { background: @selected@; }
QPushButton#manifestName { text-align: left; background: transparent; border: none; padding: 0; font-size: 13px; font-weight: 500; }
QPushButton#manifestName:focus { border: 1px solid @focus@; }
QPushButton#manifestName:hover { color: @accent@; }
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { border: none; background: transparent; width: 8px; margin: 4px 0; }
QScrollBar::handle:vertical { background: @scroll@; min-height: 32px; border-radius: 4px; }
QScrollBar::handle:vertical:hover { background: @control_hover@; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { border: none; background: transparent; height: 8px; margin: 0 4px; }
QScrollBar::handle:horizontal { background: @scroll@; min-width: 32px; border-radius: 4px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }
QCheckBox { spacing: 8px; background: transparent; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid @control_hover@; border-radius: 3px; background: @input@; }
QCheckBox::indicator:checked { background: @accent@; border-color: @accent@; image: url("ASSETS/check.svg"); }
QCheckBox::indicator:disabled { background: @surface_muted@; border-color: @rule@; }
QCheckBox:focus { color: @accent@; }
QListWidget, QTreeWidget, QTableWidget { background: @surface@; border: 1px solid @rule@; border-radius: 5px; }
QListWidget::item { padding: 5px 8px; border-bottom: 1px solid @rule@; }
QListWidget::item:selected { background: @selected@; color: @accent@; }
QListWidget#manifestList { background: transparent; border: none; }
QListWidget#manifestList::item { padding: 0; margin: 2px 0; border: none; border-radius: 6px; }
QListWidget#manifestList::item:hover { background: @hover@; }
QListWidget#manifestList::item:selected { background: @selected@; }
QFrame#manifestRow { background: transparent; border: none; }
QListWidget#editorApps { background: transparent; border: none; border-radius: 0; }
QListWidget#editorApps::item { padding: 4px 8px; border: none; border-radius: 3px; }
QListWidget#editorApps::item:hover { background: @hover@; }
QListWidget#editorApps::item:selected { background: @selected@; color: @ink@; }
QTableWidget#editorRecords, QTableWidget#editorSearchResults {
    background: transparent; border: none; border-radius: 0;
}
QTableWidget#editorRecords::item, QTableWidget#editorSearchResults::item { padding: 4px 6px; border: none; }
QTableWidget#editorRecords::item:hover, QTableWidget#editorSearchResults::item:hover { background: @hover@; }
QComboBox#manifestOption { background: transparent; border-color: transparent; padding: 3px 18px 3px 6px; font-size: 12px; }
QComboBox#manifestOption::drop-down { width: 18px; }
QComboBox#manifestOption:hover { background: @surface@; border-color: @control_hover@; }
QComboBox#manifestOption:focus { border-color: @focus@; }
QTreeWidget { padding: 4px 0; background: transparent; border: none; }
QTreeWidget::item { height: 26px; padding: 2px 4px; border: none; border-radius: 3px; }
QTreeWidget::item:hover { background: @hover@; }
QTreeWidget::item:selected { background: @selected@; color: @accent@; }
QTreeWidget::branch { background: transparent; border: none; }
QTreeWidget::branch:has-children:closed { image: url("ASSETS/chevron-right.svg"); }
QTreeWidget::branch:has-children:open { image: url("ASSETS/chevron-down.svg"); }
QTableWidget { gridline-color: @rule@; selection-background-color: @selected@; selection-color: @ink@; }
QTableWidget::item { padding: 4px 8px; }
QHeaderView::section { background: @surface@; border: none; border-bottom: 1px solid @rule@; color: @secondary@; padding: 5px 8px; font-size: 12px; }
QTableCornerButton::section { background: @surface_muted@; border: none; }
QTabWidget::pane { border: none; background: transparent; }
QTabBar::tab { background: transparent; color: @secondary@; border: none; border-bottom: 2px solid transparent; padding: 7px 12px; margin-right: 4px; }
QTabBar::tab:hover { color: @ink@; background: @hover@; }
QTabBar::tab:selected { color: @accent@; border-bottom-color: @accent@; font-weight: 500; }
QSplitter::handle { background: @canvas@; width: 8px; }
QSplitter::handle:hover { background: @rule@; }
QMenu { background: @surface@; color: @text@; border: 1px solid @control_border@; padding: 4px; }
QMenu::item { padding: 6px 24px; border-radius: 4px; }
QMenu::item:selected { background: @selected@; }
QMenu::separator { height: 1px; background: @rule@; margin: 4px; }
QToolTip { background: @ink@; color: @surface@; border: none; padding: 6px 8px; }
""")

_fonts_loaded = False


def apply_theme(app: QApplication) -> None:
    global _fonts_loaded
    if not _fonts_loaded:
        font_directory = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for filename in ("segoeui.ttf", "seguisym.ttf", "msyh.ttc", "msyhbd.ttc", "consola.ttf"):
            path = font_directory / filename
            if path.exists():
                QFontDatabase.addApplicationFont(str(path))
        _fonts_loaded = True
    app.setStyle("Fusion")
    font = QFont()
    font.setFamilies(FONT_FAMILIES)
    font.setPixelSize(TYPE["body"]["size"])
    app.setFont(font)
    palette = QPalette()
    for role, token in {
        QPalette.ColorRole.Window: "canvas",
        QPalette.ColorRole.WindowText: "ink",
        QPalette.ColorRole.Base: "input",
        QPalette.ColorRole.AlternateBase: "surface_muted",
        QPalette.ColorRole.Text: "text",
        QPalette.ColorRole.Button: "surface",
        QPalette.ColorRole.ButtonText: "text",
        QPalette.ColorRole.Highlight: "selected",
        QPalette.ColorRole.HighlightedText: "ink",
        QPalette.ColorRole.PlaceholderText: "secondary",
    }.items():
        palette.setColor(role, QColor(COLORS[token]))
    app.setPalette(palette)
    app.setStyleSheet(STYLE)
