from __future__ import annotations

import unittest

from envmanager.detection import scan_all
from envmanager.matching import TargetKey as DetectionTarget
from envmanager.models import Configuration, ConflictError, RegistryState, ValidationError, VariableSpec
from envmanager.sharing import ConflictDecision
from envmanager.store import MemoryStore
from envmanager.switching import (AmbiguousOldSchemeError, finalize_switch,
                                  identify_old_options, prepare_switch)


def spec(option="8", value="A", *, app="Java", name="Path", mode="+append", scope="User"):
    return VariableSpec(app, option, name, scope, mode, True, value)


def config_for(*specs):
    apps = {}
    for item in specs:
        apps.setdefault(item.app, {}).setdefault(item.option, []).append(item)
    return Configuration({app: {option: tuple(items) for option, items in options.items()}
                          for app, options in apps.items()}, "switch-test")


def values(*specs):
    return {item.key: item.default_value for item in specs}


def snapshots(*states):
    from envmanager.models import match_key
    return {(state.scope, match_key(state.name)): state for state in states}


def path(value="A;Other", scope="User", registry_type=1):
    return RegistryState("Path", scope, True, value, registry_type)


class OldSchemeRecognitionTests(unittest.TestCase):
    def test_manual_complete_old_is_identified_without_any_history(self):
        old = spec()
        new = spec("17", "B")
        config = config_for(old, new)
        store = MemoryStore([path()])
        detected = scan_all(config, values(old, new), store)
        candidates = identify_old_options(config, detected, "Java", "17")
        self.assertEqual(candidates.complete, ("8",))
        self.assertEqual(candidates.suggested, "8")
        draft = prepare_switch(config, "Java", "17", detected.targets, values(old, new), detection=detected)
        self.assertEqual(draft.old_options, ("8",))
        self.assertEqual(finalize_switch(draft).changes[0].after.value, "B;Other")
        self.assertEqual(store.read("User", "Path").value, "A;Other")

    def test_multiple_complete_candidates_require_explicit_old_selection(self):
        one, two, new = spec(), spec("11", "Other"), spec("17", "B")
        config = config_for(one, two, new)
        with self.assertRaises(AmbiguousOldSchemeError) as raised:
            prepare_switch(config, "Java", "17", snapshots(path()), values(one, two, new))
        self.assertEqual(raised.exception.candidates.complete, ("8", "11"))
        chosen = prepare_switch(config, "Java", "17", snapshots(path()), values(one, two, new), old_options=("11",))
        self.assertEqual(chosen.old_options, ("11",))
        self.assertEqual(chosen.changes[0].after.value, "B;A")
        no_removal = prepare_switch(config, "Java", "17", snapshots(path()), values(one, two, new), old_options=())
        self.assertEqual(no_removal.changes[0].after.value, "B;A;Other")

    def test_partial_old_only_removed_after_explicit_selection(self):
        old, new = spec(value="A;Missing"), spec("17", "B")
        config = config_for(old, new)
        inferred = prepare_switch(config, "Java", "17", snapshots(path()), values(old, new))
        self.assertEqual(inferred.candidates.partial, ("8",))
        self.assertEqual(inferred.old_options, ())
        self.assertEqual(inferred.changes[0].after.value, "B;A;Other")
        explicit = prepare_switch(config, "Java", "17", snapshots(path()), values(old, new), old_options=("8",))
        self.assertEqual(explicit.changes[0].after.value, "B;Other")
        self.assertEqual(len(explicit.skipped), 1)

    def test_invalid_old_options_and_stale_detection_are_rejected(self):
        old, new = spec(), spec("17", "B")
        config = config_for(old, new)
        for options in ("8", ("17",), ("Missing",)):
            with self.subTest(options=options), self.assertRaises(ValidationError):
                prepare_switch(config, "Java", "17", snapshots(path()), values(old, new), old_options=options)
        detected = scan_all(config, values(old, new), MemoryStore([path()]))
        stale = config_for(old, new)
        object.__setattr__(stale, "digest", "changed")
        with self.assertRaises(ValidationError):
            identify_old_options(stale, detected, "Java", "17")

    def test_detection_must_describe_the_same_values_and_windows_states(self):
        old, new = spec(), spec("17", "B")
        config = config_for(old, new)
        detected = scan_all(config, values(old, new), MemoryStore([path()]))
        with self.assertRaises(ValidationError):
            prepare_switch(config, "Java", "17", snapshots(path("A;Changed")), values(old, new), detection=detected)
        changed_values = values(old, new)
        changed_values[new.key] = "Changed"
        with self.assertRaises(ValidationError):
            prepare_switch(config, "Java", "17", detected.targets, changed_values, detection=detected)


