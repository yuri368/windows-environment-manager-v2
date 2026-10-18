'use strict';
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {JSDOM, VirtualConsole} = require('jsdom');

const root = path.resolve(__dirname, '..');
const checks = [], runtimeErrors = [], vc = new VirtualConsole();
vc.on('jsdomError', error => runtimeErrors.push(error.message));
const dom = new JSDOM(fs.readFileSync(path.join(root, 'temp.html'), 'utf8'), {
  runScripts: 'dangerously', pretendToBeVisual: true, virtualConsole: vc,
  beforeParse(window) {
    window.confirm = () => true;
    for (const file of ['demo-plan.js', 'demo-configs.js', 'windows-elements.js', 'config-format.js', 'config-editor.js'])
      window.eval(fs.readFileSync(path.join(root, file), 'utf8'));
  }
});
const w = dom.window, q = selector => w.document.querySelector(selector);
const ev = source => w.eval(source);
const json = source => JSON.parse(ev(`JSON.stringify(${source})`));
const check = (name, run) => {
  try { run(); checks.push({name, passed: true}); }
  catch (error) { checks.push({name, passed: false, error: error.stack}); }
};
const input = (selector, value) => {
  const node = q(selector); assert.ok(node, `Missing ${selector}`);
  node.value = value; node.dispatchEvent(new w.Event('input', {bubbles: true}));
};
const change = (selector, value) => {
  const node = q(selector); assert.ok(node, `Missing ${selector}`);
  node.value = value; node.dispatchEvent(new w.Event('change', {bubbles: true}));
};
const click = selector => { const node = q(selector); assert.ok(node, `Missing ${selector}`); node.click(); };
const setup = (app = 7, profile = '团队默认') => {
  ev('closeModal();reset(true)');
  ev(`editor={app:${app},profile:${JSON.stringify(profile)},varIdx:0,tab:'visual'};go('config')`);
};
const create = (kind, fields, context) => w.createConfigEntry(kind, fields, context);
const unchanged = () => ({apps: ev('JSON.stringify(apps)'), environment: ev('JSON.stringify({windows,history})')});
const assertRejectedWithoutMutation = (kind, fields, context) => {
  const before = unchanged(), result = create(kind, fields, context);
  assert.equal(result.ok, false, JSON.stringify(result));
  assert.ok(result.error, JSON.stringify(result));
  assert.deepEqual(unchanged(), before);
};

check('Add buttons open local creation rows and cancellation leaves all data untouched', () => {
  setup(); const before = unchanged();
  for (const button of ['#cfgAddApp', '#cfgAddProfile', '#cfgAddVariable']) {
    click(button);
    assert.ok(q('#cfgCreateForm'));
    for (const id of ['#cfgCreateSubmit', '#cfgCreateCancel']) assert.ok(q(id));
    assert.equal(q('#cfgCreateScope'), null); assert.equal(q('#cfgCreateMode'), null);
    assert.deepEqual(unchanged(), before);
    if (button === '#cfgAddApp') { assert.ok(q('#cfgCreateAppName')); assert.equal(q('#cfgCreateProfileName'), null); }
    if (button === '#cfgAddProfile') assert.ok(q('#cfgCreateProfileName'));
    if(button === '#cfgAddVariable'){
      for(const id of ['#cfgCreateName','#cfgScope','#cfgMode'])assert.ok(q(id));
      assert.equal(q('#cfgCreateOptions').open,false);
    }
    else {
      for(const id of ['#cfgCreateName','#cfgScope','#cfgMode'])assert.equal(q('#cfgCreateForm').querySelector(id),null);
      assert.ok(q('#cfgCreateForm').closest('.cfg-navigation'));assert.ok(q('#cfgDefault'));
    }
    click('#cfgCreateCancel');
    assert.equal(q('#cfgCreateForm'), null);
    assert.deepEqual(unchanged(), before);
  }
});

