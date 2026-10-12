"""One bounded Qt keyboard/geometry acceptance batch, using temporary data only.

Does not construct WindowsStore or confirm save/execution. This is acceptance
evidence, not a product module or a replacement for the existing regression suite.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QPlainTextEdit, QPushButton
from envmanager.config import parse_config
from envmanager.config_editor_ui import ConfigEditorDialog, ExactValueDialog
from envmanager.persistence import StateRepository
from envmanager.service import EnvironmentService
from envmanager.store import MemoryStore
from envmanager.theme import COLORS, STYLE
from envmanager.ui import FullValueDialog, MainWindow, OptionComboBox


def settle(app):
    app.processEvents()
    QTest.qWait(15)
    app.processEvents()


def identify(widget):
    if widget is None:
        return "none"
    text = widget.text() if hasattr(widget, "text") else ""
    return {"class": type(widget).__name__, "object": widget.objectName(),
            "accessible_name": widget.accessibleName(), "text": text[:90]}


def contained(widget, ancestor):
    pos = widget.mapTo(ancestor, QPoint(0, 0))
    return widget.isVisible() and ancestor.rect().contains(
        pos) and ancestor.rect().contains(pos + QPoint(widget.width() - 1, widget.height() - 1))


def traverse(app, window, start, targets):
    disabled = sorted(name for name, widget in targets.items() if not widget.isEnabled())
    targets = {name: widget for name, widget in targets.items() if widget.isEnabled()}
    reached = set()
    sequence = []
    multiline_boundaries = []
    for direction in ("forward", "reverse"):
        # Starting focus is explicit setup. All movements in each phase are
        # actual Tab/Shift+Tab. Text controls retain their native indentation
        # behavior; a reverse route reaches the footer without editing text.
        start.setFocus()
        settle(app)
        first = window.focusWidget()
        for step in range(180):
            current = window.focusWidget()
            for name, widget in targets.items():
                if current is widget or (widget.focusProxy() is not None and current is widget.focusProxy()):
                    reached.add(name)
            if isinstance(current, QPlainTextEdit) and not current.tabChangesFocus():
                multiline_boundaries.append({"direction": direction, "focus": identify(current),
                                             "reason": "Native text indentation boundary; no text-mutating Tab was sent"})
                break
            reverse = direction == "reverse"
            sequence.append({"direction": direction, "focus": identify(current),
                             "key": "Shift+Tab" if reverse else "Tab",
                             "has_focus": bool(current and current.hasFocus())})
            QTest.keyClick(current or window, Qt.Key.Key_Backtab if reverse else Qt.Key.Key_Tab,
                           Qt.KeyboardModifier.ShiftModifier if reverse else Qt.KeyboardModifier.NoModifier)
            settle(app)
            if window.focusWidget() is first and step > 0:
                break
        if not (set(targets) - reached):
            break
    return {"keys_sent": len(sequence), "reached": sorted(reached), "disabled_expected_skip": disabled,
            "missing": sorted(set(targets) - reached), "sequence": sequence, "multiline_boundaries": multiline_boundaries,
            "scope": "Real QTest forward Tab and reverse Shift+Tab from search; stops at native multiline text indentation controls. Ctrl+Tab in QTabWidget changes tabs and is not used for focus navigation. Focus border is separately checked in theme source, not screenshot-rated."}


def cancelled_enter(app, widget):
    events = []
    def cancel():
        dialog = app.activeModalWidget()
        if dialog is None:
            events.append({"modal": None})
            return
        events.append({"modal": dialog.windowTitle(), "class": type(dialog).__name__})
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        events[-1].update(result=int(dialog.result()), closed=not dialog.isVisible())
    QTimer.singleShot(80, cancel)
    widget.setFocus()
    QTest.keyClick(widget, Qt.Key.Key_Return)
    QTest.qWait(100)
    settle(app)
    return events


def luminance(hex_value):
    channels = [int(hex_value[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    channels = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in channels]
    return sum(v * weight for v, weight in zip(channels, (.2126, .7152, .0722)))


def main():
    app = QApplication.instance() or QApplication([])
    synthetic = "\n".join(
        f"[{name}.{option}]\n" + "\n".join(
            f'VALUE_{i:02} = {{ default_value = "value-{i}", scope = "User", mode = "replace", editable = true }}'
            for i in range(count))
        for name, count in (("One", 1), ("Two", 2), ("Many", 12)) for option in ("Default", "Alternate"))
    source_paths = [ROOT / "env.example.toml"] + ([ROOT / "env.toml"] if (ROOT / "env.toml").exists() else [])
    source_hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths}
    report = {"platform": app.platformName(), "scale_factor": os.environ.get("QT_SCALE_FACTOR", "1"),
              "method": "QApplication + actual QTest key events, isolated MemoryStore and temporary TOML/state", "sizes": [], "failures": []}
    with tempfile.TemporaryDirectory(prefix="compact-interaction-") as temporary:
        path = Path(temporary) / "env.toml"
        original = synthetic.encode("utf-8")
        path.write_bytes(original)
        store = MemoryStore()
        state = StateRepository(Path(temporary) / "state")
        service = EnvironmentService(parse_config(synthetic), store, state)
        window = MainWindow(service, path, demo=True)
        window.show()
        settle(app)
        initial_state = state.snapshot()
        for width, height in ((1024, 768), (1536, 864)):
            window.resize(width, height)
            settle(app)
            record = {"requested_size": [width, height], "main_size": [window.width(), window.height()], "variable_counts": []}
            for name, count in (("One", 1), ("Two", 2), ("Many", 12)):
                window._select_app(name)
                settle(app)
                row_heights = [card.height() for card in window.cards]
                vertical = window.scroll.verticalScrollBar()
                horizontal = window.scroll.horizontalScrollBar()
                vertical.setFocus()
                QTest.keyClick(vertical, Qt.Key.Key_End)
                horizontal.setFocus()
                QTest.keyClick(horizontal, Qt.Key.Key_End)
                settle(app)
                case = {"application": name, "count": count, "row_heights": row_heights,
                        "vertical_max": vertical.maximum(), "vertical_after_End": vertical.value(),
                        "horizontal_max": horizontal.maximum(), "horizontal_after_End": horizontal.value(),
                        "last_remove_visible_in_viewport": contained(window.cards[-1].remove_button, window.scroll.viewport()),
                        "primary_in_window": contained(window.apply_button, window),
                        "natural_heights": len(window.cards) == count and all(44 <= h <= 54 for h in row_heights)}
                if not case["natural_heights"] or not case["primary_in_window"] or not case["last_remove_visible_in_viewport"]:
                    report["failures"].append({"check": "row_scroll_footer", "size": [width, height], "details": case})
                record["variable_counts"].append(case)
            targets = {"search": window.search, "application_list": window.app_list,
                       "scheme": window.option_combo, "apply": window.apply_button, "removal": window.deactivate_button,
                       "last_source": window.cards[-1].selector, "last_value": window.cards[-1].editor,
                       "last_full_value": window.cards[-1].full_value_button, "last_remove": window.cards[-1].remove_button}
            targets.update({f"filter_{index}": button for index, button in enumerate(window.filter_buttons)})
            targets.update({f"workspace_{index}": button for index, button in enumerate(window.nav_buttons)})
            record["main_focus"] = traverse(app, window, window.search, targets)
            if record["main_focus"]["missing"]:
                report["failures"].append({"check": "main_tab_reach", "size": [width, height], "missing": record["main_focus"]["missing"]})
            # Arrow selection is real keyboard navigation. Enter on the name
            # button must activate its safe detail action without checking it.
            window._select_app("One")
            index = next(i for i in range(window.app_list.count()) if window.app_list.item(i).data(Qt.ItemDataRole.UserRole) == "Two")
            row = window.app_list.itemWidget(window.app_list.item(index))
            row.name_button.setFocus()
            QTest.keyClick(row.name_button, Qt.Key.Key_Return)
            settle(app)
            record["main_name_enter"] = {"expected": "Two", "actual": window.app_name,
                                         "checked_applications": sorted(window.checked_apps)}
            if window.app_name != "Two":
                report["failures"].append({"check": "main_name_enter", "size": [width, height], "details": record["main_name_enter"]})
            record["main_apply_enter_then_escape"] = cancelled_enter(app, window.apply_button)
            if not any(item.get("modal") for item in record["main_apply_enter_then_escape"]):
                report["failures"].append({"check": "main_apply_enter", "size": [width, height], "details": "Return on focused primary button did not open preview"})
            window.hide()
            editor = ConfigEditorDialog(path, service)
            editor.show()
            editor.resize(width, height)
            settle(app)
            # Create an explicitly unsaved comment by actual text keys. This
            # enables Save for reachability and its cancellation boundary.
            editor.tabs.setCurrentIndex(1)
            editor.source.setFocus()
            QTest.keyClick(editor.source, Qt.Key.Key_End, Qt.KeyboardModifier.ControlModifier)
            QTest.keyClick(editor.source, Qt.Key.Key_Return)
            QTest.keyClicks(editor.source, "# acceptance-only draft")
            editor.tabs.setCurrentIndex(0)
            settle(app)
            targets = {"search": editor.search_edit, "application_list": editor.app_list,
                       "scheme_selector": editor.nav_option_combo, "record_list": editor.record_list,
                       "overview": editor.overview, "default_value": editor.default_editor.editor,
                       "name": editor.name_edit, "scope": editor.scope_combo, "mode": editor.mode_combo,
                       "update": editor.update_button, "save": editor.save_button}
            record["editor_size"] = [editor.width(), editor.height()]
            record["editor_focus"] = traverse(app, editor, editor.search_edit, targets)
            record["editor_primary_in_window"] = contained(editor.save_button, editor)
            if record["editor_focus"]["missing"]:
                report["failures"].append({"check": "editor_tab_reach", "size": [width, height], "missing": record["editor_focus"]["missing"]})
            activated = []
            editor.overview.itemActivated.connect(lambda item, column: activated.append(item.text(0)))
            editor.overview.setFocus()
            QTest.keyClick(editor.overview, Qt.Key.Key_Return)
            settle(app)
            record["editor_overview_enter"] = {"activated_records": activated, "form_active": editor._form_active}
            record["editor_save_enter_then_escape"] = cancelled_enter(app, editor.save_button)
            record["editor_draft_retained_after_cancel"] = editor.session.dirty and not editor.saved and editor.isVisible()
            # Close the untouched source file's window using the explicit draft
            # discard button; this does not commit or restore any data.
            def discard():
                box = app.activeModalWidget()
                if box:
                    for button in box.findChildren(QPushButton):
                        if button.text() == "放弃草稿":
                            button.setFocus()
                            QTest.keyClick(button, Qt.Key.Key_Return)
                            return
                    QTest.keyClick(box, Qt.Key.Key_Escape)
            QTimer.singleShot(60, discard)
            QTest.keyClick(editor, Qt.Key.Key_Escape)
            settle(app)
            record["editor_escape_discard_result"] = int(editor.result())
            record["editor_closed"] = not editor.isVisible()
            editor.deleteLater()
            window.show()
            settle(app)
            report["sizes"].append(record)

        report["escape_dialogs"] = []
        for dialog in (FullValueDialog("original", True, False, window), ExactValueDialog("original", window), ConfigEditorDialog(path, service, window)):
            dialog.show()
            settle(app)
            QTest.keyClick(dialog, Qt.Key.Key_Escape)
            settle(app)
            report["escape_dialogs"].append({"class": type(dialog).__name__, "result": int(dialog.result()), "closed": not dialog.isVisible()})
            dialog.deleteLater()

        screen = app.primaryScreen().availableGeometry()
        anchor = OptionComboBox()
        anchor.addItems(["Long-readable-scheme-" + str(i) + "-" + "x" * 60 for i in range(16)])
        anchor.resize(220, 32)
        anchor.move(screen.right() - 220, screen.bottom() - 32)
        anchor.show()
        settle(app)
        anchor.showPopup()
        settle(app)
        popup = anchor.view().window()
        report["combo_edge"] = {"screen": screen.getRect(), "popup": popup.geometry().getRect(), "contained": screen.contains(popup.geometry())}
        QTest.keyClick(anchor.view(), Qt.Key.Key_Escape)
        anchor.close()
        menu = window.config_button.menu()
        menu.popup(QPoint(screen.right() - 1, screen.bottom() - 1))
        settle(app)
        report["menu_edge"] = {"screen": screen.getRect(), "menu": menu.geometry().getRect(), "contained": screen.contains(menu.geometry())}
        QTest.keyClick(menu, Qt.Key.Key_Escape)
        settle(app)
        report["menu_edge"]["escape_closed"] = not menu.isVisible()
        for name in ("combo_edge", "menu_edge"):
            if not report[name]["contained"]:
                report["failures"].append({"check": name, "details": report[name]})
        contrast = []
        for foreground in ("ink", "text", "secondary", "muted"):
            for background in ("canvas", "inventory", "surface", "surface_muted", "input"):
                fg, bg = luminance(COLORS[foreground]), luminance(COLORS[background])
                ratio = (max(fg, bg) + .05) / (min(fg, bg) + .05)
                contrast.append({"foreground": foreground, "background": background, "ratio": round(ratio, 3), "at_least_4_5": ratio >= 4.5})
        report["text_contrast"] = {"method": "WCAG relative sRGB luminance of current theme tokens; primary body = ink/text", "pairs": contrast,
                                   "scope": "Enabled primary/secondary/muted text on five regular surfaces only. Does not measure pixels, disabled text, focus indicators or all status badges."}
        report["focus_style_source"] = {"line_edits": "QLineEdit:focus" in STYLE, "combos": "QComboBox:focus" in STYLE,
                                         "buttons": "QPushButton:focus" in STYLE, "quiet": "QPushButton#quiet:focus" in STYLE,
                                         "scope": "Static QSS presence plus actual focus states; no visual screenshot claim."}
        report["boundaries"] = {"environment_writes": len(store.writes), "notifications": store.notifications,
                                 "state_revision": state.snapshot()["revision"], "state_unchanged": state.snapshot() == initial_state,
                                 "temporary_config_bytes_unchanged": path.read_bytes() == original,
                                 "project_config_hashes_unchanged": source_hashes == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths}}
        assert all((report["boundaries"]["environment_writes"] == 0, report["boundaries"]["notifications"] == 0,
                    report["boundaries"]["state_revision"] == 0, report["boundaries"]["state_unchanged"],
                    report["boundaries"]["temporary_config_bytes_unchanged"], report["boundaries"]["project_config_hashes_unchanged"]))
        window.close()
        window.deleteLater()
        settle(app)
    output = ROOT / "artifacts" / "compact-interaction-report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": output.name, "failures": report["failures"], "boundaries": report["boundaries"]}, ensure_ascii=False))
    if report["failures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
