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
  function build(options) {
    const { target, mode, selectedRaw } = options;
    if (typeof selectedRaw !== 'string') throw new TypeError('selectedRaw must be a string.');
    const whole = !!(options.whole || options.special);
    const snapshot = snapshotFor(options), before = state(snapshot), key = targetKey(target ?? options.snapshot?.targetKey);
    const replace = whole || mode === 'replace';
    const { parts, result: matchResult } = computeMatch(key, before, selectedRaw, mode, whole);
    const operations = [];
    let after = state(snapshot);
    if (replace) {
      after = { exists: true, raw: selectedRaw, type: copy(before.type) };
      if (!before.exists || before.raw !== after.raw) operations.push({ type: 'replace', action: 'REPLACE', whole: true, before: legacyRaw(before), after: selectedRaw });
    } else {
      const actualParts = before.exists ? before.raw.split(';') : [];
      const present = new Set(actualParts.map(value => value.trim()).filter(Boolean));
      const missing = [], position = mode === '+append' ? 'head' : 'tail';
      for (const part of parts) {
        const normalized = part.value.trim();
        if (normalized !== '' && !present.has(normalized)) {
          missing.push(part.value);
          present.add(normalized);
          operations.push({ type: 'insert', action: 'ADD', value: part.value, selectedIndex: part.index, position });
        }
      }
      if (missing.length) after = { exists: true, raw: (position === 'head' ? [...missing, ...actualParts] : [...actualParts, ...missing]).join(';'), type: copy(before.type) };
    }
    return freeze({
      target: copy(target), targetKey: key, mode, special: whole,
      kind: whole ? 'whole' : replace ? 'replace' : 'append', source: 'scheme-apply',
      baseRevision: snapshot.revision, before, desiredRaw: selectedRaw, after,
      beforeRaw: legacyRaw(before), afterRaw: legacyRaw(after), operations, parts, matchResult
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
    const remainsAbsent = !before.exists && afterRaw === '' && !(frozenAudit.elements ?? []).some(element => element.afterIndex !== null && element.afterIndex !== undefined);
    const changed = !remainsAbsent && (!before.exists || before.raw !== afterRaw);
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
    const after = remainsAbsent ? state(original) : { exists: true, raw: afterRaw, type: copy(before.type) };
    const { parts, result: matchResult } = computeMatch(key, before, afterRaw, 'replace', whole);
    return freeze({
      target: copy(target), targetKey: key, mode: 'element-edit', special: whole, kind: whole ? 'whole' : 'element-edit',
      source: 'windows-element-edit', baseRevision: original.revision, before, desiredRaw: afterRaw, after,
      beforeRaw: legacyRaw(before), afterRaw: legacyRaw(after), operations, parts, matchResult, audit: frozenAudit
    });
  }
  return Object.freeze({ build, buildElementEdit, matches, selectionChange });
})();