check('App button creates one app, its default profile and first empty variable', () => {
  setup(); const before = ev('apps.length'), environment = unchanged().environment;
  click('#cfgAddApp'); input('#cfgCreateAppName', '新建工具'); click('#cfgCreateSubmit');
  assert.equal(ev('apps.length'), before); assert.ok(q('#cfgDraftApp')); assert.equal(q('#cfgCreateForm'),null);
  click('#cfgDraftNext'); input('#cfgCreateProfileName', '默认方案'); click('#cfgCreateSubmit');
  assert.equal(ev('apps.length'), before); assert.ok(q('#cfgDraftProfile')); click('#cfgAddVariable');
  input('#cfgCreateName', 'TOOL_HOME'); click('#cfgCreateSubmit');
  const created = json('apps[apps.length-1]');
  assert.equal(ev('apps.length'), before + 1);
  assert.equal(created.name, '新建工具'); assert.equal(created.default, '默认方案');
  assert.equal(created.profiles.length, 1); assert.equal(created.profiles[0].name, '默认方案');
  assert.equal(created.profiles[0].vars.length, 1);
  assert.equal(created.profiles[0].vars[0].defaultValue, '');
  assert.equal(created.profiles[0].vars[0].name, 'TOOL_HOME');
  assert.equal(created.profiles[0].vars[0].scope, 'User');
  assert.equal(created.profiles[0].vars[0].mode, 'replace');
  assert.deepEqual(json('({app:editor.app,profile:editor.profile,varIdx:editor.varIdx})'), {app:before,profile:'默认方案',varIdx:0});
  assert.equal(q('#cfgName')?.textContent, 'TOOL_HOME');
  assert.equal(unchanged().environment, environment);
});

check('Profile button creates an appended profile with one selected variable', () => {
  setup(); const before = ev('apps[7].profiles.length');
  click('#cfgAddProfile'); input('#cfgCreateProfileName', '新方案'); click('#cfgCreateSubmit');
  assert.equal(ev('apps[7].profiles.length'),before); assert.ok(q('#cfgDraftProfile')); click('#cfgAddVariable'); input('#cfgCreateName', 'DEMO_HOME');
  change('#cfgScope', 'System'); change('#cfgMode', '+append'); click('#cfgCreateSubmit');
  const profile = json('apps[7].profiles[apps[7].profiles.length-1]');
  assert.equal(ev('apps[7].profiles.length'), before + 1);
  assert.equal(profile.name, '新方案'); assert.equal(profile.vars.length, 1);
  assert.equal(profile.vars[0].name, 'DEMO_HOME'); assert.equal(profile.vars[0].scope, 'System');
  assert.equal(profile.vars[0].mode, '+append'); assert.equal(profile.vars[0].defaultValue, '');
  assert.equal(ev('apps[7].default'), '团队默认'); assert.equal(ev('editor.profile'), '新方案');
  assert.equal(q('#cfgName')?.textContent, 'DEMO_HOME');
});

check('Variable button appends a mkVar-shaped variable and selects it', () => {
  setup(); const before = ev('configProfile().vars.length');
  click('#cfgAddVariable'); input('#cfgCreateName', 'EXTRA_HOME'); click('#cfgCreateSubmit');
  const v = json('configProfile().vars[configProfile().vars.length-1]');
  assert.equal(ev('configProfile().vars.length'), before + 1);
  assert.equal(v.id, before); assert.equal(v.name, 'EXTRA_HOME');
  assert.equal(v.defaultValue, ''); assert.equal(v.baselineValue, '');
  assert.equal(v.source, '默认值'); assert.equal(v.savedValue, null); assert.deepEqual(v.choices, []);
  assert.equal(v.editable, true); assert.equal(v.special, false);
  assert.equal(ev('editor.varIdx'), before); assert.equal(q('#cfgName')?.textContent, 'EXTRA_HOME');
});

check('Blank names, reserved profile name, invalid scope and invalid mode reject atomically', () => {
  setup();
  assertRejectedWithoutMutation('app', {appName:' ',profileName:'默认',name:'X'});
  assertRejectedWithoutMutation('app', {appName:'新应用',profileName:' ',name:'X'});
  assertRejectedWithoutMutation('profile', {profileName:'default_option',name:'X'});
  assertRejectedWithoutMutation('profile', {profileName:'新方案',name:' '});
  assertRejectedWithoutMutation('variable', {name:''});
  assertRejectedWithoutMutation('variable', {name:'X',scope:'Machine'});
  assertRejectedWithoutMutation('variable', {name:'X',mode:'prepend'});
});

check('Duplicate app/profile and same-scope variable names reject without partial children', () => {
  setup();
  assertRejectedWithoutMutation('app', {appName:'Node.js',profileName:'新方案',name:'UNIQUE'});
  assertRejectedWithoutMutation('profile', {profileName:'团队默认',name:'UNIQUE'});
  assertRejectedWithoutMutation('variable', {name:'node_home',scope:'User'});
});

