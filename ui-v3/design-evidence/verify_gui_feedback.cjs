'use strict';
/* Observable UI behavior; plans remain authoritative and presentation stays session-only. */
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const {JSDOM, VirtualConsole} = require('jsdom');
const root = path.resolve(__dirname, '..'), checks = [], errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', error => errors.push(error.message));
const dom = new JSDOM(fs.readFileSync(path.join(root, 'temp.html'), 'utf8'), {
  runScripts: 'dangerously', pretendToBeVisual: true, virtualConsole: vc,
  beforeParse(w) {
    w.confirm = () => true;
    for (const file of ['demo-plan.js', 'demo-configs.js', 'windows-elements.js'])
      w.eval(fs.readFileSync(path.join(root, file), 'utf8'));
  }
});
const w = dom.window, doc = w.document, q = selector => doc.querySelector(selector);
const all = selector => [...doc.querySelectorAll(selector)], ev = source => w.eval(source);
const check = (name, run) => {
  try { run(); checks.push({name, passed: true}); }
  catch (error) { checks.push({name, passed: false, error: error.stack}); }
};
const live = () => all('#elementList > .element-row');
const ghosts = () => all('#elementList > .element-ghost-row');
const visible = () => all('#elementList > [data-element-id]');
const ids = nodes => nodes.map(node => Number(node.dataset.elementId));
const row = id => q('#elementList > .element-row[data-element-id="' + id + '"]');
const ghost = id => q('#elementList > .element-ghost-row[data-element-id="' + id + '"]');
const pane = (selector, id) => q(selector + ' [data-element-id="' + id + '"]');
const raw = node => node.querySelector('[data-raw-value]').getAttribute('data-raw-value');
const values = () => live().map(node => node.querySelector('.element-input').value);
const serialized = () => q('#elementSerialized').textContent;
const edit = (id, value) => {
  const input = row(id).querySelector('.element-input');
  input.value = value; input.dispatchEvent(new w.Event('input', {bubbles: true}));
};
const remove = id => row(id).querySelector('[data-delete]').click();
const undo = id => ghost(id).querySelector('[data-restore-ghost]').click();
const move = (id, direction) => (row(id) || ghost(id)).querySelector('[data-move-' + direction + ']').click();
const add = value => {
  q('#addElement').click(); const id = ids(live()).at(-1);
  if (value !== undefined) edit(id, value);
  return id;
};
const noSessionMetadata = object => assert.doesNotMatch(JSON.stringify(object),
  /manualAddedIds|deletedDrafts|displayOrder|adoptionMap|removalAdoptions|is-manual-added|手动新增|icon-fallback/);
