'use strict';
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {JSDOM, VirtualConsole} = require('jsdom');
const root = path.resolve(__dirname, '../..');
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', error => errors.push(error.message));
const dom = new JSDOM(fs.readFileSync(path.join(root, 'temp.html'), 'utf8'), {
  runScripts: 'dangerously', pretendToBeVisual: true, virtualConsole: vc,
  beforeParse(w) {
    w.confirm = () => true;
    for (const file of ['demo-plan.js', 'demo-configs.js', 'windows-elements.js', 'config-format.js', 'config-editor.js'])
      w.eval(fs.readFileSync(path.join(root, file), 'utf8'));
  }
});
const w = dom.window;
const q = selector => w.document.querySelector(selector);
const ev = source => w.eval(source);
const results = [];
function check(name, run) {
  run();
  results.push(name);
}
function setup(actual = 'X;A;B;C;Y', selected = 'A;B;C') {
  ev('closeModal();reset(true);selected=1;focusedVariable=1;const v=scheme(1).vars[1];v.defaultValue=' + JSON.stringify(selected) + ';v.baselineValue=v.defaultValue;v.mode="-append";windows["User|PATH"]=' + JSON.stringify(actual) + ';render()');
}
function input(raw) {
  const field = q('#varRows [data-row="1"] .vinput');
  field.value = raw;
  field.dispatchEvent(new w.Event('input', {bubbles: true}));
  return field;
}
function inspect() {
  const review = q('#inspectReview');
  assert.equal(review.parentElement.classList.contains('inspecthead'), true);
  assert.equal(q('#inspectPreview').closest('#inspectReview'), review);
  return review;
}
check('unchanged review explains no write and retains preview action', () => {
  setup();
  const review = inspect();
  assert.equal(review.dataset.reviewState, 'unchanged');
  assert.match(review.textContent, /值不变/);
  assert.match(review.textContent, /确认方案选择不会改变当前值/);
  assert.match(q('#inspectPreview').textContent, /方案预览/);
});
check('changed review removes only duplicate text and uses frozen after', () => {
  setup();
  const field = input('A;C;E');
  const review = inspect();
  assert.equal(review.dataset.reviewState, 'changed');
  assert.match(review.textContent, /有实际变更/);
  assert.doesNotMatch(review.textContent, /实际变更待确认/);
  assert.equal(q('#compareSerialized').textContent, ev('demoPlan(scheme(1).vars[1]).after.raw'));
  assert.equal(field.value, 'A;C;E');
});
check('retained deletion reason remains visible and previewable', () => {
  setup('A;B;B;C');
  input('A;C');
  ev('windows["User|PATH"]="A;B;C";targetRevisions["User|PATH"]++;refreshVisibleState()');
  const review = inspect();
  assert.equal(review.dataset.reviewState, 'retained');
  assert.match(review.textContent, /删除保留/);
  assert.match(review.textContent, /预览中查看原因/);
  assert.ok(q('#inspectPreview'));
});
check('blocked ordering keeps reason and does not change selected input', () => {
  setup();
  const field = input('A;C;E');
  ev('scheme(1).vars[1].mode="append";renderInspector()');
  const review = inspect();
  assert.equal(review.dataset.reviewState, 'blocked');
  assert.match(review.textContent, /暂不能应用/);
  assert.match(review.textContent, /自定义排序暂不支持模拟应用/);
  assert.equal(field.value, 'A;C;E');
});
assert.deepEqual(errors, []);
console.log(JSON.stringify({passed: results.length, total: results.length, checks: results}, null, 2));

