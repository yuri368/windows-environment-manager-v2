'use strict';
/* Display movement is session UI state. Business MOVE still compares live originals only. */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {JSDOM, VirtualConsole} = require('jsdom');

const root = path.resolve(__dirname, '..');
const checks = [], errors = [];
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
const q = selector => doc.querySelector(selector);
const all = selector => [...doc.querySelectorAll(selector)];
const ids = nodes => nodes.map(node => Number(node.dataset.elementId));
const live = () => all('#elementList > .element-row');
const visible = () => all('#elementList > .element-row, #elementList > .element-ghost-row');
const editor = id => q('#elementList > [data-element-id="' + id + '"]');
const original = id => q('#elementOriginal [data-element-id="' + id + '"]');
const preview = id => q('#elementPreview [data-element-id="' + id + '"]');
const status = () => q('#elementStatus').textContent;
const legend = kind => Number(q('[data-legend-count="' + kind + '"]')?.textContent);
let applied = [];

function check(name, run) {
  try { run(); checks.push({name, passed: true}); }
  catch (error) { checks.push({name, passed: false, error: error.stack}); }
}
function open(raw, extra = {}) {
  w.WindowsElements.dismiss();
  applied = [];
  w.WindowsElements.open({container: q('#modal'), name: 'DISPLAY_MOVEMENT', scope: 'User',
    registryType: 'REG_SZ', raw, whole: false, selectedValues: [],
    close: () => w.WindowsElements.dismiss(),
    onApply: (value, audit, plan) => applied.push({value, audit, plan}), ...extra});
  return ids(live());
}
function click(id, action) {
  const node = editor(id), button = node?.querySelector('[' + action + ']');
  assert.ok(button, action + ' for element ' + id);
  assert.equal(button.disabled, false, action + ' is enabled');
  button.click();
}
const move = (id, direction) => click(id, 'data-move-' + direction);
const remove = id => click(id, 'data-delete');
const undo = id => click(id, 'data-restore-ghost');
function add(value) {
  q('#addElement').click();
  const id = ids(live()).at(-1);
  const input = editor(id).querySelector('.element-input');
  input.value = value;
  input.dispatchEvent(new w.Event('input', {bubbles: true}));
  return id;
}
function moved(node, expected) {
  assert.ok(node, 'element exists');
  assert.equal(node.classList.contains('is-moved'), expected);
  assert.equal(!!node.querySelector('.diff-label.is-moved'), expected);
  if (expected) {
    const isGhost = original(Number(node.dataset.elementId))?.classList.contains('is-removed');
    assert.equal(node.querySelector('.diff-label.is-moved').textContent, isGhost ? '幽灵移动' : '移动');
  }
}
function assertMovement(liveIds, ghostIds) {
  const liveSet = new Set(liveIds), ghostSet = new Set(ghostIds);
  for (const node of visible()) {
    const id = Number(node.dataset.elementId), isGhost = node.classList.contains('element-ghost-row');
    const expected = (isGhost ? ghostSet : liveSet).has(id);
    moved(node, expected);
    if (original(id)) moved(original(id), expected);
    moved(preview(id), expected);
  }
  assert.equal(legend('moved'), liveIds.length, 'legend live movement');
  assert.equal(legend('ghost-moved'), ghostIds.length, 'legend ghost movement');
  if (status().includes('展示移动')) {
    assert.match(status(), new RegExp('移动 ' + liveIds.length + ' · 幽灵移动 ' + ghostIds.length));
  } else {
    assert.fail('footer must explicitly name 展示移动');
  }
}
function businessMoveCount(result) {
  return result.plan.operations.filter(operation =>
    String(operation.action || operation.type).toUpperCase() === 'MOVE').length;
}
function apply() {
  assert.equal(q('#applyElements').disabled, false);
  q('#applyElements').click();
  assert.equal(applied.length, 1);
  return applied[0];
}
function noDisplayMetadata(result) {
  const json = JSON.stringify(result);
  assert.doesNotMatch(json, /displayMoved|ghostMoved|ghost-moved|展示移动|幽灵移动/);
  for (const element of result.audit.elements) {
    assert.equal(Object.hasOwn(element, 'displayMoved'), false);
    assert.equal(Object.hasOwn(element, 'ghostMoved'), false);
  }
}

