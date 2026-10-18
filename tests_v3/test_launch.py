"""Launcher policy tests; default Windows path is always replaced by a mock."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from envmanager_v3 import __main__ as launch


class LaunchTests(unittest.TestCase):
    def setUp(self):
        # All test filesystem writes stay within the packaging subtask directory.
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / "packaging_v3")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.assets = self.root / "assets"
        self.assets.mkdir()
        (self.assets / "index.html").write_text("product", encoding="utf-8")
        self.args = ["--data-dir", str(self.root / "data"), "--assets", str(self.assets)]
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.stdout))
        self.enterContext(contextlib.redirect_stderr(self.stderr))
        self.windows = self.enterContext(patch.object(launch, "WindowsEnvironment"))
        self.file = self.enterContext(patch.object(launch, "FileEnvironment"))
        self.factory = self.enterContext(patch.object(launch, "Service"))
        self.service = self.factory.return_value
        self.service.planner.engine_hash = "rules-hash"
        self.service.work.data = {"id": "workspace"}
        self.service.workspace.return_value = {"id": "workspace"}
        self.server_factory = self.enterContext(patch.object(launch, "create_server"))
        self.server = self.server_factory.return_value
        self.server.launch_url = "http://127.0.0.1:12345/#ticket=test"
        self.browser = self.enterContext(patch.object(launch.webbrowser, "open"))

    def test_default_windows_check_is_mocked_and_never_falls_back(self):
        self.enterContext(patch.object(launch, "os", SimpleNamespace(name="nt", environ=os.environ)))
        self.assertEqual(launch.main(self.args + ["--check"]), 0)
        self.windows.assert_called_once_with()
        self.file.assert_not_called()
        self.assertIs(self.factory.call_args.kwargs["environment"], self.windows.return_value)
        self.server_factory.assert_not_called()
        self.browser.assert_not_called()
        self.service.close.assert_called_once_with()
        self.assertEqual(json.loads(self.stdout.getvalue())["status"], "ok")

    def test_file_backend_uses_explicit_environment_path(self):
        target = self.root / "explicit.json"
        self.assertEqual(launch.main(self.args + ["--backend", "file", "--environment-file", str(target), "--check"]), 0)
        self.file.assert_called_once_with(target)
        self.windows.assert_not_called()

    def test_file_backend_default_environment_is_inside_data_directory(self):
        self.assertEqual(launch.main(self.args + ["--backend", "file", "--check"]), 0)
        self.file.assert_called_once_with((self.root / "data" / "environment.json").resolve())

    def test_invalid_arguments_do_not_create_adapters_or_data(self):
        for args in (["--backend", "demo"], ["--port", "bad"], ["--back", "file"], ["--unknown"]):
            with self.subTest(args=args), self.assertRaises(SystemExit) as raised:
                launch.main(self.args + args)
            self.assertEqual(raised.exception.code, 2)
        self.windows.assert_not_called()
        self.file.assert_not_called()
        self.assertFalse((self.root / "data").exists())

    def test_unc_data_directory_is_rejected_before_adapters(self):
        if os.name != "nt":
            self.skipTest("Windows UNC path parsing")
        with patch.object(launch, "assets_directory", return_value=self.assets), patch.object(Path, "resolve", return_value=Path(r"\\server\share\v3")), self.assertRaises(SystemExit):
            launch.main(["--backend", "file", "--data-dir", r"\\server\share\v3"])
        self.windows.assert_not_called()
        self.file.assert_not_called()

    def test_windows_failure_does_not_select_file_backend(self):
        self.enterContext(patch.object(launch, "os", SimpleNamespace(name="nt", environ=os.environ)))
        self.windows.side_effect = RuntimeError("windows-adapter-failed")
        self.assertEqual(launch.main(self.args + ["--check"]), 1)
        self.file.assert_not_called()
        self.factory.assert_not_called()
        self.assertIn("windows-adapter-failed", self.stderr.getvalue())

    def test_service_worker_failure_does_not_serve_or_open_browser(self):
        self.factory.side_effect = RuntimeError("runtime-version-mismatch")
        self.assertEqual(launch.main(self.args + ["--backend", "file"]), 1)
        self.server_factory.assert_not_called()
        self.browser.assert_not_called()
        self.assertIn("runtime-version-mismatch", self.stderr.getvalue())

    def test_missing_index_does_not_fall_back_to_demo(self):
        (self.assets / "index.html").unlink()
        self.assertEqual(launch.main(self.args + ["--backend", "file"]), 1)
        self.server_factory.assert_not_called()
        self.browser.assert_not_called()
        self.service.close.assert_called_once_with()
        self.assertIn("index.html", self.stderr.getvalue())

    def test_start_waits_for_server_before_browser_and_closes_resources(self):
        order = []
        self.server_factory.side_effect = lambda *args: (order.append("ready"), self.server)[1]
        self.browser.side_effect = lambda *args: order.append("browser")
        self.server.serve_forever.side_effect = lambda: order.append("serve")
        self.assertEqual(launch.main(self.args + ["--backend", "file", "--port", "12345"]), 0)
        self.assertEqual(order, ["ready", "browser", "serve"])
        self.server_factory.assert_called_once_with(self.service, self.assets, 12345)
        self.server.server_close.assert_called_once_with()
        self.service.close.assert_called_once_with()

    def test_no_browser_and_keyboard_interrupt_are_clean_shutdown(self):
        self.server.serve_forever.side_effect = KeyboardInterrupt
        self.assertEqual(launch.main(self.args + ["--backend", "file", "--no-browser"]), 0)
        self.browser.assert_not_called()
        self.server.server_close.assert_called_once_with()
        self.service.close.assert_called_once_with()

    def test_bind_error_closes_service_without_browser_or_fallback(self):
        self.server_factory.side_effect = OSError("address-in-use")
        self.assertEqual(launch.main(self.args + ["--backend", "file"]), 1)
        self.service.close.assert_called_once_with()
        self.browser.assert_not_called()
        self.windows.assert_not_called()

    def test_second_instance_is_rejected_before_constructing_adapter(self):
        with launch.InstanceLock(self.root / "data" / "instance.lock"):
            self.assertEqual(launch.main(self.args + ["--backend", "file", "--check"]), 1)
        self.file.assert_not_called()
        self.factory.assert_not_called()
        self.assertIn("V3", self.stderr.getvalue())
        self.assertEqual(launch.main(self.args + ["--backend", "file", "--check"]), 0)

    def test_system_helper_dispatch_does_not_initialize_product(self):
        with patch("envmanager_v3.system_helper.main", return_value=7) as helper:
            self.assertEqual(launch.main(["--system-helper", "request", "hash"]), 7)
            helper.assert_called_once_with(["request", "hash"])
        self.windows.assert_not_called()
        self.file.assert_not_called()
        self.factory.assert_not_called()

    def test_frozen_assets_resolve_only_inside_meipass(self):
        frozen = self.root / "frozen"
        (frozen / "ui-v3").mkdir(parents=True)
        (frozen / "ui-v3" / "demo-plan.js").write_text("rules", encoding="utf-8")
        with patch.object(launch.sys, "_MEIPASS", str(frozen), create=True):
            self.assertEqual(launch.assets_directory(), frozen / "ui-v3")


if __name__ == "__main__":
    unittest.main()
