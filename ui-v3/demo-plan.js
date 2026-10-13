/* Pure immutable plans for the offline demo; annotations never decide apply semantics. */
var DemoPlan = (() => {
  const copy = value => {
    if (Array.isArray(value)) return value.map(copy);
    if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, copy(item)]));
    return value;
  };
  function freeze(value) {
    if (value && typeof value === 'object') {
      Object.values(value).forEach(freeze);
      Object.freeze(value);
    }
    return value;
  }
  function targetKey(target) {
    if (typeof target === 'string') {
      const separator = target.indexOf('|');
      target = { scope: separator < 0 ? undefined : target.slice(0, separator), name: separator < 0 ? target : target.slice(separator + 1) };
    }
    const name = target?.name ?? '';
    return { scope: target?.scope, name, canonicalName: name.toUpperCase() };
  }
  function snapshotFor(options) {
    const snapshot = options.snapshot;
    const exists = snapshot ? snapshot.exists : options.actualRaw !== undefined && options.actualRaw !== null;
    const raw = exists ? (snapshot ? snapshot.raw : options.actualRaw) : null;
    if (exists && typeof raw !== 'string') throw new TypeError('An existing snapshot requires a string raw value.');
    return { exists: !!exists, raw, type: copy(snapshot?.type ?? snapshot?.registryType ?? null), revision: copy(snapshot?.revision ?? null) };
  }
  const state = snapshot => ({ exists: snapshot.exists, raw: snapshot.raw, type: copy(snapshot.type) });
  const legacyRaw = snapshot => snapshot.exists ? snapshot.raw : undefined;
  const normalizedParts = raw => raw.split(';').map(value => value.trim()).filter(Boolean);
  const actualPartsFor = snapshot => snapshot.exists ? snapshot.raw.split(';') : [];
  function matches(raw, values = [], whole = false) {
    if (raw === undefined || raw === null) return false;
    return whole ? values.some(value => value === raw)
      : raw.trim() !== '' && values.some(value => normalizedParts(value).includes(raw.trim()));
  }
  function computeMatch(key, before, desiredRaw, mode, whole) {
    const present = new Set(before.exists ? normalizedParts(before.raw) : []);
    const parts = (whole ? [desiredRaw] : desiredRaw.split(';')).map((value, index) => ({
      value, index,
      matched: whole ? before.exists && before.raw === value : value.trim() !== '' && present.has(value.trim())
    }));
    const effective = parts.filter(part => whole || part.value.trim() !== '');
    const matched = effective.filter(part => part.matched), missing = effective.filter(part => !part.matched);
    const desired = new Set(effective.map(part => part.value.trim()));
    const complete = missing.length === 0, exact = before.exists && before.raw === desiredRaw;
    const satisfied = before.exists && (whole ? exact : mode === 'replace'
      ? complete && [...present].every(value => desired.has(value)) : complete);
    return { parts, result: { targetKey: key, matchedCount: matched.length, totalCount: effective.length, matched, missing, complete, exact, satisfied } };
  }
  function captureBaseline(options) {
    if (typeof options.selectedRaw !== 'string') throw new TypeError('selectedRaw must be a string.');
    const snapshot = snapshotFor(options), key = targetKey(options.target ?? options.snapshot?.targetKey);
    const whole = !!(options.whole || options.special);
    const actual = actualPartsFor(snapshot), occurrences = new Map();
    if (!whole) actual.forEach((value, index) => {
      const normalized = value.trim();
      if (normalized === '') return;
      const entry = occurrences.get(normalized) || { normalized, actualIndexes: [], actualValues: [], occurrenceCount: 0 };
      entry.actualIndexes.push(index); entry.actualValues.push(value); entry.occurrenceCount++;
      occurrences.set(normalized, entry);
    });
    const { parts: selectedParts } = computeMatch(key, state(snapshot), options.selectedRaw, options.mode, whole);
    const parts = selectedParts.map(part => ({
      ...part, normalized: whole ? part.value : part.value.trim(),
      actualIndexes: whole ? (part.matched ? [0] : []) : [...(occurrences.get(part.value.trim())?.actualIndexes || [])]
    }));
    const matched = whole ? [] : [...new Set(parts.filter(part => part.matched).map(part => part.normalized))]
      .map(normalized => copy(occurrences.get(normalized)));
    return freeze({
      targetKey: key, mode: options.mode, special: whole, selectedRaw: options.selectedRaw,
      snapshot, parts, matched, ...(options.owner === undefined ? {} : { owner: copy(options.owner) })
    });
  }
  function baselineIssues(baseline, key, mode, whole, snapshot) {
    if (!baseline) return [];
    const changed = [];
    if (baseline.targetKey?.scope !== key.scope || baseline.targetKey?.canonicalName !== key.canonicalName) changed.push('target');
    if (baseline.mode !== mode) changed.push('mode');
    if (baseline.special !== whole) changed.push('special');
    for (const field of ['exists', 'raw', 'type', 'revision']) {
      if (JSON.stringify(baseline.snapshot?.[field]) !== JSON.stringify(snapshot[field])) changed.push(field);
    }
    return changed.length ? [{
      reason: 'stale', value: baseline.selectedRaw, actualIndexes: [], fields: changed,
      originalSnapshot: copy(baseline.snapshot), currentSnapshot: copy(snapshot)
    }] : [];
  }
  function removalCandidates(baseline, selectedRaw, protectedValues) {
    const desired = new Set(normalizedParts(selectedRaw));
    const protectedSet = new Set(protectedValues.flatMap(normalizedParts));
    const operations = [], issues = [];
    for (const matched of baseline?.matched || []) {
      if (desired.has(matched.normalized)) continue;
      if (matched.actualIndexes.length !== 1) {
        issues.push({ reason: 'duplicate', value: matched.normalized, actualIndexes: [...matched.actualIndexes] });
      } else if (protectedSet.has(matched.normalized)) {
        issues.push({ reason: 'shared', value: matched.normalized, actualIndexes: [...matched.actualIndexes] });
      } else {
        operations.push({ type: 'remove', action: 'REMOVE', value: matched.actualValues[0], beforeIndex: matched.actualIndexes[0] });
      }
    }
    return { operations, issues };
  }
  function appendParts(actualParts, parts, mode) {
    const present = new Set(actualParts.map(value => value.trim()).filter(Boolean));
    const missing = [], operations = [], position = mode === '+append' ? 'head' : 'tail';
    for (const part of parts) {
      const normalized = part.value.trim();
      if (normalized !== '' && !present.has(normalized)) {
        missing.push(part.value); present.add(normalized);
        operations.push({ type: 'insert', action: 'ADD', value: part.value, selectedIndex: part.index, position,
          ...(part.requestId === undefined ? {} : { requestId: part.requestId, requestSelectedIndex: part.requestSelectedIndex }) });
      }
    }
    return { parts: position === 'head' ? [...missing, ...actualParts] : [...actualParts, ...missing], operations };
  }
  const listState=(before,parts)=>({exists:parts.length>0,raw:parts.length?parts.join(';'):null,type:copy(before.type)});
  function build(options) {
    const { target, mode, selectedRaw } = options;
    if (typeof selectedRaw !== 'string') throw new TypeError('selectedRaw must be a string.');
    const whole = !!(options.whole || options.special);
    const snapshot = snapshotFor(options), before = state(snapshot), key = targetKey(target ?? options.snapshot?.targetKey);
    const replace = whole || mode === 'replace';
    const { parts, result: matchResult } = computeMatch(key, before, selectedRaw, mode, whole);
    const issues = baselineIssues(options.baseline, key, mode, whole, snapshot), valid = issues.length === 0;
    const operations = [];
    let after = state(snapshot);
    if (valid && replace) {
      after = { exists: true, raw: selectedRaw, type: copy(before.type) };
      if (!before.exists || before.raw !== after.raw) operations.push({ type: 'replace', action: 'REPLACE', whole: true, before: legacyRaw(before), after: selectedRaw });
    } else if (valid) {
      const removals = removalCandidates(options.baseline, selectedRaw, options.protectedValues || []);
      issues.push(...removals.issues); operations.push(...removals.operations);
      const removedIndexes = new Set(removals.operations.map(operation => operation.beforeIndex));
      const remaining = actualPartsFor(snapshot).filter((_, index) => !removedIndexes.has(index));
      const additions = appendParts(remaining, parts, mode);
      operations.push(...additions.operations);
      if (operations.length) after = listState(before,additions.parts);
    }
    return freeze({
      target: copy(target), targetKey: key, mode, special: whole,
      kind: whole ? 'whole' : replace ? 'replace' : 'append', source: 'scheme-apply',
      baseRevision: snapshot.revision, before, desiredRaw: selectedRaw, after,
      beforeRaw: legacyRaw(before), afterRaw: legacyRaw(after), operations, parts, matchResult, valid, issues,
      ...(options.baseline ? { baseline: copy(options.baseline) } : {})
    });
  }
  function buildBatch({ target, snapshot, requests, protectedValues = [] }) {
    if (!Array.isArray(requests) || !requests.length) throw new TypeError('A batch requires at least one request.');
    const inputs = copy(requests), original = snapshotFor({ snapshot }), before = state(original), key = targetKey(target ?? snapshot?.targetKey);
    const plans = inputs.map((request, index) => build({
      ...request, target, snapshot: original,
      protectedValues: [...protectedValues, ...inputs.filter((_, other) => other !== index).map(other => other.selectedRaw)]
    }));
    if (plans.length === 1) {
      const plan = copy(plans[0]);
      plan.requests = inputs;
      plan.operations = plan.operations.map(operation => ({ ...operation, requestId: inputs[0].id,
        ...(operation.type === 'insert' ? { requestSelectedIndex: operation.selectedIndex } : {}) }));
      plan.issues = plan.issues.map(issue => ({ ...issue, requestId: inputs[0].id }));
      return freeze(plan);
    }
    const issues = plans.flatMap((plan, index) => plan.issues.map(issue => ({ ...issue, requestId: inputs[index].id })));
    const replacements = plans.filter(plan => plan.kind !== 'append');
    const mixedSpecial=new Set(plans.map(plan=>plan.special)).size>1;
    const conflict = mixedSpecial || (replacements.length && (replacements.length !== plans.length || new Set(inputs.map(request => request.selectedRaw)).size > 1));
    if (conflict) issues.push({ reason: 'conflict', value: inputs.map(request => request.selectedRaw), actualIndexes: [], requestIds: inputs.map(request => request.id) });
    const valid = plans.every(plan => plan.valid) && !conflict;
    const whole = replacements.length === plans.length && plans.some(plan => plan.special);
    const desiredRaw = replacements.length === plans.length ? inputs[0].selectedRaw : inputs.map(request => request.selectedRaw).join(';');
    const kind = replacements.length === plans.length ? (whole ? 'whole' : 'replace') : 'append';
    const combined = computeMatch(key, before, desiredRaw, kind === 'append' ? '-append' : 'replace', whole);
    const parts = kind === 'append' ? plans.flatMap((plan, index) => plan.parts.map(part => ({
      ...part, requestId: inputs[index].id, requestSelectedIndex: part.index
    }))).map((part, index) => ({ ...part, index })) : combined.parts;
    const operations = [];
    let after = state(original);
    if (valid && kind !== 'append') {
      after = { exists: true, raw: desiredRaw, type: copy(before.type) };
      if (!before.exists || before.raw !== desiredRaw) operations.push({ type: 'replace', action: 'REPLACE', whole: true, before: legacyRaw(before), after: desiredRaw, requestId: inputs[0].id, requestIds: inputs.map(request => request.id) });
    } else if (valid) {
      const removedIndexes = new Set();
      plans.forEach((plan, index) => plan.operations.filter(operation => operation.type === 'remove').forEach(operation => {
        if (removedIndexes.has(operation.beforeIndex)) return;
        removedIndexes.add(operation.beforeIndex); operations.push({ ...copy(operation), requestId: inputs[index].id });
      }));
      let remaining = actualPartsFor(original).filter((_, index) => !removedIndexes.has(index));
      inputs.forEach((request, index) => {
        const addition = appendParts(remaining, parts.filter(part => part.requestId === request.id && part.requestSelectedIndex < plans[index].parts.length), request.mode);
        remaining = addition.parts; operations.push(...addition.operations);
      });
      if (operations.length) after = listState(before,remaining);
    }
    return freeze({
      target: copy(target), targetKey: key, mode: 'batch', special: whole, kind, source: 'scheme-apply',
      baseRevision: original.revision, before, desiredRaw, after, beforeRaw: legacyRaw(before), afterRaw: legacyRaw(after),
      operations, parts, matchResult: { ...combined.result, matched: parts.filter(part => part.matched), missing: parts.filter(part => !part.matched && part.value.trim() !== '') },
      valid: !!valid, issues, requests: inputs
    });
  }
  function selectionChange({ target, beforeRaw, afterRaw, whole = false, explicitElementEdits }) {
    if (beforeRaw === afterRaw) return null;
    const result = { targetKey: targetKey(target), beforeRaw, afterRaw, whole: !!whole };
    if (explicitElementEdits) result.explicitElementEdits = copy(explicitElementEdits);
    return freeze(result);
  }
  function buildElementEdit({ target, snapshot, afterRaw, audit }) {
    if (typeof afterRaw !== 'string') throw new TypeError('afterRaw must be the serialized editor string.');
    const original = snapshotFor({ snapshot }), before = state(original), key = targetKey(target ?? snapshot?.targetKey);
    if (!audit || audit.before !== legacyRaw(before) || audit.after !== afterRaw) throw new Error('Element audit does not describe this snapshot and serialized result.');
    const whole = !!audit.whole, frozenAudit = copy(audit), operations = [];
    // Opening an absent value with no live rows is not an instruction to create an empty string.
    const hasLiveRows=(frozenAudit.elements ?? []).some(element=>element.afterIndex!==null&&element.afterIndex!==undefined);
    const remainsAbsent = !before.exists && afterRaw === '' && !hasLiveRows;
    const deleteListValue=!whole&&before.exists&&!hasLiveRows&&(frozenAudit.elements??[]).some(element=>element.removed);
    const changed = !remainsAbsent && (!before.exists || before.raw !== afterRaw || deleteListValue);
    for (const element of frozenAudit.elements ?? []) {
      if (!(element.added || element.removed || element.replaced || element.moved)) continue;
      if (element.elementId === undefined || element.elementId === null) throw new Error('Explicit element changes require a stable elementId.');
      const relation = { elementId: element.elementId, before: element.before, after: element.after, beforeIndex: element.beforeIndex, afterIndex: element.afterIndex };
      if (!changed) continue;
      if (element.added) operations.push({ type: 'add', action: 'ADD', ...relation });
      else if (element.removed) operations.push({ type: 'remove', action: 'REMOVE', ...relation });
      else {
        if (element.replaced) operations.push({ type: 'replace', action: 'REPLACE', ...relation });
        if (element.moved) operations.push({ type: 'move', action: 'MOVE', ...relation });
      }
    }
    if (changed && !operations.length) throw new Error('A changed serialized result requires explicit element operations.');
    const after = remainsAbsent ? state(original) : deleteListValue ? listState(before,[]) : { exists: true, raw: afterRaw, type: copy(before.type) };
    const { parts, result: matchResult } = computeMatch(key, before, afterRaw, 'replace', whole);
    return freeze({
      target: copy(target), targetKey: key, mode: 'element-edit', special: whole, kind: whole ? 'whole' : 'element-edit',
      source: 'windows-element-edit', baseRevision: original.revision, before, desiredRaw: afterRaw, after,
      beforeRaw: legacyRaw(before), afterRaw: legacyRaw(after), operations, parts, matchResult, audit: frozenAudit, valid: true, issues: []
    });
  }
  return Object.freeze({ build, buildBatch, captureBaseline, buildElementEdit, matches, selectionChange });
})();
