"""Native scheme selector with one owned popup row rendering path."""
from PySide6.QtCore import Qt, QSize, QPoint, QRectF, QCoreApplication, QEvent
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QApplication, QComboBox, QFrame, QListView, QStyledItemDelegate, QStyleOptionComboBox, QStyle, QStylePainter
from .theme import COLORS

OPTION_STATUS_ROLE = int(Qt.ItemDataRole.UserRole) + 1


def _popup_status(status):
    tone, progress = status.get("tone"), status.get("progress", "")
    if not status:
        return ""
    if tone in ("complete", "partial"):
        return progress
    return {"unconfigured": "未配置", "error": "检测错误",
            "scanning": "检测中"}.get(tone, "")


def _row_fonts(font):
    fonts = []
    for pixels in (12, 11):
        item = QFont(font)
        item.setPixelSize(pixels)
        fonts.append(item)
    return fonts


class OptionStatusDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        fonts = _row_fonts(option.font)
        metrics = [QFontMetrics(font) for font in fonts]
        # The popup has a deliberate width budget. Full labels remain in the
        # model and accessible descriptions, rather than enlarging the window.
        return QSize(0, max(30, max(metric.height() for metric in metrics) + 10))

    def paint(self, painter: QPainter, option, index):
        # Never delegate the text to the native combo/menu painter: it reserves
        # an icon/check column and draws focus around only the text fragment.
        status = index.data(OPTION_STATUS_ROLE) or {}
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        view = self.parent()
        combo = view.parent() if view else None
        while combo is not None and not isinstance(combo, QComboBox):
            combo = combo.parent()
        current = combo is not None and combo.currentIndex() == index.row()
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(COLORS["surface"]))
        painter.drawRect(option.rect)
        if selected or current:
            # Navigation feedback is quieter than a successful match badge.
            # The check still identifies the prepared scheme; a fine outline
            # identifies the cursor row before keyboard/mouse confirmation.
            painter.setBrush(QColor(COLORS["selector_current"]))
            if selected and not current:
                painter.setPen(QPen(QColor(COLORS["control_border"]), 1))
            painter.drawRoundedRect(QRectF(option.rect).adjusted(.5, .5, -.5, -.5), 4, 4)
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.setBrush(QColor(COLORS["surface_muted"]))
            painter.drawRoundedRect(option.rect, 4, 4)
        name_font, count_font = _row_fonts(option.font)
        name_metrics = QFontMetrics(name_font)
        count_metrics = QFontMetrics(count_font)
        if current:
            painter.setFont(count_font)
            painter.setPen(QColor(COLORS["accent"]))
            painter.drawText(option.rect.adjusted(10, 0, 0, 0),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "✓")
        suffix = _popup_status(status)
        suffix_width = count_metrics.horizontalAdvance(suffix)
        right = option.rect.adjusted(28, 0, -10, 0)
        label_rect = right.adjusted(0, 0, -(suffix_width + 12 if suffix else 0), 0)
        name = index.data() or ""
        # Auxiliary default/activation information lives in the complete
        # tooltip and accessible description; the compact row shows name/count.
        text = name_metrics.elidedText(name, Qt.TextElideMode.ElideRight,
                                      max(0, label_rect.width()))
        painter.setFont(name_font)
        painter.setPen(QColor(COLORS["ink"]))
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        tone = status.get("tone", "scanning")
        color = {"complete": "accent", "partial": "warning", "error": "danger"}.get(tone, "secondary")
        painter.setPen(QColor(COLORS[color]))
        painter.setFont(count_font)
        painter.drawText(right, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, suffix)
        painter.restore()


