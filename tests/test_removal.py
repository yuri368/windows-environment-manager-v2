from __future__ import annotations

import unittest

from envmanager.models import Configuration, ConflictError, RegistryState, ValidationError, VariableSpec
from envmanager.elements import CleanupSettings
from envmanager.removal import (
    RemovalKind, RemovalRequest, RemovalScope, build_removal_atoms, compose_removal,
    expand_sources,
)
from envmanager.sharing import ConflictDecision, ProtectedConsumer, SharedConflict


def spec(app="Java", option="8", name="Path", value="A", mode="+append", scope="User", special=False):
    return VariableSpec(app, option, name, scope, mode, True, value, special=special)


def config_for(*specs):
    apps = {}
    for item in specs:
        apps.setdefault(item.app, {}).setdefault(item.option, []).append(item)
    return Configuration({app: {option: tuple(items) for option, items in options.items()}
                          for app, options in apps.items()}, "test")


def snapshots(*states):
    from envmanager.models import match_key
    return {(state.scope, match_key(state.name)): state for state in states}


def values(*specs):
    return {item.key: item.default_value for item in specs}


class RemovalScopeTests(unittest.TestCase):
    def setUp(self):
        self.home = spec(name="JAVA_HOME", value="jdk8", mode="replace")
        self.path = spec()
        self.new = spec(option="17", value="B")
        self.maven = spec(app="Maven", option="3", value="M")
        self.scala = spec(app="Scala", option="2", value="S")
        self.config = config_for(self.home, self.path, self.new, self.maven, self.scala)

    def test_five_scopes_in_configuration_order(self):
        cases = (
            (RemovalRequest(RemovalScope.VARIABLE, {"Java"}, {"Java": "8"}, (self.home.key,)),
             (self.home,)),
            (RemovalRequest(RemovalScope.SCHEME, {"Java"}, {"Java": "8"}), (self.home, self.path)),
            (RemovalRequest(RemovalScope.APPLICATION_ALL, {"Java"}), (self.home, self.path, self.new)),
            (RemovalRequest(RemovalScope.BATCH_CURRENT, {"Maven", "Java"}, {"Java": "17", "Maven": "3"}),
             (self.new, self.maven)),
            (RemovalRequest(RemovalScope.BATCH_ALL, {"Maven", "Java"}),
             (self.home, self.path, self.new, self.maven)),
        )
        for request, expected in cases:
            with self.subTest(scope=request.scope):
                self.assertEqual(expand_sources(self.config, request), expected)

    def test_request_freezes_current_options_and_apps(self):
        selected = {"Java"}
        options = {"Java": "8"}
        request = RemovalRequest("batch_current", selected, options)
        selected.add("Maven")
        options["Java"] = "17"
        self.assertEqual(expand_sources(self.config, request), (self.home, self.path))
        with self.assertRaises(TypeError):
            request.selected_options["Java"] = "17"

    def test_invalid_ranges_do_not_guess(self):
        requests = (
            RemovalRequest("scheme", set(), {}),
            RemovalRequest("batch_all", {"Missing"}),
            RemovalRequest("scheme", {"Java", "Maven"}, {"Java": "8"}),
            RemovalRequest("batch_current", {"Java"}),
            RemovalRequest("scheme", {"Java"}, {"Java": "absent"}),
            RemovalRequest("variable", {"Java"}, {"Java": "8"}, (self.new.key,)),
            RemovalRequest("variable", {"Java"}, {"Java": "8"}),
            RemovalRequest("scheme", {"Java"}, {"Java": "8"}, (self.home.key,)),
        )
        for request in requests:
            with self.subTest(request=request), self.assertRaises(ValidationError):
                expand_sources(self.config, request)


