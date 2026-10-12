"""Disposable native long-name fixture using the real service and selector."""
import os
import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['QT_QPA_PLATFORM'] = 'windows'
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QApplication
from tools.capture_web_inspired_ui import FIXTURE, settle
from envmanager.config import load_config
from envmanager.main import window_is_visible
from envmanager.persistence import StateRepository
from envmanager.service import EnvironmentService
from envmanager.store import MemoryStore
from envmanager.ui import MainWindow

app = QApplication([])
name = 'apache-maven-3.9.11-portable-release'
fixture = FIXTURE.replace('[Maven."3.9.11"]', f'[Maven."{name}"]')
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/selector-refinement-equal/long-name')
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='selector-long-name-') as directory:
    path = Path(directory) / 'env.toml'
    path.write_text(fixture, encoding='utf-8')
    store = MemoryStore()
    state = StateRepository(Path(directory) / 'state')
    service = EnvironmentService(load_config(path), store, state)
    result = service.execute(service.plan('Maven', name))
    assert result.success
    baseline = (len(store.writes), store.notifications, state.snapshot(), service.history())
    window = MainWindow(service, path, demo=True)
    window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    window._select_app('Maven')
    window.showMaximized()
    window.raise_()
    window.activateWindow()
    settle(app)
    combo = window.option_combo
    combo.showPopup()
    settle(app)
    popup = combo.view().window()
    assert combo.width() == popup.width() == 166
    assert name in combo.toolTip()
    assert name in combo.itemData(0, Qt.ItemDataRole.AccessibleTextRole)
    assert window_is_visible(popup)
    area = QRect(combo.mapToGlobal(QPoint(0, 0)), combo.size()).united(popup.frameGeometry())
    area = area.adjusted(-12, -24, 12, 12).intersected(window.geometry())
    image = combo.screen().grabWindow(0, area.x(), area.y(), area.width(), area.height())
    output = args.output / f'target-long-name-desktop-{round(100 * image.devicePixelRatio())}pct.png'
    assert image.save(str(output))
    combo.hidePopup()
    assert baseline == (len(store.writes), store.notifications, state.snapshot(), service.history())
    assert path.read_text(encoding='utf-8') == fixture
    window.close()
    window.deleteLater()
    app.processEvents()
    print(f'Long-name native capture: width 166, full semantics retained, browsing writes 0; {output.name}')
