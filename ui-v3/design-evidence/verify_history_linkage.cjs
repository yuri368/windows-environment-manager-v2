'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require('jsdom');
const root=path.resolve(__dirname,'..'),checks=[],errors=[],vc=new VirtualConsole();
vc.on('jsdomError',e=>errors.push(e.message));
const dom=new JSDOM(fs.readFileSync(path.join(root,'temp.html'),'utf8'),{runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:vc,beforeParse(w){w.confirm=()=>true;for(const f of ['demo-plan.js','demo-configs.js','windows-elements.js'])w.eval(fs.readFileSync(path.join(root,f),'utf8'));}});
const w=dom.window,q=s=>w.document.querySelector(s),all=s=>[...w.document.querySelectorAll(s)],ev=s=>w.eval(s);
const check=(name,run)=>{try{run();checks.push({name,passed:true});}catch(e){checks.push({name,passed:false,error:e.stack});}};
function setup(){
 ev('closeModal();reset(true);windows["User|PATH"]="A;;A;B";go("windows")');
 q('[data-windows-edit="User|PATH"]').click();let rows=all('#elementList .element-row');
 const input=rows[2].querySelector('.element-input');input.value='A2';input.dispatchEvent(new w.Event('input',{bubbles:true}));
 rows=all('#elementList .element-row');rows[2].querySelector('[data-move-up]').click();
 all('#elementList .element-row').find(row=>row.querySelector('.element-input').value==='B').querySelector('[data-delete]').click();
 q('#addElement').click();const added=all('#elementList .element-input').at(-1);added.value='TAIL';added.dispatchEvent(new w.Event('input',{bubbles:true}));
 q('#applyElements').click();q('[data-view="history"]').click();
 const details=q('.history-details');details.open=true;details.dispatchEvent(new w.Event('toggle'));
 return ev('history[0].changes[0]');
}
function token(side,index){return q('.history-structure > div:'+(side==='before'?'first':'last')+'-child [data-value-index="'+index+'"]');}
const recordedState=()=>ev('JSON.stringify({apps,windows,history})');
check('Recorded replacement/move preserves occurrence correspondence across duplicate before values',()=>{
 const c=setup(),e=c.elements.find(item=>item.beforeIndex===2),before=token('before',2),after=token('after',e.afterIndex);
 assert.equal(before.dataset.relatedKey,after.dataset.relatedKey);assert.notEqual(before.dataset.relatedKey,token('before',0).dataset.relatedKey);
 assert.ok(before.classList.contains('is-replaced'));assert.ok(after.classList.contains('is-replaced'));assert.match(before.textContent,/替换.*移动/);
 before.focus();const highlighted=all('.history-change .is-related-highlight');assert.equal(highlighted.length,3);assert.ok(highlighted.includes(after));assert.ok(highlighted.includes(q('.history-element-table tr[data-related-key="'+before.dataset.relatedKey+'"]')));
 assert.ok(!token('before',0).classList.contains('is-related-highlight'));
});
check('Recorded deletion stays in Before only; addition stays in After only, each with its own detail row',()=>{
 const c=setup(),removed=c.elements.find(e=>e.removed),added=c.elements.find(e=>e.added),before=token('before',removed.beforeIndex),after=token('after',added.afterIndex);
 assert.ok(before.classList.contains('is-removed'));assert.ok(after.classList.contains('is-added'));
 before.focus();assert.equal(all('.history-change .is-related-highlight').length,2);assert.equal(all('.history-structure > div:last-child .is-related-highlight').length,0);
 after.focus();assert.equal(all('.history-change .is-related-highlight').length,2);assert.equal(all('.history-structure > div:first-child .is-related-highlight').length,0);
});
check('Empty occurrences remain exact ordered slices; snapshot raw text is unchanged by annotations',()=>{
 const c=setup();for(const [side,raw] of [['before',c.before],['after',c.after]]){
  const host=q('.history-structure > div:'+(side==='before'?'first':'last')+'-child');assert.deepEqual(Array.from(host.querySelectorAll('[data-raw-value]'),n=>n.dataset.rawValue),raw.split(';'));
  assert.equal(q('.history-'+side).textContent,raw);
 }
});
check('Arrow navigation in values and detail rows changes focus only and tracks the corresponding elements',()=>{
 setup();const before=recordedState(),rows=all('.history-element-table tbody tr'),first=token('before',0);
 first.focus();first.dispatchEvent(new w.KeyboardEvent('keydown',{key:'End',bubbles:true,cancelable:true}));assert.equal(w.document.activeElement,token('before',3));
 rows[0].focus();rows[0].dispatchEvent(new w.KeyboardEvent('keydown',{key:'End',bubbles:true,cancelable:true}));assert.equal(w.document.activeElement,rows.at(-1));
 assert.equal(rows.filter(row=>row.tabIndex===0).length,1);assert.equal(recordedState(),before);
});
check('Hover takes precedence over focus and leaving restores focus linkage',()=>{
 setup();const first=token('before',0),other=token('before',2);first.focus();other.dispatchEvent(new w.MouseEvent('pointerover',{bubbles:true}));assert.ok(other.classList.contains('is-related-highlight'));assert.ok(!first.classList.contains('is-related-highlight'));
 q('.history-change').dispatchEvent(new w.Event('pointerleave'));assert.ok(first.classList.contains('is-related-highlight'));assert.ok(!other.classList.contains('is-related-highlight'));
});
check('Identical element IDs in separate rendered records do not cross-highlight',()=>{
 setup();ev('history.push(history[0]);renderHistory()');all('.history-details').forEach(details=>{details.open=true;details.dispatchEvent(new w.Event('toggle'))});
 const sections=all('.history-change');sections[0].querySelector('[data-value-index="2"]').focus();assert.equal(sections[0].querySelectorAll('.is-related-highlight').length,3);assert.equal(sections[1].querySelectorAll('.is-related-highlight').length,0);
});
check('History records without element audit do not infer a pairing from identical text or index',()=>{
 const host=w.document.createElement('div');host.innerHTML=w.WindowsElements.historyMarkup('A;A',{side:'before'})+w.WindowsElements.historyMarkup('A;A',{side:'after'});
 const keys=Array.from(host.querySelectorAll('[data-related-key]'),n=>n.dataset.relatedKey);assert.equal(new Set(keys).size,4);assert.equal(host.querySelectorAll('.is-added,.is-removed,.is-replaced,.is-moved').length,0);
});
check('Raw CRLF, HTML-like values and Special semicolons remain display data',()=>{
 for(const whole of [false,true]){const raw=' <img src=x>\r\nA;;A;';const host=w.document.createElement('div');host.innerHTML=w.WindowsElements.historyMarkup(raw,{whole,side:'before'});assert.equal(host.querySelector('img'),null);assert.deepEqual(Array.from(host.querySelectorAll('[data-raw-value]'),n=>n.dataset.rawValue),whole?[raw]:raw.split(';'));}
});
check('Reopening a record rebinds linkage without changing or unfreezing its history data',()=>{
 setup();const before=recordedState(),details=q('.history-details');details.open=false;details.dispatchEvent(new w.Event('toggle'));details.open=true;details.dispatchEvent(new w.Event('toggle'));token('before',2).focus();assert.equal(all('.history-change .is-related-highlight').length,3);assert.equal(recordedState(),before);assert.equal(ev('Object.isFrozen(history[0].changes[0])'),true);
});
check('History linkage workflows produce no JSDOM runtime errors',()=>assert.deepEqual(errors,[]));
console.log(JSON.stringify({passed:checks.filter(c=>c.passed).length,total:checks.length,checks},null,2));if(checks.some(c=>!c.passed))process.exitCode=1;dom.window.close();