class RemovalCandidateTests(unittest.TestCase):
    def test_ordinary_replace_generates_elements_and_empty_state_atom(self):
        for value in ("A;;B", ""):
            item = spec(value=value, mode="replace")
            state = RegistryState("pAtH", "User", True, value, 2)
            result = build_removal_atoms((item,), snapshots(state), values(item))
            self.assertEqual(len(result.atoms), 2 if value else 1)
            self.assertEqual(result.atoms[0].kind, RemovalKind.LIST_ELEMENT if value else RemovalKind.EMPTY_VALUE)
            changes = compose_removal(result.atoms, (), {}, snapshots(state))
            self.assertEqual(changes[0].after, RegistryState("pAtH", "User", False))

    def test_nonmatching_and_missing_replace_are_skipped(self):
        item = spec(value="a", mode="replace")
        for state, reason in ((RegistryState("Path", "User", True, "A", 1), "element_missing"),
                              (RegistryState("Path", "User", False), "key_missing")):
            result = build_removal_atoms((item,), snapshots(state), values(item))
            self.assertEqual(result.atoms, ())
            self.assertEqual(result.skipped[0].reason, reason)

    def test_partial_input_in_all_append_modes_ignores_order(self):
        for mode in ("replace", "+append", "-append", "append"):
            item = spec(value="A;B;C", mode=mode)
            result = build_removal_atoms((item,), snapshots(RegistryState("Path", "User", True, " B ;X;\tA", 1)), values(item))
            self.assertEqual([atom.display_value for atom in result.atoms], ["A", "B"])
            self.assertEqual([(skip.reason, skip.display_value) for skip in result.skipped], [("element_missing", "C")])

    def test_current_selected_value_is_used_for_each_option(self):
        one = spec(value="default")
        two = spec(option="17", value="default")
        selected = {one.key: "A", two.key: "B"}
        result = build_removal_atoms((one, two), snapshots(RegistryState("Path", "User", True, "B", 1)), selected)
        self.assertEqual(result.atoms[0].sources[0].option, "17")
        self.assertEqual(result.atoms[0].display_value, "B")
        self.assertEqual(result.skipped[0].display_value, "A")

    def test_same_target_element_combines_sources_and_counts_actual_copies_once(self):
        one, two = spec(value="a;A"), spec(option="17", value="A")
        result = build_removal_atoms((one, one, two), snapshots(RegistryState("Path", "User", True, "A;a;A;B", 1)), values(one, two))
        self.assertEqual(len(result.atoms), 2)
        atom = result.atoms[1]
        self.assertEqual(result.atoms[0].element_key, "a")
        self.assertEqual((len(atom.sources), atom.occurrence_count, atom.duplicate_count), (2, 2, 1))
        self.assertEqual(len(result.requested_sources), 2)
        reverse = build_removal_atoms((two, one), snapshots(RegistryState("Path", "User", True, "A;a;A;B", 1)), values(one, two))
        self.assertEqual(atom.atom_id, reverse.atoms[0].atom_id)

    def test_same_whole_key_combines_sources(self):
        one, two = spec(value="A", mode="replace", special=True), spec(option="17", value="A", mode="replace", special=True)
        result = build_removal_atoms((one, two), snapshots(RegistryState("Path", "User", True, "A", 1)), values(one, two))
        self.assertEqual(len(result.atoms), 1)
        self.assertEqual(len(result.atoms[0].sources), 2)

    def test_empty_input_does_not_remove_nonempty_or_absent_key(self):
        for mode in ("+append", "-append", "append"):
            item = spec(value="", mode=mode)
            for state in (RegistryState("Path", "User", True, "A", 1), RegistryState("Path", "User", False)):
                result = build_removal_atoms((item,), snapshots(state), values(item))
                self.assertEqual(result.atoms, ())
                self.assertEqual(result.skipped[0].reason, "value_mismatch" if state.exists else "key_missing")

    def test_exact_empty_state_active_removal_deletes_key_in_all_modes(self):
        for mode in ("replace", "+append", "-append", "append"):
            item = spec(value="", mode=mode)
            states = snapshots(RegistryState("Path", "User", True, "", 2))
            atoms = build_removal_atoms((item,), states, values(item)).atoms
            self.assertEqual(atoms[0].kind, RemovalKind.EMPTY_VALUE)
            self.assertFalse(compose_removal(atoms, (), {}, states)[0].after.exists)

    def test_special_match_requires_complete_raw_value(self):
        item = spec(value="  A;B  ", mode="replace", special=True)
        for raw, expected in (("  A;B  ", 1), ("A;B", 0), ("A", 0)):
            states = snapshots(RegistryState("Path", "User", True, raw, 2))
            atoms = build_removal_atoms((item,), states, values(item)).atoms
            self.assertEqual(len(atoms), expected)
            if atoms:
                self.assertEqual(atoms[0].kind, RemovalKind.WHOLE_KEY)
                self.assertFalse(compose_removal(atoms, (), {}, states)[0].after.exists)

    def test_references_are_case_sensitive_unexpanded_and_ascii_spaces_trimmed(self):
        item = spec(value=r"%HOME%\bin; A ;ß;SS")
        result = build_removal_atoms((item,), snapshots(RegistryState("Path", "User", True, r"%home%\bin;A;ß", 2)), values(item))
        self.assertEqual([atom.display_value for atom in result.atoms], ["A", "ß"])
        self.assertEqual([skip.display_value for skip in result.skipped], [r"%HOME%\bin", "SS"])

    def test_explicit_snapshot_and_selected_input_required(self):
        item = spec()
        for states, selected in (({}, values(item)), (snapshots(RegistryState("Path", "User", False)), {})):
            with self.assertRaises(ValidationError):
                build_removal_atoms((item,), states, selected)
        with self.assertRaises(ValidationError):
            build_removal_atoms((item,), snapshots(RegistryState("Path", "User", True, "A", 1)), {item.key: "A\0B"})


