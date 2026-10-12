"""Render the native Compact Hybrid workspaces using public synthetic data.

Run once per QT_SCALE_FACTOR (1, 1.5, 2). No WindowsStore is constructed;
configuration editing uses a disposable copy, and every source hash is checked.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, QTimer, QEventLoop
from PySide6.QtWidgets import QApplication
from envmanager.config_editor_ui import ConfigEditorDialog
from envmanager.main import build_service, window_is_visible
from envmanager.ui import MainWindow


def settle(app):
    app.processEvents()
    loop = QEventLoop()
    QTimer.singleShot(250, loop.quit)
    loop.exec()
    app.processEvents()


def contained(widget, ancestor):
    origin = widget.mapTo(ancestor, QPoint(0, 0))
    return widget.isVisible() and origin.x() >= 0 and origin.y() >= 0 and (
        origin.x() + widget.width() <= ancestor.width() and
        origin.y() + widget.height() <= ancestor.height())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/compact-ui-review")
    parser.add_argument("--platform", choices=("offscreen", "windows"), default="offscreen")
    args = parser.parse_args()
    os.environ["QT_QPA_PLATFORM"] = args.platform
    app = QApplication.instance() or QApplication([])
    args.output.mkdir(parents=True, exist_ok=True)
    sources = [ROOT / "env.example.toml"]
    if (ROOT / "env.toml").exists():
        sources.append(ROOT / "env.toml")
    hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    scale = os.environ.get("QT_SCALE_FACTOR", "1")
    report = {"scale_factor": scale, "platform": app.platformName(), "captures": []}
    with tempfile.TemporaryDirectory(prefix="compact-ui-") as temporary:
        config_path = Path(temporary) / "env.toml"
        config_path.write_bytes(sources[0].read_bytes())
        service, state_temporary = build_service(config_path, demo=True)
        main_window = MainWindow(service, config_path, demo=True)
        editor = ConfigEditorDialog(config_path, service)
        try:
            service.detection_snapshot()
            for window, name in ((main_window, "main"), (editor, "editor")):
                window.show()
                settle(app)
                for width, height in ((1024, 768), (1366, 768), (1536, 864)):
                    window.resize(width, height)
                    settle(app)
                    actual_size = [window.width(), window.height()]
                    if args.platform == "offscreen":
                        assert actual_size == [width, height], (name, actual_size, width, height)
                    action = window.apply_button if name == "main" else window.save_button
                    assert contained(action, window), f"{name} primary action clipped"
                    if name == "editor":
                        assert editor.overview.isVisible() and editor.form.isVisible()
                        assert editor.record_scroll.horizontalScrollBar().maximum() == 0, (
                            width, editor.record_scroll.viewport().width(),
                            editor.record_scroll.widget().width(),
                            editor.record_scroll.widget().minimumSizeHint().width(),
                            editor.form.minimumSizeHint().width(), editor.inspector.minimumSizeHint().width(),
                            editor.value_pane.minimumSizeHint().width())
                    pixmap = window.grab()
                    dpi = round(100 * pixmap.devicePixelRatio())
                    filename = f"{name}-{width}x{height}-{args.platform}-{dpi}pct.png"
                    assert pixmap.save(str(args.output / filename))
                    report["captures"].append({
                        "file": filename, "requested_size": [width, height], "logical_size": actual_size,
                        "pixels": [pixmap.width(), pixmap.height()],
                        "dpr": pixmap.devicePixelRatio(),
                        "qt_visible": window.isVisible(),
                        "native_visible": window_is_visible(window) if args.platform == "windows" else None,
                        "primary_action_visible": True,
                    })
                window.hide()
            assert service.store.writes == []
            assert service.store.notifications == 0
            assert service.state.snapshot()["revision"] == 0
            assert config_path.read_bytes() == sources[0].read_bytes()
            assert hashes == {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
            report.update(environment_writes=0, notifications=0, state_revision=0,
                          configuration_unchanged=True)
            (args.output / f"report-{args.platform}-{scale.replace('.', '_')}x.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False))
        finally:
            main_window.deleteLater()
            editor.deleteLater()
            app.processEvents()
            state_temporary.cleanup()


if __name__ == "__main__":
    main()
