'use strict';
/* Semantic/behavior coverage for the owner's GUI follow-ups; this is not visual acceptance. */
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
const w = dom.window, doc = w.document;
const q = selector => doc.querySelector(selector), all = selector => [...doc.querySelectorAll(selector)];
const ev = source => w.eval(source), ids = nodes => nodes.map(node => Number(node.dataset.elementId));
const live = () => all('#elementList > .element-row');
const ghosts = () => all('#elementList > .element-ghost-row');
const row = id => q('#elementList > [data-element-id="' + id + '"]');
const pane = (selector, id) => q(selector + ' [data-element-id="' + id + '"]');
let applied = [];
function check(name, run) {
  try { run(); checks.push({name, passed: true}); }
  catch (error) { checks.push({name, passed: false, error: error.stack}); }
}
function direct(raw, extra = {}) {
  ev('closeModal()'); applied = [];
  w.WindowsElements.open({container: q('#modal'), name: 'GUI_FOLLOWUPS', scope: 'User',
    registryType: 'REG_EXPAND_SZ', raw, whole: false, selectedValues: [],
    close: () => ev('closeModal()'), onApply: (value, audit, plan) => applied.push({value, audit, plan}), ...extra});
  return ids(live());
}
function click(id, attribute) {
  const button = row(id)?.querySelector('[' + attribute + ']');
  assert.ok(button, attribute + ' for element ' + id); assert.equal(button.disabled, false); button.click();
}
const move = (id, direction) => click(id, 'data-move-' + direction);
const remove = id => click(id, 'data-delete'), undo = id => click(id, 'data-restore-ghost');
function edit(id, value) {
  const input = row(id).querySelector('.element-input');
  input.value = value; input.dispatchEvent(new w.Event('input', {bubbles: true}));
}
function add(value) {
  q('#addElement').click(); const id = ids(live()).at(-1); edit(id, value); return id;
}
function total(valid, removed) {
  const node = q('#elementTotal');
  assert.ok(node?.closest('.elements-toolbar'), 'count is in the editor toolbar');
  assert.match(node.textContent, new RegExp('有效元素\\s*' + valid + '\\s*·\\s*已移除\\s*' + removed + '(?:\\D|$)'));
  assert.equal(all('#elementTotal').length, 1);
  assert.equal(live().length, valid); assert.equal(ghosts().length, removed);
}
function assertMovement(liveIds, ghostIds) {
  for (const node of all('#elementList > [data-element-id]')) {
    const id = Number(node.dataset.elementId), isGhost = node.classList.contains('element-ghost-row');
    const expected = (isGhost ? ghostIds : liveIds).includes(id);
    for (const target of [node, pane('#elementOriginal', id), pane('#elementPreview', id)].filter(Boolean)) {
      const badge = target.querySelector('.diff-label.is-moved');
      assert.equal(target.classList.contains('is-moved'), expected);
      assert.equal(!!badge, expected);
      if (expected) {
        assert.equal(badge.textContent, isGhost ? '幽灵移动' : '移动');
        assert.ok(badge.classList.contains('status-badge'));
        assert.ok(badge.querySelector('.st-cell.st-moved'), 'movement icon stays inside its badge');
      }
    }
  }
  assert.equal(Number(q('[data-legend-count="moved"]').textContent), liveIds.length);
  assert.equal(Number(q('[data-legend-count="ghost-moved"]').textContent), ghostIds.length);
  assert.match(q('#elementStatus').textContent,
    new RegExp('展示移动：移动 ' + liveIds.length + ' · 幽灵移动 ' + ghostIds.length));
}
function apply() {
  assert.equal(q('#applyElements').disabled, false); q('#applyElements').click();
  assert.equal(applied.length, 1); return applied[0];
}
function assertNoDisplayMetadata(result) {
  assert.doesNotMatch(JSON.stringify(result), /displayOrder|displayMoved|ghostMoved|ghost-moved|展示移动|幽灵移动|deletedDrafts/);
}
function setup(mode = '-append', whole = false) {
  ev('closeModal();reset(true)');
  ev('selected=1;focusedVariable=1;const v=scheme(1).vars[1];v.defaultValue="A";v.baselineValue="A";' +
    'v.mode=' + JSON.stringify(mode) + ';v.special=' + whole + ';windows["User|PATH"]="A;B;C";render()');
}
function setMode(mode, whole = false) {
  ev('scheme(1).vars[1].mode=' + JSON.stringify(mode) + ';scheme(1).vars[1].special=' + whole + ';renderInspector()');
}
function spyOpen(run) {
  const original = w.WindowsElements.open, calls = [];
  w.WindowsElements.open = options => { calls.push(options); return original(options); };
  try { run(calls); } finally { w.WindowsElements.open = original; ev('closeModal()'); }
}
function assertClosed(calls) {
  assert.equal(calls.length, 0, 'lower-level element editor was not called');
  assert.equal(q('#modal').classList.contains('open'), false);
}
function activateActual(key) {
  const node = q('#compareActual');
  if (key === 'dblclick') node.dispatchEvent(new w.MouseEvent('dblclick', {bubbles: true}));
  else node.dispatchEvent(new w.KeyboardEvent('keydown', {key, bubbles: true, cancelable: true}));
}

