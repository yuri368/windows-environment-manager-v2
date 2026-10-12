"""Cleanup preview details and frozen preferences; all stores are isolated."""

from dataclasses import FrozenInstanceError
from itertools import product
from pathlib import Path
import tempfile
import unittest

from envmanager.config import parse_config
from envmanager.elements import CleanupSettings, cleanup_value
from envmanager.models import ConflictError, RegistryState
from envmanager.persistence import StateRepository
from envmanager.removal import RemovalRequest, RemovalScope
from envmanager.service import EnvironmentService
from envmanager.store import MemoryStore
from envmanager.switching import finalize_switch, prepare_switch


def configuration(*, special=False, protected=False):
    declaration = ',special=true' if special else ''
    mode = 'replace' if special else '+append'
    value = ' A;A; B ' if special else 'A'
    text = (f"[App.Old]\nPath={{default_value={value!r},scope='User',mode={mode!r},editable=true{declaration}}}\n"
            "[App.New]\nOTHER={default_value='N',scope='User',mode='replace',editable=true}\n")
    if protected:
        text += f"[Protected.Default]\nPATH={{default_value={value!r},scope='User',mode={mode!r},editable=true{declaration}}}\n"
    return parse_config(text)


def selected(config):
    return {spec.key: spec.default_value for app in config.apps()
            for option in config.options(app) for spec in config.variables(app, option)}


def states(value):
    return {('User', 'PATH'): RegistryState('Path', 'User', True, value, 2),
            ('User', 'OTHER'): RegistryState('OTHER', 'User', False)}


class OldOnlySwitchPreviewTests(unittest.TestCase):
    def test_cleanup_has_original_raw_snapshot_and_separate_details(self):
        config = configuration()
        draft = prepare_switch(config, 'App', 'New', states(' A ;B;; B '), selected(config))
        result = finalize_switch(draft)
        change = next(change for change in result.changes if change.before.name == 'Path')
        self.assertEqual((change.before.value, change.after.value, change.after.registry_type), (' A ;B;; B ', 'B', 2))
        self.assertEqual(change.calculation.original_items, (' A ', 'B', '', ' B '))
        self.assertEqual(change.calculation.items, ('B',))
        self.assertTrue(change.calculation.warnings)
        self.assertIn(change.calculation.warnings[0], result.warnings)
        cleanup = [item for item in change.contributions if item['kind'] == 'cleanup']
        self.assertEqual(cleanup, [{'kind': 'cleanup', 'before_value': 'B;; B ',
                                    'after_value': 'B', 'decision': 'cleanup'}])
        removal = [item for item in change.contributions if item['kind'] == 'list_element']
        self.assertEqual([(item['element_key'], item['decision']) for item in removal], [('A', 'remove')])

    def test_every_cleanup_setting_is_applied_to_old_only_remaining_raw_slices(self):
        config = configuration()
        for flags in product((False, True), repeat=3):
            settings = CleanupSettings(*flags)
            draft = prepare_switch(config, 'App', 'New', states(' A ; B ;;B;\t; C '),
                                   selected(config), cleanup=settings)
            change = next(change for change in finalize_switch(draft).changes if change.before.name == 'Path')
            uncleaned = ' B ;;B;\t; C '
            with self.subTest(settings=settings):
                self.assertEqual(change.after.value, cleanup_value(uncleaned, settings))
                self.assertEqual(bool(change.calculation.warnings), change.after.value != uncleaned)
                self.assertEqual(bool([x for x in change.contributions if x['kind'] == 'cleanup']),
                                 change.after.value != uncleaned)
        self.assertEqual(draft.cleanup, settings)
        with self.assertRaises(FrozenInstanceError):
            draft.cleanup.deduplicate = False

    def test_special_old_only_shared_value_is_kept_raw_without_cleanup(self):
        config = configuration(special=True, protected=True)
        raw = ' A;A; B '
        draft = prepare_switch(config, 'App', 'New', states(raw), selected(config))
        self.assertEqual(len(draft.shared_conflicts), 1)
        change = next(change for change in finalize_switch(draft).changes if change.before.name == 'Path')
        self.assertTrue(change.after.same_content(change.before))
        self.assertIsNone(change.calculation)
        self.assertFalse(any(item['kind'] == 'cleanup' for item in change.contributions))


class FrozenCleanupServiceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.config = configuration()
        self.store = MemoryStore(states('A; B ;B;;').values())
        self.repository = StateRepository(self.directory)
        self.service = EnvironmentService(self.config, self.store, self.repository)

    def test_local_settings_update_invalidates_remove_switch_drafts_and_plan(self):
        for kind in ('remove', 'switch', 'plan'):
            with self.subTest(kind=kind):
                self.service.set_cleanup_settings(CleanupSettings())
                if kind == 'remove':
                    item = self.service.prepare_remove(RemovalRequest(RemovalScope.SCHEME, frozenset({'App'}), {'App': 'Old'}))
                elif kind == 'switch':
                    item = self.service.prepare_scheme_switch('App', 'New')
                else:
                    item = self.service.plan('App', 'New')
                self.service.set_cleanup_settings(CleanupSettings(False, False, False))
                with self.assertRaises(ConflictError):
                    if kind == 'remove':
                        self.service.finalize_remove(item.id)
                    elif kind == 'switch':
                        self.service.finalize_scheme_switch(item.id)
                    else:
                        self.service.execute(item)
                self.assertEqual(self.store.writes, [])

    def test_another_service_preferences_change_rejects_frozen_inputs(self):
        other = EnvironmentService(self.config, self.store, StateRepository(self.directory))
        for kind in ('remove', 'switch', 'plan'):
            with self.subTest(kind=kind):
                other.set_cleanup_settings(CleanupSettings())
                if kind == 'remove':
                    item = self.service.prepare_remove(RemovalRequest(RemovalScope.SCHEME, frozenset({'App'}), {'App': 'Old'}))
                elif kind == 'switch':
                    item = self.service.prepare_scheme_switch('App', 'New')
                else:
                    item = self.service.plan('App', 'New')
                other.set_cleanup_settings(CleanupSettings(False, False, False))
                with self.assertRaisesRegex(ConflictError, '自动整理设置'):
                    if kind == 'remove':
                        self.service.finalize_remove(item.id)
                    elif kind == 'switch':
                        self.service.finalize_scheme_switch(item.id)
                    else:
                        self.service.execute(item)
                self.assertEqual(self.store.writes, [])

    def test_history_restore_keeps_raw_value_type_and_current_cleanup_preferences(self):
        original = self.store.read('User', 'Path')
        applied = self.service.execute(self.service.plan('App', 'Old'))
        self.assertTrue(applied.success)
        self.assertEqual(self.store.read('User', 'Path').value, 'A;B')
        self.service.set_cleanup_settings(CleanupSettings(False, False, False))
        plan = self.service.plan_restore(applied.operation_id)
        # Another window changing preferences must not alter an exact restore.
        other = EnvironmentService(self.config, self.store, StateRepository(self.directory))
        preferences = CleanupSettings(False, True, False)
        other.set_cleanup_settings(preferences)
        restored = self.service.execute(plan)
        self.assertTrue(restored.success)
        self.assertTrue(self.store.read('User', 'Path').same_content(original))
        self.assertEqual(self.service.cleanup_settings, preferences)


if __name__ == '__main__':
    unittest.main()
