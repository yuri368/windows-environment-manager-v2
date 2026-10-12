from __future__ import annotations

from dataclasses import replace
import unittest

from envmanager.models import Choice, VariableSpec
from envmanager.selection import SelectionSession


def spec(**kwargs):
    return replace(VariableSpec("App", "Default", "Path", "User", "+append", True,
                                "Default", (Choice("First", "A"), Choice("Second", "B"))),
                   **kwargs)


class SuccessfulSelectionTests(unittest.TestCase):
    def test_sources_restore_even_with_equal_values(self):
        item = spec(default_value="A")
        for source in ("default", "last", "choice:0", "custom"):
            with self.subTest(source=source):
                session = SelectionSession({item.key: "A"})
                session.choose(item, source)
                snapshot = session.snapshot(item)
                restored = SelectionSession({item.key: "outdated"}, {item.key: snapshot})
                self.assertEqual((restored.get(item).source, restored.get(item).value),
                                 (source, "A"))
                if source == "custom":
                    self.assertEqual(restored.get(item).custom_draft, "A")

    def test_candidate_reorder_restores_identity(self):
        item = spec()
        session = SelectionSession({})
        session.choose(item, "choice:1")
        snapshot = session.snapshot(item)
        self.assertEqual((snapshot["choice_label"], snapshot["choice_value"]), ("Second", "B"))
        changed = replace(item, choices=tuple(reversed(item.choices)))
        restored = SelectionSession({}, {item.key: snapshot}).get(changed)
        self.assertEqual((restored.source, restored.value, restored.warning), ("choice:0", "B", None))

    def test_changed_source_preserves_successful_input_as_last(self):
        item = spec()
        cases = [
            ({"source": "default", "value": "old"}, item),
            ({"source": "custom", "value": "custom"}, replace(item, editable=False)),
            ({"source": "choice:0", "value": "A", "choice_label": "Old", "choice_value": "A"}, item),
            ({"source": "choice:0", "value": "old", "choice_label": "First", "choice_value": "old"}, item),
            ({"source": "unknown", "value": "saved"}, item),
        ]
        for snapshot, current in cases:
            with self.subTest(snapshot=snapshot):
                session = SelectionSession({}, {item.key: snapshot})
                selection = session.get(current)
                self.assertEqual((selection.source, selection.value), ("last", snapshot["value"]))
                self.assertTrue(selection.warning)
                self.assertIn(("last", "上次应用值", snapshot["value"]), session.sources(current))

    def test_invalid_successful_input_falls_back_to_current_default(self):
        item = spec()
        for snapshot in ({"source": "custom", "value": "A\x00B"},
                         {"source": "last", "value": 1}, {"source": "last"}, None):
            with self.subTest(snapshot=snapshot):
                selection = SelectionSession({item.key: "older"}, {item.key: snapshot}).get(item)
                self.assertEqual((selection.source, selection.value), ("default", "Default"))
                self.assertTrue(selection.warning)

    def test_successful_raw_inputs_retain_cleanable_slices(self):
        item = spec()
        for source, raw in (('custom', ' A;;A; '), ('last', ' \t ')):
            with self.subTest(source=source, raw=raw):
                session = SelectionSession({}, {item.key: {'source': source, 'value': raw}})
                selection = session.get(item)
                self.assertEqual((selection.source, selection.value), (source, raw))
                self.assertEqual(session.snapshot(item)['value'], raw)
                self.assertIsNone(selection.warning)

    def test_empty_successful_input_is_present(self):
        item = spec(default_value="")
        restored = SelectionSession({}, {item.key: {"source": "custom", "value": ""}}).get(item)
        self.assertEqual((restored.source, restored.value, restored.custom_draft), ("custom", "", ""))
        self.assertIsNone(restored.error)

    def test_refresh_success_preserves_initialized_user_edits(self):
        first = spec()
        second = spec(name="OTHER")
        session = SelectionSession({})
        session.edit(first, "running draft")
        session.refresh_success({first.key: {"source": "default", "value": "Default"},
                                 second.key: {"source": "custom", "value": "saved"}})
        self.assertEqual((session.get(first).source, session.get(first).value), ("custom", "running draft"))
        self.assertEqual((session.get(second).source, session.get(second).value), ("custom", "saved"))

    def test_no_success_snapshot_retains_legacy_initialization(self):
        item = spec()
        self.assertEqual(SelectionSession({}).get(item).source, "default")
        self.assertEqual(SelectionSession({item.key: "B"}).get(item).source, "last")


if __name__ == "__main__":
    unittest.main()
