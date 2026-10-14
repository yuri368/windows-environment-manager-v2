'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require('jsdom');
const root=path.resolve(__dirname,'..'),checks=[],errors=[];
const vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
const dom=new JSDOM(fs.readFileSync(path.join(root,'temp.html'),'utf8'),{runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:vc,beforeParse(w){w.confirm=()=>true;for(const f of ['demo-plan.js','demo-configs.js','windows-elements.js'])w.eval(fs.readFileSync(path.join(root,f),'utf8'));}});
const w=dom.window,P=w.DemoPlan,q=s=>w.document.querySelector(s),all=s=>[...w.document.querySelectorAll(s)],ev=s=>w.eval(s);
const target={scope:'User',name:'Path'},snapshot=raw=>({exists:raw!==undefined,raw:raw??null,type:'REG_EXPAND_SZ',revision:0});
function edit(raw,old,next,mode='-append'){const s=snapshot(raw);return P.build({target,mode,snapshot:s,selectedRaw:next,baseline:P.captureBaseline({target,mode,snapshot:s,selectedRaw:old})});}
function check(name,run){try{run();checks.push({name,passed:true});}catch(e){checks.push({name,passed:false,error:e.stack});}}
function setup(actual='X;A;B;C;Y',desired='A;B;C;D',mode='-append'){
 ev('closeModal();reset(true)');ev('selected=1;focusedVariable=1;const v=scheme(1).vars[1];v.defaultValue='+JSON.stringify(desired)+';v.baselineValue=v.defaultValue;v.mode='+JSON.stringify(mode)+";windows['User|PATH']="+JSON.stringify(actual)+';render()');
}
function input(raw){const f=q('#varRows [data-row="1"] .vinput');f.value=raw;f.dispatchEvent(new w.Event('input',{bubbles:true}));}
const plan=()=>ev('demoPlan(scheme(1).vars[1])');
const rawPane=id=>all('#'+id+' .diff-value[data-raw-value]').filter(n=>!n.closest('.is-ghost')).map(n=>n.dataset.rawValue).join(';');
const rawTokens=selector=>all(selector+' .diff-value[data-raw-value]').map(n=>n.dataset.rawValue);
const preview=()=>ev("showPreview('应用','三值验证',[{app:1,profile:'Python313',varIdx:1}])");
function editor(raw,after,elements){return P.buildElementEdit({target,snapshot:snapshot(raw),afterRaw:after,audit:{before:raw,after,whole:false,elements}});}
const row=(id,before,after,bi,ai,flags={})=>({elementId:id,before,after,beforeIndex:bi,afterIndex:ai,...flags});
check('Three labeled value panes replace candidate stacks and exact strings remain authoritative',()=>{
 setup();input('A;B;E;F');assert.equal(q('#comparePending'),null);for(const label of ['当前选定值','Windows 当前值','应用后的值'])assert.ok(all('.inspect-value-heading .k').some(n=>n.textContent===label));
 assert.equal(rawPane('compareDesired'),'A;B;E;F');assert.equal(rawPane('compareActual'),'X;A;B;C;Y');assert.equal(rawPane('compareAfter'),plan().after.raw);assert.equal(q('#compareSerialized').textContent,plan().after.raw);
});
check('Actual removal stays at original position; After ghost is flagged and excluded from serialization',()=>{
 setup();input('A;B;E;F');const actual=q('#compareActual [data-before-index="3"]'),ghost=q('#compareAfter [data-before-index="3"]');assert.ok(actual.classList.contains('is-removed'));assert.match(actual.textContent,/C.*待删除/);assert.ok(ghost.classList.contains('is-ghost'));assert.match(ghost.textContent,/C.*结果排除/);assert.equal(rawPane('compareAfter'),'X;A;B;Y;E;F');assert.deepEqual(rawTokens('#compareAfter'),['X','A','B','C','Y','E','F']);
});
for(const mode of ['+append','-append'])check(mode+' additions are annotated at definite result positions',()=>{
 setup('X;A;B;Y','A;B',mode);input('A;E;F');const expected=mode==='+append'?'E;F;X;A;Y':'X;A;Y;E;F';assert.equal(rawPane('compareAfter'),expected);assert.equal(q('#compareSerialized').textContent,expected);assert.deepEqual(rawTokens('#compareAfter .is-added'),['E','F']);assert.deepEqual(rawTokens('#compareDesired .is-added'),['E','F']);assert.equal(all('#compareActual .is-added').length,0);
});
check('Unchanged plans show every original whitespace empty and repeated occurrence',()=>{
 setup(' X ;;A;A; ','A');assert.equal(rawPane('compareActual'),' X ;;A;A; ');assert.equal(rawPane('compareAfter'),' X ;;A;A; ');assert.equal(all('#compareAfter .comparison-token').length,5);assert.equal(all('#compareAfter .is-ghost').length,0);assert.equal(all('#compareAfter .is-added').length,0);
});
check('Duplicate occurrences retain separate identities after preceding removal',()=>{
 setup('A;B;B;C','A');input('E');const a=q('#compareActual [data-before-index="1"]'),b=q('#compareActual [data-before-index="2"]');assert.notEqual(a.dataset.relatedKey,b.dataset.relatedKey);a.dispatchEvent(new w.Event('pointerover',{bubbles:true}));assert.equal(all('.value-comparison .is-related-highlight').length,2);assert.ok(q('#compareAfter [data-before-index="1"]').classList.contains('is-related-highlight'));assert.ok(!b.classList.contains('is-related-highlight'));assert.equal(rawPane('compareAfter'),'B;B;C;E');
});
check('Focus links definite ADD to Desired but never to deleted similar old text',()=>{
 setup('A;B','B');input('B1');const added=q('#compareAfter .is-added'),removed=q('#compareActual .is-removed');added.focus();assert.equal(all('.value-comparison .is-related-highlight').length,2);assert.ok(q('#compareDesired .is-added').classList.contains('is-related-highlight'));assert.ok(!removed.classList.contains('is-related-highlight'));removed.dispatchEvent(new w.Event('pointerover',{bubbles:true}));assert.ok(q('#compareAfter .is-ghost').classList.contains('is-related-highlight'));assert.ok(!added.classList.contains('is-related-highlight'));
});
check('Invalid plans preserve input and current snapshot but expose no definite result or operation badges',()=>{
 setup();input('A;E');ev("windows['User|PATH']+=';EXTERNAL';targetRevisions['User|PATH']=1;render()");assert.equal(plan().valid,false);assert.equal(rawPane('compareDesired'),'A;E');assert.equal(rawPane('compareActual'),'X;A;B;C;Y;EXTERNAL');assert.match(q('#compareAfter').textContent,/计划已失效/);assert.match(q('#compareSerialized').textContent,/计划已失效/);assert.equal(all('.value-comparison .is-added,.value-comparison .is-removed').length,0);preview();assert.ok(q('#confirmDialog').disabled);q('#cancelDialog').click();
});
check('Whole replacement preserves complete semicolon raw without fake element mapping',()=>{
 setup('A;B','A;B','replace');input('C;D');assert.equal(all('#compareActual .comparison-token').length,1);assert.equal(all('#compareAfter .comparison-token').length,1);assert.equal(rawPane('compareAfter'),'C;D');assert.match(q('#compareAfter').textContent,/整体待替换/);assert.equal(all('#compareAfter .is-ghost').length,0);
});
check('Single and batch removal of last actual element produce nonexistent state',()=>{
 const single=edit('A','A','');assert.equal(single.after.exists,false);assert.equal(single.after.raw,null);const s=snapshot('A'),b=P.captureBaseline({target,mode:'-append',snapshot:s,selectedRaw:'A'});const batch=P.buildBatch({target,snapshot:s,requests:[{id:'a',mode:'-append',selectedRaw:'',baseline:b}]});assert.equal(batch.after.exists,false);assert.equal(batch.after.raw,null);
});
check('Removing A from A; preserves original empty slice and existing empty value',()=>{const p=edit('A;','A','');assert.equal(p.after.exists,true);assert.equal(p.after.raw,'');});
check('Explicit clear row and new empty row create existing empty values',()=>{
 for(const p of [editor('A','',[row(1,'A','',0,0,{replaced:true})]),editor(undefined,'',[row(2,undefined,'',null,0,{added:true})])]){assert.equal(p.after.exists,true);assert.equal(p.after.raw,'');assert.equal(p.operations.length,1);}
});
check('Explicit delete all rows removes variable even if original sole row was empty',()=>{
 for(const raw of ['A','']){const p=editor(raw,'',[row(1,raw,undefined,0,null,{removed:true})]);assert.equal(p.after.exists,false);assert.equal(p.after.raw,null);assert.equal(p.operations.length,1);}
});
check('Mixed Special flags reject even identical whole replacement requests in either order',()=>{
 for(const flags of [[false,true],[true,false]]){const p=P.buildBatch({target,snapshot:snapshot('OLD'),requests:flags.map((whole,i)=>({id:String(i),whole,mode:'replace',selectedRaw:'NEW'}))});assert.equal(p.valid,false);assert.equal(p.operations.length,0);assert.equal(p.after.raw,'OLD');assert.ok(p.issues.some(i=>i.reason==='conflict'));}
});
check('Three values preview application and immutable history use the exact same final plan',()=>{
 setup();input('A;B;E;F');const p=plan();preview();assert.strictEqual(ev('modalAction.rows[0].plan'),p);q('#confirmDialog').click();assert.equal(ev("windows['User|PATH']"),p.after.raw);assert.equal(ev('history[0].changes[0].after'),p.after.raw);assert.strictEqual(ev('history[0].changes[0].plan'),p);assert.equal(rawPane('compareAfter'),p.after.raw);
});
check('Last-row editor delete shows nonexistent serialization and cancel writes nothing',()=>{
 setup('A','A');const n=ev('history.length');q('#editWindowsElements').click();q('[data-delete]').click();assert.equal(q('#elementSerialized').textContent,'（不存在）');assert.ok(!q('#applyElements').disabled);q('#cancelDialog').click();assert.equal(ev("windows['User|PATH']"),'A');assert.equal(ev('history.length'),n);
});
check('Confirmed last-row editor delete records factual missing after without audit empty overwrite',()=>{
 setup('A','A');q('#editWindowsElements').click();q('[data-delete]').click();q('#applyElements').click();assert.equal(ev("Object.hasOwn(windows,'User|PATH')"),false);const c=ev('history[0].changes[0]');assert.equal(c.after,undefined);assert.equal(c.plan.after.exists,false);assert.equal(c.plan.after.raw,null);assert.equal(c.plan.audit.after,'');
});
check('Last-element selected removal shows ghost but serialized missing through confirmation',()=>{
 setup('A','A');input('');assert.equal(plan().after.exists,false);assert.equal(all('#compareAfter .is-ghost').length,1);assert.equal(q('#compareSerialized').textContent,'（不存在）');preview();q('#confirmDialog').click();assert.equal(ev("Object.hasOwn(windows,'User|PATH')"),false);assert.equal(ev('history[0].changes[0].plan.after.exists'),false);
});
check('Tail additions follow original ghost position while head additions precede it',()=>{for(const mode of ['+append','-append']){setup('A','A',mode);input('E');assert.deepEqual(rawTokens('#compareAfter'),mode==='+append'?['E','A']:['A','E']);assert.equal(rawPane('compareAfter'),'E');}});
check('No JSDOM runtime errors in all three-value and missing-state workflows',()=>assert.deepEqual(errors,[]));
console.log(JSON.stringify({passed:checks.filter(c=>c.passed).length,total:checks.length,checks},null,2));if(checks.some(c=>!c.passed))process.exitCode=1;dom.window.close();