let applied = [];
function direct(rawValue, selectedValues = []) {
  w.WindowsElements.dismiss(); q('#modal').classList.remove('open'); applied = [];
  w.WindowsElements.open({container: q('#modal'), name: 'GUI_FEEDBACK', scope: 'User', registryType: 'REG_SZ',
    raw: rawValue, whole: false, selectedValues,
    close: () => {w.WindowsElements.dismiss(); q('#modal').classList.remove('open');},
    onApply: (value, audit, plan) => applied.push({value, audit, plan})});
  return ids(live());
}
function setup(actual = 'A;B', old = 'A', next = 'A;E') {
  ev('closeModal();reset(true)');
  ev('selected=1;focusedVariable=1;const v=scheme(1).vars[1];v.defaultValue=' + JSON.stringify(old) +
    ';v.baselineValue=v.defaultValue;v.mode="-append";windows["User|PATH"]=' + JSON.stringify(actual) + ';render()');
  const input = q('#varRows [data-row="1"] .vinput');
  input.value = next; input.dispatchEvent(new w.Event('input', {bubbles: true}));
  q('#editWindowsElements').click();
}
function assertManual(id) {
  for (const node of [row(id), pane('#elementPreview', id)]) {
    assert.ok(node.classList.contains('is-added'));
    assert.ok(node.classList.contains('is-manual-added'));
    const label = node.querySelector('.diff-label.is-manual-added');
    assert.ok(label); assert.equal(label.textContent, '手动新增');
  }
  assert.equal(pane('#elementOriginal', id), null);
}
function assertScheme(id) {
  for (const node of [row(id), pane('#elementPreview', id)]) {
    assert.ok(node.classList.contains('is-added'));
    assert.equal(node.classList.contains('is-manual-added'), false);
    assert.equal(node.querySelector('.diff-label.is-manual-added'), null);
    assert.equal(node.querySelector('.diff-label.is-added').textContent, '新增');
  }
}
function assertSummaryCounts(added, manualAdded) {
  assert.match(q('#elementStatus').textContent,
    new RegExp('新增 ' + added + ' · 手动新增 ' + manualAdded));
  assert.equal(q('[data-legend-count="added"]').textContent, String(added));
  assert.equal(q('[data-legend-count="manual-added"]').textContent, String(manualAdded));
}
function assertDraft(id, draft, original) {
  // HTML parsing normalizes literal CR/CRLF in attributes; exact raw remains in audit below.
  const displayed = value => value.replace(/\r\n?/g, '\n');
  assert.equal(row(id), null); assert.equal(raw(ghost(id)), displayed(draft));
  assert.equal(raw(pane('#elementPreview', id)), displayed(draft));
  assert.ok(pane('#elementPreview', id).classList.contains('is-ghost'));
  assert.equal(raw(pane('#elementOriginal', id)), displayed(original));
  assert.ok(pane('#elementOriginal', id).classList.contains('is-removed'));
}
check('State icons stay inside their text badges across editor rows and comparison ghosts', () => {
  const pair = (node, kind, icon, label) => {
    const badge = node.querySelector('.diff-label.is-' + kind + '.status-badge');
    assert.ok(badge, kind + ' badge exists');
    assert.equal(badge.textContent, label);
    const cell = badge.querySelector('.st-cell.st-' + icon);
    assert.ok(cell, kind + ' icon is inside badge');
    assert.equal(cell.getAttribute('aria-hidden'), 'true');
    return badge;
  };
  const [first, second, third] = direct('A;A;B');
  pair(row(first), 'duplicate', 'duplicate', '重复 ×2');
  assert.equal(row(first).querySelector('.element-plate .st-cell'), null);
  edit(first, 'A2');
  pair(row(first), 'replaced', 'replaced', '替换');
  pair(pane('#elementPreview', first), 'replaced', 'replaced', '替换');
  assert.equal(pane('#elementPreview', first).querySelector('.st-plate'), null);
  move(third, 'up');
  pair(row(third), 'moved', 'moved', '移动');
  const manual = add();
  pair(row(manual), 'manual-added', 'manual-added', '手动新增');
  pair(row(manual), 'empty', 'empty', '空元素');
  assert.equal(row(manual).querySelector('.element-plate .st-cell'), null);
  remove(second);
  pair(ghost(second), 'removed', 'removed', '本次编辑已移除');
  pair(pane('#elementOriginal', second), 'removed', 'removed', '删除');
  pair(pane('#elementPreview', second), 'removed', 'removed', '删除');
  assert.equal(ghost(second).querySelector('.st-plate'), null);
});
check('Creating a previously absent variable marks the empty manual row and preserves ADD semantics', () => {
  direct(undefined); const id = add(); assertManual(id);
  assert.equal(serialized(), ''); assert.equal(q('#applyElements').disabled, false);
  q('#applyElements').click();
  assert.equal(applied.length, 1); assert.equal(applied[0].plan.before.exists, false);
  assert.equal(applied[0].plan.after.exists, true); assert.equal(applied[0].value, '');
  assert.equal(applied[0].plan.operations.length, 1);
  assert.equal(applied[0].plan.operations[0].action, 'ADD'); noSessionMetadata(applied[0]);
});
check('Manual origin survives editing, movement and a full row redraw', () => {
  direct('A;B'); const id = add('MANUAL'); assertManual(id);
  edit(id, 'MANUAL2'); assertManual(id); move(id, 'up'); assertManual(id);
  const other = add('OTHER'); assertManual(id); assertManual(other);
  assert.deepEqual(values(), ['A', 'MANUAL2', 'B', 'OTHER']);
  assert.equal(serialized(), 'A;MANUAL2;B;OTHER');
});
for (const initial of [undefined, 'A']) check('Manual add then delete is a zero-change result for ' + String(initial), () => {
  direct(initial); const id = add('TEMP'); remove(id);
  assert.equal(row(id), null); assert.equal(ghost(id), null); assert.deepEqual(ghosts(), []);
  assert.equal(all('#elementPreview .is-manual-added').length, 0);
  assert.equal(serialized(), initial === undefined ? '（不存在）' : initial);
  assert.match(q('#elementStatus').textContent, /0 项变化/); assert.equal(q('#applyElements').disabled, true);
  q('#applyElements').click(); assert.deepEqual(applied, []);
});
check('Restore and Cancel clear manual origin before another session', () => {
  const originals = direct('A;B'); add('TEMP'); q('#restoreElements').click();
  assert.deepEqual(ids(live()), originals); assert.deepEqual(values(), ['A', 'B']);
  assert.equal(all('#elementList .is-manual-added,#elementPreview .is-manual-added').length, 0);
  assert.equal(q('#applyElements').disabled, true); add('TEMP2'); q('#cancelDialog').click();
  direct('A;B'); assert.equal(all('#elementList .is-manual-added,#elementPreview .is-manual-added').length, 0);
  assertManual(add('FRESH'));
});
check('Scheme adoption keeps green suggestion provenance and the ordinary added label', () => {
  setup(); q('[data-add-pending]').click(); const id = Number(live().find(node => node.querySelector('.element-input').value === 'E').dataset.elementId);
  assertScheme(id); const button = q('[data-add-pending]');
  assert.equal(button.getAttribute('aria-pressed'), 'true');
  assert.ok(button.closest('.editor-suggestion-token').classList.contains('is-adopted'));
  move(id, 'up'); assertScheme(id); assert.equal(q('[data-add-pending]').getAttribute('aria-pressed'), 'true');
});
check('Editing an adopted row clears the toggle provenance while its original scheme source stays added', () => {
  setup(); q('[data-add-pending]').click(); const id = ids(live()).at(-1);
  edit(id, 'F'); assertScheme(id); assert.equal(q('[data-add-pending]').getAttribute('aria-pressed'), 'false');
  move(id, 'up'); assertScheme(id); add('MANUAL'); assertScheme(id);
  q('[data-add-pending]').click(); const newId = Number(live().find(node => node.querySelector('.element-input').value === 'E').dataset.elementId);
  assert.notEqual(newId, id); assertScheme(newId); q('[data-add-pending]').click();
  assert.ok(row(id)); assert.equal(row(newId), null); assertScheme(id);
});
check('Manual and scheme rows with identical raw values retain distinct origin labels', () => {
  setup(); q('[data-add-pending]').click(); const schemeId = ids(live()).at(-1), manualId = add('E');
  assertScheme(schemeId); assertManual(manualId);
  q('[data-add-pending]').click(); assert.equal(row(schemeId), null); assertManual(manualId);
  assert.equal(values().filter(value => value === 'E').length, 1); assert.equal(q('[data-add-pending]'), null);
});
check('Summary counts adopted scheme additions separately from manual additions', () => {
  setup(); q('[data-add-pending]').click(); assertSummaryCounts(1, 0);
  add('MANUAL'); assertSummaryCounts(1, 1);
});
check('Editing an adopted row keeps it in the scheme-added summary after adoption provenance clears', () => {
  setup(); q('[data-add-pending]').click(); assertSummaryCounts(1, 0);
  const id = ids(live()).at(-1); edit(id, 'EDITED-SCHEME');
  assert.equal(q('[data-add-pending]').getAttribute('aria-pressed'), 'false');
  assertScheme(id); assertSummaryCounts(1, 0);
});
check('Deleting one manual row decrements only manual-added summary count', () => {
  setup(); q('[data-add-pending]').click(); const schemeId = ids(live()).at(-1);
  const firstManual = add('MANUAL-1'), secondManual = add('MANUAL-2');
  assertSummaryCounts(1, 2); remove(firstManual); assert.equal(row(schemeId) !== null, true);
  assertSummaryCounts(1, 1); assertManual(secondManual); assertScheme(schemeId);
});
check('Restore resets scheme and manual summary counts for this edit session', () => {
  setup(); q('[data-add-pending]').click(); add('MANUAL'); assertSummaryCounts(1, 1);
  q('#restoreElements').click();
  assert.match(q('#elementStatus').textContent, /本次编辑：0 项变化/);
  assert.doesNotMatch(q('#elementStatus').textContent, /新增 [1-9]|手动新增 [1-9]/);
  assert.equal(q('[data-legend-count="added"]').textContent, '0');
  assert.equal(q('[data-legend-count="manual-added"]').textContent, '0');
  assert.equal(all('#elementList .is-manual-added,#elementPreview .is-manual-added').length, 0);
});
check('Manual addition applies one ADD and keeps presentation metadata out of History', () => {
  setup('A', 'A', 'A'); const id = add('MANUAL'); assertManual(id); q('#applyElements').click();
  assert.equal(ev('windows["User|PATH"]'), 'A;MANUAL');
  const record = ev('history[0]'), plan = record.changes[0].plan;
  assert.equal(plan.after.raw, 'A;MANUAL'); assert.deepEqual(Array.from(plan.operations, op => op.action), ['ADD']);
  assert.equal(plan.operations[0].after, 'MANUAL'); noSessionMetadata(record);
});
for (const [label, original, draft, fieldValue] of [
  ['edited original', 'B', 'B1', 'B1'], ['original empty', '', 'FILLED', 'FILLED'],
  ['edited to empty', 'B', '', ''], ['untouched empty', '', '', ''],
  ['multiline raw', 'B\r\nC', 'B\r\nCX', 'B\nCX']
]) check(label + ': deleted list and Preview show the latest draft; Original and REMOVE before stay frozen', () => {
  const [a, id, c] = direct('A;' + original + ';Z'); edit(id, fieldValue); remove(id);
  assertDraft(id, draft, original); assert.deepEqual(ids(live()), [a, c]); assert.equal(serialized(), 'A;Z');
  q('#applyElements').click(); const result = applied[0], operation = result.plan.operations[0];
  assert.equal(result.value, 'A;Z'); assert.equal(result.plan.operations.length, 1);
  assert.equal(operation.action, 'REMOVE'); assert.equal(operation.elementId, id); assert.equal(operation.before, original);
  const removed = result.audit.elements.find(element => element.elementId === id);
  assert.equal(removed.before, original); assert.equal(removed.removed, true); noSessionMetadata(result);
});
check('Moved draft undo preserves stable ID and current visible position through repeated edit/delete cycles', () => {
  const [a, b, c, d] = direct('A;B;C;D'); edit(b, 'B1'); remove(b); assertDraft(b, 'B1', 'B');
  move(b, 'down'); assert.deepEqual(ids(visible()), [a, c, b, d]); assertDraft(b, 'B1', 'B');
  undo(b); assert.deepEqual(ids(live()), [a, c, b, d]); assert.deepEqual(values(), ['A', 'C', 'B1', 'D']);
  edit(b, 'B2'); remove(b); assertDraft(b, 'B2', 'B'); move(b, 'down'); undo(b);
  assert.deepEqual(ids(live()), [a, c, d, b]); assert.deepEqual(values(), ['A', 'C', 'D', 'B2']);
  edit(b, ''); remove(b); assertDraft(b, '', 'B'); undo(b);
  assert.deepEqual(ids(live()), [a, c, d, b]); assert.equal(serialized(), 'A;C;D;');
});
check('Deleting one duplicate uses that occurrence draft and preserves both Original identities', () => {
  const [first, second, last] = direct('A;A;Z'); edit(second, 'A2'); remove(second);
  assertDraft(second, 'A2', 'A'); assert.equal(raw(pane('#elementOriginal', first)), 'A');
  assert.equal(pane('#elementOriginal', first).classList.contains('is-removed'), false);
  move(second, 'down'); undo(second); assert.deepEqual(ids(live()), [first, last, second]);
  assert.deepEqual(values(), ['A', 'Z', 'A2']);
});
check('Restore discards ghost drafts so a later deletion shows the original again', () => {
  const [, id] = direct('A;B'); edit(id, 'B1'); remove(id); assertDraft(id, 'B1', 'B');
  q('#restoreElements').click(); assert.deepEqual(ghosts(), []); assert.equal(row(id).querySelector('.element-input').value, 'B');
  remove(id); assertDraft(id, 'B', 'B'); undo(id); assert.deepEqual(values(), ['A', 'B']);
});
check('Green suggested deletion undo restores the original at the current ghost position', () => {
  setup('X;A;B;Z', 'A;B', 'A'); const id = ids(live())[2];
  q('[data-remove-pending]').click(); assertDraft(id, 'B', 'B'); move(id, 'down');
  const order = ids(visible()); const button = q('[data-remove-pending]');
  assert.equal(button.getAttribute('aria-pressed'), 'true'); assert.ok(button.closest('.is-adopted'));
  button.click(); assert.deepEqual(ids(live()), order); assert.equal(row(id).querySelector('.element-input').value, 'B');
  edit(id, 'B1'); remove(id); assertDraft(id, 'B1', 'B'); undo(id);
  assert.equal(row(id).querySelector('.element-input').value, 'B1');
});
function assertActions(node, finalAttribute, finalIcon) {
  const buttons = [...node.querySelectorAll('.element-actions > button')];
  assert.equal(buttons.length, 3);
  const attributes = ['data-move-up', 'data-move-down', finalAttribute], icons = ['ic-up', 'ic-down', finalIcon];
  buttons.forEach((button, index) => {
    assert.ok(button.hasAttribute(attributes[index])); assert.ok(button.querySelector('b.ic.' + icons[index]));
    assert.equal(button.textContent.trim(), ''); assert.equal(button.children.length, 1);
    assert.ok(button.getAttribute('aria-label')); assert.ok(button.getAttribute('title'));
  });
}
check('Live and ghost actions share icon-only up/down/final order and retain accessible names', () => {
  const [a, b, c] = direct('A;B;C'); remove(b);
  assertActions(row(a), 'data-delete', 'ic-trash'); assertActions(ghost(b), 'data-restore-ghost', 'ic-undo');
  assertActions(row(c), 'data-delete', 'ic-trash');
  assert.equal(row(a).querySelector('[data-move-up]').disabled, true);
  assert.equal(row(c).querySelector('[data-move-down]').disabled, true);
  move(b, 'down'); assert.equal(ghost(b).querySelector('[data-move-down]').disabled, true);
  undo(b); assertActions(row(b), 'data-delete', 'ic-trash');
});
check('Action labels explain ghost movement and recovery while icon clicks invoke the same actions', () => {
  const [a, b, c] = direct('A;B;C'); edit(b, 'B1'); remove(b);
  const up = ghost(b).querySelector('[data-move-up]'), restore = ghost(b).querySelector('[data-restore-ghost]');
  assert.match(up.getAttribute('aria-label') + up.title, /仅调整.*展示位置/);
  assert.match(restore.getAttribute('aria-label') + restore.title, /恢复删除前编辑/);
  up.querySelector('b.ic').click(); assert.deepEqual(ids(visible()), [b, a, c]);
  ghost(b).querySelector('[data-restore-ghost] b.ic').click();
  assert.deepEqual(ids(live()), [b, a, c]); assert.equal(values()[0], 'B1');
});
function assertFallback(node, text) {
  assert.ok(node); assert.equal(node.textContent.trim(), text);
  const fallback = node.querySelector('span.icon-fallback'); assert.ok(fallback);
  assert.equal(fallback.textContent, text); assert.equal(fallback.getAttribute('aria-hidden'), 'true');
  assert.equal([...node.childNodes].filter(child => child.nodeType === 3).map(child => child.textContent).join('').trim(), '');
}
check('Suggestion buttons and paired state badges follow adopt and undo', () => {
  setup('X;A;B;Z', 'A;B', 'A;E');
  const badge = (selector, label, icon) => {
    const token = q(selector).closest('.editor-suggestion-token');
    const state = token.querySelector('.diff-label.status-badge');
    assert.equal(state.textContent, label);
    assert.ok(state.querySelector('.st-cell.st-' + icon));
    assert.equal(token.querySelector('.st-plate'), null);
  };
  badge('[data-add-pending]', '建议追加', 'pendingAdd');
  badge('[data-remove-pending]', '建议删除', 'pendingRemove');
  assertFallback(q('[data-add-pending]'), '＋'); assertFallback(q('[data-remove-pending]'), '−');
  q('[data-add-pending]').click(); assertFallback(q('[data-add-pending]'), '✓'); badge('[data-add-pending]', '已加入本次编辑', 'adopted');
  q('[data-remove-pending]').click(); assertFallback(q('[data-remove-pending]'), '✓'); badge('[data-remove-pending]', '已采纳删除', 'adopted');
  q('[data-remove-pending]').click(); assertFallback(q('[data-remove-pending]'), '−'); badge('[data-remove-pending]', '建议删除', 'pendingRemove');
  q('[data-add-pending]').click(); assertFallback(q('[data-add-pending]'), '＋'); badge('[data-add-pending]', '建议追加', 'pendingAdd');
});
check('Matched row and preview use the same paired icon-caption badge as the legend', () => {
  const [id] = direct('A', ['A']);
  for (const target of [row(id).querySelector('.element-match-state'), pane('#elementPreview', id)]) {
    const badge = target.querySelector('.diff-label.status-badge.is-match');
    assert.ok(badge); assert.equal(badge.textContent, '匹配');
    const icon = badge.querySelector('.st-cell.st-match .ic.ic-check');
    assert.ok(icon); assert.equal(icon.parentElement.getAttribute('aria-hidden'), 'true');
    assert.equal(target.querySelector('.element-match-mark'), null);
  }
});
check('Result tally belongs to its heading rather than a separate value row', () => {
  ev('closeModal();reset(true)');
  const tally = q('#compareTally');
  assert.ok(tally.closest('.inspect-value-heading'));
  assert.equal(tally.parentElement.querySelector('.k').textContent, '应用后的值');
  assert.equal(tally.closest('.result-pane').querySelectorAll('#compareTally').length, 1);
});
check('Empty hints use a paired badge through replacement, deletion and restore', () => {
  const [id] = direct('A'); edit(id, '');
  for(const node of [row(id), pane('#elementPreview',id)]) {
    const badge=node.querySelector('.status-badge.is-empty');
    assert.equal(badge.textContent,'空元素'); assert.ok(badge.querySelector('.st-empty .ic-void'));
    assert.equal(node.querySelector('.st-plate .st-cell'),null);
  }
  remove(id); assert.equal(ghost(id).querySelector('.is-empty.status-badge').textContent,'空元素');
  undo(id); assert.equal(row(id).querySelector('textarea').value,'');
  assert.equal(row(id).querySelector('.status-badge.is-empty').textContent,'空元素');
});
check('No JSDOM runtime errors in GUI feedback workflows', () => assert.deepEqual(errors, []));
const report = {passed: checks.filter(item => item.passed).length, total: checks.length, checks};
console.log(JSON.stringify(report, null, 2));
if (checks.some(item => !item.passed)) process.exitCode = 1;
w.close();
