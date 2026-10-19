/* Dependency-free tests of pure demo plans. Does not write reports or page state. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const context = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'demo-plan.js'), 'utf8'), context);
const build = options => context.DemoPlan.build(options);
const plain = value => JSON.parse(JSON.stringify(value));
const options = (actualRaw, selectedRaw, mode = '-append', whole = false) => ({
  target: 'User|PATH', mode, whole, actualRaw, selectedRaw
});
let checks = 0;
const check = (name, verify) => {
  verify();
  checks++;
  process.stdout.write(`PASS ${name}\n`);
};

check('Tail append preserves actual whitespace, empty slices, duplicates and order', () => {
  const plan = build(options(' A ;;A; ;Z;', 'A; B ;B;C'));
  assert.equal(plan.afterRaw, ' A ;;A; ;Z;; B ;C');
  assert.deepEqual(plain(plan.operations), [
    { type: 'insert', action: 'ADD', value: ' B ', selectedIndex: 1, position: 'tail' },
    { type: 'insert', action: 'ADD', value: 'C', selectedIndex: 3, position: 'tail' }
  ]);
});
check('Head append preserves missing selection order and existing matched positions', () => {
  const plan = build(options('Z; A ;;Z', ' B ;A;C;B', '+append'));
  assert.equal(plan.afterRaw, ' B ;C;Z; A ;;Z');
  assert.deepEqual(plain(plan.operations).map(op => [op.value, op.position]), [[' B ', 'head'], ['C', 'head']]);
});
check('Custom-order mode retains existing demo tail plan', () => {
  const plan = build(options('A;Z', 'C;A;B', 'append'));
  assert.equal(plan.kind, 'append');
  assert.equal(plan.afterRaw, 'A;Z;C;B');
  assert.ok(plan.operations.every(op => op.position === 'tail'));
});
check('Comparisons trim whitespace and remain case sensitive', () => {
  const plan = build(options(' A ;a', 'A; a ;b;B'));
  assert.equal(plan.afterRaw, ' A ;a;b;B');
  assert.deepEqual(plain(plan.parts).map(part => part.matched), [true, true, false, false]);
});
check('Selected duplicate occurrences remain visible but produce one real insertion', () => {
  const plan = build(options('X', ' B ;B;B'));
  assert.equal(plan.afterRaw, 'X; B ');
  assert.deepEqual(plain(plan.parts), [
    { value: ' B ', index: 0, matched: false },
    { value: 'B', index: 1, matched: false },
    { value: 'B', index: 2, matched: false }
  ]);
  assert.equal(plan.operations.length, 1);
  assert.equal(plan.operations[0].selectedIndex, 0);
});
check('Existing one copy satisfies all selected duplicate occurrences without count top-up', () => {
  const plan = build(options(' A ', 'A; A ;A'));
  assert.equal(plan.afterRaw, ' A ');
  assert.equal(plan.operations.length, 0);
  assert.ok(plan.parts.every(part => part.matched));
});
check('Empty and whitespace-only selected slices are preserved in parts but never inserted', () => {
  const plan = build(options('A;;', '; ;A;;'));
  assert.equal(plan.afterRaw, 'A;;');
  assert.equal(plan.parts.length, 5);
  assert.equal(plan.parts[1].value, ' ');
  assert.deepEqual(plain(plan.parts).map(part => part.matched), [false, false, true, false, false]);
  assert.equal(plan.operations.length, 0);
});
check('Absent actual and present empty actual stay distinct while appending', () => {
  const absent = build(options(undefined, 'B'));
  const empty = build(options('', 'B'));
  assert.ok(Object.hasOwn(absent, 'beforeRaw'));
  assert.equal(absent.beforeRaw, undefined);
  assert.equal(empty.beforeRaw, '');
  assert.equal(absent.afterRaw, 'B');
  assert.equal(empty.afterRaw, ';B');
  assert.equal(build(options('', 'B', '+append')).afterRaw, 'B;');
});
check('Absent actual with no effective selected value stays absent with no insertion', () => {
  const plan = build(options(undefined, '; ;'));
  assert.equal(plan.afterRaw, undefined);
  assert.deepEqual(plain(plan.after), { exists: false, raw: null, type: null });
  assert.equal(plan.operations.length, 0);
  assert.equal(build(options('', '; ;')).afterRaw, '');
});
check('Replace preserves selected original string and emits one whole replacement', () => {
  const plan = build(options('old;;', ' B ;; A ;', 'replace'));
  assert.equal(plan.kind, 'replace');
  assert.equal(plan.afterRaw, ' B ;; A ;');
  assert.deepEqual(plain(plan.operations), [{ type: 'replace', action: 'REPLACE', whole: true, before: 'old;;', after: ' B ;; A ;' }]);
});
check('Whole values never split internal semicolons and match exact raw text', () => {
  const plan = build(options('A;B', ' A;B ', '+append', true));
  assert.equal(plan.kind, 'whole');
  assert.equal(plan.afterRaw, ' A;B ');
  assert.deepEqual(plain(plan.parts), [{ value: ' A;B ', index: 0, matched: false }]);
  assert.equal(plan.operations[0].type, 'replace');
  assert.equal(build(options('A;B', 'A;B', 'replace', true)).parts[0].matched, true);
});
check('Exact no-op replace and whole plans emit no operations', () => {
  for (const whole of [false, true]) {
    const plan = build(options(' A;;B ', ' A;;B ', 'replace', whole));
    assert.equal(plan.afterRaw, plan.beforeRaw);
    assert.equal(plan.operations.length, 0);
  }
});
check('Absent versus empty replace retains exact before value', () => {
  const absent = build(options(undefined, '', 'replace'));
  const empty = build(options('', '', 'replace'));
  assert.equal(absent.operations.length, 1);
  assert.ok(Object.hasOwn(absent.operations[0], 'before'));
  assert.equal(absent.operations[0].before, undefined);
  assert.equal(empty.operations.length, 0);
});
check('Target passes through and build leaves inputs untouched', () => {
  const input = Object.freeze(options('X', 'Y'));
  const plan = build(input);
  assert.equal(plan.target, 'User|PATH');
  assert.equal(input.actualRaw, 'X');
  assert.equal(input.selectedRaw, 'Y');
  plan.parts[0].value = 'mutated plan';
  assert.equal(build(input).parts[0].value, 'Y');
});
check('Unknown mode preserves legacy tail behavior without inventing a new restriction', () => {
  assert.equal(build(options('A', 'B', 'future-mode')).afterRaw, 'A;B');
});

const snapshot = (raw, revision = 4, type = 'REG_EXPAND_SZ') => ({ exists: raw !== undefined, raw: raw ?? null, registryType: type, revision });
check('Snapshot metadata is preserved and target name canonicalizes within its scope', () => {
  const target = { scope: 'User', name: 'Path' };
  const input = { target, snapshot: snapshot('A'), selectedRaw: 'B', mode: '-append' };
  const plan = build(input);
  assert.deepEqual(plain(plan.targetKey), { scope: 'User', name: 'Path', canonicalName: 'PATH' });
  assert.deepEqual(plain(plan.before), { exists: true, raw: 'A', type: 'REG_EXPAND_SZ' });
  assert.deepEqual(plain(plan.after), { exists: true, raw: 'A;B', type: 'REG_EXPAND_SZ' });
  assert.equal(plan.baseRevision, 4);
  assert.equal(plan.source, 'scheme-apply');
  target.name = 'OTHER'; input.snapshot.raw = 'mutated';
  assert.equal(plan.target.name, 'Path'); assert.equal(plan.before.raw, 'A');
  const system = build({ ...input, target: { scope: 'System', name: 'Path' } });
  assert.notEqual(plan.targetKey.scope, system.targetKey.scope);
});
check('Every nested output is frozen and mutable metadata is copied', () => {
  const type = { registry: 'REG_SZ' }, revision = { version: 5 };
  const plan = build({ target: { scope: 'User', name: 'Path' }, snapshot: { exists: true, raw: 'A', type, revision }, selectedRaw: 'B', mode: 'append' });
  const inspect = value => {
    if (!value || typeof value !== 'object') return;
    assert.equal(Object.isFrozen(value), true);
    Object.values(value).forEach(inspect);
  };
  inspect(plan);
  type.registry = 'changed'; revision.version = 9;
  assert.equal(plan.before.type.registry, 'REG_SZ'); assert.equal(plan.baseRevision.version, 5);
  assert.notEqual(plan.before.type, type);
  assert.equal(Reflect.set(plan.operations[0], 'value', 'changed'), false);
  assert.equal(plan.operations[0].value, 'B');
});
check('Snapshot takes precedence over legacy raw and absent plus empty stays absent', () => {
  const absent = build({ target: 'User|PATH', snapshot: snapshot(undefined), actualRaw: 'wrong', selectedRaw: '', mode: 'append' });
  assert.equal(absent.before.exists, false); assert.equal(absent.after.exists, false);
  assert.equal(absent.after.raw, null); assert.equal(absent.operations.length, 0);
  const present = build({ snapshot: snapshot(''), selectedRaw: '', mode: 'append' });
  assert.equal(present.after.exists, true); assert.equal(present.after.raw, '');
  const created = build({ snapshot: snapshot(undefined), selectedRaw: '', mode: 'replace' });
  assert.equal(created.after.exists, true); assert.equal(created.after.raw, ''); assert.equal(created.operations.length, 1);
});
check('Matching excludes blanks, counts duplicate desired rows and preserves existence coverage', () => {
  const plan = build(options(' A ;X', 'A; A ;; ;B;B'));
  assert.equal(plan.matchResult.totalCount, 4); assert.equal(plan.matchResult.matchedCount, 2);
  assert.equal(plan.matchResult.missing.length, 2); assert.equal(plan.operations.length, 1);
  assert.equal(plan.matchResult.satisfied, false);
  assert.equal(build(options(' A ', 'A;A')).matchResult.satisfied, true);
  assert.equal(build(options(undefined, '')).matchResult.satisfied, false);
  assert.equal(build(options('', '')).matchResult.satisfied, true);
});
check('Replace matching follows existing set equality while operation uses exact raw', () => {
  const reordered = build(options('A;B;A', ' B ;A', 'replace'));
  assert.equal(reordered.matchResult.satisfied, true); assert.equal(reordered.matchResult.exact, false);
  assert.equal(reordered.operations[0].action, 'REPLACE');
  assert.equal(build(options('A;B;X', 'A;B', 'replace')).matchResult.satisfied, false);
  assert.equal(build(options('A;B;X', 'A;B', 'append')).matchResult.satisfied, true);
});
check('Special matching is whole and exact including empty and internal semicolons', () => {
  assert.equal(build(options('A;B', 'B;A', 'append', true)).matchResult.satisfied, false);
  const exact = build(options('', '', 'append', true));
  assert.equal(exact.matchResult.totalCount, 1); assert.equal(exact.matchResult.matchedCount, 1);
  assert.equal(exact.matchResult.satisfied, true); assert.equal(exact.operations.length, 0);
  assert.equal(build({ snapshot: snapshot(undefined), selectedRaw: '', special: true }).after.exists, true);
});
check('Row matching and selection change are pure and never infer Windows operations', () => {
  assert.equal(context.DemoPlan.matches(' A ', ['A;B']), true);
  assert.equal(context.DemoPlan.matches('a', ['A;B']), false);
  assert.equal(context.DemoPlan.matches('', ['; ;']), false);
  assert.equal(context.DemoPlan.matches(' A;B ', ['A;B'], true), false);
  assert.equal(context.DemoPlan.matches('A;B', ['A;B'], true), true);
  assert.equal(context.DemoPlan.selectionChange({ beforeRaw: 'A', afterRaw: 'A' }), null);
  const changed = context.DemoPlan.selectionChange({ target: 'User|Path', beforeRaw: 'A;B;C;D', afterRaw: 'A;B;E;F' });
  assert.equal(changed.beforeRaw, 'A;B;C;D'); assert.equal(changed.afterRaw, 'A;B;E;F');
  assert.equal(changed.operations, undefined); assert.equal(Object.isFrozen(changed), true);
  const plan = build(options('A;B;C', changed.afterRaw));
  assert.equal(plan.afterRaw, 'A;B;C;E;F');
  assert.deepEqual(plain(plan.operations).map(op => op.value), ['E', 'F']);
});
const element = (elementId, before, after, beforeIndex, afterIndex, flags = {}) => ({ elementId, before, after, beforeIndex, afterIndex, added: false, removed: false, replaced: false, moved: false, ...flags });
const edit = (before, after, elements, whole = false) => context.DemoPlan.buildElementEdit({
  target: 'User|Path', snapshot: snapshot(before), afterRaw: after,
  audit: { before, after, whole, elements }
});
check('Explicit stable identity editor plan projects add remove replace and move', () => {
  const elements = [element(11, 'A', 'A', 0, 1, { moved: true }), element(12, 'B', 'E', 1, 0, { replaced: true, moved: true }), element(13, 'C', undefined, 2, null, { removed: true }), element(14, undefined, 'F', null, 2, { added: true })];
  const plan = edit('A;B;C', 'E;A;F', elements);
  assert.equal(plan.source, 'windows-element-edit'); assert.equal(plan.afterRaw, 'E;A;F');
  assert.equal(plan.before.type, 'REG_EXPAND_SZ'); assert.equal(plan.after.type, 'REG_EXPAND_SZ');
  assert.deepEqual(plain(plan.operations).map(op => [op.action, op.elementId]), [['MOVE', 11], ['REPLACE', 12], ['MOVE', 12], ['REMOVE', 13], ['ADD', 14]]);
  assert.equal(Object.isFrozen(plan.audit.elements[0]), true);
  elements[1].after = 'mutated'; assert.equal(plan.audit.elements[1].after, 'E'); assert.equal(plan.operations[1].after, 'E');
});
check('Repeated actual elements are replaced by stable identity without guessed correspondence', () => {
  const plan = edit('X;X', 'X;Y', [element(3, 'X', 'X', 0, 0), element(4, 'X', 'Y', 1, 1, { replaced: true })]);
  assert.equal(plan.operations.length, 1); assert.equal(plan.operations[0].elementId, 4);
  assert.equal(plan.operations[0].beforeIndex, 1); assert.equal(plan.operations[0].before, 'X');
});
check('Special element edit keeps semicolons whole and emits explicit replacement', () => {
  const plan = edit('A;B', 'C;D', [element(1, 'A;B', 'C;D', 0, 0, { replaced: true })], true);
  assert.equal(plan.parts.length, 1); assert.equal(plan.special, true); assert.equal(plan.operations.length, 1);
});
check('Equal serialization emits no actual operations despite duplicate identity move', () => {
  const plan = edit('A;A', 'A;A', [element(2, 'A', 'A', 1, 0, { moved: true }), element(1, 'A', 'A', 0, 1, { moved: true })]);
  assert.equal(plan.operations.length, 0); assert.equal(plan.afterRaw, plan.beforeRaw);
});
check('Absent editor remains absent until an explicit live element is added', () => {
  const unchanged = edit(undefined, '', []);
  assert.equal(unchanged.after.exists, false); assert.equal(unchanged.after.raw, null); assert.equal(unchanged.operations.length, 0);
  const empty = edit(undefined, '', [element(1, undefined, '', null, 0, { added: true })]);
  assert.equal(empty.after.exists, true); assert.equal(empty.after.raw, ''); assert.equal(empty.operations[0].action, 'ADD');
  const emptied = edit('A', '', [element(1, 'A', undefined, 0, null, { removed: true })]);
  assert.equal(emptied.after.exists, false); assert.equal(emptied.after.raw, null); assert.equal(emptied.operations[0].action, 'REMOVE');
});
check('Editor refuses mismatched snapshots, missing identity and unexplained final changes', () => {
  assert.throws(() => context.DemoPlan.buildElementEdit({ snapshot: snapshot('A'), afterRaw: 'B', audit: { before: 'old', after: 'B', elements: [] } }), /does not describe/);
  assert.throws(() => edit('A', 'B', [element(undefined, 'A', 'B', 0, 0, { replaced: true })]), /elementId/);
  assert.throws(() => edit('A', 'B', []), /requires explicit/);
  assert.throws(() => build({ snapshot: { exists: true, raw: null }, selectedRaw: 'A' }), /string raw/);
});

process.stdout.write(`${checks} pure demo-plan checks passed.\n`);