check('Matching names may coexist in User and System and export as a V2 two-record array', () => {
  setup(); const result = create('variable', {name:'NODE_HOME',scope:'System',mode:'replace'});
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(ev('configProfile().vars.filter(v=>v.name.toUpperCase()==="NODE_HOME").length'), 2);
  assert.equal(ev('ConfigFormat.errors(apps).length'), 0);
  const text = ev('generateToml()');
  assert.match(text, /"NODE_HOME"\s*=\s*\[\s*\{[^\n]*scope = "User"[^\n]*\},\s*\{[^\n]*scope = "System"/);
});

check('Existing Special target passes special=true and replace into a new profile', () => {
  setup();
  const result = create('profile', {profileName:'继承整体处理',name:'NODE_OPTIONS',scope:'User',mode:'+append'});
  assert.equal(result.ok, true, JSON.stringify(result));
  const v = json('configProfile().vars[0]');
  assert.equal(v.name, 'NODE_OPTIONS'); assert.equal(v.special, true); assert.equal(v.mode, 'replace');
  assert.equal(ev('ConfigFormat.errors(apps).length'), 0);
});

check('Diff records app and profile subtree additions once and variable addition once', () => {
  setup(); assert.equal(create('app', {appName:'新增应用',profileName:'基础',name:'APP_ROOT'}).ok, true);
  let changes = json('configChanges()');
  assert.equal(changes.length, 1); assert.equal(changes[0].field, 'addition');
  assert.equal(changes[0].app, '新增应用');
  assert.match(ev('generateDiff()'), /新增应用/);
  setup(); assert.equal(create('profile', {profileName:'新增方案',name:'PROFILE_ROOT'}).ok, true);
  changes = json('configChanges()');
  assert.equal(changes.length, 1); assert.equal(changes[0].field, 'addition');
  assert.equal(changes[0].profile, '新增方案');
  setup(); assert.equal(create('variable', {name:'ADDED_ROOT'}).ok, true);
  changes = json('configChanges()');
  assert.equal(changes.length, 1); assert.equal(changes[0].field, 'addition');
  assert.equal(changes[0].name, 'ADDED_ROOT');
  click('#configTabDiff'); assert.match(q('#sourceText').textContent, /ADDED_ROOT/);
});

check('Restore removes all added levels, preserves original object/runtime identity and environment', () => {
  setup();
  ev('window.originalApp=apps[7];window.originalProfile=apps[7].profiles[0];window.originalVar=apps[7].profiles[0].vars[0];originalVar.source="自定义值";originalVar.custom="草稿值";originalVar.dirty=true');
  const environment = unchanged().environment;
  assert.equal(create('variable', {name:'TEMP_VAR'}).ok, true);
  assert.equal(create('profile', {profileName:'临时方案',name:'PROFILE_VAR'}).ok, true);
  assert.equal(create('app', {appName:'临时应用',profileName:'初始',name:'APP_VAR'}).ok, true);
  assert.ok(ev('configChanges().length') >= 3);
  ev('restoreConfig()');
  assert.equal(ev('apps.length'), ev('initialApps.length'));
  assert.equal(ev('apps[7].profiles.length'), ev('initialApps[7].profiles.length'));
  assert.equal(ev('apps[7].profiles[0].vars.length'), ev('initialApps[7].profiles[0].vars.length'));
  assert.equal(ev('apps[7]===originalApp && apps[7].profiles[0]===originalProfile && apps[7].profiles[0].vars[0]===originalVar'), true);
  assert.equal(ev('originalVar.source'), '自定义值'); assert.equal(ev('originalVar.custom'), '草稿值');
  assert.equal(ev('configChanges().length'), 0);
  assert.equal(unchanged().environment, environment);
  assert.ok(q('#cfgName')); assert.ok(ev('editor.app>=0 && editor.app<apps.length'));
});

check('New app is reachable in workbench and preview without writing simulated Windows/history', () => {
  setup(); const environment = unchanged().environment;
  assert.equal(create('app', {appName:'预览应用',profileName:'默认',name:'PREVIEW_ROOT'}).ok, true);
  ev('configVar().defaultValue="C:\\\\Demo\\\\New";selected=editor.app;go("workbench")');
  assert.equal(ev('appNow().name'), '预览应用');
  assert.equal(ev('curProfile().vars[0].name'), 'PREVIEW_ROOT');
  assert.match(q('#screen').textContent, /PREVIEW_ROOT/);
  click('#previewButton');
  assert.equal(q('#modal').classList.contains('open'), true);
  assert.match(q('#previewMeta').textContent, /1 条变量记录/);
  assert.equal(unchanged().environment, environment);
});

check('Restoring a selected new app leaves config and workbench navigation usable', () => {
  setup(); assert.equal(create('app', {appName:'待还原应用',profileName:'初始',name:'TMP_ROOT'}).ok, true);
  ev('restoreConfig()');
  assert.ok(ev('editor.app>=0 && editor.app<apps.length'));
  assert.ok(ev('configProfile().vars.length>0')); assert.ok(q('#editorbody'));
  ev('selected=editor.app;go("workbench")');
  assert.ok(q('#previewButton')); assert.ok(ev('selected>=0 && selected<apps.length'));
});

check('Existing invalid choice blocks any new entry without mutating the draft', () => {
  setup(); ev('setConfigProperty(configVar(),"choices",[...ConfigFormat.choices(configVar()),{label:"",value:""}]);renderEditorBody()');
  assert.ok(ev('ConfigFormat.errors(apps).length>0'));
  const before = unchanged(), initialCount = ev('configChanges().length');
  const result = create('variable', {name:'BLOCKED_ROOT'});
  assert.equal(result.ok, false); assert.ok(result.error);
  assert.deepEqual(unchanged(), before);
  assert.equal(ev('configChanges().length'), initialCount);
  click('#cfgAddVariable'); input('#cfgCreateName', 'BLOCKED_ROOT'); click('#cfgCreateSubmit');
  assert.match(q('#cfgCreateError').textContent, /候选|配置/);
  assert.deepEqual(unchanged(), before);
});

check('Failed and cancelled creation never add configuration nodes or diffs', () => {
  setup(); const before = unchanged();
  click('#cfgAddApp'); input('#cfgCreateAppName', 'Node.js');
  click('#cfgCreateSubmit'); assert.ok(q('#cfgCreateError')?.textContent);
  assert.deepEqual(unchanged(), before); assert.equal(ev('configChanges().length'), 0);
  click('#cfgCreateCancel'); assert.equal(q('#cfgCreateForm'), null);
  click('#cfgAddProfile'); input('#cfgCreateProfileName', '团队默认');
  click('#cfgCreateSubmit'); assert.ok(q('#cfgCreateError')?.textContent);
  assert.deepEqual(unchanged(), before); assert.equal(ev('configChanges().length'), 0);
  click('#cfgCreateCancel');
});

check('Same variable name in System requires an explicit scope choice and commits once', () => {
  setup(); const before = unchanged();
  click('#cfgAddVariable'); input('#cfgCreateName', 'NODE_HOME'); click('#cfgCreateSubmit');
  assert.match(q('#cfgCreateError')?.textContent || '', /同名|存在/);
  assert.deepEqual(unchanged(), before); assert.equal(ev('configChanges().length'), 0);
  click('#cfgLocateScope');assert.equal(q('#cfgCreateOptions').open,true);assert.equal(w.document.activeElement.id,'cfgScope');
  change('#cfgScope', 'System');
  assert.deepEqual(unchanged(), before);
  click('#cfgCreateSubmit');
  assert.equal(ev('configProfile().vars.filter(v=>ConfigFormat.matchName(v.name)==="NODE_HOME").length'), 2);
  assert.equal(ev('configVar().scope'), 'System'); assert.equal(ev('configChanges().length'), 1);
  assert.equal(w.document.activeElement.id, 'cfgDefault');
});

check('Whole target creation constrains the UI mode to replace and preserves the inherited rule', () => {
  setup(); click('#cfgAddProfile'); input('#cfgCreateProfileName', '整体处理继承'); click('#cfgCreateSubmit'); click('#cfgAddVariable'); input('#cfgCreateName', 'NODE_OPTIONS');
  assert.equal(q('#cfgMode').value, 'replace'); assert.equal(q('#cfgMode').disabled, true);
  click('#cfgCreateSubmit');
  assert.equal(ev('configVar().special'), true); assert.equal(ev('configVar().mode'), 'replace');
});

check('Candidate draft rejects blank or duplicate label and duplicate full value before writing', () => {
  setup(); const before = unchanged(), first = json('ConfigFormat.choices(configVar())[0]');
  click('#cfgAddChoice'); assert.ok(q('#cfgChoiceDraft')); assert.deepEqual(unchanged(), before);
  click('#cfgChoiceDraftAdd'); assert.ok(q('#cfgChoiceDraft')?.textContent.match(/不能为空|名称/)); assert.deepEqual(unchanged(), before);
  input('#cfgChoiceDraftLabel', first.label); click('#cfgChoiceDraftAdd'); assert.ok(q('#cfgChoiceDraft')?.textContent.match(/重复/)); assert.deepEqual(unchanged(), before);
  input('#cfgChoiceDraftLabel', '合法新标签'); input('#cfgChoiceDraftValue', first.value); click('#cfgChoiceDraftAdd');
  assert.ok(q('#cfgChoiceDraft')?.textContent.match(/重复/)); assert.deepEqual(unchanged(), before);
  input('#cfgChoiceDraftValue', '  X;;X;  '); click('#cfgChoiceDraftAdd');
  const last = json('ConfigFormat.choices(configVar()).at(-1)');
  assert.deepEqual(last, {label:'合法新标签',value:'  X;;X;  '}); assert.equal(ev('configChanges().length'), 1);
});

check('Candidate draft cancellation and composition keys preserve model and focus', () => {
  setup(); const before = unchanged(); click('#cfgAddChoice'); input('#cfgChoiceDraftLabel', '输入中');
  const label = q('#cfgChoiceDraftLabel');
  for (const key of ['Enter','Escape']) label.dispatchEvent(new w.KeyboardEvent('keydown', {key,isComposing:true,bubbles:true,cancelable:true}));
  assert.ok(q('#cfgChoiceDraft')); assert.equal(q('#cfgChoiceDraftLabel').value, '输入中'); assert.deepEqual(unchanged(), before);
  click('#cfgChoiceDraftCancel'); assert.equal(q('#cfgChoiceDraft'), null); assert.equal(w.document.activeElement.id, 'cfgAddChoice');
  assert.deepEqual(unchanged(), before);
});

check('Candidate draft keeps leading newline, spaces and empty elements after view and variable switches', () => {
  setup();
  ev('setConfigProperty(configVar(),"defaultValue","A\\r\\nB\\rC\\nD");renderEditorBody()');
  const original = ev('configVar().defaultValue');
  click('#cfgAddChoice'); input('#cfgChoiceDraftLabel', '保真候选');
  input('#cfgChoiceDraftValue', '\n  A;\r\n;A;\rC  ');
  const browserValue = q('#cfgChoiceDraftValue').value;
  assert.equal(browserValue, '\n  A;\n;A;\nC  ');
  click('#configTabRaw'); click('#configTabVisual');
  assert.equal(q('#cfgChoiceDraft'), null); assert.match(q('#cfgAddChoice').textContent, /继续新增/);
  click('#cfgAddChoice');
  assert.equal(q('#cfgChoiceDraftValue').value, browserValue);
  click('[data-tablevar="1"]'); assert.equal(q('#cfgChoiceDraft'), null);
  click('[data-tablevar="0"]'); assert.equal(q('#cfgChoiceDraft'), null); click('#cfgAddChoice');
  assert.equal(q('#cfgChoiceDraftValue').value, browserValue);
  click('#cfgChoiceDraftAdd');
  assert.equal(ev('ConfigFormat.choices(configVar()).at(-1).value'), browserValue);
  assert.equal(ev('configVar().defaultValue'), original);
});

check('All-profiles variable entry creates under its own group', () => {
  setup(); const other = json('apps[7].profiles.find(p=>p.name!==editor.profile).name');
  const before = ev(`apps[7].profiles.find(p=>p.name===${JSON.stringify(other)}).vars.length`);
  change('#editorView', 'all');
  const entry = [...w.document.querySelectorAll('[data-add-variable]')].find(node => node.dataset.addVariable === other);
  assert.ok(entry, `No grouped entry for ${other}`); entry.click();
  input('#cfgCreateName', 'GROUP_ONLY_HOME'); click('#cfgCreateSubmit');
  assert.equal(ev('editor.profile'), other);
  assert.equal(ev(`apps[7].profiles.find(p=>p.name===${JSON.stringify(other)}).vars.length`), before + 1);
  assert.equal(ev('configVar().name'), 'GROUP_ONLY_HOME');
});

check('Grouped entry works when the target profile has fewer or no variables than the current index', () => {
  setup();
  ev('editor.varIdx=2;apps[7].profiles[1].vars.pop();renderEditorBody()');
  change('#editorView', 'all');
  let entry = [...w.document.querySelectorAll('[data-add-variable]')].find(node => node.dataset.addVariable === '实验版本');
  assert.ok(entry); entry.click(); assert.ok(q('#cfgCreateForm'));
  input('#cfgCreateName', 'SHORT_PROFILE_HOME'); click('#cfgCreateSubmit');
  assert.equal(ev('editor.profile'), '实验版本'); assert.equal(ev('configVar().name'), 'SHORT_PROFILE_HOME');

  setup();
  ev('editor.varIdx=2;apps[7].profiles[1].vars=[];renderEditorBody()');
  change('#editorView', 'all');
  entry = [...w.document.querySelectorAll('[data-add-variable]')].find(node => node.dataset.addVariable === '实验版本');
  assert.ok(entry); entry.click(); assert.ok(q('#cfgCreateForm'));
  input('#cfgCreateName', 'EMPTY_PROFILE_HOME'); click('#cfgCreateSubmit');
  assert.equal(ev('editor.profile'), '实验版本'); assert.equal(ev('configVar().name'), 'EMPTY_PROFILE_HOME');
});

check('One creation draft stays with its original owner across navigation and requires explicit replacement', () => {
  setup(); const before = unchanged();
  click('#cfgAddVariable'); input('#cfgCreateName', 'SUSPENDED_HOME');
  click('[data-editapp="0"]'); assert.equal(q('#cfgCreateForm'), null);
  click('#cfgAddProfile'); assert.ok(q('#cfgCreateConflict'));
  assert.deepEqual(unchanged(), before); assert.equal(ev('configChanges().length'), 0);
  click('#cfgCreateResume'); assert.equal(ev('editor.app'), 7);
  assert.equal(q('#cfgCreateName')?.value, 'SUSPENDED_HOME');
  click('[data-editapp="0"]'); click('#cfgAddProfile'); click('#cfgCreateDiscard');
  assert.ok(q('#cfgCreateProfileName')); assert.equal(q('#cfgCreateName'), null);
  assert.deepEqual(unchanged(), before); click('#cfgCreateCancel');
});

check('Restore and global reset clear pending creation without manufacturing a diff', () => {
  setup(); click('#cfgAddVariable'); input('#cfgCreateName', 'PENDING_HOME');
  ev('restoreConfig()'); assert.equal(q('#cfgCreateForm'), null);
  assert.equal(ev('configChanges().length'), 0);
  click('#cfgAddVariable'); assert.equal(q('#cfgCreateName').value, '');
  input('#cfgCreateName', 'SECOND_PENDING'); ev('reset(true)');
  ev('editor={app:7,profile:"团队默认",varIdx:0,tab:"visual"};go("config")');
  click('#cfgAddVariable'); assert.equal(q('#cfgCreateName').value, '');
});

check('Composing Enter and Escape leave the name draft alone; plain Escape restores entry focus', () => {
  setup(); click('#cfgAddVariable'); input('#cfgCreateName', '输入法变量');
  const node = q('#cfgCreateName'); node.focus();
  for (const key of ['Enter','Escape']) node.dispatchEvent(new w.KeyboardEvent('keydown', {key,isComposing:true,bubbles:true,cancelable:true}));
  assert.equal(q('#cfgCreateName')?.value, '输入法变量'); assert.equal(ev('configChanges().length'), 0);
  node.dispatchEvent(new w.KeyboardEvent('keydown', {key:'Escape',bubbles:true,cancelable:true}));
  assert.equal(q('#cfgCreateForm'), null); assert.equal(w.document.activeElement.id, 'cfgAddVariable');
});

check('A new same-target selection invalidates and refreshes the open shared-target preview', () => {
  setup(); ev('selected=7;go("workbench")');
  ev('showPreview("应用","共享目标预览",[{app:7,profile:"团队默认",varIdx:0}])');
  assert.equal(q('#modal').classList.contains('open'), true);
  const before = json('modalAction.rows[0].protectionSignature');
  assert.equal(ev('modalAction.version'), 1);
  const result = create('app', {appName:'共享目标应用',profileName:'默认',name:'NODE_HOME',scope:'User'});
  assert.equal(result.ok, true, JSON.stringify(result));
  ev('apps[editor.app].profiles[0].vars[0].defaultValue="C:\\\\Shared\\\\Node"');
  assert.equal(ev('previewIsCurrent(modalAction)'), false);
  assert.equal(ev('refreshPreview()'), true);
  assert.equal(ev('modalAction.version'), 2);
  assert.notEqual(json('modalAction.rows[0].protectionSignature'), before);
  assert.match(json('modalAction.rows[0].protectionSignature'), /Shared/);
  assert.equal(ev('previewIsCurrent(modalAction)'), true);
  assert.match(q('#previewVersionStatus').textContent, /版本 2/);
});

check('A name-only profile is a resumable empty session draft outside configuration and export', () => {
  setup(); const before=unchanged(),toml=ev('generateToml()');
  click('#cfgAddProfile'); input('#cfgCreateProfileName','  分步方案  '); click('#cfgCreateSubmit');
  assert.match(q('#cfgDraftProfile').textContent,/  分步方案  /);
  assert.match(q('.cfg-container-workspace').textContent,/此方案尚无变量/);
  assert.equal(q('#cfgCreateForm'),null);assert.equal(q('#cfgScope'),null);
  assert.deepEqual(unchanged(),before);assert.equal(ev('generateToml()'),toml);assert.equal(ev('configChanges().length'),0);
  click('#configTabRaw');click('#configTabVisual');assert.ok(q('#cfgDraftProfile'));assert.equal(q('.cfg-container-workspace'),null);
  click('#cfgDraftProfile');assert.match(q('.cfg-page-heading').textContent,/  分步方案  /);
  click('#cfgAddVariable');input('#cfgCreateName','DEFERRED_HOME');click('#cfgCreateSubmit');
  assert.equal(ev('editor.profile'),'  分步方案  ');assert.equal(ev('configVar().name'),'DEFERRED_HOME');
  assert.equal(ev('configChanges().length'),1);assert.equal(q('#cfgDraftProfile'),null);
  assert.equal(unchanged().environment,before.environment);
});

check('Cancelling a first variable returns to its named container; discard and restore clear only session drafts', () => {
  setup();const before=unchanged();
  click('#cfgAddProfile');input('#cfgCreateProfileName','稍后填写');click('#cfgCreateSubmit');
  click('#cfgAddVariable');input('#cfgCreateName','LATER_HOME');click('#cfgCreateCancel');
  assert.equal(q('#cfgCreateForm'),null);assert.ok(q('#cfgDraftProfile'));assert.deepEqual(unchanged(),before);
  click('#cfgAddVariable');assert.equal(q('#cfgCreateName').value,'LATER_HOME');
  q('#cfgCreateName').dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}));
  assert.ok(q('#cfgDraftProfile'));assert.equal(w.document.activeElement.id,'cfgAddVariable');
  click('#cfgDraftDiscard');assert.equal(q('#cfgDraftProfile'),null);assert.deepEqual(unchanged(),before);
  click('#cfgAddProfile');input('#cfgCreateProfileName','待还原空方案');click('#cfgCreateSubmit');
  ev('restoreConfig()');assert.equal(q('#cfgDraftProfile'),null);assert.equal(ev('ConfigEditor.hasDraft(editor)'),false);assert.deepEqual(unchanged(),before);
});

