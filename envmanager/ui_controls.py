"""Small native controls shared by the compact desktop workspaces."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton


class ActionButton(QPushButton):
    """Activate the focused action with Enter as well as native Space."""

    def keyPressEvent(self, event):
        if (self.hasFocus() and self.isEnabled() and not event.isAutoRepeat()
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and event.modifiers() == Qt.KeyboardModifier.NoModifier):
            self.click()
            event.accept()
            return
        super().keyPressEvent(event)
