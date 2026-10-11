"""Render Issue #2 UI acceptance states using an isolated in-memory registry."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from envmanager.config import parse_config
from envmanager.models import RegistryState
from envmanager.persistence import StateRepository
from envmanager.removal import RemovalRequest, RemovalScope
from envmanager.service import EnvironmentService
from envmanager.store import MemoryStore
from envmanager.ui import MainWindow, PreviewDialog, SharedConflictDialog


CONFIG = r'''
[Java.JDK8]
JAVA_HOME = { default_value = 'C:\Java\jdk8', scope = 'User', mode = 'replace', editable = true }
Path = { default_value = '%JDK8_HOME%\bin;C:\CommonTools;C:\JavaOnly', scope = 'User', mode = '+append', editable = true }
[Java.JDK17]
JAVA_HOME = { default_value = 'C:\Java\jdk17', scope = 'User', mode = 'replace', editable = true }
Path = { default_value = '%JDK17_HOME%\bin;C:\CommonTools', scope = 'User', mode = '+append', editable = true }
[Maven.Default]
MAVEN_HOME = { default_value = 'C:\Maven', scope = 'User', mode = 'replace', editable = true }
Path = { default_value = '%JDK8_HOME%\bin;C:\CommonTools', scope = 'User', mode = '-append', editable = true }
[Python.Py311]
Path = { default_value = 'C:\CommonTools;C:\Python311', scope = 'User', mode = '+append', editable = true }
[Python.Py312]
Path = { default_value = 'C:\CommonTools;C:\Python312', scope = 'User', mode = '+append', editable = true }
'''


def capture(app, widget, destination, size):
    widget.resize(*size)
    widget.show()
    app.processEvents()
    loop = QEventLoop()
    QTimer.singleShot(100, loop.quit)
    loop.exec()
    app.processEvents()
    pixmap = widget.grab()
    assert pixmap.save(str(destination))
    return {"file": destination.name, "size": [widget.width(), widget.height()]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "artifacts" / "issue2-review")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as temporary:
        config = parse_config(CONFIG)
        store = MemoryStore([
            RegistryState("JAVA_HOME", "User", True, r"C:\Java\jdk8", 1),
            RegistryState("Path", "User", True,
                          r"C:\Windows\System32;%JDK8_HOME%\bin;C:\CommonTools;C:\JavaOnly", 2),
        ])
        service = EnvironmentService(config, store, StateRepository(Path(temporary) / "state"))
        window = MainWindow(service, Path(temporary) / "env.toml", demo=True)
        dialogs = []
        try:
            window.cards[1].details_toggle.click()
            reports = [capture(app, window, args.out / "matching-main.png", (1190, 790))]
            reports.append(capture(app, window, args.out / "matching-narrow.png", (850, 560)))
            draft = service.prepare_remove(RemovalRequest(
                RemovalScope.SCHEME, frozenset(("Java",)), {"Java": "JDK8"}))
            assert len(draft.shared_conflicts) == 2
            conflict = SharedConflictDialog(draft.shared_conflicts, len(draft.ordinary_atoms))
            dialogs.append(conflict)
            conflict.decision_boxes[draft.shared_conflicts[1].atom.atom_id].setCurrentIndex(1)
            reports.append(capture(app, conflict, args.out / "shared-conflicts.png", (760, 560)))
            plan = service.finalize_remove(draft.id, conflict.decisions())
            preview = PreviewDialog(plan)
            dialogs.append(preview)
            reports.append(capture(app, preview, args.out / "removal-preview.png", (840, 700)))
            assert store.writes == []
            assert store.notifications == 0
            assert service.state.snapshot()["revision"] == 0
            result = {"captures": reports, "registry_writes": 0, "notifications": 0,
                      "state_revision": 0, "shared_conflicts": len(draft.shared_conflicts)}
            (args.out / "report.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(result, ensure_ascii=False, indent=2))
        finally:
            for dialog in dialogs:
                dialog.close()
                dialog.deleteLater()
            window.close()
            window.deleteLater()
            app.processEvents()


if __name__ == "__main__":
    main()
