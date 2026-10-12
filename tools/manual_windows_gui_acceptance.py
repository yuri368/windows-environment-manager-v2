"""Manual installed-GUI acceptance using two explicit QA User variables.

The human/controller elevates this observer separately, then starts the installed
product from a non-administrator context using ready_for_gui.json. The product
owns its UAC request. No desktop capture, mouse, keyboard or unrelated window
actions are used. UIA operations open/cancel previews; they never confirm writes.
If installed Qt does not expose the required UIA patterns, result.json explicitly
reports partial acceptance and preserves the native main-window evidence.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from envmanager.config import parse_config
from envmanager.models import RegistryState
from envmanager.persistence import StateRepository
from envmanager.privileges import is_administrator
from envmanager.removal import RemovalRequest, RemovalScope
from envmanager.service import EnvironmentService
from envmanager.store import REG_SZ, WindowsStore
from tools.manual_windows_acceptance import (Evidence, NativeWindows as BaseNativeWindows, ScopedAcceptanceStore,
                                            absolute_path, arguments as common_arguments,
                                            canonical_test_id, durable_json, record, require, same_executable)

MAIN_TITLE = "环境变量方案管理器"
CONFLICT_TITLE = "共享环境变量冲突"
SPECIAL_VALUE = "  A;B\t  "
UIA_ACTIONS = frozenset({"snapshot", "expand", "collapse", "remove_preview", "cancel", "scroll_down", "scroll_up"})
SCROLLBAR_ID = "QApplication.SharedConflictDialog.QScrollArea.qt_scrollarea_vcontainer.QScrollBar"
VIEWPORT_ID = "QApplication.SharedConflictDialog.QScrollArea.qt_scrollarea_viewport"


class NativeWindows(BaseNativeWindows):
    """Capture the verified UIA child popup even when EnumWindows omits it."""
    def __init__(self):
        super().__init__()
        self.u.IsChild.argtypes, self.u.IsChild.restype = [wintypes.HWND, wintypes.HWND], wintypes.BOOL
        self.u.GetAncestor.argtypes, self.u.GetAncestor.restype = [wintypes.HWND, wintypes.UINT], wintypes.HWND
        self.u.GetClientRect.argtypes, self.u.GetClientRect.restype = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL
        self.u.ClientToScreen.argtypes, self.u.ClientToScreen.restype = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)], wintypes.BOOL

    def popup_window(self, window, exe, launch_after):
        root = {"pid": window["pid"], "hwnd": window["popup_root_hwnd"]}
        checked_window(self, root, exe, launch_after)
        # Qt popups can have no Win32 owner/child relation. Confirm their actual
        # membership in a freshly read, verified main-window UIA logical tree.
        fresh = dropdown_popup(root, self.scope_snapshot(root))
        require(fresh["hwnd"] == window["hwnd"] and
                fresh["popup_automation_id"] == window["popup_automation_id"],
                "popup is no longer in the verified main UIA tree")
        hwnd = window["hwnd"]
        pid = wintypes.DWORD()
        self.u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        require(pid.value == root["pid"] and hwnd != root["hwnd"], "popup native PID/HWND mismatch")
        process = self.process(pid.value)
        require(process and same_executable(process["exe"], exe) and
                process["process_started"] >= launch_after and process["token_elevated"],
                "popup process identity/token changed")
        require(self.u.IsWindowVisible(hwnd) and not self.u.IsIconic(hwnd), "popup is not visible")
        with self.physical_coordinates():
            rect = wintypes.RECT()
            require(bool(self.u.GetWindowRect(hwnd, ctypes.byref(rect))), "popup GetWindowRect failed")
            client = wintypes.RECT()
            require(bool(self.u.GetClientRect(hwnd, ctypes.byref(client))), "popup GetClientRect failed")
            first, last = wintypes.POINT(client.left, client.top), wintypes.POINT(client.right, client.bottom)
            require(bool(self.u.ClientToScreen(hwnd, ctypes.byref(first))) and
                    bool(self.u.ClientToScreen(hwnd, ctypes.byref(last))), "popup ClientToScreen failed")
        native_rect = [rect.left, rect.top, rect.right, rect.bottom]
        client_rect = [first.x, first.y, last.x, last.y]
        geometry = validate_popup_container(fresh, self.scope_snapshot({"pid": pid.value, "hwnd": hwnd}),
                                            native_rect, client_rect)
        return {**process, **window, **fresh, "rect": native_rect, "title": "",
                "nativeIsWindowVisible": True, "minimized": False, "coordinate_units": "physical_pixels",
                "popup_geometry": geometry}

    def scope_snapshot(self, root):
        completed = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", "& {" + UIA_SCRIPT + "} -RootHwnd " + str(root["hwnd"]) +
             " -ExpectedPid " + str(root["pid"]) + " -Action snapshot"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=35,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        require(completed.returncode == 0, "fresh main UIA snapshot failed")
        return json.loads(completed.stdout.lstrip("\ufeff").strip())


def popup_rect_matches(native_rect, uia_rect):
    """Accept only the popup frame itself, including a small native border."""
    if len(native_rect) != 4 or len(uia_rect) != 4:
        return False
    x, y, width, height = uia_rect
    if width <= 0 or height <= 0:
        return False
    edges = (x, y, x + width, y + height)
    return (native_rect[2] > native_rect[0] and native_rect[3] > native_rect[1] and
            all(abs(native - uia) <= 4 for native, uia in zip(native_rect, edges)))


def validate_popup_container(popup, payload, native_rect, client_rect):
    require(payload.get("pid") == popup["pid"] and payload.get("root_hwnd") == popup["hwnd"],
            "direct popup UIA target mismatch")
    nodes = payload.get("nodes", [])
    roots = [node for node in nodes if node.get("type") == "ControlType.Window"]
    require(len(roots) == 1, "direct popup UIA container is not unique")
    container = roots[0]
    require(container.get("class_name") == "QComboBoxPrivateContainer" and
            container.get("automation_id") == "QApplication.QComboBoxPrivateContainer" and
            container.get("hwnd") == popup["hwnd"] and container.get("pid") == popup["pid"] and
            container.get("enabled", False) and not container.get("offscreen", True),
            "direct popup UIA root is not the expected visible Qt container")
    require(popup_rect_matches(client_rect, container.get("rect", [])),
            "popup UIA container differs from native client bounds")
    def inside(rect, bounds):
        if len(rect) != 4 or len(bounds) != 4:
            return False
        x, y, width, height = rect
        left, top, right, bottom = bounds
        return width > 0 and height > 0 and left <= x <= x + width <= right and top <= y <= y + height <= bottom
    require(inside([client_rect[0], client_rect[1], client_rect[2] - client_rect[0],
                    client_rect[3] - client_rect[1]], native_rect), "popup native frame does not contain client")
    lists = [node for node in nodes if node.get("type") == "ControlType.List"]
    require(len(lists) == 1, "direct popup UIA list is not unique")
    listing = lists[0]
    require(listing.get("hwnd") == popup["hwnd"] and listing.get("pid") == popup["pid"] and
            listing.get("automation_id") == popup["popup_automation_id"] and
            listing.get("rect") == popup["popup_uia_rect"] and listing.get("enabled", False) and
            not listing.get("offscreen", True) and inside(listing.get("rect", []), client_rect),
            "direct popup list differs from fresh main UIA tree or lies outside native client")
    x, y, width, height = listing["rect"]
    list_bounds = [x, y, x + width, y + height]
    items = [node for node in nodes if node.get("type") == "ControlType.ListItem"]
    require(len(items) == 2 and all(node.get("pid") == popup["pid"] and
            node.get("automation_id") == popup["popup_automation_id"] and
            node.get("enabled", False) and not node.get("offscreen", True) and
            inside(node.get("rect", []), list_bounds) and inside(node.get("rect", []), client_rect)
            for node in items), "popup options are not both visible inside the verified list/client")
    require(sum(node.get("name", "").startswith("Default，") for node in items) == 1 and
            sum(node.get("name", "").startswith("Alternate_Long_Public_Option_Label，") for node in items) == 1,
            "popup does not expose both complete QA option labels")
    return {"native_frame": native_rect, "client_screen": client_rect,
            "container_uia_rect": container["rect"], "list_uia_rect": listing["rect"],
            "container_identity_verified": True, "option_bounds_verified": True}


def dropdown_popup(main, payload):
    require(payload.get("pid") == main["pid"] and payload.get("root_hwnd") == main["hwnd"],
            "dropdown snapshot target mismatch")
    boxes = [node for node in payload.get("nodes", [])
             if node.get("name") == "当前详情应用方案" and node.get("type") == "ControlType.ComboBox"]
    require(len(boxes) == 1 and boxes[0].get("pid") == main["pid"] and
            boxes[0].get("expanded") == "Expanded" and not boxes[0].get("offscreen", True) and
            boxes[0].get("enabled", False), "fresh UIA scheme ComboBox is not uniquely expanded")
    box_id = boxes[0].get("automation_id", "")
    require(box_id.startswith("QApplication.MainWindow.") and box_id.endswith(".detailOption"),
            "scheme ComboBox has unexpected UIA identity")
    nodes = [node for node in payload.get("nodes", [])
             if node.get("type") == "ControlType.List"
             and node.get("automation_id", "").endswith(".detailOption.optionPopup")
             ]
    require(len(nodes) == 1, "UIA did not expose one visible scheme popup")
    node = nodes[0]
    require(node.get("pid") == main["pid"] and node.get("automation_id") == box_id + ".optionPopup"
            and not node.get("offscreen", True) and node.get("enabled", False),
            "UIA scheme popup is not the expanded main ComboBox's visible list")
    require(type(node.get("hwnd")) is int and node["hwnd"] > 0 and node["hwnd"] != main["hwnd"],
            "UIA scheme popup has no independent native HWND")
    rect = node.get("rect", [])
    require(len(rect) == 4 and all(type(value) in (int, float) for value in rect) and
            rect[2] > 0 and rect[3] > 0, "UIA scheme popup has invalid bounds")
    return {"pid": main["pid"], "hwnd": node["hwnd"], "popup_root_hwnd": main["hwnd"],
            "popup_uia_rect": rect, "popup_automation_id": node["automation_id"]}


def names_for(test_id):
    prefix = "ENV_MANAGER_VNEXT_QA_" + canonical_test_id(test_id) + "_"
    return {"LIST": prefix + "GUI_LIST", "SPECIAL": prefix + "GUI_SPECIAL"}


def fixture_config(names):
    lines = []
    # Long public QA labels exercise layout elision/scrolling without personal data.
    apps = ("QA_Source", "QA_Consumer_Ordinary_With_Long_Public_Label", "QA_Consumer_Special")
    for app in apps:
        for option in ("Default", "Alternate_Long_Public_Option_Label"):
            lines.append(f"[{app}.{option}]")
            if app != apps[2]:
                lines.append(names["LIST"] + " = " + record("A;B", "User"))
            if app != apps[1]:
                lines.append(names["SPECIAL"] + " = " + record(SPECIAL_VALUE, "User", special=True))
    return "\n".join(lines) + "\n"


def prepare_fixture(store, names, evidence):
    """Create only allowlisted QA values; do not execute any product plan."""
    text = fixture_config(names)
    config = parse_config(text)
    path = evidence.directory / "qa.toml"
    path.write_text(text, encoding="utf-8")
    store.preflight()
    states = (RegistryState(names["LIST"], "User", True, "A;B", REG_SZ),
              RegistryState(names["SPECIAL"], "User", True, SPECIAL_VALUE, REG_SZ))
    for state in states:
        store.write(state)
    store.notify()
    # Verify the fixture actually produces two ordinary and one special conflict.
    service = EnvironmentService(config, store, StateRepository(evidence.directory / "fixture-analysis"))
    draft = service.prepare_remove(RemovalRequest(RemovalScope.SCHEME, frozenset({"QA_Source"}),
                                                  {"QA_Source": "Default"}))
    require(len(draft.shared_conflicts) == 3, "QA fixture did not create exactly three shared objects")
    require(len({conflict.atom.target for conflict in draft.shared_conflicts}) == 2,
            "QA fixture did not create two variable groups")
    evidence.phase("two GUID User variables and read-only sharing fixture prepared",
                   variables=[state.to_dict() for state in states], conflict_count=3, target_groups=2)
    return path, states


# Shipped .NET UIA assembly, no additional product dependency. Every action is
# restricted to one verified HWND/PID. Only safe preview/expand/cancel operations
# exist; there is no arbitrary target-name/action argument or confirmation action.
UIA_SCRIPT = r'''
param([Int64]$RootHwnd, [Int32]$ExpectedPid, [string]$Action)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]::new($RootHwnd))
if ($null -eq $root -or $root.Current.ProcessId -ne $ExpectedPid -or
    $root.Current.NativeWindowHandle -ne $RootHwnd) { throw 'UIA root PID/HWND mismatch' }
function Elements {
    $result = @($root)
    $children = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants,
                              [System.Windows.Automation.Condition]::TrueCondition)
    if ($children.Count -gt 2000) { throw 'UIA target subtree too large' }
    for ($index = 0; $index -lt $children.Count; $index++) {
        if ($children[$index].Current.ProcessId -eq $ExpectedPid) { $result += $children[$index] }
    }
    return $result
}
function Find-One([string]$Name, [System.Windows.Automation.ControlType]$Kind) {
    $found = @(Elements | Where-Object { $_.Current.Name -eq $Name -and $_.Current.ControlType -eq $Kind })
    if ($found.Count -ne 1) { throw ('UIA expected one ' + $Name + ', found ' + $found.Count) }
    if (-not $found[0].Current.IsEnabled -or $found[0].Current.IsOffscreen) { throw 'UIA control not visible/enabled' }
    return $found[0]
}
function Pattern($Element, $Id) {
    $pattern = $null
    if (-not $Element.TryGetCurrentPattern($Id, [ref]$pattern)) { throw 'Required UIA pattern not supported' }
    return $pattern
}
function Scroll-Conflict([bool]$Down) {
    if ($root.Current.AutomationId -ne 'QApplication.SharedConflictDialog') { throw 'Not the scoped conflict dialog' }
    $bars = @(Elements | Where-Object {
        $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::ScrollBar -and
        $_.Current.AutomationId -eq 'QApplication.SharedConflictDialog.QScrollArea.qt_scrollarea_vcontainer.QScrollBar'
    })
    if ($bars.Count -ne 1) { throw 'UIA expected one scoped vertical conflict scrollbar' }
    if ($bars[0].Current.IsOffscreen -or -not $bars[0].Current.IsEnabled) { throw 'Conflict scrollbar unavailable' }
    $range = Pattern $bars[0] ([System.Windows.Automation.RangeValuePattern]::Pattern)
    if ($range.Current.IsReadOnly -or $range.Current.Maximum -le $range.Current.Minimum) { throw 'Conflict scrollbar has no writable range' }
    if ($Down) { $range.SetValue($range.Current.Maximum) } else { $range.SetValue($range.Current.Minimum) }
}
switch ($Action) {
    'snapshot' { }
    'expand' {
        $box = Find-One '当前详情应用方案' ([System.Windows.Automation.ControlType]::ComboBox)
        (Pattern $box ([System.Windows.Automation.ExpandCollapsePattern]::Pattern)).Expand()
    }
    'collapse' {
        $box = Find-One '当前详情应用方案' ([System.Windows.Automation.ControlType]::ComboBox)
        (Pattern $box ([System.Windows.Automation.ExpandCollapsePattern]::Pattern)).Collapse()
    }
    'remove_preview' {
        $button = Find-One '撤销方案' ([System.Windows.Automation.ControlType]::Button)
        (Pattern $button ([System.Windows.Automation.InvokePattern]::Pattern)).Invoke()
    }
    'cancel' {
        $button = Find-One '取消整个操作' ([System.Windows.Automation.ControlType]::Button)
        (Pattern $button ([System.Windows.Automation.InvokePattern]::Pattern)).Invoke()
    }
    'scroll_down' { Scroll-Conflict $true }
    'scroll_up' { Scroll-Conflict $false }
    default { throw 'Unsupported UIA acceptance action' }
}
Start-Sleep -Milliseconds 250
# The Cancel action destroys its target dialog. Do not query a stale UIA root;
# the controller separately confirms that this exact dialog HWND disappeared.
if ($Action -eq 'cancel') {
    [PSCustomObject]@{status='ok'; action=$Action; root_hwnd=$RootHwnd; pid=$ExpectedPid; nodes=@()} |
        ConvertTo-Json -Depth 8 -Compress
    exit 0
}
$nodes = @()
foreach ($element in (Elements)) {
    $current = $element.Current
    $box = $current.BoundingRectangle
    $node = [ordered]@{
        name=$current.Name; type=$current.ControlType.ProgrammaticName;
        automation_id=$current.AutomationId; class_name=$current.ClassName;
        pid=$current.ProcessId; hwnd=$current.NativeWindowHandle;
        runtime_id=@($element.GetRuntimeId());
        offscreen=$current.IsOffscreen; enabled=$current.IsEnabled;
        help=$current.HelpText; rect=@($box.X,$box.Y,$box.Width,$box.Height);
        patterns=@($element.GetSupportedPatterns() | ForEach-Object { $_.ProgrammaticName })
    }
    $pattern = $null
    if ($element.TryGetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern,[ref]$pattern)) {
        $node.expanded = $pattern.Current.ExpandCollapseState.ToString()
    }
    $pattern = $null
    if ($element.TryGetCurrentPattern([System.Windows.Automation.SelectionPattern]::Pattern,[ref]$pattern)) {
        $node.selected = @($pattern.Current.GetSelection() | ForEach-Object { $_.Current.Name })
    }
    $pattern = $null
    if ($element.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$pattern)) {
        $node.value = $pattern.Current.Value
    }
    $pattern = $null
    if ($element.TryGetCurrentPattern([System.Windows.Automation.ScrollPattern]::Pattern,[ref]$pattern)) {
        $node.vertical_scrollable = $pattern.Current.VerticallyScrollable
        $node.vertical_percent = $pattern.Current.VerticalScrollPercent
        $node.vertical_view_percent = $pattern.Current.VerticalViewSize
    }
    $pattern = $null
    if ($element.TryGetCurrentPattern([System.Windows.Automation.RangeValuePattern]::Pattern,[ref]$pattern)) {
        $node.range_min = $pattern.Current.Minimum
        $node.range_max = $pattern.Current.Maximum
        $node.range_value = $pattern.Current.Value
        $node.range_read_only = $pattern.Current.IsReadOnly
    }
    $nodes += [PSCustomObject]$node
}
[PSCustomObject]@{status='ok'; action=$Action; root_hwnd=$RootHwnd; pid=$ExpectedPid; nodes=$nodes} |
    ConvertTo-Json -Depth 8 -Compress
'''


def uia_command(script, window, action):
    if action not in UIA_ACTIONS or type(window.get("pid")) is not int or type(window.get("hwnd")) is not int:
        raise ValueError("invalid scoped UIA request")
    require(window["pid"] > 0 and window["hwnd"] > 0, "UIA requires positive verified PID/HWND")
    return ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(script), "-RootHwnd", str(window["hwnd"]), "-ExpectedPid", str(window["pid"]),
            "-Action", action]


def checked_window(native, window, exe, launch_after):
    if "popup_root_hwnd" in window:
        return native.popup_window(window, exe, launch_after)
    found = [item for item in native.windows(exe, launch_after, include_owned=True)
             if item["pid"] == window["pid"] and item["hwnd"] == window["hwnd"]
             and item["token_elevated"] and item["nativeIsWindowVisible"]]
    require(len(found) == 1, "target window identity, token or visibility changed")
    return found[0]


def uia(native, window, exe, launch_after, evidence, action, label):
    current = checked_window(native, window, exe, launch_after)
    script = evidence.directory / "scoped-uia.ps1"
    if not script.exists():
        script.write_text(UIA_SCRIPT, encoding="utf-8-sig")
    command = uia_command(script, current, action)
    evidence.log("uia_intent", action=action, pid=current["pid"], hwnd=current["hwnd"])
    try:
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   timeout=35, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        result = {"action": action, "returncode": completed.returncode,
                  "stderr": completed.stderr, "stdout": completed.stdout}
        durable_json(evidence.directory / f"uia-{label}.json", result)
        require(completed.returncode == 0, "installed Qt UIA action failed; see uia evidence")
        payload = json.loads(completed.stdout.lstrip("\ufeff").strip())
        require(payload.get("pid") == current["pid"] and payload.get("root_hwnd") == current["hwnd"],
                "UIA output target mismatch")
        return payload
    except subprocess.TimeoutExpired as exc:
        durable_json(evidence.directory / f"uia-{label}.json",
                     {"status": "timeout", "action": action, "seconds": 35})
        raise RuntimeError("UIA provider timed out; installed dialog acceptance is incomplete") from exc


def validate_conflict_snapshot(payload, names, frame_rect=None):
    nodes = payload.get("nodes", [])
    combined = "\n".join(node.get("name", "") for node in nodes)
    groups = {name: any(name in (node.get("name", "") + node.get("help", "")) and
                        ("普通变量" in (node.get("name", "") + node.get("help", "")) or
                         "特殊完整值" in (node.get("name", "") + node.get("help", "")))
                        for node in nodes) for name in names.values()}
    decisions = [node for node in nodes if node.get("type") == "ControlType.ComboBox"
                 and "共享冲突决定" in node.get("name", "")]
    def is_keep(node):
        selected = node.get("selected", [])
        return (bool(selected) and all(value in ("保留", "保留完整值") for value in selected)) or \
               node.get("value") in ("保留", "保留完整值") or \
               "当前决定：保留" in node.get("help", "")
    keep = len(decisions) == 3 and all(is_keep(node) for node in decisions)
    viewports = [node for node in nodes if node.get("automation_id") == VIEWPORT_ID]
    require(len(viewports) <= 1, "UIA conflict viewport is not unique")
    viewport_rect = None
    if viewports:
        x, y, width, height = viewports[0]["rect"]
        require(width > 0 and height > 0, "UIA conflict viewport has invalid bounds")
        viewport_rect = [x, y, x + width, y + height]
    def node_inside(node, bounds=frame_rect):
        if node.get("offscreen", True):
            return False
        if bounds is None:
            return True
        x, y, width, height = node.get("rect", [0, 0, 0, 0])
        left, top, right, bottom = bounds
        return width > 0 and height > 0 and left <= x <= x + width <= right and top <= y <= y + height <= bottom
    def visible_inside(name):
        found = [node for node in nodes if node.get("name") == name and node.get("type") == "ControlType.Button"]
        return len(found) == 1 and found[0].get("enabled", False) and node_inside(found[0])
    content = [node for node in nodes if "共享冲突决定" in node.get("name", "") or
               node.get("name", "").startswith(("元素：", "完整原始值：", "撤销来源：", "共享方：", "影响：")) or
               any(name in (node.get("name", "") + node.get("help", "")) and
                   ("普通变量" in (node.get("name", "") + node.get("help", "")) or
                    "特殊完整值" in (node.get("name", "") + node.get("help", ""))) for name in names.values())]
    content_bounds = viewport_rect or frame_rect
    fully_visible = len(content) >= 5 and all(node_inside(node, content_bounds) for node in content)
    bars = [node for node in nodes if node.get("type") == "ControlType.ScrollBar" and
            node.get("automation_id") == SCROLLBAR_ID]
    require(len(bars) <= 1, "UIA conflict vertical scrollbar is not unique")
    bar = bars[0] if bars else {}
    numbers = [bar.get(key) for key in ("range_min", "range_max", "range_value")]
    range_valid = (all(type(value) in (int, float) for value in numbers) and
                   numbers[0] <= numbers[2] <= numbers[1])
    scrollable = (range_valid and numbers[1] > numbers[0] and bar.get("range_read_only") is False
                  and bar.get("enabled", False) and node_inside(bar))
    content_ids = [json.dumps(node.get("runtime_id")) for node in content]
    identities_verified = bool(content_ids) and all(node.get("runtime_id") for node in content) and \
                          len(set(content_ids)) == len(content_ids)
    visible_ids = [identity for identity, node in zip(content_ids, content) if node_inside(node, content_bounds)]
    return {"group_headers": groups, "grouping_verified": all(groups.values()),
            "decision_count": len(decisions), "default_keep_verified": keep,
            "scroll_provider_exposed": range_valid,
            "vertical_scrollable": scrollable,
            "content_fully_visible": fully_visible,
            "scroll_not_needed": not scrollable and fully_visible,
            "scroll_range": numbers[1] - numbers[0] if range_valid else (0 if fully_visible else None),
            "scroll_min": bar.get("range_min"), "scroll_max": bar.get("range_max"),
            "scroll_value": bar.get("range_value"), "viewport_rect": viewport_rect,
            "content_ids": content_ids, "visible_content_ids": visible_ids,
            "content_identities_verified": identities_verified,
            "summary_keep_three": "共享项目：保留 3 · 选择撤销 0" in combined,
            "cancel_visible_inside_frame": visible_inside("取消整个操作"),
            "preview_visible_inside_frame": visible_inside("继续预览")}


def validate_scroll_sequence(before, down, up):
    require(all(item["vertical_scrollable"] and item["content_identities_verified"]
                for item in (before, down, up)), "conflict scroll/identity evidence incomplete")
    require(all(item["scroll_min"] == before["scroll_min"] and item["scroll_max"] == before["scroll_max"]
                for item in (down, up)), "conflict scroll range changed")
    require(down["scroll_value"] == before["scroll_max"] and up["scroll_value"] == before["scroll_min"]
            and down["scroll_value"] > before["scroll_value"] and up["scroll_value"] < down["scroll_value"],
            "conflict scrollbar did not move to maximum and back to minimum")
    required = set(before["content_ids"])
    require(all(set(item["content_ids"]) == required for item in (down, up)),
            "conflict content identity changed during scrolling")
    visible = set().union(*(item["visible_content_ids"] for item in (before, down, up)))
    require(required <= visible, "conflict content was not all visible across scroll positions")
    require(set(down["visible_content_ids"]) != set(before["visible_content_ids"]),
            "conflict content visibility did not change with scrolling")
    require(all(item["cancel_visible_inside_frame"] and item["preview_visible_inside_frame"] and
                item["grouping_verified"] and item["default_keep_verified"] for item in (down, up)),
            "conflict controls/default decisions changed during scrolling")
    return {"scroll_movement_verified": True, "content_coverage_verified": True,
            "content_count": len(required)}


def capture(native, window, exe, launch_after, evidence, filename):
    current = checked_window(native, window, exe, launch_after)
    current.update(native.display_metrics(current["hwnd"]))
    native.screenshot(current, evidence.directory / filename)
    require(current["capture_size"] == [current["rect"][2] - current["rect"][0],
                                        current["rect"][3] - current["rect"][1]], "frame/capture size mismatch")
    durable_json(evidence.directory / (filename + ".json"), current)
    evidence.phase(filename, window=current)
    return current


def wait_window(native, args, launch_after, title, pid=None, timeout=None):
    deadline = time.monotonic() + (args.gui_timeout if timeout is None else timeout)
    while time.monotonic() < deadline:
        windows = native.windows(args.exe, launch_after, include_owned=True)
        matches = [window for window in windows if window["title"] == title and window["token_elevated"]
                   and window["nativeIsWindowVisible"] and not window["minimized"]
                   and (pid is None or window["pid"] == pid)]
        if len(matches) == 1:
            return matches[0]
        require(len(matches) <= 1, "ambiguous same-path QA windows; refusing automation")
        time.sleep(0.3)
    raise TimeoutError(f"visible elevated QA window missing: {title}")


def installed_gui(args, names, config, evidence):
    state = evidence.directory / "gui-state"
    state.mkdir()
    launch_after = time.time()
    ready = {"status": "ready_for_gui", "exe": str(args.exe), "config": str(config),
             "state_dir": str(state), "launch_after": launch_after, "timeout_seconds": args.gui_timeout,
             "launch_from_non_admin": True, "runner_does_not_launch": True}
    durable_json(evidence.directory / "ready_for_gui.json", ready)
    evidence.phase("waiting for controller's installed GUI/UAC launch", "waiting", readiness=ready)
    native = NativeWindows()
    main = wait_window(native, args, launch_after, MAIN_TITLE)
    time.sleep(1)
    details = {"installed_main": None, "dropdown": None, "conflict": None, "uia_error": None,
               "checks": {}, "uia_errors": []}
    dialog = None
    expanded = False
    current_check = "main"
    def incomplete(check, exc):
        error = {"check": check, "type": type(exc).__name__, "message": str(exc),
                 "traceback": traceback.format_exc()}
        details["checks"][check] = "partial"
        details["uia_errors"].append(error)
        details["uia_error"] = details["uia_error"] or error
        evidence.phase("installed " + check + " acceptance", "incomplete", error=error)

    try:
        details["installed_main"] = capture(native, main, args.exe, launch_after, evidence, "installed-main.png")
        details["checks"]["main"] = "passed"
        initial = uia(native, main, args.exe, launch_after, evidence, "snapshot", "main")
        # Wait for the product's initial read-only scan to enable the preview button.
        for index in range(15):
            if any(node.get("name") == "撤销方案" and node.get("enabled") for node in initial.get("nodes", [])):
                break
            time.sleep(0.5)
            initial = uia(native, main, args.exe, launch_after, evidence, "snapshot", f"scan-{index}")
        current_check = "dropdown"
        try:
            require(any(node.get("name") == "当前详情应用方案" and node.get("type") == "ControlType.ComboBox"
                        for node in initial.get("nodes", [])), "installed Qt did not expose the scheme ComboBox in UIA")
            # Expand can succeed before a provider snapshot fails; always collapse afterward.
            expanded = True
            expanded_state = uia(native, main, args.exe, launch_after, evidence, "expand", "dropdown-open")
            require(any(node.get("name") == "当前详情应用方案" and node.get("expanded") == "Expanded"
                        for node in expanded_state.get("nodes", [])), "UIA did not confirm expanded scheme dropdown")
            capture(native, main, args.exe, launch_after, evidence, "installed-dropdown-main.png")
            popup = dropdown_popup(main, expanded_state)
            details["dropdown"] = capture(native, popup, args.exe, launch_after, evidence,
                                           "installed-dropdown-popup.png")
            popup_snapshot = uia(native, popup, args.exe, launch_after, evidence,
                                 "snapshot", "dropdown-popup")
            option_text = "\n".join(node.get("name", "") for node in
                                     expanded_state.get("nodes", []) + popup_snapshot.get("nodes", []))
            require("Default" in option_text and "Alternate_Long_Public_Option_Label" in option_text,
                    "UIA dropdown did not expose both complete QA option labels")
            details["checks"]["dropdown"] = "passed"
        except Exception as exc:
            incomplete("dropdown", exc)
        finally:
            if expanded:
                collapsed = uia(native, main, args.exe, launch_after, evidence, "collapse", "dropdown-close")
                require(any(node.get("name") == "当前详情应用方案" and node.get("expanded") == "Collapsed"
                            for node in collapsed.get("nodes", [])), "UIA did not confirm collapsed scheme dropdown")
                expanded = False
        # Dropdown evidence is independent of the shared-conflict dialog. Proceed
        # after a verified safe collapse even when dropdown capture is partial.
        current_check = "conflict"
        uia(native, main, args.exe, launch_after, evidence, "remove_preview", "remove-preview")
        dialog = wait_window(native, args, launch_after, CONFLICT_TITLE, main["pid"], timeout=15)
        time.sleep(0.5)
        details["conflict"] = capture(native, dialog, args.exe, launch_after, evidence, "installed-shared-conflict.png")
        snapshot = uia(native, dialog, args.exe, launch_after, evidence, "snapshot", "conflict")
        validation = validate_conflict_snapshot(snapshot, names, details["conflict"]["rect"])
        details["conflict_validation"] = validation
        require(validation["grouping_verified"] and validation["default_keep_verified"],
                "UIA could not verify two target groups and all three default KEEP decisions")
        require(validation["cancel_visible_inside_frame"] and validation["preview_visible_inside_frame"],
                "conflict dialog buttons are not both visible inside its physical frame")
        require(validation["vertical_scrollable"] or validation["content_fully_visible"],
                "conflict content is clipped and UIA exposes no usable scroll pattern")
        if validation["vertical_scrollable"]:
            down = uia(native, dialog, args.exe, launch_after, evidence, "scroll_down", "conflict-scroll-down")
            down_frame = capture(native, dialog, args.exe, launch_after, evidence, "installed-shared-conflict-scrolled.png")
            up = uia(native, dialog, args.exe, launch_after, evidence, "scroll_up", "conflict-scroll-up")
            up_frame = capture(native, dialog, args.exe, launch_after, evidence, "installed-shared-conflict-scroll-restored.png")
            down_validation = validate_conflict_snapshot(down, names, down_frame["rect"])
            up_validation = validate_conflict_snapshot(up, names, up_frame["rect"])
            details["conflict_scroll_validation"] = validate_scroll_sequence(validation, down_validation, up_validation)
            details["conflict_scroll_positions"] = {"down": down_validation, "up": up_validation}
        evidence.phase("installed conflict grouping, default KEEP and content visibility verified", validation=validation)
        uia(native, dialog, args.exe, launch_after, evidence, "cancel", "conflict-cancel")
        dialog = None
        remaining = native.windows(args.exe, launch_after, include_owned=True)
        require(not any(window["pid"] == main["pid"] and window["title"] == CONFLICT_TITLE
                        and window["nativeIsWindowVisible"] for window in remaining), "conflict Cancel did not close dialog")
        details["checks"]["conflict"] = "passed"
    except Exception as exc:
        incomplete("dropdown_collapse" if expanded else current_check, exc)
        details["checks"].setdefault("dropdown", "skipped")
        details["checks"].setdefault("conflict", "skipped")
    finally:
        # Cancel only our same-PID conflict dialog; never invoke a confirmation.
        dialogs = [item for item in native.windows(args.exe, launch_after, include_owned=True)
                   if item["pid"] == main["pid"] and item["title"] == CONFLICT_TITLE and item["nativeIsWindowVisible"]]
        for item in dialogs:
            try:
                uia(native, item, args.exe, launch_after, evidence, "cancel", "cleanup-cancel")
            except Exception as exc:
                # WM_CLOSE on this identified QDialog rejects its operation.
                evidence.log("uia_cancel_unavailable", error=str(exc), dialog=item)
                checked = checked_window(native, item, args.exe, launch_after)
                require(bool(native.u.PostMessageW(checked["hwnd"], 0x0010, 0, 0)), "QA dialog WM_CLOSE failed")
                time.sleep(0.3)
        if expanded:
            try:
                uia(native, main, args.exe, launch_after, evidence, "collapse", "cleanup-collapse")
            except Exception as exc:
                evidence.log("uia_collapse_unavailable", error=str(exc))
        native.close(main, args.exe, launch_after)
        evidence.phase("installed QA process closed and exited")
    return details


def main(argv=None):
    args = common_arguments(argv)
    if os.name != "nt" or not is_administrator():
        print("REFUSED: run the observer only after human administrator UAC approval; no writes performed.", file=sys.stderr)
        return 2
    args.work_dir.mkdir(parents=True, exist_ok=True)
    evidence = Evidence(args.work_dir)
    names = names_for(args.test_id)
    store = ScopedAcceptanceStore(WindowsStore(), names, evidence, scopes=("User",))
    details, error, residuals = None, None, []
    executable_hash = hashlib.sha256(args.exe.read_bytes()).hexdigest()
    original_states = None
    try:
        native = NativeWindows()
        token = native.process(os.getpid())
        require(token and token["token_elevated"], "observer Windows token is not elevated")
        evidence.phase("real observer elevated token", process=token, exe_sha256=executable_hash)
        config, original_states = prepare_fixture(store, names, evidence)
        details = installed_gui(args, names, config, evidence)
        # All UIA actions are read-only/cancel. Verify the two fixture values remain
        # byte-identical before cleanup, and there are no committed product writes.
        require(all(store.read(state.scope, state.name).same_content(state) for state in original_states),
                "QA fixture changed during GUI preview; preserving unknown residuals")
        gui_repository = StateRepository(args.work_dir / "gui-state")
        require(gui_repository.snapshot()["revision"] == 0 and not gui_repository.history(),
                "product state indicates a committed GUI operation")
        require(hashlib.sha256(args.exe.read_bytes()).hexdigest() == executable_hash,
                "installed executable changed during acceptance")
        evidence.phase("GUI previews caused no registry or committed state writes")
    except BaseException as exc:
        error = {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}
        evidence.phase("GUI acceptance stopped", "failed", error=error)
    finally:
        residuals = store.cleanup()
        status = ("failed" if error or residuals else
                  "partial" if not details or details.get("uia_error") else "passed")
        result = {"status": status, "test_id": args.test_id, "exe": str(args.exe),
                  "exe_sha256": executable_hash, "installed_gui": details, "error": error,
                  "phases": evidence.phases, "residuals": residuals,
                  "source_fixture_not_substituted": True}
        durable_json(args.work_dir / "result.json", result)
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
