"""Pure, JSON-persistable history contributions; no environment or DB writes.

Keep ONE Contributions(planner) per service lifetime. Persist the list returned
by advance/sync under settings['historyContributions'] in the service's commit
transaction. Never reuse its epoch after restart. build_undo takes snapshots
keyed by (scope, canonical_name(name)) or 'scope|CANONICAL_NAME' (a snapshot list
is also accepted). protectedfn(scope, name, excluded_record_ids) returns raw
selection strings, exactly like Workspace.protected. It must retain dependencies
outside the excluded records. This module additionally protects shared recordIds.

build_undo is read-only. For an observed unknown refresh, persist sync's returned
list, even if a later refresh returns to the old string. The environment adapter
must observe ABA transitions: no algorithm can detect an entirely unobserved ABA.
Undo targets include contributionIds and withdrawals for the frozen receipt;
Executor need not understand them. advance reads them from receipt, not result.
"""
from __future__ import annotations

from copy import deepcopy
import uuid

from .environment import canonical_name


def _key(item):
    return item['scope'], canonical_name(item['name'])


def _value(snapshot):
    return {key: snapshot[key] for key in ('exists', 'raw', 'type')}


def _same_snapshot(left, right):
    return (_key(left) == _key(right)
            and all(left.get(k) == right.get(k) for k in ('exists', 'raw', 'type', 'revision')))


def _live(item):
    withdrawn = set(item.get('withdrawnRecordIds', []))
    return [r for r in item.get('recordIds', []) if r not in withdrawn] if item.get('active') else []


def _snapshots(source):
    if isinstance(source, dict):
        return {_key(s): s for s in source.values()}
    return {_key(s): s for s in source}