check('Name-only app and profile stages survive navigation without creating a placeholder variable', () => {
  setup();const before=unchanged();
  click('#cfgAddApp');input('#cfgCreateAppName','分步应用');click('#cfgCreateSubmit');
  assert.ok(q('#cfgDraftApp'));assert.equal(q('#cfgCreateForm'),null);assert.equal(q('#cfgDraftProfile'),null);assert.deepEqual(unchanged(),before);
  click('[data-editapp="0"]');assert.ok(q('#cfgDraftApp'));assert.equal(q('.cfg-container-workspace'),null);
  click('#cfgDraftApp');assert.equal(ev('editor.app'),7);assert.match(q('.cfg-page-heading').textContent,/分步应用/);
  click('#cfgDraftNext');input('#cfgCreateProfileName','第一方案');click('#cfgCreateCancel');
  assert.ok(q('#cfgDraftApp'));assert.equal(q('#cfgDraftProfile'),null);assert.deepEqual(unchanged(),before);
  click('#cfgDraftNext');assert.equal(q('#cfgCreateProfileName').value,'第一方案');click('#cfgCreateSubmit');
  assert.ok(q('#cfgDraftProfile'));assert.equal(q('#cfgCreateName'),null);assert.deepEqual(unchanged(),before);
  click('#cfgDraftDiscard');assert.equal(q('#cfgDraftApp'),null);assert.deepEqual(unchanged(),before);
});

