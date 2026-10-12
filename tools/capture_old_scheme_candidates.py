"""Native apply-flow evidence with public TOML and disposable MemoryStore."""
import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "windows"
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QLabel
from envmanager.config import load_config
from envmanager.main import window_is_visible
from envmanager.models import RegistryState
from envmanager.persistence import StateRepository
from envmanager.service import EnvironmentService
from envmanager.store import MemoryStore
from envmanager.ui import MainWindow, OldSchemeDialog, PreviewDialog
from tools.capture_web_inspired_ui import settle

FIXTURE = r'''
[Git]
default_option = "PortableGit"
[Git.PortableGit]
Path = { default_value = 'C:\Tools\PortableGit\cmd', scope = "User", mode = "+append", editable = false }
[Git.GitPortable]
Path = { default_value = 'C:\Tools\GitPortable\cmd', scope = "User", mode = "+append", editable = false }
[Editor.Default]
Path = { default_value = 'C:\Tools\Editor\bin', scope = "User", mode = "+append", editable = false }
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/old-scheme-candidates")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    report = {"platform": app.platformName(), "fixture": "public TOML, MemoryStore, disposable state", "cases": []}
    for case in ("single-zero-match", "batch-zero-match", "actual-partial-residue"):
        with tempfile.TemporaryDirectory(prefix="old-scheme-candidate-") as temporary:
            path = Path(temporary) / "env.toml"
            fixture = FIXTURE
            value = r"C:\Tools\PortableGit\cmd;C:\Tools\Editor\bin;C:\Other"
            if case == "actual-partial-residue":
                fixture = fixture.replace(r"C:\Tools\GitPortable\cmd'", r"C:\Tools\GitPortable\cmd;C:\Tools\Missing'")
                value += r";C:\Tools\GitPortable\cmd"
            path.write_text(fixture, encoding="utf-8")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            store = MemoryStore([RegistryState("Path", "User", True, value, 1)])
            state = StateRepository(Path(temporary) / "state")
            service = EnvironmentService(load_config(path), store, state)
            window = MainWindow(service, path, demo=True)
            window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            window.resize(1100, 680)
            window.show()
            window.raise_()
            window.activateWindow()
            settle(app)
            if case == "batch-zero-match":
                for index in range(window.app_list.count()):
                    window.app_list.item(index).setCheckState(Qt.CheckState.Checked)
            baseline = (store.read("User", "Path"), len(store.writes), store.notifications,
                        state.snapshot(), service.history(), digest)
            seen = []

            def capture(dialog):
                seen.append(type(dialog).__name__)
                dialog.show()
                dialog.raise_()
                settle(app)
                assert window_is_visible(dialog)
                assert dialog.width() <= app.primaryScreen().availableGeometry().width()
                assert dialog.height() <= app.primaryScreen().availableGeometry().height()
                labels = "\n".join(label.text() for label in dialog.findChildren(QLabel))
                if case == "actual-partial-residue":
                    assert dialog.list.count() == 1
                    assert "准备应用：Git / PortableGit" in labels
                    assert "确认前不会写入环境变量" in labels
                else:
                    assert dialog.plan.can_execute
                    assert all(not change.changed for change in dialog.plan.changes)
                image = dialog.grab()
                dpr = round(100 * image.devicePixelRatio())
                filename = f"{case}-{dpr}pct.png"
                assert image.save(str(args.output / filename))
                report["cases"].append({"case": case, "dialog": type(dialog).__name__,
                                        "dpr": image.devicePixelRatio(), "size": [dialog.width(), dialog.height()],
                                        "native_visible": True, "screenshot": filename})
                dialog.hide()
                return QDialog.DialogCode.Rejected

            with patch.object(OldSchemeDialog, "exec", new=capture), patch.object(PreviewDialog, "exec", new=capture):
                window._apply()
            expected = "OldSchemeDialog" if case == "actual-partial-residue" else "PreviewDialog"
            assert seen == [expected], (case, seen)
            assert baseline == (store.read("User", "Path"), len(store.writes), store.notifications,
                                state.snapshot(), service.history(), hashlib.sha256(path.read_bytes()).hexdigest())
            report["cases"][-1].update(writes=0, notifications=0, state_history_config_unchanged=True)
            window.close()
            window.deleteLater()
            settle(app)
    report_path = args.output / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
