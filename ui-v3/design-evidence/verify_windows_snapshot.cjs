'use strict';
// Windows-page entry points are exercised through the DOM; no registry or browser access.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require('jsdom');
const root=path.resolve(__dirname,'..'),checks=[],errors=[],opened=[];
const vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
const dom=new JSDOM(fs.readFileSync(path.join(root,'temp.html'),'utf8'),{runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:vc,beforeParse(w){ for(const configModule of ['config-format.js','config-editor.js'])w.eval(require('node:fs').readFileSync(require('node:path').resolve(__dirname,"..",configModule),'utf8'));w.confirm=()=>true;for(const f of ['demo-plan.js','demo-configs.js','windows-elements.js'])w.eval(fs.readFileSync(path.join(root,f),'utf8'));}});
const w=dom.window,q=s=>w.document.querySelector(s),all=s=>[...w.document.querySelectorAll(s)],ev=s=>w.eval(s);
const originalOpen=w.WindowsElements.open;
w.WindowsElements.open=options=>{opened.push(options);return originalOpen(options);};
const check=(name,run)=>{try{run();checks.push({name,passed:true});}catch(e){checks.push({name,passed:false,error:e.stack});}};
const setInput=(field,raw)=>{assert.ok(field,'input must exist');field.value=raw;field.dispatchEvent(new w.Event('input',{bubbles:true}));};
const rows=()=>all('#elementList .element-row'),values=()=>rows().map(n=>n.querySelector('.element-input').value);
const serialized=()=>q('#elementSerialized').textContent,historyLength=()=>ev('history.length');
const state=()=>ev('JSON.stringify({apps,selected,profileChoice,checked:[...checked]})');
const draftState=()=>JSON.stringify(JSON.parse(state()),(name,value)=>name==='removalState'?undefined:value);
const windowsState=()=>ev('JSON.stringify(windows)');
const visible=node=>!!node&&!node.closest('[hidden]')&&w.getComputedStyle(node).display!=='none';
const targetNode=(selector,target)=>all(selector).find(n=>(n.dataset.windowsValue??n.dataset.windowsEdit)===target);
const pageValue=target=>targetNode('[data-windows-value]',target);
const pageRaw=target=>all('[data-windows-raw]').find(node=>node.dataset.windowsRaw===target).textContent;
function setup(raw='X;A;C;Y',old='A;C',desired='A;E'){
 ev('closeModal();reset(true)');opened.length=0;
 ev('selected=1;focusedVariable=1;const v=scheme(1).vars[1];v.defaultValue='+JSON.stringify(old)+';v.baselineValue=v.defaultValue;v.mode="-append";windows["User|PATH"]='+JSON.stringify(raw)+';render()');
 if(desired!==undefined)setInput(q('#varRows [data-row="1"] .vinput'),desired);
 q('[data-view="windows"]').click();
}
function enter(target='User|PATH',source='button'){
 const node=targetNode(source==='button'?'[data-windows-edit]':'[data-windows-value]',target);assert.ok(node,'Windows entry must exist: '+target);node.focus();
 if(source==='button')node.click();
 else if(source==='double-click')node.dispatchEvent(new w.MouseEvent('dblclick',{bubbles:true}));
 else node.dispatchEvent(new w.KeyboardEvent('keydown',{key:source,bubbles:true,cancelable:true}));
 assert.ok(q('#modal').classList.contains('open'),'entry opens editor');return opened.at(-1);
}
function noSchemeContext(){
 const pending=q('#pendingElements');assert.ok(!visible(pending),'scheme suggestion region is absent or hidden');
 assert.equal(all('#modal [data-add-pending],#modal [data-remove-pending]').length,0);
 assert.ok(!visible(q('#modal .legend-match')),'match legend is absent or hidden');
 assert.ok(!visible(q('#modal [data-legend-count="match"]')),'match counter is absent or hidden');
 assert.equal(all('#modal .is-matched,#modal .diff-label.is-match').length,0);
 assert.doesNotMatch(q('#elementStatus').textContent,/方案建议|待追加|待删除|冲突/);
}
function assertIndependentOptions(options,target='User|PATH'){
 const [scope,name]=target.split('|');assert.equal(options.scope,scope);assert.equal(options.name.toUpperCase(),name);
 assert.equal(options.raw,ev('windows['+JSON.stringify(target)+']'));
 assert.deepEqual(Array.from(options.selectedValues??[]),[],'Windows page supplies no selected scheme values');
 assert.ok(options.selectionPlan==null,'Windows page supplies no selection plan');
 assert.ok(options.appendEntries==null||options.appendEntries.length===0,'Windows page supplies no append entries');
 assert.deepEqual(Array.from(options.appendValues??[]),[],'Windows page supplies no append suggestions');
 assert.equal(options.snapshot.raw,options.raw);assert.equal(options.snapshot.exists,true);
}
function declare(scope,name,special,profile=1){
 ev('apps[1].profiles['+profile+'].vars.push(mkVar('+JSON.stringify([name,scope,'replace','UNRELATED;SCHEME',{special}])+',99))');
}
check('Windows page shows escaped snapshot text with original spaces and CRLF, without scheme overlays',()=>{
 const raw='  <img src=x onerror="evil()"> & \'quoted\'\r\nnext\rline\nlast ;;A;A; ';setup(raw);
 assert.equal(pageRaw('User|PATH'),raw);assert.equal(pageValue('User|PATH').querySelector('img,script,mark'),null);
 assert.equal(all('#screen mark,#screen .pending-add,#screen .pending-remove,#screen .pending-elements,#screen .match-legend,#screen .is-matched,#screen .is-conflict').length,0);
 assert.doesNotMatch(q('#screen').textContent,/高亮为当前准备方案|建议追加|建议删除|待追加|待删除|方案冲突/);
});
check('Current values use comparison plates directly; raw text starts collapsed without a second structure layer',()=>{
 setup(' A\r\nB;;A;A;');const host=pageValue('User|PATH');
 assert.deepEqual(Array.from(host.querySelectorAll('[data-raw-value]'),n=>n.dataset.rawValue),[' A\r\nB','','A','A','']);
 assert.equal(host.querySelectorAll('.comparison-static-token').length,5);
 assert.equal(host.querySelectorAll('.token-position').length,0);
 assert.equal(host.querySelectorAll('.comparison-static-token.is-duplicate').length,4);
 assert.ok(all('[data-windows-raw]').every(n=>n.hidden));
 assert.doesNotMatch(q('#screen').textContent,/快照结构/);
});
check('Raw disclosure keeps exact whitespace and line endings and updates its accessible state',()=>{
 const raw=' \tA\r\nB;;A;';setup(raw);const button=all('[data-windows-show-raw]').find(n=>n.dataset.windowsShowRaw==='User|PATH'),pre=w.document.getElementById(button.getAttribute('aria-controls'));
 button.click();assert.equal(pre.hidden,false);assert.equal(pre.textContent,raw);assert.equal(button.getAttribute('aria-expanded'),'true');assert.equal(button.textContent,'收起原文');
 button.click();assert.equal(pre.hidden,true);assert.equal(button.getAttribute('aria-expanded'),'false');assert.equal(button.textContent,'原文');
});
check('Arrow and Home/End navigation select occurrences without changing snapshot or opening an editor',()=>{
 setup('A;;A;');const before=windowsState(),tokens=Array.from(pageValue('User|PATH').querySelectorAll('button'));
 const key=k=>w.document.activeElement.dispatchEvent(new w.KeyboardEvent('keydown',{key:k,bubbles:true,cancelable:true}));
 tokens[0].focus();key('ArrowRight');assert.equal(w.document.activeElement,tokens[1]);key('End');assert.equal(w.document.activeElement,tokens[3]);key('Home');assert.equal(w.document.activeElement,tokens[0]);
 assert.deepEqual(tokens.map(n=>n.tabIndex),[0,-1,-1,-1]);assert.equal(windowsState(),before);assert.equal(opened.length,0);
});
check('Clicking the second duplicate opens and focuses its exact occurrence; Cancel preserves the value',()=>{
 setup('A;;A;B');const before=windowsState(),tokens=pageValue('User|PATH').querySelectorAll('button');
 tokens[2].click();assertIndependentOptions(opened.at(-1));assert.equal(w.document.activeElement,rows()[2].querySelector('.element-input'));
 setInput(w.document.activeElement,'SECOND');assert.equal(serialized(),'A;;SECOND;B');q('#cancelDialog').click();assert.equal(windowsState(),before);
});
check('Static comparison focus highlights only the selected occurrence, including duplicate values',()=>{
 setup('A;;A');const host=pageValue('User|PATH'),tokens=host.querySelectorAll('button');tokens[2].focus();
 assert.equal(host.querySelectorAll('.is-related-highlight').length,1);assert.ok(tokens[2].classList.contains('is-related-highlight'));assert.ok(!tokens[0].classList.contains('is-related-highlight'));
});
check('Whole Special strings keep one comparison plate even when they contain semicolons',()=>{
 setup();declare('User','SNAPSHOT_COMMAND',true);ev('windows["User|SNAPSHOT_COMMAND"]="CMD;ARGS";render()');
 const host=pageValue('User|SNAPSHOT_COMMAND');assert.equal(host.querySelectorAll('button').length,1);assert.equal(host.querySelector('[data-raw-value]').dataset.rawValue,'CMD;ARGS');host.querySelector('button').click();assert.equal(rows().length,1);assert.equal(w.document.activeElement,rows()[0].querySelector('.element-input'));
});
check('Empty Windows string remains an existing editable value rather than a missing variable',()=>{
 setup('');assert.ok(pageValue('User|PATH'));const o=enter();assertIndependentOptions(o);assert.deepEqual(values(),['']);assert.equal(serialized(),'');assert.ok(q('#applyElements').disabled);noSchemeContext();
});
check('Selected app, profile, checked batch and selected-value changes leave Windows snapshot markup unchanged',()=>{
 setup();const before=all('[data-windows-value]').map(n=>[n.dataset.windowsValue,n.innerHTML]);
 ev('selected=0;profileChoice[0]="JDK17";profileChoice[1]="Python311";checked=new Set([0,1,2]);apps[0].profiles[1].vars[1].custom="DIFFERENT;DRAFT";apps[0].profiles[1].vars[1].source="自定义值";render()');
 assert.deepEqual(all('[data-windows-value]').map(n=>[n.dataset.windowsValue,n.innerHTML]),before);assertIndependentOptions(enter());noSchemeContext();
});
for(const source of ['button','double-click','Enter',' '])check('Real Windows '+JSON.stringify(source)+' entry opens snapshot-only editor',()=>{
 setup();const before=state();const o=enter('User|PATH',source);assertIndependentOptions(o);assert.equal(o.whole,false);assert.deepEqual(values(),['X','A','C','Y']);assert.equal(serialized(),'X;A;C;Y');noSchemeContext();assert.equal(state(),before);
});
check('Windows renderer and entry do not consult current scheme or selected-value planning helpers',()=>{
 setup();const saved={scheme:w.scheme,value:w.value,recordsFor:w.recordsFor};
 try{for(const name of Object.keys(saved))w[name]=()=>{throw new Error('Windows snapshot consulted '+name);};ev('renderWindows()');assertIndependentOptions(enter());noSchemeContext();}
 finally{Object.assign(w,saved);}
});
check('Inactive-profile Special declaration with case-insensitive same scope/name keeps command semicolons whole',()=>{
 setup();declare('User','snapshot_command',true);const raw='  tool.exe --require "C:\\工具\\init;local.js"; echo done\r\n  tail  ';
 ev('windows["User|SNAPSHOT_COMMAND"]='+JSON.stringify(raw)+';render()');
 assert.equal(pageRaw('User|SNAPSHOT_COMMAND'),raw);const o=enter('User|SNAPSHOT_COMMAND');assertIndependentOptions(o,'User|SNAPSHOT_COMMAND');assert.equal(o.whole,true);assert.equal(rows().length,1);assert.equal(serialized(),raw);noSchemeContext();
});
check('Consistent Special declarations across apps keep whole-string editing independent of selection',()=>{
 setup();declare('User','SNAPSHOT_COMMAND',true,0);declare('User','SNAPSHOT_COMMAND',true,1);
 ev('windows["User|SNAPSHOT_COMMAND"]="ONE;TWO";profileChoice[1]="Python313";render()');
 assert.equal(enter('User|SNAPSHOT_COMMAND').whole,true);assert.deepEqual(values(),['ONE;TWO']);
});
check('Same-name Special in other scope does not make ordinary current-scope variable whole',()=>{
 setup();declare('System','SCOPE_COMMAND',true);declare('User','SCOPE_COMMAND',false);
 ev('windows["User|SCOPE_COMMAND"]="ONE;TWO";windows["System|SCOPE_COMMAND"]="CMD;ARGS";render()');
 assert.equal(enter('User|SCOPE_COMMAND').whole,false);assert.deepEqual(values(),['ONE','TWO']);q('#cancelDialog').click();
 assert.equal(enter('System|SCOPE_COMMAND').whole,true);assert.deepEqual(values(),['CMD;ARGS']);
});
check('Different-name Special and ordinary replace-mode declaration do not disable element editing',()=>{
 setup();declare('User','OTHER_COMMAND',true);declare('User','PLAIN_COMMAND',false);
 ev('windows["User|PLAIN_COMMAND"]="ONE;;TWO";render()');assert.equal(enter('User|PLAIN_COMMAND').whole,false);assert.deepEqual(values(),['ONE','','TWO']);assert.ok(!q('#addElement').hidden);
});
check('Undeclared snapshot variable remains editable as individual elements',()=>{
 setup();ev('windows["User|UNDECLARED"]="A;;A;";render()');const o=enter('User|UNDECLARED');assertIndependentOptions(o,'User|UNDECLARED');assert.equal(o.whole,false);assert.deepEqual(values(),['A','','A','']);noSchemeContext();
});
check('Untouched raw CRLF, tabs, repeated elements and empty slices survive add and Apply exactly',()=>{
 const raw=' \tA\r\nB ;;A;A;\t;';setup(raw);const before=draftState(),n=historyLength();enter();assert.equal(serialized(),raw);q('#addElement').click();setInput(rows().at(-1).querySelector('.element-input'),'TAIL');assert.equal(serialized(),raw+';TAIL');noSchemeContext();
 q('#applyElements').click();assert.equal(ev('windows["User|PATH"]'),raw+';TAIL');assert.equal(draftState(),before);assert.equal(historyLength(),n+1);
 const c=ev('history[0].changes[0]');assert.equal(c.before,raw);assert.equal(c.after,raw+';TAIL');assert.equal(c.plan.before.raw,raw);assert.equal(c.plan.after.raw,raw+';TAIL');
});
check('Manual replacement, move, delete, per-row recovery and reset remain local until Apply',()=>{
 setup('A;;A;B');const before=windowsState(),draft=state(),n=historyLength();enter();const ids=rows().map(n=>n.dataset.elementId),get=id=>q('#elementList .element-row[data-element-id="'+id+'"]');
 setInput(get(ids[3]).querySelector('.element-input'),'EDIT');get(ids[3]).querySelector('[data-move-up]').click();assert.equal(serialized(),'A;;EDIT;A');
 get(ids[1]).querySelector('[data-delete]').click();assert.equal(serialized(),'A;EDIT;A');q('[data-restore-ghost="'+ids[1]+'"]').click();assert.equal(serialized(),'A;;EDIT;A');
 q('#addElement').click();setInput(rows().at(-1).querySelector('.element-input'),'NEW');assert.equal(serialized(),'A;;EDIT;A;NEW');noSchemeContext();
 assert.equal(windowsState(),before);assert.equal(historyLength(),n);assert.equal(state(),draft);q('#restoreElements').click();assert.equal(serialized(),'A;;A;B');assert.deepEqual(rows().map(n=>n.dataset.elementId),ids);assert.ok(q('#applyElements').disabled);noSchemeContext();
});
check('Cancel discards direct edits and leaves workbench drafts and history unchanged',()=>{
 setup();const before=windowsState(),draft=state(),n=historyLength();enter();setInput(rows()[0].querySelector('.element-input'),'MANUAL');q('#cancelDialog').click();assert.equal(windowsState(),before);assert.equal(historyLength(),n);assert.equal(state(),draft);
 enter();assert.equal(serialized(),'X;A;C;Y');assert.ok(q('#applyElements').disabled);
});
check('Direct Apply records only manual operations and factual History while preserving workbench drafts',()=>{
 setup();const draft=draftState(),n=historyLength();enter();setInput(rows()[2].querySelector('.element-input'),'MANUAL');q('#applyElements').click();assert.equal(ev('windows["User|PATH"]'),'X;A;MANUAL;Y');assert.equal(draftState(),draft);assert.equal(historyLength(),n+1);
 const c=ev('history[0].changes[0]');assert.equal(c.before,'X;A;C;Y');assert.equal(c.after,'X;A;MANUAL;Y');assert.deepEqual(Array.from(c.plan.operations,o=>o.action),['REPLACE']);assert.ok(Object.isFrozen(c));
 q('[data-view="history"]').click();assert.ok(q('.history-details'));assert.equal(q('.history-before').textContent,'X;A;C;Y');assert.equal(q('.history-after').textContent,'X;A;MANUAL;Y');assert.equal(draftState(),draft);
});
check('Direct empty-element editing preserves empty-variable state and explicit last-row deletion removes it',()=>{
 setup('A;');enter();rows()[0].querySelector('[data-delete]').click();assert.equal(serialized(),'');q('#applyElements').click();assert.equal(ev('Object.hasOwn(windows,"User|PATH")'),true);assert.equal(ev('windows["User|PATH"]'),'');
 enter();rows()[0].querySelector('[data-delete]').click();assert.equal(serialized(),'（不存在）');q('#applyElements').click();assert.equal(ev('Object.hasOwn(windows,"User|PATH")'),false);assert.equal(ev('history[0].changes[0].plan.after.exists'),false);
});
check('Whole Special direct manual edit applies complete string and records whole audit without draft changes',()=>{
 setup();declare('User','SNAPSHOT_COMMAND',true);ev('windows["User|SNAPSHOT_COMMAND"]="OLD;ARGS";render()');const draft=state();enter('User|SNAPSHOT_COMMAND');setInput(rows()[0].querySelector('.element-input'),'NEW;ARGS\nNEXT');noSchemeContext();q('#applyElements').click();assert.equal(ev('windows["User|SNAPSHOT_COMMAND"]'),'NEW;ARGS\nNEXT');assert.equal(ev('history[0].changes[0].plan.special'),true);assert.equal(state(),draft);
});
check('Snapshot raw change rejects direct Apply and creates no successful history',()=>{
 setup();enter();const n=historyLength(),draft=state();setInput(rows()[0].querySelector('.element-input'),'MANUAL');ev('windows["User|PATH"]="EXTERNAL";targetRevisions["User|PATH"]=1');q('#applyElements').click();assert.equal(ev('windows["User|PATH"]'),'EXTERNAL');assert.equal(historyLength(),n);assert.equal(state(),draft);assert.match(q('#toast').textContent,/失效/);
});
check('Snapshot revision change rejects direct Apply even when original raw is equal',()=>{
 setup();enter();const n=historyLength();setInput(rows()[0].querySelector('.element-input'),'MANUAL');ev('targetRevisions["User|PATH"]=1');q('#applyElements').click();assert.equal(ev('windows["User|PATH"]'),'X;A;C;Y');assert.equal(historyLength(),n);assert.match(q('#toast').textContent,/失效/);
});
check('Workbench still exposes scheme suggestions and previews the selected-value plan',()=>{
 setup();q('[data-view="workbench"]').click();q('#editWindowsElements').click();assert.ok(visible(q('#pendingElements')));assert.ok(q('[data-add-pending]'));assert.ok(q('[data-remove-pending]'));assert.ok(visible(q('.legend-match')));q('#cancelDialog').click();
 ev('showPreview("应用","Snapshot regression",[{app:1,profile:"Python313",varIdx:1}])');const p=ev('modalAction.rows[0].plan');assert.equal(p.valid,true);assert.equal(p.after.raw,'X;A;Y;E');assert.ok(!q('#confirmDialog').disabled);q('#cancelDialog').click();
});
check('Workbench batch preview still blocks incompatible Special and ordinary target requests',()=>{
 setup();q('[data-view="workbench"]').click();ev('scheme(0).vars[1].special=true;scheme(0).vars[1].mode="replace";checked=new Set([0,1]);render()');q('#previewButton').click();
 const p=ev('modalAction.rows.find(r=>r.v.scope==="User"&&r.v.name.toUpperCase()==="PATH").plan');assert.equal(p.valid,false);assert.ok(Array.from(p.issues).some(issue=>issue.reason==='conflict'));assert.ok(q('#confirmDialog').disabled);q('#cancelDialog').click();
});
check('Windows page remains independent even when selected workbench draft has stale baseline',()=>{
 setup();ev('windows["User|PATH"]="EXTERNAL;RAW";targetRevisions["User|PATH"]=1;render()');assert.equal(pageRaw('User|PATH'),'EXTERNAL;RAW');assert.doesNotMatch(q('#screen').textContent,/计划已失效|方案建议已失效|冲突/);assertIndependentOptions(enter());noSchemeContext();setInput(rows()[0].querySelector('.element-input'),'DIRECT');q('#applyElements').click();assert.equal(ev('windows["User|PATH"]'),'DIRECT;RAW');
});
check('No JSDOM runtime errors in Windows snapshot and workbench regression workflows',()=>assert.deepEqual(errors,[]));
console.log(JSON.stringify({passed:checks.filter(c=>c.passed).length,total:checks.length,checks},null,2));if(checks.some(c=>!c.passed))process.exitCode=1;dom.window.close();