check('Swapped originals keep both movement labels when one becomes a ghost', () => {
  const [a, b, c] = open('A;B;C');
  move(b, 'up');
  assert.deepEqual(ids(visible()), [b, a, c]);
  assertMovement([a, b], []);
  remove(a);
  assert.deepEqual(ids(visible()), [b, a, c]);
  assert.ok(editor(a).classList.contains('element-ghost-row'));
  assertMovement([b], [a]);
  for (const node of [editor(a), original(a), preview(a)]) {
    assert.ok(node.querySelector('.diff-label.is-removed'));
  }
  assert.ok(original(a).classList.contains('is-removed'));
  assert.ok(preview(a).classList.contains('is-removed'));
  assert.match(status(), /删除 1/);
  const result = apply();
  assert.equal(result.value, 'B;C');
  assert.equal(result.audit.counts.moved, 0);
  assert.equal(businessMoveCount(result), 0);
  assert.equal(result.audit.elements.find(element => element.elementId === a).moved, false);
  noDisplayMetadata(result);
});

check('Swapping a ghost and a live original marks both in UI without a business MOVE', () => {
  const [a, b, c] = open('A;B;C');
  remove(b);
  move(b, 'down');
  assert.deepEqual(ids(visible()), [a, c, b]);
  assertMovement([c], [b]);
  assert.equal(q('#elementSerialized').textContent, 'A;C');
  const result = apply();
  assert.equal(result.audit.counts.moved, 0);
  assert.equal(businessMoveCount(result), 0);
  noDisplayMetadata(result);
});

check('Returning a ghost to its baseline slot clears both display movement flags', () => {
  const [a, b, c] = open('A;B;C');
  remove(b);
  move(b, 'down');
  assertMovement([c], [b]);
  move(b, 'up');
  assert.deepEqual(ids(visible()), [a, b, c]);
  assertMovement([], []);
  assert.match(status(), /删除 1/);
});

check('Restoring a displaced ghost at its current position recalculates live MOVE', () => {
  const [a, b, c] = open('A;B;C');
  remove(b);
  move(b, 'down');
  assertMovement([c], [b]);
  undo(b);
  assert.deepEqual(ids(live()), [a, c, b]);
  assertMovement([c, b], []);
  const result = apply();
  assert.equal(result.value, 'A;C;B');
  assert.equal(result.audit.counts.moved, 2);
  assert.equal(businessMoveCount(result), 2);
  noDisplayMetadata(result);
});

check('Manual and adopted additions do not shift original movement flags', () => {
  const [a, b, c] = open('A;B;C', {appendEntries: [{value: 'SCHEME', mode: '-append'}]});
  const manual = add('MANUAL');
  for (let n = 0; n < 3; n++) move(manual, 'up');
  assert.deepEqual(ids(visible()), [manual, a, b, c]);
  assertMovement([], []);
  assert.equal(editor(manual).classList.contains('is-moved'), false);
  const adopt = q('[data-add-pending]');
  assert.ok(adopt, 'scheme suggestion is available');
  adopt.click();
  const scheme = ids(live()).find(id => ![a, b, c, manual].includes(id));
  assert.ok(scheme);
  for (let n = 0; n < 3; n++) move(scheme, 'up');
  assertMovement([], []);
  for (const id of [a, b, c, manual, scheme]) moved(editor(id), false);
});

check('Move, delete, ghost move and undo cycles recalculate flags from stable IDs', () => {
  const [a, b, c, d] = open('A;B;C;D');
  move(c, 'up');
  assertMovement([b, c], []);
  remove(b);
  assertMovement([c], [b]);
  move(b, 'down');
  assert.deepEqual(ids(visible()), [a, c, d, b]);
  assertMovement([c, d], [b]);
  undo(b);
  assertMovement([c, d, b], []);
  remove(b);
  assertMovement([c, d], [b]);
  q('#restoreElements').click();
  assert.deepEqual(ids(visible()), [a, b, c, d]);
  assertMovement([], []);
});

check('Duplicate raw values retain separate original identities and movement labels', () => {
  const [first, second, third] = open('A;A;B');
  move(second, 'down');
  assert.deepEqual(ids(visible()), [first, third, second]);
  assertMovement([second, third], []);
  remove(second);
  assertMovement([third], [second]);
  assert.equal(original(first).classList.contains('is-moved'), false);
  assert.equal(original(second).classList.contains('is-moved'), true);
  assert.equal(original(first).querySelector('[data-raw-value]').getAttribute('data-raw-value'), 'A');
  assert.equal(original(second).querySelector('[data-raw-value]').getAttribute('data-raw-value'), 'A');
});

check('No JSDOM runtime errors in display movement workflows', () => assert.deepEqual(errors, []));
const report = {passed: checks.filter(result => result.passed).length, total: checks.length, checks};
console.log(JSON.stringify(report, null, 2));
if (report.passed !== report.total) process.exitCode = 1;
dom.window.close();
