from __future__ import annotations

from dataclasses import FrozenInstanceError
import unittest

from envmanager.matching import (DetectionError, SchemeStatus, TargetKey,
                                 VariableMatch, VariableMatchStatus,
                                 match_scheme, match_variable)
from envmanager.models import RegistryState, ValidationError, VariableSpec


def spec(mode="replace", value="A", scope="User", name="Path"):
    return VariableSpec("App", "Default", name, scope, mode, True, value)


def state(value="A", exists=True, kind=1, scope="User", name="Path"):
    return RegistryState(name, scope, exists, value if exists else None,
                         kind if exists else None)


class VariableMatchingTests(unittest.TestCase):
    def test_replace_is_strict_complete_string(self):
        record = spec(value="A;B")
        for current in ("a;B", "B;A", "A;B ", " A;B", "A", "A;B;C"):
            with self.subTest(current=current):
                result = match_variable(record, "A;B", state(current))
                self.assertIs(result.status, VariableMatchStatus.VALUE_MISMATCH)
                self.assertEqual(result.matched_elements, ())
        self.assertIs(match_variable(record, "A;B", state("A;B")).status,
                      VariableMatchStatus.MATCH)

    def test_replace_empty_exists_and_missing_are_distinct(self):
        self.assertIs(match_variable(spec(value=""), "", state("")).status,
                      VariableMatchStatus.MATCH)
        self.assertIs(match_variable(spec(value=""), "", state(exists=False)).status,
                      VariableMatchStatus.KEY_MISSING)

    def test_registry_type_is_not_part_of_matching(self):
        for mode in ("replace", "+append", "-append", "append"):
            for kind in (1, 2):
                with self.subTest(mode=mode, kind=kind):
                    result = match_variable(spec(mode), "A", state("A", kind=kind))
                    self.assertIs(result.status, VariableMatchStatus.MATCH)
                    self.assertEqual(result.windows_state.registry_type, kind)

    def test_all_append_modes_ignore_order_and_case(self):
        for mode in ("+append", "-append", "append"):
            for current in ("Other;b;a", "b;Other;A", "a;B;Other"):
                with self.subTest(mode=mode, current=current):
                    result = match_variable(spec(mode), "A;B", state(current))
                    self.assertIs(result.status, VariableMatchStatus.MATCH)
                    self.assertEqual(result.matched_elements, ("A", "B"))
                    self.assertEqual(result.missing_elements, ())

    def test_append_partial_progress_and_windows_duplicate_counts(self):
        result = match_variable(spec("append"), "a;A;B;b;C", state("A;a;Other;b;B;b"))
        self.assertIs(result.status, VariableMatchStatus.ELEMENTS_PARTIAL)
        self.assertEqual(result.matched_elements, ("a", "B"))
        self.assertEqual(result.missing_elements, ("C",))
        self.assertEqual(dict(result.occurrence_counts), {"a": 2, "B": 3, "C": 0})
        self.assertEqual(dict(result.duplicate_counts), {"a": 2, "B": 3})

    def test_missing_append_reports_all_unique_input_elements(self):
        result = match_variable(spec("append"), "A;a;B", state(exists=False))
        self.assertIs(result.status, VariableMatchStatus.KEY_MISSING)
        self.assertEqual(result.missing_elements, ("A", "B"))
        self.assertEqual(result.matched_elements, ())

    def test_append_empty_input_only_matches_existing_empty_value(self):
        for mode in ("+append", "-append", "append"):
            for current, expected in (
                ("", VariableMatchStatus.MATCH),
                ("A;B", VariableMatchStatus.VALUE_MISMATCH),
                (";", VariableMatchStatus.VALUE_MISMATCH),
            ):
                with self.subTest(mode=mode, current=current):
                    result = match_variable(spec(mode), "", state(current))
                    self.assertIs(result.status, expected)
                    self.assertEqual(result.matched_elements, ())
                    self.assertEqual(result.missing_elements, ())
                    self.assertEqual(dict(result.occurrence_counts), {})
            self.assertIs(match_variable(spec(mode), "", state(exists=False)).status,
                          VariableMatchStatus.KEY_MISSING)

    def test_append_empty_selection_nonempty_windows_is_partial_scheme(self):
        for mode in ("+append", "-append", "append"):
            with self.subTest(mode=mode):
                variable = match_variable(spec(mode), "", state("A;B"))
                scheme = match_scheme("App", "Default", (variable,))
                self.assertIs(variable.status, VariableMatchStatus.VALUE_MISMATCH)
                self.assertIs(scheme.status, SchemeStatus.PARTIAL)
                self.assertEqual((scheme.matched_count, scheme.total_count), (0, 1))

    def test_append_preserves_raw_path_and_reference_semantics(self):
        pairs = ((r"%JAVA_HOME%\bin", r"D:\Java\jdk8\bin"),
                 (r"D:\Tools", "D:\\Tools\\"),
                 (r"D:\Tools", "D:/Tools"), ("A", " A"), ("A", "A "))
        for selected, current in pairs:
            with self.subTest(selected=selected, current=current):
                result = match_variable(spec("append"), selected, state(current))
                self.assertIs(result.status, VariableMatchStatus.ELEMENTS_PARTIAL)
                self.assertEqual(result.missing_elements, (selected,))

    def test_append_uses_match_key_without_unicode_expansion(self):
        result = match_variable(spec("append"), "ß", state("SS"))
        self.assertIs(result.status, VariableMatchStatus.ELEMENTS_PARTIAL)

    def test_existing_anomalies_are_not_cleaned_or_rejected(self):
        current = "; ; A ;a;A;;Other;Other;"
        original = state(current)
        result = match_variable(spec("append"), "a", original)
        self.assertIs(result.status, VariableMatchStatus.MATCH)
        self.assertEqual(result.occurrence_counts["a"], 2)
        self.assertIs(result.windows_state, original)
        self.assertEqual(original.value, current)

    def test_invalid_selection_is_error_not_missing(self):
        for value in (" ", "A;", ";A", "A;;B", "A; ;B", "A\0B", None):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    match_variable(spec("append"), value, state(exists=False))

    def test_invalid_state_or_wrong_target_is_error(self):
        bad_states = (state(kind=3), state(scope="System"), state(name="OTHER"),
                      RegistryState("Path", "User", True, None, 1),
                      RegistryState("Path", "User", False, "", 1))
        for current in bad_states:
            with self.subTest(current=current):
                with self.assertRaises(ValidationError):
                    match_variable(spec(), "A", current)

    def test_case_insensitive_name_and_scope_identity(self):
        result = match_variable(spec(name="path"), "A", state(name="PATH"))
        self.assertEqual(result.target, TargetKey("User", "PATH"))

    def test_result_is_immutable(self):
        result = match_variable(spec("append"), "A", state("a;A"))
        with self.assertRaises(FrozenInstanceError):
            result.status = VariableMatchStatus.KEY_MISSING
        with self.assertRaises(TypeError):
            result.occurrence_counts["A"] = 0


