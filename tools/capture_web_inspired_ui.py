"""Capture the real native UI with public fixtures, two Maven schemes and history.

Only MemoryStore and temporary TOML/state are used. Initial history comes from
the actual service execution path; subsequent browsing must leave that baseline
unchanged. Run per QT_SCALE_FACTOR=1,1.5,2 with --platform windows or offscreen.
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

from PySide6.QtCore import QEventLoop, QPoint, QRect, QTimer, Qt
from PySide6.QtWidgets import QApplication
from envmanager.config import load_config
from envmanager.config_editor_ui import ConfigEditorDialog
from envmanager.main import window_is_visible
from envmanager.models import RegistryState
from envmanager.persistence import StateRepository
from envmanager.service import EnvironmentService
from envmanager.store import MemoryStore
from envmanager.ui import MainWindow


FIXTURE = r'''# Public synthetic UI fixture; no personal paths or credentials.
[Java.JDK17]
JAVA_HOME = { default_value = 'C:\Tools\Java\jdk-17', scope = "User", mode = "replace", editable = false }
Path = { default_value = '%JAVA_HOME%\bin', scope = "User", mode = "+append", editable = false }

[Python.Python313]
PYTHON_HOME = { default_value = 'C:\Tools\Python313', scope = "User", mode = "replace", editable = false }
Path = { default_value = '%PYTHON_HOME%;%PYTHON_HOME%\Scripts', scope = "User", mode = "append", editable = false }

[Maven."3.9.11"]
MAVEN_HOME = { default_value = 'C:\Tools\Maven\apache-maven-3.9.11', scope = "User", mode = "replace", editable = true, choices = [{ label = "默认安装目录", value = 'C:\Tools\Maven\apache-maven-3.9.11' }, { label = "便携目录", value = 'D:\Portable\Maven\3.9.11' }] }
Path = { default_value = '%MAVEN_HOME%\bin', scope = "User", mode = "+append", editable = false }

[Maven."3.3.3"]
MAVEN_HOME = { default_value = 'C:\Tools\Maven\apache-maven-3.3.3', scope = "User", mode = "replace", editable = true }
Path = { default_value = '%MAVEN_HOME%\bin', scope = "User", mode = "+append", editable = false }

[Hadoop."2.9.2"]
HADOOP_HOME = { default_value = 'C:\Tools\Hadoop', scope = "User", mode = "replace", editable = false }
Path = { default_value = '%HADOOP_HOME%\bin', scope = "User", mode = "+append", editable = false }

[Scala."2.12.15"]
SCALA_HOME = { default_value = 'C:\Tools\Scala', scope = "User", mode = "replace", editable = false }
Path = { default_value = '%SCALA_HOME%\bin', scope = "User", mode = "+append", editable = false }

[Git.PortableGit]
Path = { default_value = 'C:\Tools\PortableGit\cmd', scope = "User", mode = "append", editable = false }

[VSCode.Insiders]
Path = { default_value = 'C:\Tools\VSCode\bin', scope = "User", mode = "append", editable = false }
'''


def settle(app):
    app.processEvents()
    loop = QEventLoop()
    QTimer.singleShot(100, loop.quit)
    loop.exec()
    app.processEvents()


def contained(widget, parent):
    origin = widget.mapTo(parent, QPoint(0, 0))
    return widget.isVisible() and parent.rect().contains(origin) and parent.rect().contains(
        origin + QPoint(widget.width() - 1, widget.height() - 1))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/web-inspired-ui")
    parser.add_argument("--platform", choices=("offscreen", "windows"), default="offscreen")
    args = parser.parse_args()
    os.environ["QT_QPA_PLATFORM"] = args.platform
    app = QApplication.instance() or QApplication([])
    args.output.mkdir(parents=True, exist_ok=True)
    report = {"platform": app.platformName(), "scale_factor": os.environ.get("QT_SCALE_FACTOR", "1"),
              "data": "public synthetic fixture; actual service, MemoryStore, disposable state", "captures": []}
    sources = [p for p in (ROOT / "env.example.toml", ROOT / "env.toml") if p.exists()]
    hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    with tempfile.TemporaryDirectory(prefix="web-inspired-ui-") as temporary:
        path = Path(temporary) / "env.toml"
        path.write_text(FIXTURE, encoding="utf-8", newline="")
        store = MemoryStore([RegistryState("Path", "User", True, r"C:\Tools\Common\bin", 2),
                             RegistryState("Path", "System", True, r"C:\Windows;C:\Windows\System32", 2),
                             RegistryState("PUBLIC_EXTRA", "User", True, "not declared in TOML", 1)])
        state = StateRepository(Path(temporary) / "state")
        service = EnvironmentService(load_config(path), store, state)
        for name in ("Java", "Python", "Maven", "Git", "VSCode"):
            plan = service.plan(name, service.selected_option(name))
            if plan.needs_order:
                orders = {change.record_key: tuple(change.calculation.items) for change in plan.changes
                          if change.calculation and change.calculation.needs_order}
                plan = service.plan(name, service.selected_option(name), orders=orders)
            assert plan.can_execute, (name, plan.conflicts, plan.needs_order)
            result = service.execute(plan)
            assert result.success, result
        report["fixture_setup"] = {"memory_writes": len(store.writes), "notifications": store.notifications,
                                   "history_records": len(service.history())}
        baseline = (len(store.writes), store.notifications, state.snapshot(), service.history())
        window = MainWindow(service, path, demo=True)
        editor = ConfigEditorDialog(path, service)
        if args.platform == "windows":
            # Keep this disposable fixture in front for desktop crops; native
            # visibility alone also permits an occluded background window.
            window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)

        def capture(widget, name, size=(1366, 768)):
            if size is not None:
                widget.resize(*size)
                widget.show()
            settle(app)
            action = window.apply_button if widget is window and window.workspace_stack.currentIndex() == 0 else (
                editor.save_button if widget is editor else None)
            if action is not None:
                assert contained(action, widget), f"{name}: primary action clipped"
            assert widget.isVisible(), f"{name}: Qt window is hidden"
            if args.platform == "windows":
                assert window_is_visible(widget), f"{name}: native window is hidden"
            pixmap = widget.grab()
            dpi = round(100 * pixmap.devicePixelRatio())
            requested = size or (widget.width(), widget.height())
            filename = f"{name}-{requested[0]}x{requested[1]}-{args.platform}-{dpi}pct.png"
            assert pixmap.save(str(args.output / filename))
            report["captures"].append({"file": filename, "logical_size": [widget.width(), widget.height()],
                "pixels": [pixmap.width(), pixmap.height()], "dpr": pixmap.devicePixelRatio(),
                "qt_visible": widget.isVisible(),
                "native_visible": window_is_visible(widget) if args.platform == "windows" else None})

        try:
            window._select_app("Maven")
            capture(window, "workbench")
            report["rail_geometry"] = []
            for index in range(window.app_list.count()):
                item = window.app_list.item(index)
                row = window.app_list.itemWidget(item)
                assert contained(row.match_label, row), item.text()
                assert contained(row.option_combo, row), item.text()
                assert row.width() <= window.app_list.viewport().width()
                report["rail_geometry"].append({"application": item.data(Qt.ItemDataRole.UserRole), "row_width": row.width(),
                    "viewport_width": window.app_list.viewport().width(),
                    "match_count": row.match_label.text(), "match_count_and_scheme_visible": True})
            if args.platform == "windows":
                window.showMaximized()
                window.raise_()
                window.activateWindow()
                settle(app)
            for combo, label in ((window.option_combo, "target-scheme"),
                                 (window.app_list.itemWidget(window.app_list.currentItem()).option_combo, "rail-scheme")):
                combo.showPopup()
                settle(app)
                view = combo.view()
                popup = view.window()
                assert combo.screen().availableGeometry().contains(popup.frameGeometry())
                assert popup.frameGeometry() == popup.geometry()
                assert view.geometry() == popup.rect()
                assert view.viewport().height() == sum(view.sizeHintForRow(i) for i in range(combo.count()))
                capture(popup, label, None)
                if args.platform == "windows":
                    # A real desktop crop includes native chrome, which a
                    # QWidget.grab alone cannot prove absent. Crop only the
                    # public-fixture window and the popup/selector area.
                    selector = QRect(combo.mapToGlobal(QPoint(0, 0)), combo.size())
                    area = selector.united(popup.frameGeometry()).adjusted(-12, -24, 12, 12)
                    area = area.intersected(window.geometry()).intersected(combo.screen().geometry())
                    assert area.contains(popup.frameGeometry())
                    pixmap = combo.screen().grabWindow(0, area.x(), area.y(), area.width(), area.height())
                    assert not pixmap.isNull()
                    dpi = round(100 * pixmap.devicePixelRatio())
                    filename = f"{label}-desktop-{dpi}pct.png"
                    assert pixmap.save(str(args.output / filename))
                    report["captures"].append({"file": filename, "source": "native desktop crop",
                        "logical_size": [area.width(), area.height()],
                        "pixels": [pixmap.width(), pixmap.height()], "dpr": pixmap.devicePixelRatio(),
                        "qt_visible": popup.isVisible(), "native_visible": window_is_visible(popup),
                        "frame_and_list_share_bounds": True, "all_rows_visible": True})
                combo.hidePopup()
            if args.platform == "windows":
                window.showNormal()
            capture(window, "workbench", (1024, 768))
            window._show_workspace(1)
            capture(window, "windows-environment")
            window._show_workspace(2)
            capture(window, "history")
            window.hide()
            editor.select_application("Maven")
            spec = editor.session.document.records("Maven", "3.9.11")[0]
            editor.select_record(spec)
            capture(editor, "editor-schemes")
            assert editor.overview.topLevelItemCount() == 2
            groups = [{"title": editor.overview.topLevelItem(i).text(0),
                       "records": [editor.overview.topLevelItem(i).child(j).data(0, Qt.ItemDataRole.UserRole)[1].key
                                   for j in range(editor.overview.topLevelItem(i).childCount())]}
                      for i in range(editor.overview.topLevelItemCount())]
            report["scheme_groups"] = groups
            capture(editor, "editor-schemes", (1024, 768))
            assert baseline == (len(store.writes), store.notifications, state.snapshot(), service.history())
            assert path.read_text(encoding="utf-8") == FIXTURE
            assert hashes == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
            report["browsing"] = {"additional_writes": 0, "additional_notifications": 0,
                                   "state_and_history_unchanged": True, "project_configuration_unchanged": True}
            scale = report["scale_factor"].replace(".", "_")
            (args.output / f"report-{args.platform}-{scale}x.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False))
        finally:
            window.deleteLater()
            editor.deleteLater()
            app.processEvents()


if __name__ == "__main__":
    main()