class OptionComboBox(QComboBox):
    def __init__(self, parent=None, *, compact=False):
        super().__init__(parent)
        self.compact = compact
        font = QFont(self.font())
        font.setPixelSize(12)
        self.setFont(font)
        # Set the list-popup hint BEFORE setView creates Qt's popup container.
        # Menu-style containers otherwise retain native top/bottom spacers.
        self.setStyleSheet("QComboBox { combobox-popup: 0; }")
        self.ensurePolished()
        view = QListView(self)
        view.setObjectName("optionPopup")
        view.setMouseTracking(True)
        view.setResizeMode(QListView.ResizeMode.Adjust)
        view.setItemDelegate(OptionStatusDelegate(view))
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setView(view)
        popup = view.window()
        # An already-polished application can construct the container during
        # the stylesheet change. Refresh its cached menu spacers with the final
        # list-popup hint before applying our frame, without touching Qt internals.
        QCoreApplication.sendEvent(popup, QEvent(QEvent.Type.StyleChange))
        popup.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
                             | Qt.WindowType.NoDropShadowWindowHint)
        popup.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        popup.setFrameShape(QFrame.Shape.NoFrame)
        popup.setContentsMargins(0, 0, 0, 0)
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
        return QSize(80 if self.compact else 150,
                     max(24 if self.compact else 31,
                         self.fontMetrics().height() + (6 if self.compact else 10)))

    def sizeHint(self):
        return QSize(80 if self.compact else 166, self.minimumSizeHint().height())

    def displayedText(self):
        return self.fontMetrics().elidedText(self.currentText(), Qt.TextElideMode.ElideRight,
                                             max(0, self._text_rect().width() - 4))

    def _text_rect(self):
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        return self.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, option,
                                          QStyle.SubControl.SC_ComboBoxEditField, self)

    def paintEvent(self, event):
        painter = QStylePainter(self)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        painter.setFont(self.font())
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(self._text_rect(), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         self.displayedText())
        if not self.compact and self.currentIndex() >= 0:
            painter.setPen(QColor(COLORS["accent"] if self.isEnabled() else COLORS["disabled"]))
            painter.drawText(self.rect().adjusted(14, 0, 0, 0),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "✓")

    def showPopup(self):
        view = self.view()
        view.ensurePolished()
        # Keep the entire frameless popup inside the current work area.
        screen = self.screen().availableGeometry().adjusted(4, 4, -4, -4)
        margins = view.contentsMargins()
        # Opening must not turn the primary selector into a wider panel.
        # Scrollbars consume the existing width; long names elide in that budget.
        width = min(max(166, self.width()) if self.compact else self.width(), screen.width())
        view.setMinimumWidth(width)
        view.setMaximumWidth(width)
        # Windows' scroll effect temporarily hides the actual popup and shows
        # an animation window instead. Position the real surface immediately;
        # restore the application preference so other selectors keep theirs.
        animate = QApplication.isEffectEnabled(Qt.UIEffect.UI_AnimateCombo)
        QApplication.setEffectEnabled(Qt.UIEffect.UI_AnimateCombo, False)
        try:
            super().showPopup()
        finally:
            QApplication.setEffectEnabled(Qt.UIEffect.UI_AnimateCombo, animate)
        if not view.isVisible():
            return
        popup = view.window()
        height = sum(view.sizeHintForRow(row) for row in range(min(self.count(), self.maxVisibleItems())))
        popup.resize(width, min(height + margins.top() + margins.bottom(), screen.height()))
        popup.layout().activate()
        below = self.mapToGlobal(QPoint(0, self.height() + 4))
        above = self.mapToGlobal(QPoint(0, -popup.height() - 4))
        y = below.y() if below.y() + popup.height() <= screen.bottom() + 1 else above.y()
        x = below.x()
        if self.compact:
            # Keep the menu inside the inventory where there is enough room,
            # rather than covering the first column of the detail pane.
            inventory = self.parentWidget()
            while inventory is not None and inventory.objectName() != "manifestList":
                inventory = inventory.parentWidget()
            if inventory is not None and inventory.width() >= popup.width():
                edge = inventory.mapToGlobal(QPoint(inventory.width(), 0)).x()
                x = min(x, edge - popup.width())
        else:
            x += self.width() - popup.width()
        popup.move(max(screen.left(), min(x, screen.right() - popup.width() + 1)),
                   max(screen.top(), min(y, screen.bottom() - popup.height() + 1)))
        view.scrollTo(view.currentIndex())
