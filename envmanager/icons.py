"""Small, original vector icons shared by the desktop views."""

from __future__ import annotations

from html import escape
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer


_SHAPES = {
    "search": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4.5 4.5"/>',
    "history": '<path d="M3 10a9 9 0 1 1 2 8M3 4v6h6M12 7v5l3 2"/>',
    "edit": '<path d="m15 4 5 5M4 20l5-1L20 8a2 2 0 0 0-4-4L5 15l-1 5Z"/>',
    "code": '<path d="m8 6-6 6 6 6m8-12 6 6-6 6m-3-14-2 16"/>',
    "folder": '<path d="M3 7V5h6l3 3h9v12H3V7Z"/>',
    "refresh": '<path d="M20 9a8 8 0 0 0-14-4L3 8m0-5v5h5m-4 7a8 8 0 0 0 14 4l3-3m0 5v-5h-5"/>',
    "arrow_right": '<path d="M4 12h16m-6-6 6 6-6 6"/>',
    "trash": '<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7"/>',
    "check": '<path d="m5 12 4 4 10-10"/>',
    "expand": '<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "undo": '<path d="M9 5 3 11l6 6M3 11h10a7 7 0 0 1 7 7"/>',
    "redo": '<path d="m15 5 6 6-6 6m6-6H11a7 7 0 0 0-7 7"/>',
    "grid": '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    "sliders": '<path d="M4 6h7m5 0h4M4 12h2m5 0h9M4 18h10m5 0h1"/><circle cx="13.5" cy="6" r="2.5"/><circle cx="8.5" cy="12" r="2.5"/><circle cx="16.5" cy="18" r="2.5"/>',
    "chevron_down": '<path d="m6 9 6 6 6-6"/>',
    "chevron_right": '<path d="m9 6 6 6-6 6"/>',
    "more": '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
}


def _render(svg: str, size: int) -> QIcon:
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    result = QIcon()
    for scale in (1, 2, 3):
        pixmap = QPixmap(size * scale, size * scale)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter, QRectF(0, 0, size * scale, size * scale))
        painter.end()
        pixmap.setDevicePixelRatio(scale)
        result.addPixmap(pixmap)
    return result


def icon(name: str, color: str = "#73736B", size: int = 20) -> QIcon:
    shape = _SHAPES[name]
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
           f'fill="none" stroke="{escape(color, quote=True)}" stroke-width="1.6" '
           f'stroke-linecap="round" stroke-linejoin="round">{shape}</svg>')
    return _render(svg, size)


def window_icon() -> QIcon:
    return QIcon(str(Path(__file__).parent / "assets" / "app.svg"))


def app_icon(name: str, size: int = 28) -> QIcon:
    letter = next((character for character in name if character.isalnum()), "·").upper()[0]
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
           f'<rect x="1" y="1" width="30" height="30" rx="7" fill="#E5E4DC"/>'
           f'<text x="16" y="21" text-anchor="middle" fill="#5D665F" '
           f'font-family="Segoe UI, Microsoft YaHei" font-size="15" font-weight="600">'
           f'{escape(letter)}</text></svg>')
    return _render(svg, size)
