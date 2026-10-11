from __future__ import annotations

import unittest

from envmanager.models import RegistryState
from envmanager.removal import build_removal_atoms, compose_removal
from envmanager.sharing import ConflictDecision, classify_conflicts, protected_specs
from tests.test_removal import config_for, snapshots, spec, values


class SharingTests(unittest.TestCase):
    def analyze(self, source, *others, actual="A;B;C", selected_apps=None, selected_values=None):
        specs = (source,) + others
        config = config_for(*specs)
        selected = values(*specs) if selected_values is None else selected_values
        states = snapshots(RegistryState(source.name, source.scope, True, actual, 2))
        atoms = build_removal_atoms((source,), states, selected).atoms
        return classify_conflicts(config, selected_apps or {source.app}, atoms, states, selected)

    def test_selected_apps_other_options_are_not_protected(self):
        source = spec()
        selected_other = spec(option="17")
        scala = spec(app="Scala", option="2")
        result = self.analyze(source, selected_other, scala)
        self.assertEqual([(c.app, c.option) for c in result.shared_conflicts[0].consumers], [("Scala", "2")])

    def test_all_options_of_unselected_apps_are_protected(self):
        source = spec()
        other_options = (spec(app="Scala", option="2"), spec(app="Scala", option="3"), spec(app="Hadoop"))
        result = self.analyze(source, *other_options)
        self.assertEqual([(c.app, c.option) for c in result.shared_conflicts[0].consumers],
                         [("Scala", "2"), ("Scala", "3"), ("Hadoop", "8")])
        self.assertEqual(result.shared_conflicts[0].default_decision, ConflictDecision.KEEP)

    def test_selected_batch_apps_never_protect_each_other(self):
        source = spec()
        maven = spec(app="Maven")
        hadoop = spec(app="Hadoop", option="other")
        result = self.analyze(source, maven, hadoop, selected_apps={"Java", "Maven"})
        self.assertEqual([c.app for c in result.shared_conflicts[0].consumers], ["Hadoop"])

    def test_partial_append_record_still_protects_existing_element(self):
        source = spec()
        other = spec(app="Scala", value="A;missing")
        result = self.analyze(source, other)
        self.assertEqual(len(result.shared_conflicts), 1)
        self.assertEqual(result.shared_conflicts[0].consumers[0].spec_key, other.key)

    def test_different_elements_and_scope_do_not_share(self):
        source = spec()
        irrelevant = spec(app="Scala", value="B")
        system = spec(app="Hadoop", scope="System")
        result = self.analyze(source, irrelevant, system)
        self.assertEqual(len(result.ordinary_atoms), 1)
        self.assertEqual(result.shared_conflicts, ())

    def test_declared_but_not_matching_replace_does_not_protect(self):
        source = spec()
        other = spec(app="Scala", value="a;B;C", mode="replace")
        result = self.analyze(source, other, actual="A;B;C")
        self.assertEqual(result.shared_conflicts, ())

    def test_list_removal_breaks_matching_replace_whole_value(self):
        source = spec()
        other = spec(app="Scala", value="A;B;C", mode="replace")
        result = self.analyze(source, other)
        consumer = result.shared_conflicts[0].consumers[0]
        self.assertEqual((consumer.app, consumer.option, consumer.spec_key, consumer.mode),
                         (other.app, other.option, other.key, "replace"))
        self.assertIn("replace", consumer.reason)

    def test_whole_delete_protects_matching_replace_and_partial_list(self):
        source = spec(value="A;B;C", mode="replace")
        same = spec(app="Scala", value="A;B;C", mode="replace")
        partial = spec(app="Hadoop", value="B;missing")
        unmatched = spec(app="Maven", value="missing")
        result = self.analyze(source, same, partial, unmatched)
        self.assertEqual([c.app for c in result.shared_conflicts[0].consumers], ["Scala", "Hadoop"])
        self.assertIn("B", result.shared_conflicts[0].consumers[1].reason)

    def test_whole_delete_breaks_matched_empty_append_key_existence(self):
        source = spec(value="A;B;C", mode="replace")
        empty = spec(app="Scala", value="")
        result = self.analyze(source, empty)
        self.assertEqual(len(result.shared_conflicts), 1)
        self.assertIn("键存在", result.shared_conflicts[0].consumers[0].reason)

    def test_each_options_own_selected_value_controls_protection(self):
        source = spec()
        one, two = spec(app="Scala", option="2", value="default"), spec(app="Scala", option="3", value="A")
        selected = {source.key: "A", one.key: "A", two.key: "B"}
        result = self.analyze(source, one, two, selected_values=selected)
        self.assertEqual([c.option for c in result.shared_conflicts[0].consumers], ["2"])

    def test_exact_scenario_two_shared_elements_one_ordinary_and_independent_whole_keys(self):
        java = spec(value=r"%JAVA_HOME%\bin;%JAVA_HOME%\lib")
        java_home = spec(name="JAVA_HOME", value="jdk8", mode="replace")
        maven = spec(app="Maven", option="3.9.11", value=r"%MAVEN_HOME%\bin")
        maven_home = spec(app="Maven", option="3.9.11", name="MAVEN_HOME", value="maven", mode="replace")
        scala = spec(app="Scala", option="2.12.15", value=java.default_value)
        hadoop = spec(app="Hadoop", option="3.3.1", value=r"%JAVA_HOME%\bin;missing")
        all_specs = (java, java_home, maven, maven_home, scala, hadoop)
        original = r"C:\Windows;%JAVA_HOME%\bin;%JAVA_HOME%\lib;%MAVEN_HOME%\bin;D:\Git\bin"
        states = snapshots(RegistryState("Path", "User", True, original, 2),
                           RegistryState("JAVA_HOME", "User", True, "jdk8", 1),
                           RegistryState("MAVEN_HOME", "User", True, "maven", 1))
        candidates = build_removal_atoms(all_specs[:4], states, values(*all_specs))
        result = classify_conflicts(config_for(*all_specs), {"Java", "Maven"}, candidates.atoms, states, values(*all_specs))
        self.assertEqual([c.atom.display_value for c in result.shared_conflicts],
                         [r"%JAVA_HOME%\bin", r"%JAVA_HOME%\lib"])
        self.assertEqual([[c.app for c in conflict.consumers] for conflict in result.shared_conflicts],
                         [["Scala", "Hadoop"], ["Scala"]])
        self.assertEqual(len(result.ordinary_atoms), 3)
        changes = compose_removal(result.ordinary_atoms, result.shared_conflicts,
                                  {result.shared_conflicts[1].atom.atom_id: "remove"}, states)
        by_name = {c.before.name: c for c in changes}
        self.assertEqual(by_name["Path"].after.value, r"C:\Windows;%JAVA_HOME%\bin;D:\Git\bin")
        self.assertFalse(by_name["JAVA_HOME"].after.exists)
        self.assertFalse(by_name["MAVEN_HOME"].after.exists)

    def test_protected_specs_return_full_complement_without_current_option_inputs(self):
        source = spec()
        others = (spec(app="Scala", option="2"), spec(app="Scala", option="3"), spec(app="Hadoop"))
        self.assertEqual(protected_specs(config_for(source, *others), {"Java"}), others)


if __name__ == "__main__":
    unittest.main()
