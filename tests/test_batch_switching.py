from __future__ import annotations

import unittest

from envmanager.models import ConflictError, RegistryState, ValidationError
from envmanager.sharing import ConflictDecision
from envmanager.switching import AmbiguousOldSchemeError, finalize_switch, prepare_batch_switch
from tests.test_switching import config_for, path, snapshots, spec, values


class BatchSwitchingTests(unittest.TestCase):
    def prepare(self, specs, current, requested, **kwargs):
        return prepare_batch_switch(config_for(*specs), requested, snapshots(current), values(*specs), **kwargs)

    def test_manual_old_schemes_share_one_net_target(self):
        specs = (spec(value="COMMON;OLD"), spec("17", "COMMON;NEW"),
                 spec("Old", "MOLD", app="Maven"), spec("New", "MNEW", app="Maven"))
        draft = self.prepare(specs, path("BASE;COMMON;OLD;MOLD", registry_type=2),
                             {"Java": "17", "Maven": "New"})
        self.assertEqual(dict(draft.old_options_by_app), {"Java": ("8",), "Maven": ("Old",)})
        result = finalize_switch(draft)
        self.assertEqual(len(result.changes), 1)
        self.assertEqual(result.changes[0].after.value, "COMMON;NEW;MNEW;BASE")
        self.assertEqual(result.changes[0].after.registry_type, 2)
        self.assertEqual(result.metrics.removed_element_count, 2)

    def test_selected_peer_new_requirement_preserves_another_apps_old_element(self):
        specs = (spec(value="OLD"), spec("17", "NEW"),
                 spec("Old", "MOLD", app="Maven"), spec("New", "OLD;MNEW", app="Maven"))
        draft = self.prepare(specs, path("BASE;OLD;MOLD"), {"Java": "17", "Maven": "New"})
        self.assertEqual(draft.shared_conflicts, ())
        self.assertEqual(finalize_switch(draft).changes[0].after.value, "NEW;OLD;MNEW;BASE")
        self.assertEqual([atom.display_value for atom in draft.ordinary_atoms], ["MOLD"])

    def test_only_unselected_apps_all_schemes_protect_old_content(self):
        specs = (spec(value="OLD"), spec("17", "NEW"),
                 spec("Old", "OLD", app="Maven"), spec("New", "MNEW", app="Maven"),
                 spec("Default", "NONE", app="Scala"), spec("Other", "OLD", app="Scala"))
        draft = self.prepare(specs, path("BASE;OLD"), {"Java": "17", "Maven": "New"})
        self.assertEqual(len(draft.shared_conflicts), 1)
        conflict = draft.shared_conflicts[0]
        self.assertEqual([(item.app, item.option) for item in conflict.consumers], [("Scala", "Other")])
        self.assertIn("OLD", finalize_switch(draft).changes[0].after.value.split(";"))
        result = finalize_switch(draft, {conflict.atom.atom_id: ConflictDecision.REMOVE})
        self.assertNotIn("OLD", result.changes[0].after.value.split(";"))

    def test_ambiguous_old_reports_app_and_supports_per_app_explicit_selection(self):
        specs = (spec(value="OLD"), spec("11", "OTHER"), spec("17", "NEW"),
                 spec("Old", "MOLD", app="Maven"), spec("New", "MNEW", app="Maven"))
        with self.assertRaises(AmbiguousOldSchemeError) as raised:
            self.prepare(specs, path("OLD;OTHER;MOLD"), {"Java": "17", "Maven": "New"})
        self.assertEqual(raised.exception.app, "Java")
        draft = self.prepare(specs, path("OLD;OTHER;MOLD"), {"Java": "17", "Maven": "New"},
                             old_options={"Java": ("8",)})
        self.assertEqual(draft.old_options_by_app["Maven"], ("Old",))
        self.assertIn("OTHER", draft.changes[0].after.value.split(";"))

    def test_partial_requires_explicit_choice_and_empty_choice_only_applies(self):
        specs = (spec(value="OLD;MISSING"), spec("17", "NEW"))
        inferred = self.prepare(specs, path("BASE;OLD"), {"Java": "17"})
        self.assertEqual(inferred.old_options_by_app["Java"], ())
        self.assertEqual(inferred.candidates_by_app["Java"].partial, ("8",))
        explicit = self.prepare(specs, path("BASE;OLD"), {"Java": "17"}, old_options={"Java": ("8",)})
        self.assertEqual(explicit.changes[0].after.value, "NEW;BASE")
        no_removal = self.prepare(specs, path("BASE;OLD;MISSING"), {"Java": "17"}, old_options={"Java": ()})
        self.assertEqual(no_removal.changes[0].after.value, "NEW;BASE;OLD;MISSING")

    def test_new_replace_cannot_silently_drop_unselected_shared_requirement(self):
        specs = (spec(value="OLD"), spec("17", "NEW", mode="replace"),
                 spec("Old", "MOLD", app="Maven"), spec("New", "NEW", mode="replace", app="Maven"),
                 spec("Shared", "OLD", app="Scala"))
        draft = self.prepare(specs, path("OLD;MOLD"), {"Java": "17", "Maven": "New"})
        with self.assertRaises(ConflictError):
            finalize_switch(draft)
        result = finalize_switch(draft, {item.atom.atom_id: ConflictDecision.REMOVE for item in draft.shared_conflicts})
        self.assertEqual(result.changes[0].after.value, "NEW")

    def test_invalid_requested_and_explicit_scopes_are_rejected(self):
        specs = (spec(), spec("17", "NEW"))
        for requested, old_options in (({}, None), ({"Missing": "17"}, None),
                                       ({"Java": "17"}, {"Other": ()}),
                                       ({"Java": "17"}, {"Java": ("17",)})):
            with self.subTest(requested=requested, old_options=old_options), self.assertRaises(ValidationError):
                self.prepare(specs, path(), requested, old_options=old_options)

    def test_final_order_keeps_ordinary_replace_elements_without_conflict(self):
        old = spec(name="OLD_HOME", value="old", mode="replace")
        new = spec("17", "A", mode="append")
        peer = spec("New", "value", name="PEER_HOME", mode="replace", app="Maven")
        other = spec("Exact", "Other;A", app="Scala", mode="replace")
        specs = (old, new, peer, other)
        states = snapshots(path("Other;A"), RegistryState("OLD_HOME", "User", True, "old", 1),
                           RegistryState("PEER_HOME", "User", False))
        draft = prepare_batch_switch(config_for(*specs), {"Java": "17", "Maven": "New"}, states, values(*specs))
        self.assertEqual(draft.shared_conflicts, ())
        ordered = finalize_switch(draft, orders={new.key: ("A", "Other")})
        self.assertEqual(next(c for c in ordered.changes if c.before.name == "Path").after.value, "A;Other")
        refreshed = prepare_batch_switch(config_for(*specs), {"Java": "17", "Maven": "New"}, states,
            values(*specs), old_options=dict(draft.old_options_by_app), orders={new.key: ("A", "Other")})
        self.assertEqual(refreshed.old_options_by_app, draft.old_options_by_app)
        self.assertEqual(refreshed.shared_conflicts, ())
        confirmed = finalize_switch(refreshed)
        self.assertEqual(next(change for change in confirmed.changes if change.before.name == "Path").after.value,
                         "A;Other")


if __name__ == "__main__":
    unittest.main()