class RemovalCompositionTests(unittest.TestCase):
    def compose(self, value, input_value, registry_type=2, cleanup=None):
        item = spec(value=input_value)
        states = snapshots(RegistryState("Path", "User", True, value, registry_type))
        result = build_removal_atoms((item,), states, values(item))
        return compose_removal(result.atoms, (), {}, states, cleanup)

    def test_removes_case_sensitive_copies_and_cleans_remaining_elements(self):
        self.assertEqual(self.compose("A;b;B;C", "b")[0].after.value, "A;B;C")
        self.assertEqual(self.compose("A;B;B;C", "A")[0].after.value, "B;C")
        change = self.compose("A;; ;B;B; A ;", "A")[0]
        self.assertEqual(change.after.value, "B")
        self.assertTrue(change.calculation.warnings)
        self.assertEqual(change.contributions[-1]["kind"], "cleanup")

    def test_removal_of_last_effective_element_deletes_key(self):
        for registry_type in (1, 2):
            after = self.compose("A;; A ;", "A", registry_type)[0].after
            self.assertEqual(after, RegistryState("Path", "User", False))
            retained = self.compose("A;a", "A", registry_type)[0].after
            self.assertEqual(retained, RegistryState("Path", "User", True, "a", registry_type))

    def test_disabled_cleanup_preserves_unmatched_raw_slices_and_deletes_last(self):
        disabled = CleanupSettings(False, False, False)
        self.assertEqual(self.compose(" A ;B;; C ", "B", cleanup=disabled)[0].after.value, " A ;; C ")
        self.assertEqual(self.compose("A;; ;B;B; A ;", "A", cleanup=disabled)[0].after.value, "; ;B;B;")
        self.assertFalse(self.compose("A;;", "A", cleanup=disabled)[0].after.exists)

    def test_same_target_composed_once_and_scope_independent(self):
        user = spec(value="A;B")
        system = spec(value="A", scope="System")
        states = snapshots(RegistryState("Path", "User", True, "A;B;C", 2),
                           RegistryState("Path", "System", True, "A;D", 1))
        result = build_removal_atoms((user, system), states, values(user, system))
        changes = compose_removal(result.atoms, (), {}, states)
        self.assertEqual([(c.after.scope, c.after.value) for c in changes], [("User", "C"), ("System", "D")])
        self.assertEqual(len(changes[0].contributions), 2)

    def test_shared_decisions_are_independent_and_default_keep(self):
        item = spec(value="A;B;C")
        states = snapshots(RegistryState("Path", "User", True, "A;B;C;D", 1))
        atoms = build_removal_atoms((item,), states, values(item)).atoms
        consumer = ProtectedConsumer("Scala", "2", "key", "+append", "uses element")
        conflicts = tuple(SharedConflict(atom, (consumer,)) for atom in atoms[:2])
        changes = compose_removal(atoms[2:], conflicts, {atoms[1].atom_id: ConflictDecision.REMOVE}, states)
        self.assertEqual(changes[0].after.value, "A;D")
        metadata = {entry["atom_id"]: entry for entry in changes[0].contributions}
        self.assertEqual(metadata[atoms[0].atom_id]["decision"], "keep")
        self.assertEqual(metadata[atoms[1].atom_id]["consumers"][0]["app"], "Scala")
        self.assertEqual(compose_removal((), conflicts, {}, states), ())

    def test_ordinary_replace_remove_can_keep_one_shared_element(self):
        whole, element = spec(value="A;B", mode="replace"), spec(option="17", value="A")
        states = snapshots(RegistryState("Path", "User", True, "A;B", 1))
        atoms = build_removal_atoms((whole, element), states, values(whole, element)).atoms
        conflict = SharedConflict(atoms[1], (ProtectedConsumer("Scala", "2", "k", "append", "uses A"),))
        kept = compose_removal(atoms[:1], (conflict,), {}, states)
        self.assertEqual(kept[0].after.value, "B")
        changes = compose_removal(atoms[:1], (conflict,), {conflict.atom.atom_id: "remove"}, states)
        self.assertEqual(len(changes), 1)
        self.assertFalse(changes[0].after.exists)

    def test_unknown_decision_never_overrides_ordinary_atom(self):
        item = spec()
        states = snapshots(RegistryState("Path", "User", True, "A", 1))
        atoms = build_removal_atoms((item,), states, values(item)).atoms
        with self.assertRaises(ValidationError):
            compose_removal(atoms, (), {atoms[0].atom_id: "keep"}, states)

    def test_repeated_removal_has_no_candidates_or_changes(self):
        item = spec()
        first = self.compose("A;B", "A")[0].after
        states = snapshots(first)
        candidates = build_removal_atoms((item,), states, values(item))
        self.assertEqual(candidates.atoms, ())
        self.assertEqual(compose_removal(candidates.atoms, (), {}, states), ())


if __name__ == "__main__":
    unittest.main()