check('Moved ghosts use 幽灵移动 in list, Original and Preview while displaced live rows still use 移动', () => {
  const [a, b, c] = direct('A;B;C'); remove(b); move(b, 'down');
  assert.deepEqual(ids(all('#elementList > [data-element-id]')), [a, c, b]);
  assertMovement([c], [b]); total(2, 1);
  for (const target of [row(b), pane('#elementOriginal', b), pane('#elementPreview', b)])
    assert.ok(target.querySelector('.diff-label.is-removed'), 'removal and ghost movement remain separate badges');
  assert.equal(q('#elementSerialized').textContent, 'A;C');
  const result = apply(); assert.equal(result.value, 'A;C'); assert.equal(result.audit.counts.moved, 0);
  assert.equal(result.audit.elements.find(element => element.elementId === b).moved, false);
  assert.equal(result.plan.operations.filter(operation => operation.action === 'MOVE').length, 0);
  assert.deepEqual(Array.from(result.plan.operations, operation => operation.action), ['REMOVE']);
  assertNoDisplayMetadata(result);
});
check('Returning a ghost clears its movement badge and restoring it converts the badge to ordinary 移动', () => {
  const [, b, c] = direct('A;B;C'); remove(b); move(b, 'down'); assertMovement([c], [b]);
  move(b, 'up'); assertMovement([], []); move(b, 'down'); undo(b);
  assertMovement([c, b], []); total(3, 0); assert.equal(q('#elementSerialized').textContent, 'A;C;B');
  const result = apply(); assert.equal(result.audit.counts.moved, 2);
  assert.equal(result.plan.operations.filter(operation => operation.action === 'MOVE').length, 2);
  assertNoDisplayMetadata(result);
});
check('Toolbar counts include duplicate and empty live elements and track remove/add/undo/restore', () => {
  const [, duplicate, empty] = direct('A;A;'); total(3, 0);
  assert.equal(q('.elements-meta'), null, 'duplicate standalone metadata row is removed');
  assert.ok(all('#modal [title]').some(node => node.title.includes('REG_EXPAND_SZ')), 'registry type remains discoverable in a tooltip');
  remove(duplicate); total(2, 1); remove(empty); total(1, 2);
  const manual = add('NEW'); total(2, 2); remove(manual); total(1, 2);
  undo(empty); total(2, 1); q('#restoreElements').click(); total(3, 0);
  assert.equal(q('#elementSerialized').textContent, 'A;A;'); assert.equal(q('#applyElements').disabled, true);
});
check('Absent values have zero effective elements and a deleted manual addition leaves no ghost count', () => {
  direct(undefined); total(0, 0); const id = add('NEW'); total(1, 0); remove(id); total(0, 0);
  assert.equal(q('#elementSerialized').textContent, '（不存在）'); assert.equal(q('#applyElements').disabled, true);
});
check('Replace workbench removes editor semantics and clears handlers on the current-value pane', () => {
  setup('replace'); const button = q('#editWindowsElements'), node = q('#compareActual');
  assert.ok(button); assert.equal(button.hidden, true); assert.equal(button.onclick, null);
  assert.equal(node.hasAttribute('role'), false); assert.equal(node.hasAttribute('tabindex'), false);
  assert.equal(node.hasAttribute('aria-label'), false); assert.equal(node.hasAttribute('title'), false);
  assert.equal(node.classList.contains('inspect-editable'), false);
  assert.equal(node.ondblclick, null); assert.equal(node.onkeydown, null);
  spyOpen(calls => { button.click(); assertClosed(calls); });
});
for (const key of ['dblclick', 'Enter', ' ']) check('Replace current-value ' + JSON.stringify(key) + ' cannot open the editor', () => {
  setup('replace'); spyOpen(calls => { activateActual(key); assertClosed(calls); });
});
check('A retained append button cannot open the editor after switching the same inspector to replace', () => {
  setup('-append'); const oldButton = q('#editWindowsElements'); setMode('replace');
  spyOpen(calls => { oldButton.click(); assertClosed(calls); });
});
for (const whole of [false, true]) check('Programmatic workbench replace entry is guarded, whole=' + whole, () => {
  setup('replace', whole);
  spyOpen(calls => {
    ev('openWindowsElements("User","PATH",["A"],' + whole + ',"replace")'); assertClosed(calls);
  });
});
check('A null-mode argument cannot bypass the focused replace workbench restriction', () => {
  setup('replace');
  spyOpen(calls => {
    ev('openWindowsElements("User","PATH",["A"],false,null)'); assertClosed(calls);
  });
});
check('Replace still accepts selected-value input and previews/applies the exact replacement', () => {
  setup('replace'); const input = q('#varRows [data-row="1"] .vinput');
  input.value = 'REPLACED;VALUE'; input.dispatchEvent(new w.Event('input', {bubbles: true}));
  assert.equal(ev('value(scheme(1).vars[1])'), 'REPLACED;VALUE');
  assert.equal(q('#compareSerialized').textContent, 'REPLACED;VALUE');
  assert.match(q('#compareDesired').textContent, /REPLACED/); assert.match(q('#compareAfter').textContent, /VALUE/);
  assert.equal(q('#editWindowsElements').hidden, true);
  q('#previewButton').click(); assert.ok(q('#modal').classList.contains('open'));
  assert.ok(all('#modal .diffrow').some(node => node.querySelector('.pair.add').textContent.includes('REPLACED;VALUE')));
  assert.equal(q('#confirmDialog').disabled, false); q('#confirmDialog').click();
  assert.equal(ev('windows["User|PATH"]'), 'REPLACED;VALUE'); assert.equal(q('#modal').classList.contains('open'), false);
});
for (const mode of ['+append', '-append', 'append']) check('Switching replace back to ' + mode + ' restores button, pointer and keyboard editing', () => {
  setup(mode); setMode('replace'); setMode(mode);
  const node = q('#compareActual'), button = q('#editWindowsElements');
  assert.equal(button.hidden, false); assert.equal(node.getAttribute('role'), 'button');
  assert.equal(node.getAttribute('tabindex'), '0'); assert.ok(node.classList.contains('inspect-editable'));
  for (const trigger of [() => button.click(), ...['dblclick', 'Enter', ' '].map(key => () => activateActual(key))]) {
    spyOpen(calls => { trigger(); assert.equal(calls.length, 1); assert.ok(q('#modal').classList.contains('open')); assert.ok(q('#elementList')); });
  }
});
check('Windows environment page retains direct editing for a replace-mode target', () => {
  setup('replace'); q('[data-view="windows"]').click();
  const button = q('[data-windows-edit="User|PATH"]'), node = q('[data-windows-value="User|PATH"]');
  assert.ok(button); assert.equal(node.getAttribute('role'), 'button'); assert.equal(node.getAttribute('tabindex'), '0');
  for (const trigger of [() => button.click(),
    () => node.dispatchEvent(new w.MouseEvent('dblclick', {bubbles: true})),
    ...['Enter', ' '].map(key => () => node.dispatchEvent(new w.KeyboardEvent('keydown', {key, bubbles: true, cancelable: true})))]) {
    spyOpen(calls => { trigger(); assert.equal(calls.length, 1); assert.ok(q('#elementList')); assert.equal(live().length, 3); });
  }
});
check('Windows-page null-mode direct entry and the low-level whole-string editor remain available', () => {
  setup('replace'); q('[data-view="windows"]').click();
  spyOpen(calls => { ev('openWindowsElements("User","PATH",["A"],false,null)'); assert.equal(calls.length, 1); assert.equal(live().length, 3); });
  const [id] = direct('A;B', {whole: true}); assert.equal(live().length, 1); edit(id, 'C;D');
  assert.equal(q('#elementSerialized').textContent, 'C;D'); assert.equal(apply().value, 'C;D');
});
check('No JSDOM runtime errors in GUI follow-up workflows', () => assert.deepEqual(errors, []));
const report = {passed: checks.filter(item => item.passed).length, total: checks.length, checks};
console.log(JSON.stringify(report, null, 2));
if (report.passed !== report.total) process.exitCode = 1;
w.close();
