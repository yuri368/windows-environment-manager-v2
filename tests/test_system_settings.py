"""Windows shortcut dispatch and UI errors; no real settings window is opened."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton
from envmanager import windows_shell
from envmanager.main import build_service, PROJECT_ROOT
from envmanager.ui import MainWindow


class SystemSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_dispatch_uses_system_files_and_no_shell(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            for name in ("rundll32.exe", "sysdm.cpl"):
                (directory / name).touch()
            # Linux subprocess lacks this Windows constant. Model the native
            # flag explicitly while keeping the dispatch contract test active.
            with patch.object(windows_shell, "system_directory", return_value=directory), \
                    patch.object(windows_shell.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True), \
                    patch.object(windows_shell.subprocess, "Popen") as launch:
                windows_shell.open_environment_settings()
            launch.assert_called_once()
            args, options = launch.call_args
            self.assertEqual(args[0], [str(directory / "rundll32.exe"), "sysdm.cpl,EditEnvironmentVariables"])
            self.assertEqual(options["cwd"], str(directory))
            self.assertFalse(options["shell"])
            self.assertEqual(options["creationflags"], 0x08000000)
            for stream in ("stdin", "stdout", "stderr"):
                self.assertEqual(options[stream], windows_shell.subprocess.DEVNULL)

    def test_missing_components_do_not_launch(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(windows_shell, "system_directory", return_value=Path(temp)), \
                patch.object(windows_shell.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(OSError, "找不到"):
                windows_shell.open_environment_settings()
            launch.assert_not_called()

    def test_non_windows_reports_unsupported(self):
        with patch.object(windows_shell.sys, "platform", "linux"):
            with self.assertRaisesRegex(OSError, "仅支持 Windows"):
                windows_shell.system_directory()

    def test_button_dispatch_error_and_narrow_geometry_without_writes(self):
        service, temporary = build_service(PROJECT_ROOT / "env.example.toml", demo=True)
        self.addCleanup(temporary.cleanup)
        window = MainWindow(service, PROJECT_ROOT / "env.example.toml", demo=True)
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        window.resize(850, 560)
        window.show()
        self.app.processEvents()
        button = window.system_settings_button
        self.assertTrue(button.isVisible())
        self.assertTrue(button.parentWidget().rect().contains(button.geometry()))
        self.assertGreaterEqual(button.width(), button.sizeHint().width())
        buttons = button.parentWidget().findChildren(QPushButton)
        for other in buttons:
            if other is not button:
                self.assertFalse(button.geometry().intersects(other.geometry()))
        with patch("envmanager.ui.open_environment_settings") as launch:
            button.click()
            launch.assert_called_once_with()
        with patch("envmanager.ui.open_environment_settings", side_effect=OSError("测试失败")), \
                patch("envmanager.ui.QMessageBox.warning") as warning:
            button.click()
            self.assertEqual(warning.call_args.args[1], "无法打开环境变量设置")
            self.assertIn("测试失败", warning.call_args.args[2])
        self.assertEqual(service.store.writes, [])
        self.assertEqual(service.state.snapshot()["revision"], 0)


if __name__ == "__main__":
    unittest.main()