class SchemeMatchingTests(unittest.TestCase):
    def test_complete_partial_and_unconfigured_counts(self):
        good = match_variable(spec(), "A", state())
        wrong = match_variable(spec(), "A", state("B"))
        missing = match_variable(spec(), "A", state(exists=False))
        partial_element = match_variable(spec("append"), "A;B", state("A"))
        cases = (((good, good), SchemeStatus.COMPLETE, 2),
                 ((missing, missing), SchemeStatus.UNCONFIGURED, 0),
                 ((wrong,), SchemeStatus.PARTIAL, 0),
                 ((partial_element, missing), SchemeStatus.PARTIAL, 0),
                 ((good, wrong), SchemeStatus.PARTIAL, 1))
        for variables, status, count in cases:
            with self.subTest(status=status, count=count):
                result = match_scheme("App", "Default", variables)
                self.assertIs(result.status, status)
                self.assertEqual((result.matched_count, result.total_count), (count, len(variables)))
                self.assertTrue(result.available)

    def test_empty_scheme_is_configuration_error(self):
        result = match_scheme("App", "Empty", ())
        self.assertIsNone(result.status)
        self.assertFalse(result.available)
        self.assertEqual(result.errors[0].kind, "config")

    def test_error_prevents_business_classification(self):
        record = spec()
        error = DetectionError("read", "Access denied", TargetKey.from_spec(record), record.key)
        failed = VariableMatch(record.key, TargetKey.from_spec(record), record.mode,
                               None, "A", error=error)
        result = match_scheme("App", "Default", (match_variable(record, "A", state()), failed))
        self.assertIsNone(result.status)
        self.assertEqual((result.matched_count, result.total_count), (1, 2))
        self.assertEqual(result.errors, (error,))


if __name__ == "__main__":
    unittest.main()
