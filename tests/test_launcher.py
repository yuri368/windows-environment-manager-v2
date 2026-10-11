from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")


@unittest.skipUnless(os.name == "nt" and POWERSHELL.is_file(), "需要 Windows PowerShell 5.1")
class LauncherTests(unittest.TestCase):
    def run_launcher(self, project: Path, *, env: dict[str, str] | None = None, demo: bool = False,
                     extra_args: list[str] | None = None, cwd: Path | None = None):
        # A PowerShell function shadows the GUI command, while the launcher and
        # its native Python calls still run in Windows PowerShell 5.1.
        script = r'''
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
function Start-Process {
    param($FilePath, $ArgumentList, $WorkingDirectory, $WindowStyle)
    [Console]::Out.WriteLine('START_PROCESS=' + (ConvertTo-Json -Compress @{
        file = $FilePath; args = @($ArgumentList)
        cwd = $WorkingDirectory; style = $WindowStyle
    }))
}
& 'PROJECT_LAUNCHER' DEMO_ARGUMENT
'''.replace("PROJECT_LAUNCHER", str(project / "launcher.ps1").replace("'", "''"))
        script = script.replace("DEMO_ARGUMENT", "-Demo" if demo else "")
        if extra_args:
            script = script.rstrip() + " " + " ".join("'" + value.replace("'", "''") + "'" if not value.startswith("-") else value
                                                        for value in extra_args)
        command = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        run_env = os.environ.copy()
        run_env.pop("PYTHONPATH", None)
        if env:
            run_env.update(env)
        return subprocess.run(
            [str(POWERSHELL), "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-OutputFormat", "Text", "-EncodedCommand", command],
            cwd=cwd or project, env=run_env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60,
        )

    def assert_gui_started(self, result: subprocess.CompletedProcess[str], project: Path):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        starts = [line.removeprefix("START_PROCESS=") for line in result.stdout.splitlines()
                  if line.startswith("START_PROCESS=")]
        self.assertEqual(len(starts), 1, result.stdout + result.stderr)
        launch = json.loads(starts[0])
        self.assertEqual(Path(launch["file"]), project / ".venv" / "Scripts" / "pythonw.exe")
        self.assertEqual(Path(launch["cwd"]), project)
        self.assertEqual(launch["style"], "Hidden")
        return launch["args"]

    def make_project(self, temporary: Path) -> tuple[Path, Path, dict[str, str]]:
        project = temporary / "带空格的项目"
        project.mkdir()
        shutil.copy2(ROOT / "launcher.ps1", project / "launcher.ps1")
        shutil.copy2(ROOT / "requirements.txt", project / "requirements.txt")
        subprocess.run(
            [str(ROOT / ".venv" / "Scripts" / "python.exe"), "-m", "venv",
             "--without-pip", str(project / ".venv")],
            check=True, capture_output=True, text=True, timeout=60,
        )
        stubs = temporary / "python_stubs"
        stubs.mkdir()
        pip = stubs / "pip"
        pip.mkdir()
        (pip / "__init__.py").write_text("", encoding="utf-8")
        (pip / "__main__.py").write_text(
            "from pathlib import Path\n"
            "import os, sys\n"
            "root = Path(os.environ['LAUNCHER_TEST_SITE'])\n"
            "Path(os.environ['LAUNCHER_TEST_PIP_MARKER']).write_text(' '.join(sys.argv[1:]), encoding='utf-8')\n"
            "if os.environ.get('LAUNCHER_TEST_INSTALL') == 'fail':\n"
            "    print('simulated pip error', file=sys.stderr)\n"
            "    raise SystemExit(3)\n"
            "if os.environ.get('LAUNCHER_TEST_INSTALL') == 'yes':\n"
            "    for name, version in [('PySide6', '6.11.2'), ('tomlkit', '0.13.3')]:\n"
            "        package = root / name\n"
            "        package.mkdir(exist_ok=True)\n"
            "        (package / '__init__.py').write_text('', encoding='utf-8')\n"
            "        info = root / f'{name}-{version}.dist-info'\n"
            "        info.mkdir(exist_ok=True)\n"
            "        (info / 'METADATA').write_text(f'Name: {name}\\nVersion: {version}\\n', encoding='utf-8')\n",
            encoding="utf-8",
        )
        marker = temporary / "pip-called.txt"
        return project, stubs, {
            "PYTHONPATH": str(stubs),
            "LAUNCHER_TEST_SITE": str(stubs),
            "LAUNCHER_TEST_PIP_MARKER": str(marker),
        }

    def test_existing_venv_with_required_packages_starts_gui(self):
        result = self.run_launcher(ROOT, demo=True)
        self.assertEqual(self.assert_gui_started(result, ROOT), ["-m", "envmanager", "--demo"])
        self.assertNotIn("正在安装", result.stdout)

    def test_missing_packages_are_installed_then_gui_starts(self):
        with tempfile.TemporaryDirectory(prefix="启动测试 空格 ") as directory:
            project, _stubs, env = self.make_project(Path(directory))
            env["LAUNCHER_TEST_INSTALL"] = "yes"
            result = self.run_launcher(project, env=env)
            self.assertEqual(self.assert_gui_started(result, project), ["-m", "envmanager"])
            self.assertIn("正在安装", result.stdout)
            self.assertIn("install --disable-pip-version-check -r", Path(env["LAUNCHER_TEST_PIP_MARKER"]).read_text(encoding="utf-8"))

    def test_wrong_package_version_reports_post_install_failure(self):
        with tempfile.TemporaryDirectory(prefix="启动测试 空格 ") as directory:
            project, stubs, env = self.make_project(Path(directory))
            for name, version in (("PySide6", "6.11.1"), ("tomlkit", "0.13.3")):
                package = stubs / name
                package.mkdir()
                (package / "__init__.py").write_text("", encoding="utf-8")
                info = stubs / f"{name}-{version}.dist-info"
                info.mkdir()
                (info / "METADATA").write_text(f"Name: {name}\nVersion: {version}\n", encoding="utf-8")
            result = self.run_launcher(project, env=env)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertTrue(Path(env["LAUNCHER_TEST_PIP_MARKER"]).is_file(), result.stdout + result.stderr)
            self.assertIn("安装后仍不可用", result.stderr)
            self.assertNotIn("START_PROCESS=", result.stdout)

    def test_failed_package_install_reports_install_error(self):
        with tempfile.TemporaryDirectory(prefix="启动测试 空格 ") as directory:
            project, _stubs, env = self.make_project(Path(directory))
            env["LAUNCHER_TEST_INSTALL"] = "fail"
            result = self.run_launcher(project, env=env)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertTrue(Path(env["LAUNCHER_TEST_PIP_MARKER"]).is_file(), result.stdout + result.stderr)
            self.assertIn("依赖安装失败", result.stderr)
            self.assertNotIn("START_PROCESS=", result.stdout)

    def test_gui_paths_preserve_caller_directory_and_native_argument_decoding(self):
        with tempfile.TemporaryDirectory(prefix="启动参数 中文 (空格)' ") as directory:
            caller = Path(directory)
            config = caller / "配置 (引号)' .toml"
            state = caller / "状态 (独立)'"
            result = self.run_launcher(ROOT, demo=True, cwd=caller,
                                       extra_args=["-Config", config.name, "-StateDir", state.name + "\\"])
            arguments = self.assert_gui_started(result, ROOT)
            # An inert envmanager module receives the same Windows command line
            # a real Start-Process would pass. This cannot touch registry or UAC.
            module = caller / "envmanager"
            module.mkdir()
            (module / "__init__.py").write_text("", encoding="utf-8")
            (module / "__main__.py").write_text("import json,sys\nprint(json.dumps(sys.argv[1:]))\n", encoding="utf-8")
            probe = subprocess.run(subprocess.list2cmdline([str(ROOT / ".venv" / "Scripts" / "python.exe")])
                                   + " " + " ".join(arguments), cwd=caller,
                                   capture_output=True, text=True, check=True, timeout=20)
            decoded = json.loads(probe.stdout)
            self.assertEqual(decoded[:3], ["--demo", "--config", str(config)])
            self.assertEqual(decoded[3:], ["--state-dir", str(state) + "\\"])

    def test_check_preserves_relative_configuration_and_cli_exit_code(self):
        with tempfile.TemporaryDirectory(prefix="只读校验 中文 (空格)' ") as directory:
            caller = Path(directory)
            config = caller / "方案 (配置)' .toml"
            config.write_text('[Test.Default]\nX = { default_value = "", scope = "User", mode = "replace", editable = false }\n', encoding="utf-8")
            result = self.run_launcher(ROOT, cwd=caller, env={"PYTHONIOENCODING": "utf-8"},
                                       extra_args=["-Check", "-Config", config.name])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("配置有效", result.stdout)
            self.assertNotIn("START_PROCESS=", result.stdout)
            config.write_text("bad toml =", encoding="utf-8")
            failed = self.run_launcher(ROOT, cwd=caller, env={"PYTHONIOENCODING": "utf-8"},
                                       extra_args=["-Check", "-Config", config.name])
            self.assertEqual(failed.returncode, 1, failed.stdout + failed.stderr)
            self.assertIn("配置校验失败", failed.stderr)

    def test_check_admin_reports_live_token_without_gui(self):
        result = self.run_launcher(ROOT, env={"PYTHONIOENCODING": "utf-8"}, extra_args=["-CheckAdmin"])
        self.assertIn(result.returncode, (0, 1), result.stdout + result.stderr)
        self.assertIn("当前进程", result.stdout)
        self.assertIn("管理员权限", result.stdout)
        self.assertNotIn("START_PROCESS=", result.stdout)


if __name__ == "__main__":
    unittest.main()