class Contributions:
    def __init__(self, planner):
        self.planner = planner
        self.epoch = uuid.uuid4().hex

    def _matches(self, raw, values, whole=False):
        # Normalization belongs exclusively to DemoPlan (JS trim != Python strip).
        return self.planner.call('matches', args=[raw, values, whole])

    def _sync_one(self, item, snapshot):
        baseline = item.get('baseline')
        if baseline is None:
            return
        state = item.get('removalState')
        if item.get('epoch') != self.epoch:
            # Force a real sync transition even when persisted strings/revisions
            # happen to compare equal. Never recapture a baseline after restart.
            lost = {**snapshot, 'revision': 'lost-epoch:' + self.epoch}
            state = self.planner.call('syncRemovalState', {
                'baseline': baseline, 'state': state, 'snapshot': lost})
        item['removalState'] = self.planner.call('syncRemovalState', {
            'baseline': baseline, 'state': state, 'snapshot': snapshot})

    def sync(self, contributions, snapshotsByTarget):
        """Unknown observations only revoke; persist this list on refresh."""
        updated = deepcopy(contributions)
        snapshots = _snapshots(snapshotsByTarget)
        for item in updated:
            if item.get('active') and _key(item) in snapshots:
                self._sync_one(item, snapshots[_key(item)])
        return updated

    @staticmethod
    def _requests(target, workspace):
        plan = target['plan']
        if plan.get('requests'):
            return deepcopy(plan['requests'])
        selections = workspace.get('selections', {})
        return [{'id': record, 'selectedRaw': selections.get(record, {}).get(
                    'selectedRaw', plan.get('desiredRaw', ''))}
                for record in target.get('members', [])]

    def advance(self, contributions, receipt, result, workspaceData):
        """Fold a committed, verified receipt; return a NEW settings list.

        Existing ADD continuity uses planner.advanceTrustedRemovalState, which
        validates opaque before/after revisions and complete operation mapping.
        No-op apply can link a record to an existing contribution, never mint one.
        Replayed operation IDs do not mint duplicates or resurrect withdrawals.
        """
        if result.get('status') != 'committed':
            return deepcopy(contributions)
        updated = deepcopy(contributions)
        operation = result['operationId']
        if any(item.get('operationId') == operation
               or operation in item.get('advancedOperationIds', [])
               or operation in item.get('withdrawalOperationIds', []) for item in updated):
            return updated
        intent = receipt.get('intent', {})
        actual = {_key(t): t for t in result['targets']}
        for target in receipt['targets']:
            plan, before = target['plan'], target['beforeSnapshot']
            evidence = actual.get(_key(target))
            if evidence is None or not _same_snapshot(evidence['before'], before):
                raise ValueError('Contribution update requires the exact executed before snapshot.')
            snapshot = evidence.get('verifiedAfter')
            if _value(before) == plan['after']:
                snapshot = snapshot or evidence['before']
            if (snapshot is None or _key(snapshot) != _key(target)
                    or _value(snapshot) != plan['after'] or not plan.get('valid')):
                raise ValueError('Contribution update requires a verified frozen after snapshot.')
            for item in updated:
                if (not item.get('active') or _key(item) != _key(target)
                        or item.get('lastAdvancedOperationId') == operation):
                    continue
                if item.get('baseline') is not None:
                    if item.get('epoch') == self.epoch:
                        # An external observation may precede this known write.
                        # Revoke that gap first; the trusted fold cannot bridge it.
                        self._sync_one(item, before)
                        item['removalState'] = self.planner.call('advanceTrustedRemovalState', {
                            'baseline': item['baseline'], 'state': item['removalState'],
                            'plan': plan, 'snapshot': snapshot})
                    else:
                        self._sync_one(item, snapshot)
                item['lastAdvancedOperationId'] = operation
                item.setdefault('advancedOperationIds', []).append(operation)
            if intent.get('kind') == 'undo':
                selected = set(target.get('undoRecordIds', intent.get('recordIds', [])))
                withdrawals = {w['contributionId']: w['recordIds'] for w in target.get('withdrawals', [])}
                for item in updated:
                    if (_key(item) != _key(target)
                            or item['contributionId'] not in target.get('contributionIds', [])):
                        continue
                    records = set(withdrawals.get(item['contributionId'], selected))
                    records.intersection_update(item['recordIds'])
                    item['withdrawnRecordIds'] = sorted(set(item.get('withdrawnRecordIds', [])) | records)
                    item['active'] = bool(set(item['recordIds']) - set(item['withdrawnRecordIds']))
                    item.setdefault('withdrawalOperationIds', [])
                    if operation not in item['withdrawalOperationIds']:
                        item['withdrawalOperationIds'].append(operation)
                continue
            if intent.get('kind') != 'apply':
                continue
            requests = self._requests(target, workspaceData)
            members = target.get('members', [])
            for index, op in enumerate(plan['operations']):
                contribution_id = str(uuid.uuid5(uuid.NAMESPACE_URL,
                    'v3-contribution:' + operation + ':' + repr(_key(target)) + ':' + str(index)))
                if any(c['contributionId'] == contribution_id for c in updated):
                    continue
                is_add = op.get('action') == 'ADD' and op.get('type') in ('insert', 'add')
                is_whole = op.get('type') == 'replace' and (op.get('whole') or plan.get('kind') in ('whole', 'replace'))
                raw = op.get('value', op.get('after')) if is_add else plan['after']['raw']
                if is_add:
                    owners = [r['id'] for r in requests if self._matches(raw, [r['selectedRaw']])]
                    if op.get('requestId') in members:
                        owners.append(op['requestId'])
                else:
                    owners = list(op.get('requestIds', [])) or ([op['requestId']] if op.get('requestId') else list(members))
                owners = sorted(set(owners).intersection(members))
                if not owners:
                    continue  # Direct Windows edits have no configuration owner.
                item = {'contributionId': contribution_id, 'scope': target['scope'], 'name': target['name'],
                        'recordIds': owners, 'withdrawnRecordIds': [], 'selectedRaw': raw,
                        'baseline': None, 'removalState': None, 'active': True,
                        'operationId': operation, 'requestId': op.get('requestId'),
                        'epoch': self.epoch, 'epochSnapshot': deepcopy(snapshot),
                        'before': deepcopy(before), 'verifiedAfter': deepcopy(snapshot),
                        'operations': [deepcopy(op)], 'lastAdvancedOperationId': operation,
                        'advancedOperationIds': [operation], 'special': bool(plan.get('special')),
                        'kind': 'add' if is_add else 'whole-replace' if is_whole else 'unresolved'}
                if is_add:
                    # One contribution per actual ADD. Capture ACTUAL raw slices,
                    # never the entire desired selection (which may contain no-ops).
                    baseline = self.planner.call('captureBaseline', {
                        'target': {'scope': target['scope'], 'name': target['name']},
                        'snapshot': snapshot, 'mode': '-append', 'whole': False,
                        'selectedRaw': raw})
                    item['baseline'] = baseline
                    item['removalState'] = self.planner.call('createRemovalState', args=[baseline])
                updated.append(item)
            # The only matching-based association is a dependency ON a recorded
            # contribution. It never grants ownership of preexisting actual text.
            for item in updated:
                if (not item.get('active') or _key(item) != _key(target)
                        or item.get('kind') not in ('add', 'whole-replace')):
                    continue
                whole = item['kind'] == 'whole-replace'
                present = snapshot['exists'] and (snapshot['raw'] == item['selectedRaw'] if whole else
                    self._matches(item['selectedRaw'], [snapshot['raw']]))
                if not present:
                    continue
                for request in requests:
                    if (request['id'] not in item['recordIds']
                            and self._matches(item['selectedRaw'], [request['selectedRaw']], whole)):
                        item['recordIds'].append(request['id'])
                item['recordIds'].sort()
        return updated

    @staticmethod
    def _issue(item, reason, **details):
        return {'reason': reason, 'contributionId': item['contributionId'],
                'recordIds': _live(item), 'value': item.get('selectedRaw'),
                'actualIndexes': [], **details}

    def build_undo(self, contributions, record_ids, snapshotsByTarget, protectedfn):
        """One frozen target/write per canonical target; unresolved plans are invalid.

        Shared dependencies are retained and reported with reason='shared'; a
        reviewed no-op can withdraw just the selected dependency. duplicate,
        continuity, and inverse_unproven block the complete target. Whole replace
        can only restore its exact before with a complete verifiedAfter match,
        this epoch, no other contribution, and no outside protection. Such plans
        explicitly carry requiresExplicitReview and the original snapshots.
        """
        selected = set(record_ids)
        snapshots = _snapshots(snapshotsByTarget)
        groups = {}
        for item in contributions:
            if selected.intersection(_live(item)):
                groups.setdefault(_key(item), []).append(item)
        targets = []
        for key, originals in groups.items():
            snapshot = snapshots[key]
            scope, name = snapshot['scope'], snapshot['name']
            items = deepcopy(originals)
            protected = list(protectedfn(scope, name, list(record_ids)))
            for item in contributions:
                if (_key(item) == key and set(_live(item)) - selected
                        and item.get('selectedRaw') is not None):
                    protected.append(item['selectedRaw'])
            issues, requests = [], []
            for item in items:
                if item.get('kind') == 'add' or item.get('baseline') is not None:
                    self._sync_one(item, snapshot)
                    requests.append({'id': item['contributionId'], 'mode': '-append', 'whole': False,
                                     'selectedRaw': '', 'baseline': item['baseline'],
                                     'removalState': item['removalState']})
                    # DemoPlan omits missing candidates. Lost provenance must
                    # remain visible even when current text happens to be absent.
                    for entry in item['removalState']['entries']:
                        if not entry['eligible']:
                            issues.append(self._issue(item, entry.get('reason') or 'continuity',
                                                      value=entry['normalized']))
                elif item.get('kind') != 'whole-replace':
                    issues.append(self._issue(item, 'inverse_unproven', operations=item.get('operations', [])))
            wholes = [i for i in items if i.get('kind') == 'whole-replace']
            if wholes:
                item = wholes[0]
                outside = [i for i in contributions if _key(i) == key and _live(i)
                           and i['contributionId'] != item['contributionId']]
                shared = bool(protected or set(_live(item)) - selected)
                if shared:
                    issues.append(self._issue(item, 'shared'))
                if (item.get('epoch') != self.epoch
                        or not _same_snapshot(snapshot, item['verifiedAfter'])):
                    issues.append(self._issue(item, 'continuity'))
                if len(items) != 1 or outside:
                    issues.append(self._issue(item, 'inverse_unproven'))
                plan = self._empty_plan(snapshot)
                verification = {'method': 'restore', 'origin': deepcopy(item),
                                'desiredSnapshot': deepcopy(item['before']), 'requiresExplicitReview': True}
                if not issues:
                    plan['after'] = _value(item['before'])
                    plan['afterRaw'] = plan['after']['raw']
                    if plan['after'] != plan['before']:
                        plan['operations'] = [{'type': 'restore', 'action': 'RESTORE_CONTRIBUTION',
                                               'contributionId': item['contributionId']}]
                    plan.update(mode='contribution-whole-restore', requiresExplicitReview=True,
                                special=bool(item.get('special')),
                                restoreBefore=deepcopy(item['before']), verifiedAfter=deepcopy(item['verifiedAfter']))
            elif requests:
                options = {
                    'target': {'scope': scope, 'name': name}, 'snapshot': snapshot,
                    'requests': requests, 'protectedValues': protected}
                plan = self.planner.call('buildBatch', options)
                verification = {'method': 'buildBatch', 'options': options,
                                'registryType': snapshot['type'] or 'REG_EXPAND_SZ'}
            else:
                plan = self._empty_plan(snapshot)
                verification = {'method': 'restore', 'origin': None,
                                'desiredSnapshot': deepcopy(snapshot), 'requiresExplicitReview': True}
            for issue in plan.get('issues', []):
                item = next((i for i in items if i['contributionId'] == issue.get('requestId')), None)
                if item is not None:
                    issue['contributionId'] = item['contributionId']
            plan['issues'].extend(issues)
            unresolved = [i for i in plan['issues'] if i['reason'] != 'shared']
            if unresolved:
                plan['valid'] = False
            plan['unresolvedIssues'] = deepcopy(unresolved)
            # listState may retain a type when deleting the final occurrence.
            # Executor requires the complete absent value state with null type.
            if not plan['after']['exists']:
                plan['after'] = {'exists': False, 'raw': None, 'type': None}
            targets.append({'scope': scope, 'name': name, 'beforeSnapshot': deepcopy(snapshot),
                            'plan': plan, 'members': [], 'verification': verification, 'undoRecordIds': sorted(selected),
                            'contributionIds': [i['contributionId'] for i in items],
                            'withdrawals': [{'contributionId': i['contributionId'],
                                             'recordIds': sorted(selected.intersection(_live(i)))} for i in items]})
        return targets

    @staticmethod
    def _empty_plan(snapshot):
        return {'targetKey': {'scope': snapshot['scope'], 'name': snapshot['name'],
                              'canonicalName': canonical_name(snapshot['name'])},
                'before': _value(snapshot), 'after': _value(snapshot),
                'baseRevision': snapshot['revision'], 'beforeRaw': snapshot['raw'],
                'afterRaw': snapshot['raw'], 'mode': 'contribution-undo',
                'special': False, 'valid': True, 'operations': [], 'issues': []}