class SwitchCompositionTests(unittest.TestCase):
    def test_common_list_element_has_no_false_shared_conflict(self):
        old = spec(value="Common;Old")
        new = spec("17", "Common;New")
        other = spec("Shared", "common", app="Scala")
        config = config_for(old, new, other)
        initial = path("Unrelated;Common;Old")
        draft = prepare_switch(config, "Java", "17", snapshots(initial), values(old, new, other))
        self.assertEqual(draft.shared_conflicts, ())
        result = finalize_switch(draft)
        self.assertEqual(result.changes[0].after.value, "Common;New;Unrelated")
        self.assertEqual(result.metrics.removed_element_count, 1)
        self.assertEqual(result.metrics.added_element_count, 1)
        self.assertEqual(result.changes[0].calculation.deduplicated, ())

    def test_whole_old_and_new_apply_coalesce_and_preserve_registry_type(self):
        home8 = spec(name="JAVA_HOME", mode="replace", value="JDK8")
        home17 = spec("17", "JDK17", name="JAVA_HOME", mode="replace")
        old, new = spec(value="Old;Common"), spec("17", "Common;New")
        config = config_for(home8, old, home17, new)
        states = snapshots(RegistryState("JAVA_HOME", "User", True, "JDK8", 2), path("Other;Common;Old", registry_type=2))
        result = finalize_switch(prepare_switch(config, "Java", "17", states, values(home8, old, home17, new)))
        self.assertEqual(len(result.changes), 2)
        self.assertEqual(len({(c.after.scope, c.after.name) for c in result.changes}), 2)
        self.assertEqual(result.changes[0].after, RegistryState("JAVA_HOME", "User", True, "JDK17", 2))
        self.assertEqual(result.changes[1].after.value, "Common;New;Other")
        self.assertEqual(result.changes[1].after.registry_type, 2)
        self.assertEqual(result.metrics.removed_whole_key_count, 0)

    def test_old_only_target_is_removed_and_empty_list_key_is_preserved(self):
        home = spec(name="OLD_HOME", value="old", mode="replace")
        old = spec()
        new = spec("17", "new", name="NEW_HOME", mode="replace")
        states = snapshots(RegistryState("OLD_HOME", "User", True, "old", 1), path("A"),
                           RegistryState("NEW_HOME", "User", False))
        result = finalize_switch(prepare_switch(config_for(home, old, new), "Java", "17", states,
                                               values(home, old, new)))
        by_name = {change.after.name: change.after for change in result.changes}
        self.assertFalse(by_name["OLD_HOME"].exists)
        self.assertEqual(by_name["Path"], path(""))
        self.assertEqual(by_name["NEW_HOME"].value, "new")
        self.assertEqual(result.metrics.removed_whole_key_count, 1)

    def test_manual_order_uses_existing_engine_contract(self):
        old, new = spec(), spec("17", "B", mode="append")
        config = config_for(old, new)
        draft = prepare_switch(config, "Java", "17", snapshots(path()), values(old, new))
        self.assertTrue(finalize_switch(draft).needs_order)
        self.assertEqual(draft.changes[0].calculation.items, ("Other", "B"))
        ordered = finalize_switch(draft, orders={new.key: ("B", "Other")})
        self.assertEqual(ordered.changes[0].after.value, "B;Other")
        self.assertFalse(ordered.needs_order)
        with self.assertRaises(ValidationError):
            finalize_switch(draft, orders={new.key: ("B",)})

    def test_front_and_back_modes_and_scope_separation(self):
        old_user, old_system = spec(), spec(scope="System")
        new_user = spec("17", "B", mode="-append")
        new_system = spec("17", "B", scope="System")
        result = finalize_switch(prepare_switch(config_for(old_user, old_system, new_user, new_system),
                                               "Java", "17", snapshots(path(), path("A;SystemOnly", "System")),
                                               values(old_user, old_system, new_user, new_system)))
        self.assertEqual(tuple(change.after.value for change in result.changes), ("Other;B", "B;SystemOnly"))
        self.assertEqual(tuple(change.after.scope for change in result.changes), ("User", "System"))

    def test_same_result_is_retained_as_no_change(self):
        old, new = spec(mode="replace", value="Raw"), spec("17", "Raw", mode="replace")
        result = finalize_switch(prepare_switch(config_for(old, new), "Java", "17", snapshots(path("Raw", registry_type=2)),
                                               values(old, new)))
        self.assertEqual(len(result.changes), 1)
        self.assertFalse(result.changes[0].changed)
        self.assertEqual(result.metrics.changed_target_count, 0)

    def test_freezes_input_mappings_and_requires_explicit_snapshots(self):
        old, new = spec(), spec("17", "B")
        states, selected = snapshots(path()), values(old, new)
        draft = prepare_switch(config_for(old, new), "Java", "17", states, selected)
        states[("User", "PATH")] = path("ExternallyChanged")
        selected[new.key] = "Modified"
        self.assertEqual(finalize_switch(draft).changes[0].after.value, "B;Other")
        with self.assertRaises(TypeError):
            draft.states[("User", "PATH")] = path()
        with self.assertRaises(ValidationError):
            prepare_switch(config_for(old, new), "Java", "17", {}, values(old, new))
        converted = {DetectionTarget("User", "PATH"): path()}
        self.assertEqual(prepare_switch(config_for(old, new), "Java", "17", converted, values(old, new)).old_options, ("8",))


