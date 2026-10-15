'use strict';
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const context=vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname,'..','demo-plan.js'),'utf8'),context);
const P=context.DemoPlan,target={scope:'User',name:'Path'},checks=[];
const snap=(raw,revision=4)=>({exists:raw!==undefined,raw:raw??null,type:'REG_EXPAND_SZ',revision});
const baseline=(raw,selected)=>P.captureBaseline({target,mode:'-append',snapshot:snap(raw),selectedRaw:selected});
const state=b=>P.createRemovalState(b);
const build=(b,s,raw,selected,protectedValues=[])=>P.build({target,mode:'-append',snapshot:raw,selectedRaw:selected,baseline:b,removalState:s,protectedValues});
const types=p=>Array.from(p.operations,operation=>operation.type);
function check(name,run){try{run();checks.push({name,passed:true});}catch(error){checks.push({name,passed:false,error:error.stack});}}

check('Known Windows edit moves original unique deletion identity without changing the original baseline',()=>{
 const b=baseline('A;B;C','A;B;C'),s=state(b),current=snap('A;B;D',5);
 const audit={before:'A;B;C',after:'A;B;D',whole:false,elements:[
  {elementId:1,before:'A',after:'A',beforeIndex:0,afterIndex:0,added:false,removed:false,replaced:false},
  {elementId:2,before:'B',after:'B',beforeIndex:1,afterIndex:1,added:false,removed:false,replaced:false},
  {elementId:3,before:'C',after:'D',beforeIndex:2,afterIndex:2,added:false,removed:false,replaced:true}
 ]};
 const edit=P.buildElementEdit({target,snapshot:snap('A;B;C'),afterRaw:'A;B;D',audit});
 const advanced=P.advanceRemovalState({baseline:b,state:s,plan:edit,snapshot:current});
 assert.strictEqual(b.snapshot.raw,'A;B;C');assert.notStrictEqual(advanced,s);assert.ok(Object.isFrozen(advanced));
 const plan=build(b,advanced,current,'A;E');assert.equal(plan.valid,true);assert.equal(plan.afterRaw,'A;D;E');assert.deepEqual(types(plan),['remove','insert']);assert.equal(plan.operations[0].value,'B');
});
check('Unknown write, ABA revision, and replaced original never reacquire deletion eligibility',()=>{
 for(const raw of ['A;B;C;EXTERNAL','A;B;C']){
  const b=baseline('A;B;C','A;B;C'),unknown=P.syncRemovalState({baseline:b,state:state(b),snapshot:snap(raw,5)});
  const returned=P.syncRemovalState({baseline:b,state:unknown,snapshot:snap('A;B;C',6)});
  for(const [s,current] of [[unknown,snap(raw,5)],[returned,snap('A;B;C',6)]]){
   const plan=build(b,s,current,'A;C;E');assert.equal(plan.valid,true);assert.equal(plan.operations.some(op=>op.type==='remove'),false);assert.ok(plan.issues.some(issue=>issue.reason==='continuity'));assert.equal(plan.operations.at(-1).action,'ADD');
  }
 }
});
check('Initially duplicated original and later duplicated actual remain blocked',()=>{
 const duplicated=baseline('A;B;B','A;B'),p1=build(duplicated,state(duplicated),snap('A;B;B'),'A');
 assert.equal(p1.operations.length,0);assert.ok(p1.issues.some(issue=>issue.reason==='duplicate'));
 const unique=baseline('A;B','A;B'),later=P.syncRemovalState({baseline:unique,state:state(unique),snapshot:snap('A;B;B',5)});
 const p2=build(unique,later,snap('A;B;B',5),'A');assert.equal(p2.operations.length,0);assert.ok(p2.issues.some(issue=>issue.reason==='duplicate'));
});
check('Shared or unresolved deletion preserves a safe addition and reports the pending issue',()=>{
 const b=baseline('A;B','A;B'),plan=build(b,state(b),snap('A;B'),'A;E',['B']);
 assert.equal(plan.valid,true);assert.equal(plan.afterRaw,'A;B;E');assert.deepEqual(types(plan),['insert']);assert.ok(plan.issues.some(issue=>issue.reason==='shared'));
});
check('Mode and target still invalidate a captured baseline after snapshot refresh',()=>{
 const b=baseline('A;B','A;B'),s=state(b);
 for(const override of [{mode:'+append'},{target:{scope:'System',name:'Path'}},{whole:true}]){
  const p=P.build({target,mode:'-append',snapshot:snap('A;B'),selectedRaw:'A',baseline:b,removalState:s,...override});
  assert.equal(p.valid,false);assert.equal(p.operations.length,0);
 }
});
check('Gone old value resolves deletion intent without an issue, including initial duplicates',()=>{
 for(const initial of ['A;B','A;B;B']){
  const b=baseline(initial,'A;B'),current=snap('A;C',5);
  const s=P.syncRemovalState({baseline:b,state:state(b),snapshot:current});
  const p=build(b,s,current,'A;C');
  assert.deepEqual(types(p),[]);assert.equal(p.issues.length,0);assert.equal(p.afterRaw,'A;C');
 }
});
check('Direct B to C replacement completes old B deletion intent and preserves C',()=>{
 const b=baseline('A;B','A;B'),audit={before:'A;B',after:'A;C',whole:false,elements:[
  {elementId:1,before:'A',after:'A',beforeIndex:0,afterIndex:0,added:false,removed:false,replaced:false},
  {elementId:2,before:'B',after:'C',beforeIndex:1,afterIndex:1,added:false,removed:false,replaced:true}
 ]};
 const edit=P.buildElementEdit({target,snapshot:snap('A;B'),afterRaw:'A;C',audit}),current=snap('A;C',5);
 const s=P.advanceRemovalState({baseline:b,state:state(b),plan:edit,snapshot:current});
 const p=build(b,s,current,'A;C');
 assert.deepEqual(types(p),[]);assert.equal(p.issues.length,0);assert.equal(p.afterRaw,'A;C');
});
check('Canonical target case allows continuity while skipped revision revokes it',()=>{
 const b=baseline('A;B','A;B'),plan=P.build({target:{scope:'User',name:'PATH'},snapshot:snap('A;B'),mode:'+append',selectedRaw:'X'});
 const current=snap('X;A;B',5),s=P.advanceRemovalState({baseline:b,state:state(b),plan,snapshot:current});
 const p=build(b,s,current,'A');assert.deepEqual(types(p),['remove']);assert.equal(p.operations[0].beforeIndex,2);
 const skipped=P.advanceRemovalState({baseline:b,state:state(b),plan,snapshot:snap('X;A;B',6)});
 assert.equal(build(b,skipped,snap('X;A;B',6),'A').operations.some(op=>op.type==='remove'),false);
});
check('Forged whole audit before or after cannot propagate element continuity',()=>{
 const b=baseline('A;B','A;B'),audit={before:'A;B',after:'X;A;B',whole:false,elements:[
  {elementId:1,before:'A',after:'A',beforeIndex:0,afterIndex:1,added:false,removed:false,replaced:false},
  {elementId:2,before:'B',after:'B',beforeIndex:1,afterIndex:2,added:false,removed:false,replaced:false},
  {elementId:3,before:undefined,after:'X',beforeIndex:null,afterIndex:0,added:true,removed:false,replaced:false}
 ]};
 const plan=P.buildElementEdit({target,snapshot:snap('A;B'),afterRaw:'X;A;B',audit});
 for(const field of ['before','after']){
  const forged=JSON.parse(JSON.stringify(plan));forged.audit[field]='wrong';
  const s=P.advanceRemovalState({baseline:b,state:state(b),plan:forged,snapshot:snap('X;A;B',5)});
  assert.equal(build(b,s,snap('X;A;B',5),'A').operations.some(op=>op.type==='remove'),false);
 }
});
check('Initial duplicate becoming singleton and initially missing value appearing do not gain authority',()=>{
 const duplicate=baseline('A;B;B','A;B'),single=snap('A;B',5);
 const s=P.syncRemovalState({baseline:duplicate,state:state(duplicate),snapshot:single});
 const p=build(duplicate,s,single,'A');
 assert.equal(p.operations.length,0);assert.deepEqual(Array.from(p.issues[0].actualIndexes),[1]);
 const missing=baseline('A','A;B'),appeared=snap('A;B',5),m=P.syncRemovalState({baseline:missing,state:state(missing),snapshot:appeared});
 assert.equal(build(missing,m,appeared,'A').operations.length,0);
});
check('Incomplete element audit cannot claim continuity for omitted old occurrence',()=>{
 const b=baseline('A;B','A;B'),audit={before:'A;B',after:'X;A;B',whole:false,elements:[
  {elementId:1,before:'A',after:'A',beforeIndex:0,afterIndex:1,added:false,removed:false,replaced:false},
  {elementId:3,before:undefined,after:'X',beforeIndex:null,afterIndex:0,added:true,removed:false,replaced:false}
 ]};
 const plan=P.buildElementEdit({target,snapshot:snap('A;B'),afterRaw:'X;A;B',audit}),current=snap('X;A;B',5);
 const s=P.advanceRemovalState({baseline:b,state:state(b),plan,snapshot:current});
 assert.equal(build(b,s,current,'A').operations.some(op=>op.type==='remove'),false);
});
check('Batch head insert keeps each request state and maps prior occurrence positions',()=>{
 const b=baseline('A;B','A;B'),plan=P.buildBatch({target,snapshot:snap('A;B'),requests:[
  {id:'first',mode:'+append',selectedRaw:'X'},
  {id:'second',mode:'+append',selectedRaw:'Y'}
 ]});
 assert.equal(plan.afterRaw,'Y;X;A;B');
 const current=snap(plan.afterRaw,5),s=P.advanceRemovalState({baseline:b,state:state(b),plan,snapshot:current});
 const result=build(b,s,current,'A');
 assert.deepEqual(types(result),['remove']);assert.equal(result.operations[0].beforeIndex,3);
});
check('Removed old occurrence stays unauthorized after the same value is added again',()=>{
 const b=baseline('A;B','A;B'),first=build(b,state(b),snap('A;B'),'A');
 const afterRemoval=snap('A',5),once=P.advanceRemovalState({baseline:b,state:state(b),plan:first,snapshot:afterRemoval});
 const second=P.build({target,mode:'-append',snapshot:afterRemoval,selectedRaw:'B'});
 const restored=snap('A;B',6),twice=P.advanceRemovalState({baseline:b,state:once,plan:second,snapshot:restored});
 const result=build(b,twice,restored,'A');
 assert.deepEqual(types(result),[]);assert.ok(result.issues.some(issue=>issue.reason==='continuity'));
});
check('Replaced old occurrence stays unauthorized after a new same-value addition',()=>{
 const b=baseline('A;B','A;B'),audit={before:'A;B',after:'A;X',whole:false,elements:[
  {elementId:1,before:'A',after:'A',beforeIndex:0,afterIndex:0,added:false,removed:false,replaced:false},
  {elementId:2,before:'B',after:'X',beforeIndex:1,afterIndex:1,added:false,removed:false,replaced:true}
 ]};
 const edit=P.buildElementEdit({target,snapshot:snap('A;B'),afterRaw:'A;X',audit});
 const once=P.advanceRemovalState({baseline:b,state:state(b),plan:edit,snapshot:snap('A;X',5)});
 const add=P.build({target,mode:'-append',snapshot:snap('A;X',5),selectedRaw:'B'});
 const current=snap('A;X;B',6),twice=P.advanceRemovalState({baseline:b,state:once,plan:add,snapshot:current});
 const result=build(b,twice,current,'A');
 assert.deepEqual(types(result),[]);assert.ok(result.issues.some(issue=>issue.reason==='continuity'));
});
check('Duplicate audit index cannot establish occurrence continuity',()=>{
 const b=baseline('A;B','A;B'),audit={before:'A;B',after:'X;A;B',whole:false,elements:[
  {elementId:1,before:'A',after:'A',beforeIndex:0,afterIndex:1,added:false,removed:false,replaced:false,moved:true},
  {elementId:2,before:'A',after:'B',beforeIndex:0,afterIndex:2,added:false,removed:false,replaced:true,moved:true},
  {elementId:3,before:undefined,after:'X',beforeIndex:null,afterIndex:0,added:true,removed:false,replaced:false}
 ]};
 const plan=P.buildElementEdit({target,snapshot:snap('A;B'),afterRaw:'X;A;B',audit}),current=snap('X;A;B',5);
 const s=P.advanceRemovalState({baseline:b,state:state(b),plan,snapshot:current});
 assert.equal(build(b,s,current,'A').operations.some(op=>op.type==='remove'),false);
});
check('Head insertion and deletion retain raw whitespace, CRLF, and empty slices',()=>{
 const b=baseline(' A \r\n;B;;','A;B'),head=P.build({target,mode:'+append',snapshot:snap(' A \r\n;B;;'),selectedRaw:'X'});
 assert.equal(head.afterRaw,'X; A \r\n;B;;');
 const current=snap(head.afterRaw,5),s=P.advanceRemovalState({baseline:b,state:state(b),plan:head,snapshot:current});
 const p=build(b,s,current,'A');
 assert.equal(p.operations[0].beforeIndex,2);assert.equal(p.operations[0].value,'B');
 assert.equal(p.afterRaw,'X; A \r\n;;');
});
console.log(JSON.stringify({passed:checks.filter(c=>c.passed).length,total:checks.length,checks},null,2));
if(checks.some(c=>!c.passed))process.exitCode=1;
