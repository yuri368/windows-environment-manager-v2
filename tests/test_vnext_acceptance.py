"""Executable T01-T31 contract from the confirmed 2026-10-12 specification.

Uses only MemoryStore and temporary repositories: no host registry writes.
"""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from envmanager.config import parse_config
from envmanager.elements import CleanupSettings, cleanup_value
from envmanager.engine import calculate
from envmanager.matching import SchemeStatus, VariableMatchStatus, match_scheme, match_variable
from envmanager.models import ConfigError, ConflictError, PermissionDenied, RegistryState
from envmanager.persistence import StateRepository
from envmanager.removal import RemovalKind, RemovalRequest, RemovalScope, build_removal_atoms, compose_removal, expand_sources
from envmanager.service import EnvironmentService
from envmanager.sharing import ConflictDecision, classify_conflicts
from envmanager.store import MemoryStore
from tests.test_removal import config_for, snapshots, spec, values


class VNextAcceptanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = StateRepository(Path(temporary.name))

    def make_service(self, records, states=(), store_type=MemoryStore):
        store = store_type(states)
        return EnvironmentService(config_for(*records), store, self.repo), store

    @staticmethod
    def raw(value, *, name="Path", scope="User", kind=2):
        return RegistryState(name, scope, True, value, kind)

    def match(self, selected, current, mode="replace", *, special=False):
        item = spec(value=selected, mode=mode, special=special)
        return match_variable(item, selected, self.raw(current))

    def remove(self, selected, current, mode="replace", cleanup=None):
        item = spec(value=selected, mode=mode)
        states = snapshots(self.raw(current))
        candidates = build_removal_atoms((item,), states, values(item))
        return candidates, compose_removal(candidates.atoms, (), {}, states, cleanup)

    def sharing(self, source, consumers, current):
        records = (source, *consumers)
        states = snapshots(self.raw(current, name=source.name, scope=source.scope))
        candidates = build_removal_atoms((source,), states, values(*records))
        analysis = classify_conflicts(config_for(*records), {source.app}, candidates.atoms,
                                      states, values(*records))
        return analysis, states

    @staticmethod
    def config_text(records):
        sections = {}
        for item in records:
            definition = {"default_value": item.default_value, "scope": item.scope,
                          "mode": item.mode, "editable": item.editable, "special": item.special}
            fields = ",".join(f"{key}={json.dumps(value, ensure_ascii=False)}"
                               for key, value in definition.items())
            sections.setdefault((item.app, item.option), []).append(f"{item.name}={{{fields}}}")
        return "\n".join(f"[{app}.{option}]\n" + "\n".join(lines)
                         for (app, option), lines in sections.items())

    def test_T01_target_names_case_insensitive_and_scopes_independent(self):
        one, two = spec(name="Path"), spec(option="17", name="PATH")
        system = spec(name="Path", scope="System")
        self.assertEqual(one.target, two.target)
        self.assertNotEqual(one.target, system.target)
        states = snapshots(self.raw("A"), self.raw("B", scope="System"))
        atoms = build_removal_atoms((one, two, system), states, values(one, two, system)).atoms
        self.assertEqual(len(atoms), 1)
        self.assertEqual(len(atoms[0].sources), 2)

    def test_T02_values_case_sensitive_and_wrong_case_not_removed(self):
        self.assertFalse(self.match("A", "a").complete_match)
        candidates, changes = self.remove("A", "a")
        self.assertEqual(candidates.atoms, ())
        self.assertEqual(changes, ())

    def test_T03_matching_ignores_order_and_ascii_edge_whitespace(self):
        result = self.match("A;B", "B; A ")
        self.assertTrue(result.complete_match)
        self.assertEqual(result.matched_elements, ("A", "B"))

    def test_T04_replace_matches_sets_but_applies_selected_order(self):
        self.assertTrue(self.match("A;B", "B;A").complete_match)
        self.assertEqual(calculate(spec(value="A;B", mode="replace"), "A;B", self.raw("B;A")).result.value,
                         "A;B")

    def test_T05_replace_extra_elements_are_unmatched_and_preserved(self):
        result = self.match("A;B", "BASE;A;B")
        self.assertFalse(result.complete_match)
        self.assertEqual(result.extra_elements, ("BASE",))
        candidates, changes = self.remove("A;B", "BASE;A;B")
        self.assertEqual([a.element_key for a in candidates.atoms], ["A", "B"])
        self.assertEqual(changes[0].after.value, "BASE")

    def test_T06_partial_replace_removes_only_existing_configured_element(self):
        result = self.match("A;B", "A;C")
        self.assertEqual((result.matched_elements, result.missing_elements, result.extra_elements),
                         (("A",), ("B",), ("C",)))
        scheme = match_scheme("Java", "8", (result,))
        self.assertEqual((scheme.matched_count, scheme.total_count, scheme.status), (0, 1, SchemeStatus.PARTIAL))
        self.assertEqual(self.remove("A;B", "A;C")[1][0].after.value, "C")

    def test_T07_lowercase_elements_have_no_uppercase_matches(self):
        result = self.match("A;B", "a;b")
        self.assertEqual(result.matched_elements, ())
        self.assertEqual(result.missing_elements, ("A", "B"))

    def test_T08_write_cleanup_removes_duplicates_empty_items_and_edge_whitespace(self):
        current = "A;A; B ;;\t;C;"
        calculation = calculate(spec(value="A;B;C", mode="+append"), "A;B;C", self.raw(current))
        self.assertEqual(calculation.result.value, "A;B;C")
        self.assertTrue(calculation.warnings)
        self.assertEqual(current, "A;A; B ;;\t;C;")

    def test_T09_cleanup_preserves_case_distinct_elements(self):
        self.assertEqual(cleanup_value("A;a;A"), "A;a")

    def test_T10_last_nonempty_element_removal_deletes_key(self):
        self.assertFalse(self.remove("A", "A")[1][0].after.exists)

    def test_T11_cleanup_preserves_existing_unique_empty(self):
        before = self.raw("")
        after = calculate(spec(value="", mode="replace"), "", before).result
        self.assertTrue(after.same_content(before))

    def test_T12_empty_append_input_never_matches_nonempty_or_delimiters(self):
        for mode in ("+append", "-append", "append"):
            for current in ("A;B", ";"):
                with self.subTest(mode=mode, current=current):
                    self.assertIs(self.match("", current, mode).status, VariableMatchStatus.VALUE_MISMATCH)

    def test_T13_empty_append_matches_only_existing_raw_empty(self):
        for mode in ("+append", "-append", "append"):
            item = spec(value="", mode=mode)
            self.assertTrue(match_variable(item, "", self.raw("")).complete_match)
            self.assertIs(match_variable(item, "", RegistryState("Path", "User", False)).status,
                          VariableMatchStatus.KEY_MISSING)

    def test_T14_special_values_match_and_write_raw_complete_text(self):
        value = "  A;B  "
        item = spec(value=value, mode="replace", special=True)
        self.assertTrue(self.match(value, value, special=True).complete_match)
        for current in ("A;B", "  a;B  ", "  A;B\t"):
            self.assertFalse(self.match(value, current, special=True).complete_match)
        self.assertEqual(calculate(item, value, self.raw("other")).result.value, value)

    def test_T15_special_append_configuration_is_rejected(self):
        for mode in ("+append", "-append", "append"):
            with self.assertRaises(ConfigError):
                parse_config(self.config_text((spec(mode=mode, special=True),)))

    def test_T16_mixed_special_definitions_fail_across_all_apps_options(self):
        records = (spec(mode="replace", special=True), spec(app="Other", name="PATH", mode="replace"))
        with self.assertRaises(ConfigError):
            parse_config(self.config_text(records))

    def test_T17_special_consistency_is_scoped(self):
        records = (spec(mode="replace", special=True), spec(app="Other", scope="System"))
        loaded = parse_config(self.config_text(records))
        self.assertTrue(loaded.variables("Java", "8")[0].special)
        self.assertFalse(loaded.variables("Other", "8")[0].special)

    def test_T18_special_shared_complete_value_defaults_keep_and_explicit_remove(self):
        source = spec(value="  A;B  ", mode="replace", special=True)
        consumer = spec(app="Other", value=source.default_value, mode="replace", special=True)
        analysis, states = self.sharing(source, (consumer,), source.default_value)
        conflict = analysis.shared_conflicts[0]
        self.assertEqual(conflict.atom.kind, RemovalKind.WHOLE_KEY)
        self.assertEqual(conflict.default_decision, ConflictDecision.KEEP)
        self.assertEqual(compose_removal((), (conflict,), {}, states), ())
        self.assertFalse(compose_removal((), (conflict,), {conflict.atom.atom_id: "remove"}, states)[0].after.exists)

    def test_T19_shared_element_survives_cleanup_of_invalid_copies(self):
        source = spec(value="A;B")
        analysis, states = self.sharing(source, (spec(app="Other", value="A"),), " A ;A;;\t;B;")
        change = compose_removal(analysis.ordinary_atoms, analysis.shared_conflicts, {}, states)[0]
        self.assertEqual(change.after.value, "A")
        self.assertEqual(change.contributions[-1]["kind"], "cleanup")

    def test_T20_conflict_ui_groups_target_and_keeps_independent_element_choices(self):
        from PySide6.QtWidgets import QApplication
        from envmanager.ui import SharedConflictDialog
        app = QApplication.instance() or QApplication([])
        analysis, _ = self.sharing(spec(value="A;B"), (spec(app="Other", value="A;B"),), "A;B")
        dialog = SharedConflictDialog(analysis.shared_conflicts)
        self.addCleanup(dialog.close)
        self.assertEqual(len(dialog.target_groups), 1)
        self.assertEqual(len(dialog.decision_boxes), 2)
        boxes = tuple(dialog.decision_boxes.values())
        self.assertTrue(all(box.currentData() == "keep" for box in boxes))
        boxes[1].setCurrentIndex(boxes[1].findData("remove"))
        self.assertEqual([box.currentData() for box in boxes], ["keep", "remove"])
        app.processEvents()

    def test_T21_readonly_detection_changes_no_config_selection_preferences_or_registry(self):
        item = spec(value="A;B", mode="replace")
        service, store = self.make_service((item,), (self.raw(" B ;A;A;;"),))
        before = deepcopy(self.repo.snapshot())
        selections = deepcopy(vars(service.selections.get(item)))
        configuration = deepcopy(service.config)
        for _ in range(2):
            detected = service.scan_all()
            self.assertEqual(detected.schemes[(item.app, item.option)].status, SchemeStatus.COMPLETE)
        self.assertEqual(self.repo.snapshot(), before)
        self.assertEqual(vars(service.selections.get(item)), selections)
        self.assertEqual(service.config, configuration)
        self.assertEqual(store.writes, [])
        self.assertEqual(store.notifications, 0)
        self.assertEqual(self.repo.history(), [])

    def test_T22_failed_transaction_restores_raw_order_duplicates_empty_items_type_and_absence(self):
        class FailAfterMutation(MemoryStore):
            def write(self, state):
                super().write(state)
                if state.name == "FAIL" and getattr(self, "enabled", True):
                    self.enabled = False
                    raise OSError("injected after mutation")
        records = (spec(name="Path", value="NEW", mode="replace"),
                   spec(name="ABSENT", value="new", mode="replace"),
                   spec(name="FAIL", value="new", mode="replace"))
        originals = (self.raw(" B ;; A ;A;\t;", kind=2), self.raw("old;;old", name="FAIL", kind=1))
        service, store = self.make_service(records, originals, FailAfterMutation)
        result = service.execute(service.plan("Java", "8"))
        self.assertFalse(result.success)
        for original in originals:
            self.assertTrue(store.read(original.scope, original.name).same_content(original))
        self.assertFalse(store.read("User", "ABSENT").exists)
        self.assertEqual(self.repo.snapshot()["last_values"], {})

    def test_T23_history_restore_bypasses_default_cleanup(self):
        item = spec(value="A", mode="replace")
        original = self.raw(" A ;A;;\t; B ;", kind=2)
        service, store = self.make_service((item,), (original,))
        request = RemovalRequest(RemovalScope.SCHEME, {"Java"}, {"Java": "8"})
        removed = service.execute(service.finalize_remove(service.prepare_remove(request).id))
        self.assertEqual(store.read("User", "Path").value, "B")
        restored = service.execute(service.plan_restore(removed.operation_id))
        self.assertTrue(restored.success)
        self.assertTrue(store.read("User", "Path").same_content(original))

    def test_T24_cleanup_settings_never_change_matching_results(self):
        item = spec(value="A;B", mode="replace")
        service, _ = self.make_service((item,), (self.raw(" B ;A;A;;"),))
        before = service.scan_all().schemes[("Java", "8")].variables[0]
        service.set_cleanup_settings(CleanupSettings(False, False, False))
        after = service.scan_all().schemes[("Java", "8")].variables[0]
        self.assertEqual(before, after)

    def test_T25_user_system_deletions_have_preview_risks_and_enforce_permission(self):
        user, system = spec(value="A"), spec(value="A", scope="System")
        service, store = self.make_service((user, system), (self.raw("A"), self.raw("A", scope="System")))
        request = RemovalRequest(RemovalScope.SCHEME, {"Java"}, {"Java": "8"})
        draft = service.prepare_remove(request)
        plan = service.finalize_remove(draft.id)
        self.assertTrue(all(not change.after.exists for change in plan.changes))
        self.assertTrue(any("User / Path" in warning and "删除" in warning for warning in plan.warnings))
        self.assertTrue(any("System / Path" in warning and "删除" in warning for warning in plan.warnings))
        self.assertTrue(any("管理员权限" in warning for warning in plan.warnings))
        store.denied_scopes.add("System")
        with self.assertRaises(PermissionDenied):
            service.finalize_remove(draft.id)
        self.assertEqual(store.writes, [])

    def test_T26_active_revocation_of_exact_unique_empty_deletes_key(self):
        for mode in ("replace", "+append", "-append", "append"):
            candidates, changes = self.remove("", "", mode)
            self.assertEqual(candidates.atoms[0].kind, RemovalKind.EMPTY_VALUE)
            self.assertFalse(changes[0].after.exists)

    def test_T27_disabled_cleanup_preserves_exact_unmatched_raw_slices(self):
        _, changes = self.remove("B", " A ;B;; C ", cleanup=CleanupSettings(False, False, False))
        self.assertEqual(changes[0].after.value, " A ;; C ")

    def test_T28_disabled_cleanup_still_deletes_no_effective_remaining_elements(self):
        self.assertFalse(self.remove("A", "A;;", cleanup=CleanupSettings(False, False, False))[1][0].after.exists)

    def test_T29_partial_ordinary_replace_protects_present_shared_element(self):
        analysis, _ = self.sharing(spec(value="A"), (spec(app="Other", value="A;B", mode="replace"),), "A;C")
        self.assertEqual(len(analysis.shared_conflicts), 1)
        self.assertEqual(analysis.shared_conflicts[0].atom.element_key, "A")
        self.assertEqual(analysis.shared_conflicts[0].default_decision, ConflictDecision.KEEP)

    def test_T30_empty_state_shared_dependency_protects_delete_and_nonempty_switch_apply(self):
        old = spec(option="Old", value="", mode="replace")
        new = spec(option="New", value="A", mode="replace")
        consumer = spec(app="Other", value="", mode="replace")
        service, store = self.make_service((old, new, consumer), (self.raw(""),))
        request = RemovalRequest(RemovalScope.SCHEME, {"Java"}, {"Java": "Old"})
        removal = service.prepare_remove(request)
        self.assertEqual(len(removal.shared_conflicts), 1)
        self.assertEqual(service.finalize_remove(removal.id).changes, ())
        switch = service.prepare_scheme_switch("Java", "New", old_options=("Old",))
        self.assertTrue(switch.shared_conflicts)
        try:
            plan = service.finalize_scheme_switch(switch.id)
        except ConflictError:
            pass
        else:
            self.assertTrue(all(not change.changed or change.after.value == "" for change in plan.changes))
        try:
            direct = service.plan("Java", "New")
        except ConflictError:
            pass
        else:
            self.assertFalse(direct.can_execute, "Compatibility apply must not bypass an empty-state consumer")
        self.assertTrue(store.read("User", "Path").same_content(self.raw("")))
        self.assertEqual(store.writes, [])

    def test_T31_batch_replace_equal_sets_different_write_order_is_conflict(self):
        one = spec(value="A;B", mode="replace")
        two = spec(app="Other", value="B;A", mode="replace")
        self.assertTrue(match_variable(one, one.default_value, self.raw("B;A")).complete_match)
        self.assertTrue(match_variable(two, two.default_value, self.raw("B;A")).complete_match)
        service, store = self.make_service((one, two), (self.raw("B;A"),))
        try:
            plan = service.plan_batch({"Java": "8", "Other": "8"})
        except ConflictError:
            pass
        else:
            self.assertFalse(plan.can_execute)
            self.assertTrue(plan.conflicts)
        self.assertEqual(store.writes, [])

    def test_all_five_removal_scopes_expand_sources_and_protect_other_apps_all_options(self):
        one = spec(option="One", value="A")
        two = spec(option="Two", value="B")
        other_one = spec(app="Other", option="One", value="A")
        other_two = spec(app="Other", option="Two", value="B")
        configuration = config_for(one, two, other_one, other_two)
        requests = (
            (RemovalRequest(RemovalScope.VARIABLE, {"Java"}, {"Java": "One"}, (one.key,)), (one,)),
            (RemovalRequest(RemovalScope.SCHEME, {"Java"}, {"Java": "One"}), (one,)),
            (RemovalRequest(RemovalScope.APPLICATION_ALL, {"Java"}), (one, two)),
            (RemovalRequest(RemovalScope.BATCH_CURRENT, {"Java"}, {"Java": "One"}), (one,)),
            (RemovalRequest(RemovalScope.BATCH_ALL, {"Java"}), (one, two)),
        )
        for request, expected in requests:
            with self.subTest(scope=request.scope):
                self.assertEqual(expand_sources(configuration, request), expected)
                state = snapshots(self.raw("A;B"))
                atoms = build_removal_atoms(expected, state, values(one, two)).atoms
                shared = classify_conflicts(configuration, {"Java"}, atoms, state, values(one, two, other_one, other_two))
                self.assertEqual(len(shared.shared_conflicts), len(expected))
                self.assertEqual(shared.ordinary_atoms, ())

    def test_switch_composes_once_and_frozen_snapshot_rejects_external_change(self):
        old = spec(option="Old", value="COMMON;OLD")
        new = spec(option="New", value="COMMON;NEW")
        service, store = self.make_service((old, new), (self.raw("BASE;COMMON;OLD"),))
        draft = service.prepare_scheme_switch("Java", "New")
        plan = service.finalize_scheme_switch(draft.id)
        self.assertEqual(len(plan.changes), 1)
        self.assertEqual(plan.changes[0].after.value, "COMMON;NEW;BASE")
        store.values[("User", "PATH")] = self.raw("EXTERNAL;COMMON;OLD")
        with self.assertRaises(ConflictError):
            service.execute(plan)
        self.assertEqual(store.writes, [])
        fresh = service.prepare_scheme_switch("Java", "New")
        result = service.execute(service.finalize_scheme_switch(fresh.id))
        self.assertTrue(result.success)
        self.assertEqual(len(store.writes), 1)

    def test_effective_empty_selected_inputs_never_match_nonempty_values(self):
        for mode in ("replace", "+append", "-append", "append"):
            for selected in (" ", "\t", ";", " ; \t;"):
                with self.subTest(mode=mode, selected=selected):
                    self.assertFalse(self.match(selected, "A", mode).complete_match)
                    self.assertTrue(self.match(selected, "", mode).complete_match)

    def test_effective_empty_matched_revocation_and_shared_state_use_same_semantics(self):
        for mode in ("replace", "+append", "-append", "append"):
            selected = " ;\t "
            with self.subTest(mode=mode):
                candidates, changes = self.remove(selected, "", mode)
                self.assertEqual(len(candidates.atoms), 1)
                atom = candidates.atoms[0]
                self.assertEqual((atom.kind, atom.display_value), (RemovalKind.EMPTY_VALUE, ""))
                self.assertEqual(atom.sources[0].selected_value, selected)
                self.assertFalse(changes[0].after.exists)
                source = spec(value=selected, mode=mode)
                consumer = spec(app="Other", value="\t; ", mode=mode)
                analysis, states = self.sharing(source, (consumer,), "")
                self.assertEqual(len(analysis.shared_conflicts), 1)
                self.assertEqual(compose_removal((), analysis.shared_conflicts, {}, states), ())

    def test_effective_empty_protected_state_blocks_nonempty_switch(self):
        old = spec(option="Old", value=" ;\t ", mode="replace")
        new = spec(option="New", value="A", mode="replace")
        consumer = spec(app="Other", value="\t; ", mode="+append")
        service, store = self.make_service((old, new, consumer), (self.raw(""),))
        draft = service.prepare_scheme_switch("Java", "New", old_options=("Old",))
        self.assertTrue(draft.shared_conflicts)
        try:
            plan = service.finalize_scheme_switch(draft.id)
        except ConflictError:
            pass
        else:
            self.assertTrue(all(not change.changed or change.after.value == "" for change in plan.changes))
        self.assertEqual(store.writes, [])

    def test_duplicate_counts_include_extra_windows_elements(self):
        result = self.match("A", "A; C ;C")
        self.assertEqual(result.extra_elements, ("C",))
        self.assertEqual(result.occurrence_counts["C"], 2)
        self.assertEqual(dict(result.duplicate_counts), {"C": 2})


if __name__ == "__main__":
    unittest.main()
