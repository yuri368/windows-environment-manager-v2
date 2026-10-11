from __future__ import annotations

import unittest

from envmanager.engine import calculate, calculate_batch
from envmanager.models import RegistryState, ValidationError, VariableSpec


def spec(mode, app="App", scope="User", name="Path"):
    return VariableSpec(app, "Default", name, scope, mode, True, "")


def state(value="A;B;A;C", *, exists=True, registry_type=1):
    return RegistryState("Path", "User", exists, value if exists else None,
                         registry_type if exists else None)


class BatchEngineTests(unittest.TestCase):
    def test_single_contribution_preserves_original_api(self):
        for mode in ("replace", "+append", "-append", "append"):
            with self.subTest(mode=mode):
                item = spec(mode)
                self.assertEqual(calculate_batch([(item, "a")], state()), calculate(item, "a", state()))

    def test_front_back_combined_dedup_and_idempotency(self):
        contributions = [(spec("+append"), "a;X;a"),
                         (spec("+append", "Second"), "x;Y"),
                         (spec("-append", "Third"), "C;Z")]
        result = calculate_batch(contributions, state())
        self.assertEqual(result.result.value, "a;X;Y;B;C;Z")
        self.assertEqual(result.added, ("X", "Y", "Z"))
        self.assertEqual(result.deduplicated, ("A", "a", "x"))
        self.assertEqual(calculate_batch(contributions, result.result).result.value, result.result.value)

    def test_front_back_overlap_is_conflict(self):
        with self.assertRaisesRegex(ValidationError, "置前和置后"):
            calculate_batch([(spec("+append"), "X"), (spec("-append", "Other"), "x")], state())

    def test_replace_exact_input_and_mixed_modes_conflict(self):
        identical = [(spec("replace"), "raw;;text"), (spec("replace", "Other"), "raw;;text")]
        self.assertEqual(calculate_batch(identical, state(registry_type=2)).result,
                         RegistryState("Path", "User", True, "raw;;text", 2))
        for contributions in ([(spec("replace"), "A"), (spec("replace", "Other"), "a")],
                              [(spec("replace"), "A"), (spec("+append", "Other"), "A")]):
            with self.subTest(contributions=contributions), self.assertRaises(ValidationError):
                calculate_batch(contributions, state())

    def test_manual_merge_uses_existing_positions_and_one_confirmation(self):
        contributions = [(spec("+append"), "a;X"), (spec("-append", "Other"), "A;Y"),
                         (spec("append", "Manual"), "C;Z")]
        initial = calculate_batch(contributions, state("B;A;A;B;C;; "))
        self.assertTrue(initial.needs_order)
        self.assertEqual(initial.items, ("B", "a", "B", "C", "", " ", "X", "Y", "Z"))
        confirmed = tuple(reversed(initial.items))
        result = calculate_batch(contributions, state("B;A;A;B;C;; "), confirmed)
        self.assertFalse(result.needs_order)
        self.assertEqual(result.items, confirmed)
        for invalid in (confirmed[:-1], tuple("A" if x == "a" else x for x in confirmed), list(confirmed)):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                calculate_batch(contributions, state("B;A;A;B;C;; "), invalid)

    def test_unmatched_existing_abnormal_items_and_duplicates_are_untouched(self):
        original = "A;; ;A; B ;"
        result = calculate_batch([(spec("+append"), "X"), (spec("-append", "Other"), "Y")], state(original))
        self.assertEqual(result.result.value, "X;" + original + ";Y")
        self.assertEqual(len(result.warnings), 2)

    def test_empty_inputs_preserve_existing_or_create_present_empty(self):
        contributions = [(spec("+append"), ""), (spec("-append", "Other"), "")]
        self.assertEqual(calculate_batch(contributions, state("A;;B")).result.value, "A;;B")
        for existing in (state(""), state(exists=False)):
            result = calculate_batch(contributions, existing).result
            self.assertEqual((result.exists, result.value, result.registry_type), (True, "", 1))

    def test_scope_type_input_and_total_length_validation(self):
        with self.assertRaises(ValidationError):
            calculate_batch([], state())
        with self.assertRaises(ValidationError):
            calculate_batch([(spec("+append"), "X"), (spec("+append", scope="System"), "Y")], state())
        with self.assertRaises(ValidationError):
            calculate_batch([(spec("+append"), "X"), (spec("+append", name="OTHER"), "Y")], state())
        with self.assertRaises(ValidationError):
            calculate_batch([(spec("+append"), "X"), (spec("-append", "Other"), "Y")], state(registry_type=7))
        with self.assertRaises(ValidationError):
            calculate_batch([(spec("+append"), "X;;Y"), (spec("-append", "Other"), "Z")], state())
        with self.assertRaises(ValidationError):
            calculate_batch([(spec("+append"), "X" * 16383), (spec("-append", "Other"), "Y" * 16383)], state("A"))

    def test_references_and_case_matching_keep_raw_strings(self):
        contributions = [(spec("+append"), r"%HOME%\Bin;ß"), (spec("-append", "Other"), "SS")]
        result = calculate_batch(contributions, state(exists=False)).result
        self.assertEqual((result.value, result.registry_type), (r"%HOME%\Bin;ß;SS", 2))


if __name__ == "__main__":
    unittest.main()
