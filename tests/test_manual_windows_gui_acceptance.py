"""GUI acceptance fixture/UIA boundaries; no host registry/window operations."""
from __future__ import annotations

from pathlib import Path
from contextlib import nullcontext
import os
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from envmanager.config import parse_config
from envmanager.store import MemoryStore
from tools.manual_windows_acceptance import Evidence, ScopedAcceptanceStore
from tools.manual_windows_gui_acceptance import (SPECIAL_VALUE, UIA_ACTIONS, UIA_SCRIPT, NativeWindows,
                                                dropdown_popup, fixture_config, installed_gui,
                                                names_for, popup_rect_matches, prepare_fixture, uia_command,
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

    def popup_snapshot(self):
        return {"pid": 1234, "root_hwnd": 4321, "nodes": [
            {"pid": 1234, "hwnd": 5678, "type": "ControlType.List", "offscreen": False,
             "enabled": True, "automation_id": "QApplication.MainWindow.detailOption.optionPopup",
             "rect": [1782, 216, 778, 140]}]}

    def test_popup_uses_unique_real_uia_child_hwnd_and_physical_bounds(self):
        popup = dropdown_popup({"pid": 1234, "hwnd": 4321}, self.popup_snapshot())
        self.assertEqual(popup["hwnd"], 5678)
        self.assertEqual(popup["popup_root_hwnd"], 4321)
        self.assertTrue(popup_rect_matches([1782, 216, 2560, 356], popup["popup_uia_rect"]))
        self.assertFalse(popup_rect_matches([0, 0, 2560, 1440], popup["popup_uia_rect"]))
        self.assertFalse(popup_rect_matches([1782, 216, 2560, 356], [1782, 216, 0, 140]))
        self.assertFalse(popup_rect_matches([1782, 216, 2560, 356], [float("nan"), 216, 778, 140]))
        for change in ({"pid": 9}, {"hwnd": 0}, {"hwnd": 4321}, {"offscreen": True},
                       {"automation_id": "unrelated.optionPopup"}, {"rect": [1, 2, -1, 40]}):
            payload = self.popup_snapshot()
            payload["nodes"][0].update(change)
            with self.subTest(change=change), self.assertRaises(AssertionError):
                dropdown_popup({"pid": 1234, "hwnd": 4321}, payload)
        payload = self.popup_snapshot()
        payload["nodes"] *= 2
        with self.assertRaises(AssertionError):
            dropdown_popup({"pid": 1234, "hwnd": 4321}, payload)

    def popup_native(self):
        native = object.__new__(NativeWindows)
        root = {"pid": 1234, "hwnd": 4321, "token_elevated": True, "nativeIsWindowVisible": True}
        native.windows = Mock(return_value=[root])  # EnumWindows deliberately omits child.
        native.process = Mock(return_value={"pid": 1234, "exe": str(self.directory / "qa.exe"),
                                            "process_started": 20, "token_elevated": True})
        native.physical_coordinates = nullcontext
        def pid(hwnd, pointer):
            pointer._obj.value = 1234
            return 1
        def rect(hwnd, pointer):
            pointer._obj.left, pointer._obj.top = 1782, 216
            pointer._obj.right, pointer._obj.bottom = 2560, 356
            return True
        native.u = SimpleNamespace(GetWindowThreadProcessId=Mock(side_effect=pid),
                                   IsChild=Mock(return_value=True), GetAncestor=Mock(return_value=4321),
                                   IsWindowVisible=Mock(return_value=True), IsIconic=Mock(return_value=False),
                                   GetWindowRect=Mock(side_effect=rect))
        return native

    def test_popup_native_identity_relation_visibility_and_bounds_are_rechecked(self):
        popup = dropdown_popup({"pid": 1234, "hwnd": 4321}, self.popup_snapshot())
        native = self.popup_native()
        result = native.popup_window(popup, self.directory / "qa.exe", 10)
        self.assertEqual(result["rect"], [1782, 216, 2560, 356])
        self.assertTrue(result["nativeIsWindowVisible"])
        for boundary in ("foreign_pid", "other_executable", "older_process", "not_elevated",
                         "unrelated_window", "hidden", "main_frame"):
            native = self.popup_native()
            if boundary == "foreign_pid":
                native.u.GetWindowThreadProcessId.side_effect = lambda hwnd, pointer: setattr(pointer._obj, "value", 9)
            elif boundary == "other_executable":
                native.process.return_value["exe"] = str(self.directory / "other.exe")
            elif boundary == "older_process":
                native.process.return_value["process_started"] = 1
            elif boundary == "not_elevated":
                native.process.return_value["token_elevated"] = False
            elif boundary == "unrelated_window":
                native.u.IsChild.return_value = False
                native.u.GetAncestor.return_value = 999
            elif boundary == "hidden":
                native.u.IsWindowVisible.return_value = False
            else:
                native.u.GetWindowRect.side_effect = lambda hwnd, pointer: True
            with self.subTest(boundary=boundary), self.assertRaises(AssertionError):
                native.popup_window(popup, self.directory / "qa.exe", 10)

    def test_partial_dropdown_still_collapses_and_accepts_independent_conflict(self):
        main = {"pid": 1234, "hwnd": 4321, "title": "环境变量方案管理器"}
        dialog = {"pid": 1234, "hwnd": 5678, "title": "共享环境变量冲突"}
        native = Mock()
        native.windows.return_value = []
        actions = []
        def action(native, window, exe, launch_after, evidence, name, label):
            actions.append(name)
            if name in ("expand", "collapse"):
                return {"pid": 1234, "root_hwnd": 4321, "nodes": [
                    {"name": "当前详情应用方案", "type": "ControlType.ComboBox",
                     "expanded": "Expanded" if name == "expand" else "Collapsed"}]}
            return {"nodes": [{"name": "当前详情应用方案", "type": "ControlType.ComboBox"},
                              {"name": "撤销方案", "enabled": True}]}
        validation = {name: True for name in ("grouping_verified", "default_keep_verified",
                     "cancel_visible_inside_frame", "preview_visible_inside_frame", "content_fully_visible")}
        validation["vertical_scrollable"] = False
        with patch("tools.manual_windows_gui_acceptance.NativeWindows", return_value=native), \
             patch("tools.manual_windows_gui_acceptance.wait_window", side_effect=[main, dialog]), \
             patch("tools.manual_windows_gui_acceptance.uia", side_effect=action), \
             patch("tools.manual_windows_gui_acceptance.capture", return_value={"rect": [0, 0, 800, 600]}), \
             patch("tools.manual_windows_gui_acceptance.validate_conflict_snapshot", return_value=validation), \
             patch("tools.manual_windows_gui_acceptance.time.sleep"):
            details = installed_gui(SimpleNamespace(exe=self.directory / "qa.exe", gui_timeout=2), self.names,
                                    self.directory / "qa.toml", Evidence(self.directory))
        self.assertEqual(details["checks"], {"main": "passed", "dropdown": "partial", "conflict": "passed"})
        self.assertEqual(details["uia_errors"][0]["check"], "dropdown")
        self.assertLess(actions.index("collapse"), actions.index("remove_preview"))
        self.assertIn("cancel", actions)
        self.assertNotIn("confirm", actions)
        native.close.assert_called_once()

    def test_unverified_collapse_blocks_preview_and_closes_qa_process(self):
        main = {"pid": 1234, "hwnd": 4321, "title": "环境变量方案管理器"}
        native = Mock()
        native.windows.return_value = []
        initial = {"nodes": [{"name": "当前详情应用方案", "type": "ControlType.ComboBox"},
                             {"name": "撤销方案", "enabled": True}]}
        expanded = {"pid": 1234, "root_hwnd": 4321, "nodes": [
            {"name": "当前详情应用方案", "expanded": "Expanded"}]}
        with patch("tools.manual_windows_gui_acceptance.NativeWindows", return_value=native), \
             patch("tools.manual_windows_gui_acceptance.wait_window", return_value=main), \
             patch("tools.manual_windows_gui_acceptance.uia", side_effect=[initial, expanded, {"nodes": []},
                                                                         {"nodes": []}]) as actions, \
             patch("tools.manual_windows_gui_acceptance.capture", return_value={"rect": [0, 0, 800, 600]}), \
             patch("tools.manual_windows_gui_acceptance.time.sleep"):
            details = installed_gui(SimpleNamespace(exe=self.directory / "qa.exe", gui_timeout=2), self.names,
                                    self.directory / "qa.toml", Evidence(self.directory))
        self.assertEqual(details["checks"]["dropdown"], "partial")
        self.assertEqual(details["checks"]["dropdown_collapse"], "partial")
        self.assertEqual(details["checks"]["conflict"], "skipped")
        self.assertNotIn("remove_preview", [call.args[6] for call in actions.call_args_list])
        native.close.assert_called_once()

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
