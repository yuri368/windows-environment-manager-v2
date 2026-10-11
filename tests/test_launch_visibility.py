"""Exercise Windows startup show flags with real Qt windows and no UAC."""

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys
import unittest


@unittest.skipUnless(sys.platform == "win32", "Windows startup window flags")
class LaunchVisibilityTests(unittest.TestCase):
    def test_native_hidden_window_is_detected_even_when_qt_reports_visible(self):
        # Other UI tests use offscreen Qt. Run native visibility in its own
        # process so it cannot inherit their QApplication/platform plugin.
        environment = os.environ.copy()
        environment["QT_QPA_PLATFORM"] = "windows"
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        result = subprocess.run(
            [str(pythonw), "-c", "from tests.test_launch_visibility import native_visibility_probe; native_visibility_probe()"],
            cwd=Path(__file__).resolve().parents[1], env=environment,
            capture_output=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, repr(result.stderr))


def native_visibility_probe():
    from PySide6.QtWidgets import QApplication, QWidget
    from envmanager.main import window_is_visible
    app = QApplication([])
    window = QWidget()
    window.setWindowTitle("EnvironmentManager visibility regression test")
    native_show = ctypes.WinDLL("user32", use_last_error=True).ShowWindow
    native_show.argtypes = [wintypes.HWND, ctypes.c_int]
    native_show.restype = wintypes.BOOL
    try:
        window.show()
        app.processEvents()
        assert window_is_visible(window), "Normal native window is not visible"
        # Only this test-owned HWND is changed.
        native_show(int(window.winId()), 0)
        assert window.isVisible(), "Qt no longer reports visible"
        assert not window_is_visible(window), "Hidden HWND passed verification"
        native_show(int(window.winId()), 1)
        assert window_is_visible(window), "Restored HWND is not visible"
    finally:
        window.close()


if __name__ == "__main__":
    unittest.main()
