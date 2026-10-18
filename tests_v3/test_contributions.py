"""Actual DemoPlan + temporary FileEnvironment/SQLite/Executor, never Registry."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import uuid

from envmanager_v3.contributions import Contributions
from envmanager_v3.database import Database, encode
from envmanager_v3.environment import FileEnvironment, canonical_name
from envmanager_v3.execution import Executor
from envmanager_v3.planning import Planner


class ContributionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.planner = Planner()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env = FileEnvironment(self.root / 'environment.json')
        self.db = Database(self.root / 'state.sqlite3')
        self.addCleanup(self.db.close)
        self.manager = Contributions(self.planner)
        self.workspace = {'selections': {}}
        self.executor = Executor(self.env, self.db, on_committed=self.committed)

    @property
    def contributions(self):
        return self.db.get('historyContributions', [])

    def committed(self, receipt, result):
        return {'historyContributions': self.manager.advance(
            self.contributions, receipt, result, self.workspace)}

    def snapshots(self):
        return [self.env.read(item['scope'], item['name']) for item in self.contributions]

    def execute(self, targets, intent):
        receipt = {'planId': uuid.uuid4().hex, 'version': 1, 'targets': targets,
                   'intent': intent, 'dependencies': {}}
        receipt['digest'] = hashlib.sha256(encode(receipt).encode()).hexdigest()
        self.db.preview(receipt)
        identifiers = {k: receipt[k] for k in ('planId', 'version', 'digest')}
        review = self.executor.review(identifiers)
        self.assertIn('reviewToken', review, review)
        result = self.executor.execute({**identifiers, 'reviewToken': review['reviewToken'],
                                        'idempotencyKey': uuid.uuid4().hex})
        self.assertEqual(result['status'], 'committed', result)
        return receipt, result

    def apply(self, requests, *, scope='User', name='Path'):
        before = self.env.read(scope, name)
        requests = [{'id': r, 'selectedRaw': raw, 'mode': '-append', **extra}
                    for r, raw, extra in requests]
        for request in requests:
            self.workspace['selections'][request['id']] = {'selectedRaw': request['selectedRaw']}
        plan = self.planner.call('buildBatch', {'target': {'scope': scope, 'name': name},
                                               'snapshot': before, 'requests': requests})
        plan['after']['type'] = (before['type'] or 'REG_EXPAND_SZ') if plan['after']['exists'] else None
        target = {'scope': scope, 'name': name, 'beforeSnapshot': before, 'plan': plan,
                  'members': [r['id'] for r in requests]}
        return self.execute([target], {'kind': 'apply', 'recordIds': target['members']})

    def undo(self, records, protected=()):
        return self.manager.build_undo(self.contributions, records, self.snapshots(),
                                       lambda scope, name, excluded: list(protected))

    def finish_undo(self, records, targets=None):
        return self.execute(self.undo(records) if targets is None else targets,
                            {'kind': 'undo', 'recordIds': records})

    def assertBlocked(self, targets, reason):
        self.assertEqual(len(targets), 1)
        self.assertFalse(targets[0]['plan']['valid'], targets)
        self.assertIn(reason, [i['reason'] for i in targets[0]['plan']['issues']])

    def test_same_batch_A_B_undo_R1_keeps_B_and_one_write(self):
        self.env.write('User', 'Path', 'BASE;;', 'REG_EXPAND_SZ')
        self.apply([('R1', 'A', {}), ('R2', 'B', {})])
        self.assertEqual(len(self.contributions), 2)
        targets = self.undo(['R1'])
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]['plan']['after']['raw'], 'BASE;;;B')
        self.assertEqual([o['value'] for o in targets[0]['plan']['operations']], ['A'])
        self.finish_undo(['R1'], targets)
        self.assertEqual(self.env.read('User', 'Path')['raw'], 'BASE;;;B')
        self.assertTrue(next(c for c in self.contributions if c['selectedRaw'] == 'B')['active'])
        # B's occurrence moved one index; trusted advancement must preserve it.
        self.finish_undo(['R2'])
        self.assertEqual(self.env.read('User', 'Path')['raw'], 'BASE;;')

    def test_noop_R3_links_to_A_without_creating_an_ADD(self):
        self.apply([('R1', 'A', {}), ('R2', 'B', {})])
        self.apply([('R3', 'A', {})])
        self.assertEqual(len(self.contributions), 2)
        a = next(c for c in self.contributions if c['selectedRaw'] == 'A')
        self.assertEqual(a['recordIds'], ['R1', 'R3'])
        targets = self.undo(['R1'])
        self.assertEqual(targets[0]['plan']['operations'], [])
        self.assertIn('shared', [i['reason'] for i in targets[0]['plan']['issues']])
        self.finish_undo(['R1'], targets)
        a = next(c for c in self.contributions if c['selectedRaw'] == 'A')
        self.assertTrue(a['active'])
        self.assertEqual(a['withdrawnRecordIds'], ['R1'])
        self.assertEqual(self.undo(['R1']), [])
        self.finish_undo(['R3'])
        self.assertEqual(self.env.read('User', 'Path')['raw'], 'B')

    def test_same_record_continuous_adds_A_then_B_collects_all_history(self):
        self.apply([('R', 'A', {})])
        self.apply([('R', 'B', {})])
        targets = self.undo(['R'])
        self.assertEqual(len(targets), 1)
        self.assertTrue(targets[0]['plan']['valid'], targets)
        self.assertEqual(len(targets[0]['plan']['operations']), 2)
        self.assertEqual(targets[0]['plan']['after'], {'exists': False, 'raw': None, 'type': None})
        self.finish_undo(['R'], targets)
        self.assertFalse(self.env.read('User', 'Path')['exists'])
        self.assertEqual(self.undo(['R']), [])
        self.assertEqual(len(self.contributions), 2)  # Immutable historical evidence retained.
        self.assertTrue(all(not c['active'] for c in self.contributions))

    def test_observed_ABA_never_regains_removal_rights(self):
        self.apply([('R', 'A', {})])
        self.env.write('User', 'Path', 'X')
        self.db.set('historyContributions', self.manager.sync(self.contributions, self.snapshots()))
        self.env.write('User', 'Path', 'A')
        self.assertBlocked(self.undo(['R']), 'continuity')
        self.assertEqual(self.env.read('User', 'Path')['raw'], 'A')

    def test_unknown_refresh_absent_remains_explicitly_unresolved(self):
        self.apply([('R', 'A', {})])
        self.env.delete('User', 'Path')
        self.assertBlocked(self.undo(['R']), 'continuity')

    def test_restart_even_equal_complete_snapshot_cannot_regrant_rights(self):
        self.apply([('R', 'A', {})])
        stored = json.loads(json.dumps(self.contributions))
        old_snapshot = self.snapshots()
        self.manager = Contributions(self.planner)
        targets = self.manager.build_undo(stored, ['R'], old_snapshot, lambda *_: [])
        self.assertBlocked(targets, 'continuity')
        self.assertEqual(stored, self.contributions)  # Pure builder, no hidden state write.

    def test_same_name_different_scopes_are_independent(self):
        self.apply([('RU', 'A', {})])
        user_before = deepcopy(self.contributions)
        self.apply([('RS', 'B', {})], scope='System', name='PATH')
        self.assertEqual(self.contributions[0], user_before[0])
        targets = self.undo(['RU'])
        self.assertEqual([t['scope'] for t in targets], ['User'])
        self.finish_undo(['RU'], targets)
        self.assertEqual(self.env.read('System', 'Path')['raw'], 'B')

    def test_preexisting_noops_never_create_ADD_contributions(self):
        self.env.write('User', 'Path', 'A;A;B')
        self.apply([('R1', 'A', {}), ('R2', 'B', {})])
        self.assertEqual(self.contributions, [])
        self.assertEqual(self.undo(['R1', 'R2']), [])

    def test_only_actual_addition_is_captured_from_mixed_selection(self):
        self.env.write('User', 'Path', 'A;; A;X')
        self.apply([('R', 'A; B ', {})])
        self.assertEqual([c['selectedRaw'] for c in self.contributions], [' B '])
        self.finish_undo(['R'])
        self.assertEqual(self.env.read('User', 'Path')['raw'], 'A;; A;X')

    def test_current_duplicates_block_then_do_not_revive_when_unique(self):
        self.apply([('R', 'A', {})])
        self.env.write('User', 'Path', 'A; A')
        self.assertBlocked(self.undo(['R']), 'duplicate')
        self.db.set('historyContributions', self.manager.sync(self.contributions, self.snapshots()))
        self.env.write('User', 'Path', 'A')
        self.assertBlocked(self.undo(['R']), 'continuity')

    def test_actual_duplicate_ADD_has_no_unique_deletion_right(self):
        before = self.env.write('User', 'Path', 'A')
        audit = {'before': 'A', 'after': 'A;A', 'whole': False, 'elements': [
            {'elementId': 'original', 'before': 'A', 'after': 'A', 'beforeIndex': 0,
             'afterIndex': 0, 'added': False, 'removed': False, 'replaced': False},
            {'elementId': 'new', 'after': 'A', 'beforeIndex': None, 'afterIndex': 1,
             'added': True, 'removed': False, 'replaced': False}]}
        plan = self.planner.call('buildElementEdit', {'target': {'scope': 'User', 'name': 'Path'},
            'snapshot': before, 'afterRaw': 'A;A', 'audit': audit})
        target = {'scope': 'User', 'name': 'Path', 'beforeSnapshot': before,
                  'plan': plan, 'members': ['R']}
        self.workspace['selections']['R'] = {'selectedRaw': 'A'}
        self.execute([target], {'kind': 'apply', 'recordIds': ['R']})
        self.assertEqual(len(self.contributions), 1)
        self.assertBlocked(self.undo(['R']), 'duplicate')

    def test_same_batch_shared_A_has_one_ADD_and_two_dependencies(self):
        self.apply([('R1', 'A', {}), ('R2', 'A', {})])
        self.assertEqual(len(self.contributions), 1)
        self.assertEqual(self.contributions[0]['recordIds'], ['R1', 'R2'])
        targets = self.undo(['R1', 'R2'])
        self.assertEqual(len(targets[0]['plan']['operations']), 1)
        self.finish_undo(['R1', 'R2'], targets)
        self.assertFalse(self.env.read('User', 'Path')['exists'])

    def test_external_protection_keeps_value_and_withdraws_once(self):
        self.apply([('R', 'A', {})])
        targets = self.undo(['R'], protected=['A'])
        self.assertTrue(targets[0]['plan']['valid'])
        self.assertEqual(targets[0]['plan']['operations'], [])
        self.finish_undo(['R'], targets)
        self.assertEqual(self.env.read('User', 'Path')['raw'], 'A')
        self.assertEqual(self.undo(['R']), [])

    def test_whole_replace_full_snapshot_restore_requires_review(self):
        self.env.write('User', 'TOKEN', 'original', 'REG_SZ')
        self.apply([('R', 'new;literal', {'whole': True, 'mode': 'replace'})], name='TOKEN')
        targets = self.undo(['R'])
        self.assertTrue(targets[0]['plan']['requiresExplicitReview'])
        self.assertEqual(targets[0]['plan']['after']['raw'], 'original')
        self.finish_undo(['R'], targets)
        self.assertEqual(self.env.read('User', 'TOKEN')['raw'], 'original')

    def test_whole_replace_ABA_or_restart_block_full_restore(self):
        self.apply([('R', 'new', {'whole': True, 'mode': 'replace'})], name='TOKEN')
        self.env.write('User', 'TOKEN', 'other')
        self.env.write('User', 'TOKEN', 'new')
        self.assertBlocked(self.undo(['R']), 'continuity')
        self.manager = Contributions(self.planner)
        self.assertBlocked(self.undo(['R']), 'continuity')

    def test_unproven_REMOVE_cannot_silently_skip_inverse(self):
        before = self.env.write('User', 'Path', 'A;B')
        baseline = self.planner.call('captureBaseline', {'target': {'scope': 'User', 'name': 'Path'},
            'snapshot': before, 'mode': '-append', 'selectedRaw': 'A'})
        state = self.planner.call('createRemovalState', args=[baseline])
        self.apply([('R', 'B', {'baseline': baseline, 'removalState': state})])
        self.assertEqual(self.env.read('User', 'Path')['raw'], 'B')
        self.assertBlocked(self.undo(['R']), 'inverse_unproven')

    def test_replayed_commit_is_idempotent_and_does_not_resurrect_withdrawals(self):
        receipt, result = self.apply([('R', 'A', {})])
        original = self.contributions
        self.assertEqual(self.manager.advance(original, receipt, result, self.workspace), original)
        self.finish_undo(['R'])
        withdrawn = self.contributions
        self.assertEqual(self.manager.advance(withdrawn, receipt, result, self.workspace), withdrawn)

    def test_older_commit_replay_does_not_rewind_later_contributions(self):
        receipt, result = self.apply([('R', 'A', {})])
        noop_receipt, noop_result = self.apply([('R2', 'A', {})])
        self.apply([('R', 'B', {})])
        current = self.contributions
        self.assertEqual(self.manager.advance(current, receipt, result, self.workspace), current)
        self.assertEqual(self.manager.advance(current, noop_receipt, noop_result, self.workspace), current)

    def test_whole_replacement_shared_protection_can_withdraw_without_overwrite(self):
        self.env.write('User', 'TOKEN', 'before')
        self.apply([('R', 'after', {'whole': True, 'mode': 'replace'})], name='TOKEN')
        targets = self.undo(['R'], protected=['after'])
        self.assertTrue(targets[0]['plan']['valid'])
        self.assertEqual(targets[0]['plan']['operations'], [])
        self.assertIn('shared', [i['reason'] for i in targets[0]['plan']['issues']])
        self.finish_undo(['R'], targets)
        self.assertEqual(self.env.read('User', 'TOKEN')['raw'], 'after')

    def test_failed_execution_has_no_contribution_or_mutation(self):
        data = [{'example': 'preserved'}]
        self.assertEqual(self.manager.advance(data, {}, {'status': 'compensated'}, {}), data)

    def test_JS_whitespace_matching_preserves_unrelated_python_whitespace(self):
        # U+001C is Python strip whitespace, but NOT ECMAScript trim whitespace.
        self.env.write('User', 'Path', '\x1cA\x1c')
        self.apply([('R', 'A', {})])
        self.finish_undo(['R'])
        self.assertEqual(self.env.read('User', 'Path')['raw'], '\x1cA\x1c')

    def test_unknown_gap_then_verified_write_does_not_bridge_continuity(self):
        self.apply([('R1', 'A', {})])
        self.env.write('User', 'Path', 'X;A')
        self.apply([('R2', 'B', {})])
        self.assertBlocked(self.undo(['R1']), 'continuity')
        self.finish_undo(['R2'])
        self.assertEqual(self.env.read('User', 'Path')['raw'], 'X;A')

    def test_canonical_target_mapping_and_protected_callback_exclusions(self):
        self.apply([('R', 'A', {})])
        observed = []
        def protected(scope, name, excluded):
            observed.append((scope, canonical_name(name), excluded))
            return []
        snapshot = self.env.read('User', 'PATH')
        targets = self.manager.build_undo(self.contributions, ['R'],
            {('User', 'PATH'): snapshot}, protected)
        self.assertEqual(observed, [('User', 'PATH', ['R'])])
        self.assertTrue(targets[0]['plan']['valid'])


if __name__ == '__main__':
    unittest.main()
