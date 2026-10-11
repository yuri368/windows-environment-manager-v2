"""Capture public UI previews from the example and temporary simulated state."""

from __future__ import annotations

import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication
from envmanager.main import build_service
from envmanager.ui import MainWindow


def main():
    protected = [ROOT / "env.example.toml"]
    if (ROOT / "env.toml").exists():
        protected.append(ROOT / "env.toml")
    hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in protected}
    app = QApplication.instance() or QApplication([])
    service, temporary = build_service(ROOT / "env.example.toml", demo=True)
    window = MainWindow(service, ROOT / "env.example.toml", demo=True)
    output = ROOT / "docs" / "images"
    output.mkdir(parents=True, exist_ok=True)
    try:
        for name, size in (("main-window.png", (1280, 800)), ("main-window-narrow.png", (850, 560))):
            window.resize(*size)
            window.show()
            app.processEvents()
            loop = QEventLoop()
            QTimer.singleShot(180, loop.quit)
            loop.exec()
            button = window.system_settings_button
            assert button.isVisible()
            assert button.parentWidget().rect().contains(button.geometry())
            assert button.width() >= button.sizeHint().width()
            pixmap = window.grab()
            assert pixmap.save(str(output / name))
            print(f"{name}: {window.width()}x{window.height()}, DPR={pixmap.devicePixelRatio()}")
        assert service.store.writes == []
        assert service.store.notifications == 0
        assert service.state.snapshot()["revision"] == 0
        assert hashes == {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in protected}
        print("PASS: native demo screenshots; no persistent environment writes or input changes.")
    finally:
        window.close()
        temporary.cleanup()


if __name__ == "__main__":
    main()
