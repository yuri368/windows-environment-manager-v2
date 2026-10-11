from __future__ import annotations

from dataclasses import replace
import unittest

from envmanager.models import Choice, VariableSpec
from envmanager.selection import SelectionSession


def spec(name="Path", default="Default", choices=()):
    return VariableSpec("App", "Default", name, "User", "+append", True,
                        default, choices)


class SelectionRevisionTests(unittest.TestCase):
    def test_revision_tracks_only_actual_user_changes(self):
        item = spec(choices=(Choice("First", "A"), Choice("Second", "B")))
        session = SelectionSession({})
        self.assertEqual(session.revision, 0)

        session.choose(item, "default")
        self.assertEqual(session.revision, 0)
        session.choose(item, "choice:0")
        self.assertEqual(session.revision, 1)
        session.choose(item, "choice:0")
        self.assertEqual(session.revision, 1)
        session.choose(item, "choice:1")
        self.assertEqual(session.revision, 2)

        session.edit(item, "B")
        self.assertEqual(session.revision, 2)
        session.edit(item, "draft")
        self.assertEqual(session.revision, 3)
        session.edit(item, "draft")
        self.assertEqual(session.revision, 3)
        session.choose(item, "custom")
        self.assertEqual(session.revision, 3)
        session.choose(item, "custom")
        self.assertEqual(session.revision, 3)

    def test_revision_is_read_only_and_refresh_does_not_reset_it(self):
        item = spec()
        session = SelectionSession({})
        session.edit(item, "draft")
        self.assertEqual(session.revision, 1)
        with self.assertRaises(AttributeError):
            session.revision = 10

        session.refresh_last({item.key: "last"})
        session.refresh_success({item.key: {"source": "last", "value": "saved"}})
        session.reconcile((replace(item, default_value="new"),))
        self.assertEqual(session.revision, 1)

    def test_snapshot_many_copies_source_and_value_without_touching_drafts(self):
        first = spec("First", choices=(Choice("A", "A"),))
        second = spec("Second")
        unseen = spec("Unseen")
        session = SelectionSession({})
        session.edit(first, "invalid;;draft")
        second_selection = session.get(second)
        first_selection = session.get(first)
        before = (session.revision, first_selection.source, first_selection.value,
                  first_selection.custom_draft, second_selection.source,
                  second_selection.value, second_selection.custom_draft)

        snapshots = session.snapshot_many((first, second, unseen))

        self.assertEqual(snapshots, {
            first.key: {"source": "custom", "value": "invalid;;draft"},
            second.key: {"source": "default", "value": "Default"},
            unseen.key: {"source": "default", "value": "Default"},
        })
        self.assertNotIn(unseen.key, session._selections)
        self.assertNotIn(unseen.key, session._specs)
        snapshots[first.key]["value"] = "mutated copy"
        after = (session.revision, first_selection.source, first_selection.value,
                 first_selection.custom_draft, second_selection.source,
                 second_selection.value, second_selection.custom_draft)
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
