from __future__ import annotations

import contextlib
import ctypes
from ctypes import wintypes
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from envmanager import privileges
from envmanager.main import main


class StartupPrivilegeTests(unittest.TestCase):
    def test_existing_administrator_continues_without_relaunch(self):
        with patch.object(privileges.os, "name", "nt"), \
                patch.object(privileges, "is_administrator", return_value=True), \
                patch.object(privileges, "relaunch_as_administrator") as relaunch:
            self.assertIsNone(privileges.ensure_administrator([], elevated=True))
            relaunch.assert_not_called()

    def test_non_administrator_returns_child_code(self):
        arguments = ["--config", "配置 目录(正式)'\\env.toml", "--state-dir", "相对状态"]
        with patch.object(privileges.os, "name", "nt"), \
                patch.object(privileges, "is_administrator", return_value=False), \
                patch.object(privileges, "relaunch_as_administrator", return_value=7) as relaunch:
            self.assertEqual(privileges.ensure_administrator(arguments), 7)
            relaunch.assert_called_once_with(arguments)

    def test_relaunched_process_checks_token_and_stops_retry(self):
        with patch.object(privileges.os, "name", "nt"), \
                patch.object(privileges, "is_administrator", return_value=False) as check, \
                patch.object(privileges, "relaunch_as_administrator") as relaunch:
            with self.assertRaisesRegex(privileges.ElevationError, "避免重复请求 UAC"):
                privileges.ensure_administrator([privileges.ELEVATED_ARGUMENT], elevated=True)
            check.assert_called_once()
            relaunch.assert_not_called()

    def test_relaunch_returns_before_service_or_qt_is_created(self):
        arguments = ["--config", "不存在配置(中文)' .toml", "--state-dir", "状态"]
        with patch("envmanager.main.ensure_administrator", return_value=7) as check, \
                patch("envmanager.main.build_service") as service:
            self.assertEqual(main(arguments), 7)
            check.assert_called_once_with(arguments, elevated=False)
            service.assert_not_called()

    def test_cancelled_request_reports_and_does_not_create_service(self):
        with patch("envmanager.main.ensure_administrator", side_effect=privileges.ElevationError("已取消管理员权限请求，正式界面未启动。")), \
                patch("envmanager.main.build_service") as service, \
                contextlib.redirect_stderr(io.StringIO()) as output:
            self.assertEqual(main([]), 1)
            self.assertIn("已取消", output.getvalue())
            service.assert_not_called()

    def test_configuration_check_never_checks_permission_or_builds_service(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "配置.toml"
            config.write_text('["应用"."默认"]\nX = { default_value = "", scope = "User", mode = "replace", editable = false }\n', encoding="utf-8")
            with patch("envmanager.main.ensure_administrator", side_effect=AssertionError("UAC forbidden")), \
                    patch("envmanager.main.is_administrator", side_effect=AssertionError("token check forbidden")), \
                    patch("envmanager.main.build_service", side_effect=AssertionError("service forbidden")), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--check", "--config", str(config)]), 0)

    def test_demo_skips_permission_check(self):
        # Stop immediately at service creation, before a window can be shown.
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        with patch("envmanager.main.ensure_administrator", side_effect=AssertionError("UAC forbidden")), \
                patch("envmanager.main.is_administrator", side_effect=AssertionError("token check forbidden")), \
                patch("envmanager.main.build_service", side_effect=RuntimeError("demo boundary")), \
                patch("PySide6.QtWidgets.QMessageBox.critical") as message, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--demo"]), 1)
            self.assertIn("demo boundary", message.call_args.args[2])
        self.assertIsNotNone(app)

    def test_check_admin_outputs_token_status_and_never_relaunches(self):
        for administrator in (False, True):
            with self.subTest(administrator=administrator), \
                    patch("envmanager.main.is_administrator", return_value=administrator), \
                    patch("envmanager.main.ensure_administrator", side_effect=AssertionError("UAC forbidden")), \
                    patch("envmanager.main.build_service", side_effect=AssertionError("service forbidden")), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["--check-admin"]), 0 if administrator else 1)
                self.assertIn("具有" if administrator else "没有", output.getvalue())


