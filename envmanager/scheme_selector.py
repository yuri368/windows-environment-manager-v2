"""Native scheme selector with one owned popup row rendering path."""
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QComboBox, QListView, QStyledItemDelegate, QStyleOptionComboBox, QStyle, QStylePainter
from .theme import COLORS

OPTION_STATUS_ROLE = int(Qt.ItemDataRole.UserRole) + 1


def _popup_status(status):
    tone, progress = status.get("tone"), status.get("progress", "")
    if not status:
        return ""
    text = {"complete": "匹配", "partial": "未匹配" if progress.startswith("0/") else "部分匹配",
            "unconfigured": "未配置", "error": "检测错误", "scanning": "检测中"}.get(tone, "")
    return f"{text} {progress}".strip()


class OptionStatusDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        status = index.data(OPTION_STATUS_ROLE) or {}
        suffix = _popup_status(status)
        markers = " · 默认" if status.get("default") else ""
        if status.get("active"):
            markers += " · 已激活"
        width = option.fontMetrics.horizontalAdvance((index.data() or "") + markers)
        width += option.fontMetrics.horizontalAdvance(suffix) + 72
        return QSize(width, max(34, option.fontMetrics.height() + 14))

    def paint(self, painter: QPainter, option, index):
        # Never delegate the text to the native combo/menu painter: it reserves
        # an icon/check column and draws focus around only the text fragment.
        status = index.data(OPTION_STATUS_ROLE) or {}
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.save()
        painter.fillRect(option.rect, QColor(COLORS["selected"] if selected else COLORS["surface"]))
        painter.setFont(option.font)
        view = self.parent()
        combo = view.parent() if view else None
        while combo is not None and not isinstance(combo, QComboBox):
            combo = combo.parent()
        current = combo is not None and combo.currentIndex() == index.row()
        if current:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(COLORS["accent"]))
            painter.drawEllipse(option.rect.left() + 12, option.rect.center().y() - 3, 6, 6)
        suffix = _popup_status(status)
        suffix_width = option.fontMetrics.horizontalAdvance(suffix)
        right = option.rect.adjusted(32, 0, -12, 0)
        label_rect = right.adjusted(0, 0, -(suffix_width + 22 if suffix else 0), 0)
        marker = " · 默认" if status.get("default") else ""
        if status.get("active"):
            marker += " · 已激活"
        text = option.fontMetrics.elidedText((index.data() or "") + marker,
                                             Qt.TextElideMode.ElideRight, label_rect.width())
        painter.setPen(QColor(COLORS["ink"]))
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        tone = status.get("tone", "scanning")
        color = {"complete": "accent", "partial": "warning", "error": "danger"}.get(tone, "secondary")
        painter.setPen(QColor(COLORS[color]))
        painter.drawText(right, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, suffix)
        painter.restore()


class OptionComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        view = QListView(self)
        view.setObjectName("optionPopup")
        view.setItemDelegate(OptionStatusDelegate(view))
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setView(view)
        self.setMaxVisibleItems(3)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(8)
        self.currentIndexChanged.connect(self._current_tooltip)

    def _current_tooltip(self, *_):
        self.setToolTip(self.itemData(self.currentIndex(), Qt.ItemDataRole.ToolTipRole) or self.currentText())

    def setOptionStatus(self, index, scheme, *, default=False, active=False, presentation=None):
        if index < 0 or index >= self.count():
            return
        # Presentation is supplied by ui.py to keep this component independent
        # from business status enums and their explanatory wording.
        text, tone, progress, detail = presentation or ("未检测", "scanning", "", "尚未取得 Windows 检测结果")
        display = f"{text} {progress}".strip()
        option_id = self.itemText(index)
        self.setItemData(index, option_id, Qt.ItemDataRole.UserRole)
        self.setItemData(index, {"text": text, "tone": tone, "progress": progress,
                                "display": display, "default": default, "active": active}, OPTION_STATUS_ROLE)
        markers = ("，默认方案" if default else "") + ("，当前激活方案" if active else "")
        tooltip = f"{option_id}{markers}\n{display}\n{detail}"
        self.setItemData(index, tooltip, Qt.ItemDataRole.ToolTipRole)
        self.setItemData(index, f"{option_id}{markers}，{display}", Qt.ItemDataRole.AccessibleTextRole)
        self._current_tooltip()

    def minimumSizeHint(self):
        return QSize(80, max(30, super().minimumSizeHint().height()))

    def displayedText(self):
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        field = self.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, option,
                                            QStyle.SubControl.SC_ComboBoxEditField, self)
        return self.fontMetrics().elidedText(self.currentText(), Qt.TextElideMode.ElideRight,
                                             max(0, field.width() - 4))

    def paintEvent(self, event):
        painter = QStylePainter(self)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        option.currentText = self.displayedText()
        painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, option)

    def showPopup(self):
        view = self.view()
        view.ensurePolished()
        # Popup native borders and platform position correction need a small
        # inset; a client width equal to the work area can still overflow.
        screen = self.screen().availableGeometry().adjusted(4, 4, -4, -4)
        width = min(max(self.width(), view.sizeHintForColumn(0) + 28), screen.width())
        view.setMinimumWidth(width)
        view.setMaximumWidth(width)
        super().showPopup()
        if not view.isVisible():
            return
        popup = view.window()
        chrome = popup.height() - view.viewport().height()
        height = sum(view.sizeHintForRow(row) for row in range(min(self.count(), 3)))
        popup.resize(width, min(height + chrome, screen.height()))
        popup.move(max(screen.left(), min(popup.x(), screen.right() - popup.width() + 1)),
                   max(screen.top(), min(popup.y(), screen.bottom() - popup.height() + 1)))
        view.scrollTo(view.currentIndex())