class SwitchProtectionTests(unittest.TestCase):
    def test_shared_old_elements_keep_remove_independently(self):
        old, new = spec(value="A;B;C"), spec("17", "New")
        other_a = spec("One", "A", app="Scala")
        other_b = spec("Two", "B", app="Scala")
        draft = prepare_switch(config_for(old, new, other_a, other_b), "Java", "17", snapshots(path("A;B;C;Other")),
                               values(old, new, other_a, other_b))
        self.assertEqual(tuple(conflict.atom.display_value for conflict in draft.shared_conflicts), ("A", "B"))
        self.assertEqual(tuple(atom.display_value for atom in draft.ordinary_impacts), ("C",))
        decisions = {draft.shared_conflicts[1].atom.atom_id: ConflictDecision.REMOVE}
        result = finalize_switch(draft, decisions)
        self.assertEqual(result.changes[0].after.value, "New;A;Other")
        self.assertEqual(result.metrics.kept_shared_count, 1)
        self.assertEqual(result.metrics.removed_element_count, 2)
        self.assertTrue(any("Scala / Two" in warning for warning in result.warnings))

    def test_all_related_apps_options_are_reported_and_same_app_is_not_protected(self):
        old, new = spec(), spec("17", "New")
        same = spec("Other", "A")
        one = spec("One", "A", app="Scala")
        two = spec("Two", "a", app="Scala")
        three = spec("Three", "A", app="Maven")
        draft = prepare_switch(config_for(old, new, same, one, two, three), "Java", "17", snapshots(path()),
                               values(old, new, same, one, two, three), old_options=("8",))
        self.assertEqual(tuple((consumer.app, consumer.option) for consumer in draft.shared_conflicts[0].consumers),
                         (("Scala", "One"), ("Scala", "Two"), ("Maven", "Three")))

    def test_new_replace_cannot_discard_protected_append_without_remove(self):
        old, new = spec(), spec("17", "NewRaw", mode="replace")
        other = spec("Shared", "Other", app="Scala")
        draft = prepare_switch(config_for(old, new, other), "Java", "17", snapshots(path()), values(old, new, other))
        self.assertEqual(tuple(conflict.atom.display_value for conflict in draft.shared_conflicts), ("Other",))
        with self.assertRaisesRegex(ConflictError, "无法同时满足"):
            finalize_switch(draft)
        allow = {draft.shared_conflicts[0].atom.atom_id: "remove"}
        result = finalize_switch(draft, allow)
        self.assertEqual(result.changes[0].after.value, "NewRaw")
        self.assertEqual(result.metrics.removed_element_count, 2)

    def test_new_replace_cannot_overwrite_protected_exact_replace(self):
        old, new = spec(), spec("17", "NewRaw", mode="replace")
        protected = spec("Exact", "A;Other", app="Scala", mode="replace")
        draft = prepare_switch(config_for(old, new, protected), "Java", "17", snapshots(path()), values(old, new, protected))
        self.assertEqual(len(draft.shared_conflicts), 1)
        self.assertEqual(draft.shared_conflicts[0].atom.kind.value, "whole_key")
        with self.assertRaises(ConflictError):
            finalize_switch(draft)
        self.assertEqual(finalize_switch(draft, {draft.shared_conflicts[0].atom.atom_id: "remove"}).changes[0].after.value,
                         "NewRaw")

    def test_new_append_reorder_can_break_protected_exact_replace(self):
        old, new = spec(value="Old"), spec("17", "Common")
        protected = spec("Exact", "Other;Common;Old", app="Scala", mode="replace")
        draft = prepare_switch(config_for(old, new, protected), "Java", "17", snapshots(path("Other;Common;Old")),
                               values(old, new, protected))
        with self.assertRaises(ConflictError):
            finalize_switch(draft)
        result = finalize_switch(draft, {draft.shared_conflicts[0].atom.atom_id: "remove"})
        self.assertEqual(result.changes[0].after.value, "Common;Other")

    def test_old_whole_delete_shared_with_append_defaults_to_keep_key(self):
        old = spec(name="OLD_HOME", mode="replace", value="A;B")
        new = spec("17", "new", name="NEW_HOME", mode="replace")
        other = spec("Shared", "B", name="OLD_HOME", app="Scala")
        states = snapshots(RegistryState("OLD_HOME", "User", True, "A;B", 2),
                           RegistryState("NEW_HOME", "User", False))
        draft = prepare_switch(config_for(old, new, other), "Java", "17", states, values(old, new, other))
        result = finalize_switch(draft)
        self.assertEqual(result.changes[0].after.value, "A;B")
        self.assertFalse(result.changes[0].changed)
        self.assertTrue(finalize_switch(draft, {draft.shared_conflicts[0].atom.atom_id: "remove"}).changes[0].changed)

    def test_partial_protected_append_only_protects_actual_matched_elements(self):
        old, new = spec(), spec("17", "New")
        other = spec("Partial", "A;Absent", app="Scala")
        draft = prepare_switch(config_for(old, new, other), "Java", "17", snapshots(path()), values(old, new, other))
        self.assertEqual(tuple(conflict.atom.display_value for conflict in draft.shared_conflicts), ("A",))
        self.assertEqual(finalize_switch(draft).changes[0].after.value, "New;A;Other")

    def test_shared_conflict_ids_are_stable_and_unknown_decision_rejected(self):
        old, new, other = spec(), spec("17", "New"), spec("Shared", "a", app="Scala")
        config = config_for(old, new, other)
        one = prepare_switch(config, "Java", "17", snapshots(path()), values(old, new, other))
        two = prepare_switch(config, "Java", "17", snapshots(path("a;Other")), values(old, new, other))
        self.assertEqual(one.shared_conflicts[0].atom.atom_id, two.shared_conflicts[0].atom.atom_id)
        with self.assertRaises(ValidationError):
            finalize_switch(one, {"unknown": "remove"})
        with self.assertRaises(ValidationError):
            finalize_switch(one, {one.shared_conflicts[0].atom.atom_id: "invalid"})

    def test_exact_protected_value_can_be_kept_on_old_only_list_target(self):
        old = spec()
        new = spec("17", "new", name="NEW_HOME", mode="replace")
        other = spec("Exact", "A;Other", app="Scala", mode="replace")
        states = snapshots(path(), RegistryState("NEW_HOME", "User", False))
        draft = prepare_switch(config_for(old, new, other), "Java", "17", states, values(old, new, other))
        self.assertEqual(draft.ordinary_impacts, ())
        kept = finalize_switch(draft)
        self.assertEqual(kept.changes[0].after.value, "A;Other")
        removed = finalize_switch(draft, {draft.shared_conflicts[0].atom.atom_id: "remove"})
        self.assertEqual(removed.changes[0].after.value, "Other")

    def test_final_order_cannot_introduce_an_unconfirmed_shared_conflict(self):
        old = spec(name="OLD_HOME", value="old", mode="replace")
        new = spec("17", "A", mode="append")
        other = spec("Exact", "Other;A", app="Scala", mode="replace")
        states = snapshots(path("Other;A"), RegistryState("OLD_HOME", "User", True, "old", 1))
        draft = prepare_switch(config_for(old, new, other), "Java", "17", states, values(old, new, other))
        self.assertEqual(draft.shared_conflicts, ())
        with self.assertRaisesRegex(ConflictError, "新的共享冲突"):
            finalize_switch(draft, orders={new.key: ("A", "Other")})
        ordered_draft = prepare_switch(config_for(old, new, other), "Java", "17", states, values(old, new, other),
                                       orders={new.key: ("A", "Other")})
        self.assertEqual(len(ordered_draft.shared_conflicts), 1)
        self.assertEqual(finalize_switch(ordered_draft, {ordered_draft.shared_conflicts[0].atom.atom_id: "remove"}).changes[1].after.value,
                         "A;Other")


if __name__ == "__main__":
    unittest.main()