check('Named container stages reject invalid names and final validation failures without losing their owner', () => {
  setup();const before=unchanged();
  click('#cfgAddProfile');input('#cfgCreateProfileName','default_option');click('#cfgCreateSubmit');
  assert.match(q('#cfgCreateError').textContent,/保留/);assert.deepEqual(unchanged(),before);
  input('#cfgCreateProfileName','延后校验');click('#cfgCreateSubmit');click('#cfgAddVariable');input('#cfgCreateName','BAD=NAME');click('#cfgCreateSubmit');
  assert.match(q('#cfgCreateError').textContent,/变量名/);assert.ok(q('#cfgDraftProfile'));assert.deepEqual(unchanged(),before);
  click('#cfgDraftDiscard');click('#cfgAddApp');input('#cfgCreateAppName','Node.js');click('#cfgCreateSubmit');
  assert.match(q('#cfgCreateError').textContent,/已存在/);assert.deepEqual(unchanged(),before);
});

check('Variable creation requires only its name; optional properties stay collapsed and default values edit after creation', () => {
  setup();const before=unchanged();click('#cfgAddVariable');
  assert.equal(q('#cfgCreateForm').querySelectorAll('input').length,1);assert.equal(q('#cfgCreateOptions').open,false);
  assert.equal(q('#cfgDefault'),null);assert.deepEqual(unchanged(),before);
  input('#cfgCreateName','NAME_ONLY_HOME');click('#cfgCreateSubmit');
  assert.equal(ev('configVar().name'),'NAME_ONLY_HOME');assert.equal(ev('configVar().defaultValue'),'');
  assert.equal(ev('configVar().scope'),'User');assert.equal(ev('configVar().mode'),'replace');assert.ok(q('#cfgDefault'));
  assert.equal(q('#cfgCreateOptions'),null);assert.equal(unchanged().environment,before.environment);
});