@unittest.skipUnless(os.name == "nt", "需要 Windows ctypes")
class NativePrivilegeTests(unittest.TestCase):
    def setUp(self):
        self.shell = MagicMock()
        self.kernel = MagicMock()
        self.ole = MagicMock()
        self.ole.CoInitializeEx.return_value = 0
        self.kernel.WaitForSingleObject.return_value = 0
        self.kernel.GetExitCodeProcess.side_effect = self.set_exit_code
        self.exit_code = 0
        self.libraries = patch.object(privileges.ctypes, "WinDLL", side_effect=lambda name, **kwargs: {
            "shell32": self.shell, "kernel32": self.kernel, "ole32": self.ole,
        }[name])
        self.libraries.start()
        self.addCleanup(self.libraries.stop)

    def set_exit_code(self, handle, code):
        ctypes.cast(code, ctypes.POINTER(wintypes.DWORD)).contents.value = self.exit_code
        return True

    def successful_execute(self, pointer):
        info = ctypes.cast(pointer, ctypes.POINTER(privileges._ShellExecuteInfo)).contents
        self.info = {
            "file": info.lpFile, "parameters": info.lpParameters,
            "cwd": info.lpDirectory, "verb": info.lpVerb,
            "show": info.nShow, "mask": info.fMask,
        }
        info.hProcess = 123
        return True

    def test_native_shell_structure_and_unicode_argument_roundtrip(self):
        self.shell.ShellExecuteExW.side_effect = self.successful_execute
        self.exit_code = 9
        arguments = ["--config", "配置 目录 (正式)'\\方案.toml", "--state-dir", "状态 目录\\", "--config=候选.toml"]
        with tempfile.TemporaryDirectory(prefix="提权参数 中文(空格)' ") as directory:
            project = Path(directory)
            module = project / "envmanager"
            module.mkdir()
            (module / "__init__.py").write_text("", encoding="utf-8")
            (module / "__main__.py").write_text(
                "import json,os,sys\nprint(json.dumps({'argv':sys.argv[1:],'cwd':os.getcwd()}))\n", encoding="utf-8")
            with patch.object(privileges.os, "getcwd", return_value=directory):
                self.assertEqual(privileges.relaunch_as_administrator(arguments), 9)
            # Execute the captured Windows command line against an inert module
            # in a temporary cwd, verifying Python's real argument decoding.
            probe = subprocess.run(subprocess.list2cmdline([self.info["file"]]) + " " + self.info["parameters"],
                                   cwd=directory, capture_output=True, text=True, check=True, timeout=20)
            captured = json.loads(probe.stdout)
            self.assertEqual(captured["argv"], [*arguments, privileges.ELEVATED_ARGUMENT])
            self.assertEqual(captured["cwd"], directory)
        self.assertEqual(self.info["verb"], "runas")
        self.assertEqual(self.info["show"], 0)
        self.assertEqual(self.info["mask"], 0x540)
        self.kernel.CloseHandle.assert_called_once_with(123)
        self.ole.CoUninitialize.assert_called_once()

    def test_cancel_and_failure_use_windows_error_and_never_wait(self):
        self.shell.ShellExecuteExW.return_value = False
        for code, message in ((1223, "已取消"), (5, "Windows 错误 5")):
            with self.subTest(code=code), patch.object(privileges.ctypes, "get_last_error", return_value=code):
                with self.assertRaisesRegex(privileges.ElevationError, message):
                    privileges.relaunch_as_administrator([])
        self.kernel.WaitForSingleObject.assert_not_called()
        self.kernel.CloseHandle.assert_not_called()

    def test_frozen_relaunch_uses_executable_arguments_without_python_module(self):
        self.shell.ShellExecuteExW.side_effect = self.successful_execute
        self.exit_code = 9
        with patch.object(privileges.sys, "frozen", True, create=True):
            self.assertEqual(privileges.relaunch_as_administrator(["--demo", "--config", "配置 有空格.toml"]), 9)
        self.assertNotIn("-m envmanager", self.info["parameters"])
        self.assertIn("--demo", self.info["parameters"])
        self.assertIn("配置 有空格.toml", self.info["parameters"])
        self.assertIn(privileges.ELEVATED_ARGUMENT, self.info["parameters"])

    def test_wait_failure_closes_process_handle(self):
        self.shell.ShellExecuteExW.side_effect = self.successful_execute
        self.kernel.WaitForSingleObject.return_value = 0xFFFFFFFF
        with patch.object(privileges.ctypes, "get_last_error", return_value=6):
            with self.assertRaisesRegex(privileges.ElevationError, "无法等待"):
                privileges.relaunch_as_administrator([])
        self.kernel.CloseHandle.assert_called_once_with(123)
        self.ole.CoUninitialize.assert_called_once()

    def test_pythonw_failure_uses_native_message_dialog(self):
        user = MagicMock()
        with patch.object(privileges.ctypes, "WinDLL", return_value=user), patch.object(privileges.sys, "stderr", None):
            privileges.report_startup_error("已取消管理员权限请求，正式界面未启动。")
        user.MessageBoxW.assert_called_once()
        self.assertIn("已取消", user.MessageBoxW.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
