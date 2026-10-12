"""GUI acceptance fixture/UIA boundaries; no host registry/window operations."""
from __future__ import annotations

from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from envmanager.config import parse_config
from envmanager.store import MemoryStore
from tools.manual_windows_acceptance import Evidence, ScopedAcceptanceStore
from tools.manual_windows_gui_acceptance import (SPECIAL_VALUE, UIA_ACTIONS, UIA_SCRIPT, fixture_config,
                                                names_for, prepare_fixture, uia_command,
                                                validate_conflict_snapshot)

GUID = "51a868c4-ec2b-4f30-999f-673d31c0ef76"


class GuiFixtureBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.names = names_for(GUID)

    def test_config_is_three_apps_two_options_and_only_two_user_targets(self):
        config = parse_config(fixture_config(self.names))
        self.assertEqual(len(config.apps()), 3)
        self.assertEqual(config.apps()[0], "QA_Source")
        records = [spec for app in config.apps() for option in config.options(app)
                   for spec in config.variables(app, option)]
        self.assertTrue(all(len(config.options(app)) == 2 for app in config.apps()))
        self.assertEqual({spec.name for spec in records}, set(self.names.values()))
        self.assertEqual({spec.scope for spec in records}, {"User"})
        self.assertTrue(all(spec.default_value == SPECIAL_VALUE for spec in records if spec.special))

    def test_memory_fixture_creates_two_values_and_three_grouped_conflicts_then_cleans(self):
        backend = MemoryStore()
        evidence = Evidence(self.directory)
        guard = ScopedAcceptanceStore(backend, self.names, evidence, scopes=("User",))
        config_path, states = prepare_fixture(guard, self.names, evidence)
        self.assertTrue(config_path.is_file())
        self.assertEqual(len(backend.writes), 2)
        self.assertTrue(all(backend.read(state.scope, state.name).same_content(state) for state in states))
        self.assertEqual(evidence.phases[-1]["conflict_count"], 3)
        self.assertEqual(evidence.phases[-1]["target_groups"], 2)
        with self.assertRaises(ValueError):
            guard.delete("System", self.names["LIST"])
        with self.assertRaises(ValueError):
            guard.delete("User", "Path")
        self.assertEqual(guard.cleanup(), [])
        self.assertTrue(all(not backend.read("User", name).exists for name in self.names.values()))

    def test_uia_supports_only_scoped_preview_and_cancel_actions(self):
        window = {"hwnd": 4321, "pid": 1234}
        for action in UIA_ACTIONS:
            command = uia_command(self.directory / "fixture.ps1", window, action)
            self.assertIn("-ExpectedPid", command)
            self.assertIn("1234", command)
            self.assertIn("-RootHwnd", command)
            self.assertEqual(command[-1], action)
        for action in ("confirm", "apply", "remove_all", "click", "invoke_arbitrary", "send_keys"):
            with self.subTest(action=action), self.assertRaises(ValueError):
                uia_command(self.directory / "fixture.ps1", window, action)
        with self.assertRaises(AssertionError):
            uia_command(self.directory / "fixture.ps1", {"hwnd": 0, "pid": 1234}, "snapshot")
        with self.assertRaises(ValueError):
            uia_command(self.directory / "fixture.ps1", {"hwnd": "4321", "pid": 1234}, "snapshot")

    def snapshot(self):
        visible = {"offscreen": False, "rect": [50, 100, 100, 40]}
        groups = [{"name": "truncated…", "help": self.names["LIST"] + " · 普通变量", **visible},
                  {"name": self.names["SPECIAL"] + " · 特殊完整值", **visible}]
        decisions = [{"type": "ControlType.ComboBox", "name": f"item {index} 的共享冲突决定",
                      "selected": ["保留完整值" if index == 2 else "保留"], **visible} for index in range(3)]
        buttons = [{"type": "ControlType.Button", "name": name, "offscreen": False,
                    "enabled": True, "rect": [200, 500, 80, 40]} for name in ("取消整个操作", "继续预览")]
        return {"nodes": groups + decisions + buttons + [{"vertical_scrollable": True}]}

    def test_keep_grouping_and_button_visibility_are_verified_without_guessing(self):
        result = validate_conflict_snapshot(self.snapshot(), self.names, [0, 0, 800, 600])
        self.assertTrue(result["grouping_verified"])
        self.assertTrue(result["default_keep_verified"])
        self.assertTrue(result["cancel_visible_inside_frame"])
        self.assertTrue(result["preview_visible_inside_frame"])
        self.assertTrue(result["vertical_scrollable"])
        snapshot = self.snapshot()
        snapshot["nodes"][2]["selected"] = ["撤销"]
        self.assertFalse(validate_conflict_snapshot(snapshot, self.names)["default_keep_verified"])
        self.assertFalse(validate_conflict_snapshot(self.snapshot(), self.names, [0, 0, 250, 520])[
                         "cancel_visible_inside_frame"])

    def test_missing_uia_providers_remain_unverified(self):
        result = validate_conflict_snapshot({"nodes": []}, self.names)
        self.assertFalse(result["grouping_verified"])
        self.assertFalse(result["default_keep_verified"])
        self.assertFalse(result["scroll_provider_exposed"])
        self.assertFalse(result["cancel_visible_inside_frame"])

    def test_no_scrollbar_is_valid_when_all_content_fits(self):
        snapshot = self.snapshot()
        snapshot["nodes"].pop()  # Simulate a Qt provider with no ScrollPattern.
        result = validate_conflict_snapshot(snapshot, self.names, [0, 0, 800, 600])
        self.assertTrue(result["content_fully_visible"])
        self.assertTrue(result["scroll_not_needed"])
        self.assertEqual(result["scroll_range"], 0)
        snapshot["nodes"][2]["offscreen"] = True
        self.assertFalse(validate_conflict_snapshot(snapshot, self.names, [0, 0, 800, 600])[
                         "scroll_not_needed"])

    @unittest.skipUnless(os.name == "nt", "PowerShell Parser/assembly read-only check is Windows-specific")
    def test_powershell_script_syntax_and_builtin_uia_assembly_load(self):
        source = self.directory / "scoped-uia.ps1"
        source.write_text(UIA_SCRIPT, encoding="utf-8-sig")
        parser = self.directory / "parse-only.ps1"
        parser.write_text("""param([string]$Source)
$ErrorActionPreference='Stop'
$tokens=$null; $errors=$null
[System.Management.Automation.Language.Parser]::ParseFile($Source,[ref]$tokens,[ref]$errors) | Out-Null
if ($errors.Count) { $errors | ForEach-Object { Write-Error $_.Message }; exit 1 }
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
if ($null -eq [System.Windows.Automation.AutomationElement].GetMethod('FromHandle')) { throw 'Missing FromHandle' }
$current = [System.Windows.Automation.SelectionPattern].GetProperty('Current')
if ($null -eq $current) { throw 'Missing SelectionPattern.Current' }
$getSelection = $current.PropertyType.GetMethod('GetSelection')
if ($null -eq $getSelection -or $getSelection.GetParameters().Count -ne 0 -or
    $getSelection.ReturnType -ne [System.Windows.Automation.AutomationElement[]]) {
    throw 'SelectionPattern.Current.GetSelection signature mismatch'
}
Write-Output 'Parser and built-in UIA assembly passed; no AutomationElement/window access.'
""", encoding="utf-8-sig")
        completed = subprocess.run(["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
                                    "-ExecutionPolicy", "Bypass", "-File", str(parser), "-Source", str(source)],
                                   capture_output=True, text=True, timeout=20,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
