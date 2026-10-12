"""Initial work-area sizing with MemoryStore; no native screen changes."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication

from envmanager.config import parse_config
from envmanager.persistence import StateRepository
from envmanager.service import EnvironmentService
from envmanager.store import MemoryStore
from envmanager.ui import MainWindow


class WindowWorkAreaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        folder = Path(temporary.name)
        source = '[App.Default]\nVALUE = {default_value="A",scope="User",mode="replace",editable=true}\n'
        path = folder / 'qa.toml'
        path.write_text(source, encoding='utf-8')
        service = EnvironmentService(parse_config(source), MemoryStore(), StateRepository(folder / 'state'))
        self.window = MainWindow(service, path, demo=True)
        self.addCleanup(self.window.close)

    def test_200_percent_equivalent_work_area_fits_frame_and_footer(self):
        self.window.show()
        self.app.processEvents()
        available = QRect(100, 50, 1280, 760)
        self.assertGreater(self.window.frameGeometry().height(), available.height())
        self.window._fit_startup_geometry(available)
        self.app.processEvents()
        self.assertTrue(available.contains(self.window.frameGeometry()))
        for control in (self.window.apply_button, self.window.deactivate_button):
            top_left = control.mapTo(self.window, QPoint(0, 0))
            self.assertTrue(self.window.rect().contains(QRect(top_left, control.size())))
            self.assertTrue(control.isVisible())

    def test_larger_work_area_preserves_default_client_size(self):
        self.window.show()
        self.app.processEvents()
        original = self.window.size()
        available = QRect(-1800, 40, 1800, 1050)
        self.window._fit_startup_geometry(available)
        self.app.processEvents()
        self.assertEqual(self.window.size(), original)
        self.assertTrue(available.contains(self.window.frameGeometry()))

    def test_native_first_show_fits_once_and_preserves_later_user_resize(self):
        class Screen:
            def availableGeometry(self):
                return QRect(0, 0, 1280, 760)
        with patch.object(self.window, 'screen', return_value=Screen()), \
                patch.object(QApplication, 'platformName', return_value='windows'):
            self.window.show()
            self.app.processEvents()
            self.assertTrue(Screen().availableGeometry().contains(self.window.frameGeometry()))
            self.assertTrue(self.window._startup_geometry_fitted)
            self.window.resize(900, 600)
            self.window.hide()
            self.window.show()
            self.app.processEvents()
            self.assertEqual((self.window.width(), self.window.height()), (900, 600))


if __name__ == '__main__':
    unittest.main()