check('App/profile name panels stay beside their entries and the current variable remains editable', () => {
  setup();const environment=unchanged().environment;
  for(const [button,field] of [['#cfgAddApp','#cfgCreateAppName'],['#cfgAddProfile','#cfgCreateProfileName']]){
    click(button);input(field,'就地命名草稿');
    assert.ok(q('#cfgCreateForm').closest('.editor-nav-group'));assert.equal(q('.cfg-editor-main #cfgCreateForm'),null);
    assert.equal(q('.cfg-container-workspace'),null);assert.equal(q('#cfgName').textContent,'NODE_HOME');
    input('#cfgDefault','原面板仍可编辑');assert.equal(ev('configVar().defaultValue'),'原面板仍可编辑');
    change('#cfgMode','append');assert.equal(ev('configVar().mode'),'append');
    assert.equal(q(field).value,'就地命名草稿');assert.equal(w.document.querySelectorAll('#cfgCreateForm').length,1);
    click('#cfgCreateCancel');
  }
  assert.equal(unchanged().environment,environment);
});

check('The first profile of a named app opens at the profile entry without moving its form to the canvas', () => {
  setup();const before=unchanged();click('#cfgAddApp');input('#cfgCreateAppName','就地新增应用');click('#cfgCreateSubmit');
  click('#cfgDraftNext');assert.ok(q('#cfgCreateProfileName'));
  assert.ok(q('#cfgCreateForm').closest('.cfg-navigation'));assert.equal(q('.cfg-editor-main #cfgCreateForm'),null);
  assert.match(q('.cfg-page-heading').textContent,/就地新增应用/);assert.deepEqual(unchanged(),before);
  input('#cfgCreateProfileName','就地方案');click('#cfgCreateSubmit');assert.ok(q('#cfgDraftProfile'));assert.deepEqual(unchanged(),before);
});

check('The add-variable entry and name field occupy the last slot in the same variable strip', () => {
  setup();const before=unchanged(),strip=q('.cfg-variable-strip');
  assert.ok(strip.contains(q('#cfgAddVariable')));assert.ok(strip.lastElementChild.contains(q('#cfgAddVariable')));
  assert.ok(strip.lastElementChild.previousElementSibling.matches('.cfg-variable-select'));
  click('#cfgAddVariable');const form=q('#cfgCreateForm');
  assert.equal(form.parentElement,q('.cfg-variable-strip'));assert.equal(form.nextElementSibling,null);
  assert.ok(form.previousElementSibling.matches('.cfg-variable-select'));
  assert.equal(q('#cfgCreateSubmit').getAttribute('aria-label'),'添加变量');
  assert.ok(q('#cfgCreateCancel').getAttribute('aria-label'));
  input('#cfgCreateName','VARIABLE_NAME_WITH_A_LONG_READABLE_SUFFIX');assert.deepEqual(unchanged(),before);
  q('#cfgCreateName').dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}));
  assert.ok(q('.cfg-variable-strip').contains(q('#cfgAddVariable')));assert.equal(w.document.activeElement.id,'cfgAddVariable');
  assert.deepEqual(unchanged(),before);
});

check('Config creation and rendering produce no JSDOM runtime errors', () => assert.deepEqual(runtimeErrors, []));
console.log(JSON.stringify({passed: checks.filter(item => item.passed).length, total: checks.length, checks}, null, 2));
if (checks.some(item => !item.passed)) process.exitCode = 1;
dom.window.close();
