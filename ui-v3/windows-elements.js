/* Element editing and annotations are UI-only. Only serialized live rows are applied. */
var WindowsElements = (() => {
  const escape = text => String(text ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let session = null, sequence = 0;
  const normalized = value => value.trim();
  const matches = (raw,values,whole) => DemoPlan.matches(raw,values,whole);
  function selectionInfo(raw,values=[],whole=false){
    const parts=values.flatMap(selectedRaw=>DemoPlan.build({mode:'-append',whole,actualRaw:raw,selectedRaw}).parts).map((part,index)=>({...part,index}));
    const effective=parts.filter(part=>whole||normalized(part.value)!=='');
    return {parts,total:effective.length,matched:effective.filter(part=>part.matched).length,missing:effective.filter(part=>!part.matched).map(({value,index})=>({value,index}))};
  }
  const isAppendMode = mode => ['+append','-append','append'].includes(mode);
  const pendingToken = (part,mode=null) => {
    const position=mode==='+append'?'开头':'末尾';
    const button=isAppendMode(mode)?`<button type="button" class="pending-add-button" data-add-pending="${part.index}" title="加入当前编辑列表${position}" aria-label="将 ${escape(part.value)} 加入当前编辑列表${position}">＋</button>`:'';
    return `<span class="pending-add-token${button?' is-actionable':''}" data-selected-index="${part.index}"><span class="diff-value" data-raw-value="${escape(part.value)}">${escape(part.value)}</span><span class="diff-label is-pending-add">待追加</span>${button}</span>`;
  };
  function selectionMarkup(raw, values = [], whole = false, append = false, plan = null) {
    if (whole || !append) return escape(values.join(';')) || '<span class="empty-element">（空字符串）</span>';
    if (values.length===1 && values[0]==='') return '<span class="empty-element">（空字符串）</span>';
    const selectedPlan=plan||DemoPlan.build({mode:'-append',actualRaw:raw,selectedRaw:values.join(';')});
    const insertionIndexes=new Set(selectedPlan.operations.filter(op=>op.type==='insert').map(op=>op.selectedIndex));
    return selectedPlan.parts.map(part => {
      if (insertionIndexes.has(part.index)) return pendingToken(part);
      if (part.matched) return `<mark class="matched-element"><span data-raw-value="${escape(part.value)}">${escape(part.value)}</span></mark>`;
      return part.value === '' ? '<span class="empty-element" data-raw-value="">（空元素）</span>' : escape(part.value);
    }).join('<span class="element-separator">;</span>');
  }
  const removalToken=(op,editing=false)=>`<span class="pending-remove-token" data-pending-before-index="${op.beforeIndex}"${op.elementId===undefined?'':` data-element-id="${op.elementId}"`}><span class="diff-value" data-raw-value="${escape(op.value)}">${escape(op.value)}</span><span class="diff-label is-removed">待删除</span>${editing?`<button type="button" class="pending-remove-button" data-remove-pending="${op.beforeIndex}" aria-label="从本次编辑列表移除 ${escape(op.value)}" title="从本次编辑列表移除">−</button>`:''}</span>`;
  function removalMarkup(plan,editing=false){
    const removals=(plan?.operations||[]).filter(op=>op.type==='remove');
    return removals.length?`<div class="pending-removals"><span class="pending-additions-title">方案待删除 · ${editing?'尚未从本次编辑列表移除':'确认应用后移除'}</span><div class="pending-additions-values">${removals.map(op=>removalToken(op,editing)).join('<span class="element-separator">;</span>')}</div></div>`:'';
  }
  function issuesMarkup(plan){
    return (plan?.issues||[]).length?`<div class="plan-issues">${plan.issues.map(issue=>`<span class="plan-issue"><code>${escape(issue.value)}</code><span class="diff-label is-retained">${issue.reason==='duplicate'?'存在重复，删除需确认':issue.reason==='shared'?'共享保留':issue.reason==='stale'?'编辑基线已失效':'目标计划冲突'}</span></span>`).join('')}</div>`:'';
  }
  function pendingMarkup(raw, values = [], whole = false, editing = false, modes = [], plan = null) {
    if (whole) return '';
    const pendingPlan=plan||DemoPlan.build({mode:'-append',whole,actualRaw:raw,selectedRaw:values.join(';')});
    const missing=pendingPlan.operations.filter(op=>op.type==='insert').map(op=>({value:op.value,index:op.selectedIndex,mode:op.position==='head'?'+append':'-append'}));
    const additions=missing.length?`<div class="pending-additions"><span class="pending-additions-title">方案待追加 · ${editing?'尚未加入本次编辑结果':'尚未加入当前值'}</span><div class="pending-additions-values">${missing.map(part=>pendingToken(part,editing?modes[part.index]:null)).join('<span class="element-separator">;</span>')}</div></div>`:'';
    return removalMarkup(pendingPlan,editing)+additions+issuesMarkup(pendingPlan);
  }
  function highlight(raw, values = [], whole = false, willReplace = false, plan = null) {
    if (raw === undefined) return '<span class="empty-element">（不存在）</span>';
    if (raw === '') return '<span class="empty-element">（空字符串）</span>' + (willReplace ? '<span class="diff-label replacement-label">整体待替换</span>' : '');
    const parts = whole ? [raw] : raw.split(';');
    const removals=new Map((plan?.operations||[]).filter(op=>op.type==='remove').map(op=>[op.beforeIndex,op]));
    const content = parts.map((part,index) => {
      if(removals.has(index))return removalToken(removals.get(index));
      const issue=plan?.issues?.find(item=>item.actualIndexes?.includes(index));
      const rawValue=matches(part,values,whole)?`<mark class="matched-element" title="与准备方案中的元素匹配">${escape(part)}</mark>`:escape(part);
      return issue?`<span class="retained-element">${rawValue}<span class="diff-label is-retained">${issue.reason==='shared'?'共享保留':'删除需确认'}</span></span>`:rawValue;
    }).join('<span class="element-separator">;</span>');
    return willReplace ? `<span class="pending-replacement"><span class="diff-value" data-raw-value="${escape(raw)}">${content}</span><span class="diff-label replacement-label">整体待替换</span></span>` : content;
  }
  // Display metadata comes from the frozen plan; it never supplies an apply result.
  function comparisonMarkup(plan){
    const whole=plan.kind!=='append',active=plan.valid!==false;
    const actual=plan.before.exists?(whole?[{value:plan.before.raw,key:'actual:whole'}]:plan.before.raw.split(';').map((value,index)=>({value,key:'actual:'+index,beforeIndex:index}))):[];
    const removals=new Set(active?plan.operations.filter(op=>op.type==='remove').map(op=>op.beforeIndex):[]);
    const additions=active?plan.operations.filter(op=>op.type==='insert'):[];
    const selectedParts=whole?[{value:plan.desiredRaw,index:0,matched:plan.matchResult.exact}]:plan.parts;
    const selected=selectedParts.map(part=>{
      const normalized=part.value.trim(),actualMatches=actual.filter(item=>item.value.trim()===normalized);
      const selectedMatches=selectedParts.filter(item=>item.value.trim()===normalized);
      const uniqueMatch=part.matched&&actualMatches.length===1&&selectedMatches.length===1;
      return {...part,key:uniqueMatch?actualMatches[0].key:'selected:'+part.index,added:additions.some(op=>op.selectedIndex===part.index)};
    });
    let after=whole?[{value:plan.after.raw,key:plan.operations.length?'selected:0':'actual:whole',wholeChange:active&&plan.operations.length>0}]:actual.map(item=>({...item,ghost:removals.has(item.beforeIndex)}));
    if(!whole){
      const groups=[];
      for(const op of additions){const last=groups.at(-1);if(last&&last.requestId===op.requestId&&last.position===op.position)last.items.push(op);else groups.push({requestId:op.requestId,position:op.position,items:[op]});}
      for(const group of groups){const items=group.items.map(op=>({value:op.value,key:'selected:'+op.selectedIndex,index:op.selectedIndex,added:true}));after=group.position==='head'?[...items,...after]:[...after,...items];}
      // If annotation metadata cannot represent the authoritative after, show that raw value without invented links.
      if(plan.after.exists&&after.filter(item=>!item.ghost).map(item=>item.value).join(';')!==plan.after.raw)after=plan.after.raw.split(';').map((value,index)=>({value,key:'after:'+index}));
    }
    // Ghosts explain removals but never participate in the serialized result.
    const render=(items,side)=>{
      const counts=new Map();items.filter(item=>!item.ghost).forEach(item=>counts.set(item.value,(counts.get(item.value)||0)+1));
      return items.map(item=>{
        const removed=item.ghost||(side==='actual'&&removals.has(item.beforeIndex));
        const issue=side==='actual'||side==='after'?plan.issues?.find(issue=>issue.actualIndexes?.includes(item.beforeIndex)):null;
        const matched=side==='selected'?item.matched:whole?item.value===plan.desiredRaw:matches(item.value,[plan.desiredRaw]);
        const added=(side==='after'||side==='selected')&&item.added;
        const classes=['diff-token','comparison-token',item.ghost?'is-ghost':'',removed?'is-removed':added?'is-added pending-add-token':item.wholeChange?'is-replaced':matched?'is-matched':''].filter(Boolean).join(' ');
        const label=removed?'<span class="diff-label is-removed">待删除</span>':added?'<span class="diff-label is-pending-add">待追加</span>':item.wholeChange?'<span class="diff-label is-replaced">整体待替换</span>':issue?'<span class="diff-label is-retained">'+(issue.reason==='shared'?'共享保留':'删除需确认')+'</span>':'';
        return '<span class="'+classes+'" tabindex="0" data-related-key="'+item.key+'"'+(item.beforeIndex===undefined?'':' data-before-index="'+item.beforeIndex+'"')+'><span class="diff-value" data-raw-value="'+escape(item.value)+'">'+(item.value===''?'<span class="empty-element">'+(whole?'（空字符串）':'（空元素）')+'</span>':escape(item.value))+'</span>'+label+(!whole?duplicateLabel(counts.get(item.value)):'')+'</span>';
      }).join('<span class="element-separator">;</span>');
    };
    return {selected:'<span class="selection-current">'+render(selected,'selected')+'</span>',
      actual:plan.before.exists?(render(actual,'actual')||'<span class="empty-element">（空字符串）</span>'):'<span class="empty-element">（不存在）</span>',
      after:!active?'<span class="invalid-result">计划已失效，需要重新检测并预览</span>':render(after,'after')||'<span class="empty-element">'+(plan.after.exists?'（空字符串）':'（不存在）')+'</span>'};
  }
  const comparisonBindings=new WeakMap();
  function bindComparison(root){
    comparisonBindings.get(root)?.();
    let hovered=null,focused=null;
    const keyFor=target=>target?.closest?.('[data-related-key]')?.dataset.relatedKey??null;
    const paint=()=>root.querySelectorAll('[data-related-key]').forEach(node=>node.classList.toggle('is-related-highlight',node.dataset.relatedKey===(hovered??focused)));
    const handlers={
      pointerover:e=>{hovered=keyFor(e.target);paint()},
      pointerout:e=>{hovered=root.contains(e.relatedTarget)?keyFor(e.relatedTarget):null;paint()},
      pointerleave:()=>{hovered=null;paint()},
      focusin:e=>{focused=keyFor(e.target);paint()},
      focusout:e=>{focused=root.contains(e.relatedTarget)?keyFor(e.relatedTarget):null;paint()}
    };
    for(const [type,handler] of Object.entries(handlers))root.addEventListener(type,handler);
    comparisonBindings.set(root,()=>{for(const [type,handler] of Object.entries(handlers))root.removeEventListener(type,handler)});
  }
  const serialized = () => session.rows.map(row => row.value).join(session.whole ? '' : ';');
  const valid = () => !session.rows.some(row => row.value.includes('\0') || (!session.whole && row.value.includes(';')));
  function countsFor(rows) {
    const counts = new Map();
    for (const row of rows) counts.set(row.value, (counts.get(row.value) || 0) + 1);
    return counts;
  }
  const duplicateLabel = count => count > 1
    ? `<span class="diff-label is-duplicate" title="此栏有 ${count} 个原始文本完全相同的元素；仅提示，不自动去重">重复 ×${count}</span>` : '';
  function editStateFor(row) {
    const original = session.originalById.get(row.id);
    if (!original) return {added:true,replaced:false,moved:false};
    const existing = session.rows.filter(item => session.originalById.has(item.id));
    const remaining = session.originalRows.filter(item => session.rows.some(current => current.id === item.id));
    return {added:false,replaced:row.value !== original.value,
      moved:existing.findIndex(item => item.id === row.id) !== remaining.findIndex(item => item.id === row.id)};
  }
  function stateFor(row){
    return session.plan?.audit.elements.find(element=>element.elementId===row.id)||editStateFor(row);
  }
  function labels(state, removed = false, planned = false) {
    return (removed ? [['removed','删除']] : [
      ...(state.added ? [['added','新增']] : []), ...(state.replaced ? [['replaced','替换']] : []),
      ...(state.moved ? [['moved','移动']] : []), ...(planned && !state.added && !state.replaced ? [['planned','将替换']] : [])
    ]).map(([kind,text]) => `<span class="diff-label is-${kind}">${text}</span>`).join('');
  }
  function auditSnapshot() {
    const elements=session.rows.map((row,afterIndex)=>{
      const beforeIndex=session.originalRows.findIndex(original=>original.id===row.id);
      const state=editStateFor(row);
      return {elementId:row.id,beforeIndex:beforeIndex<0?null:beforeIndex,afterIndex,
        before:beforeIndex<0?undefined:session.originalRows[beforeIndex].value,after:row.value,
        ...state,removed:false};
    });
    session.originalRows.forEach((row,beforeIndex)=>{
      if(!session.rows.some(current=>current.id===row.id))elements.push({elementId:row.id,beforeIndex,afterIndex:null,
        before:row.value,after:undefined,added:false,removed:true,replaced:false,moved:false});
    });
    return {before:session.original,after:serialized(),whole:session.whole,elements,
      counts:Object.fromEntries(['added','removed','replaced','moved'].map(kind=>[kind,elements.filter(element=>element[kind]).length]))};
  }
  function token(row, state, removed = false, duplicateCount = 1) {
    const matched = matches(row.value,session.selectedValues || [],session.whole);
    const classes = [removed?'is-removed':'',state.added?'is-added':'',state.replaced?'is-replaced':'',state.moved?'is-moved':'',!removed&&plannedRemoval(row)?'is-pending-remove':'',matched?'is-matched':'',duplicateCount>1?'is-duplicate':''].filter(Boolean).join(' ');
    return `<span class="diff-token ${classes}" data-element-id="${row.id}" data-duplicate-count="${duplicateCount}" tabindex="-1"><span class="diff-value" data-raw-value="${escape(row.value)}">${row.value === '' ? `<span class="empty-element">${session.whole?'（空字符串）':'（空元素）'}</span>` : escape(row.value)}</span>${labels(state,removed,session.replacementExpected)}${!removed&&plannedRemoval(row)?'<span class="diff-label is-removed">待删除</span>':''}${duplicateLabel(duplicateCount)}${matched&&!removed?'<span class="element-match-mark" title="与准备方案匹配" aria-label="匹配">✓</span>':''}</span>`;
  }
  function renderComparison() {
    const root=session.container;
    root.querySelector('#elementOriginal').innerHTML=session.original === undefined ? '<span class="empty-element">（不存在）</span>'
      : session.originalRows.map(original=>{const current=session.rows.find(row=>row.id===original.id);return token(original,current?stateFor(current):{},!current,session.originalCounts.get(original.value));}).join('<span class="element-separator">;</span>');
    const removed=session.originalRows.filter(original=>!session.rows.some(row=>row.id===original.id));
    const current=session.rows.map(row=>token(row,stateFor(row),false,session.currentCounts.get(row.value))).join('<span class="element-separator">;</span>');
    root.querySelector('#elementPreview').innerHTML=(current || `<span class="empty-element">${session.plan.after.exists?'（空字符串）':'（不存在）'}</span>`)
      +(removed.length?`<span class="removed-elements"><span class="removed-title">已删除 · 不计入结果</span>${removed.map(row=>token(row,{},true)).join('<span class="element-separator">;</span>')}</span>`:'');
    root.querySelector('#elementSerialized').textContent=session.plan.after.exists?serialized():'（不存在）';
    paintRelated();
  }
  function paintRelated() {
    if (!session) return;
    const id = session.hoveredId ?? session.focusedId;
    session.container.querySelectorAll('[data-element-id]').forEach(element => {
      element.classList.toggle('is-related-highlight', id !== null && Number(element.dataset.elementId) === id);
    });
  }
  function elementAt(target) {
    if (!target || target.nodeType !== 1) return null;
    const element = target.closest('[data-element-id]');
    return element && session.container.contains(element) ? element : null;
  }
  function bindRelated() {
    const root = session.container;
    const handlers = {};
    handlers.pointerover = event => {
      if (!session) return;
      const element = elementAt(event.target);
      const id = element ? Number(element.dataset.elementId) : null;
      if (session.hoveredId !== id) { session.hoveredId = id; paintRelated(); }
    };
    handlers.pointerout = event => {
      if (!session) return;
      const next = elementAt(event.relatedTarget);
      const id = next ? Number(next.dataset.elementId) : null;
      if (session.hoveredId !== id) { session.hoveredId = id; paintRelated(); }
    };
    handlers.pointerleave = () => {
      if (session) { session.hoveredId = null; paintRelated(); }
    };
    handlers.focusin = event => {
      if (!session) return;
      const element = elementAt(event.target);
      session.focusedId = element ? Number(element.dataset.elementId) : null;
      paintRelated();
    };
    handlers.focusout = event => {
      if (!session) return;
      const next = elementAt(event.relatedTarget);
      session.focusedId = next ? Number(next.dataset.elementId) : null;
      paintRelated();
    };
    for (const [type, handler] of Object.entries(handlers)) root.addEventListener(type, handler);
    session.relatedHandlers = handlers;
  }
  function paintRow(element,row) {
    const matched=matches(row.value,session.selectedValues || [],session.whole),state=stateFor(row);
    element.classList.toggle('is-matched',matched);
    for(const flag of ['added','replaced','moved'])element.classList.toggle('is-'+flag,!!state[flag]);
    const duplicateCount = session.currentCounts.get(row.value);
    element.classList.toggle('is-duplicate', duplicateCount > 1);
    element.dataset.duplicateCount = duplicateCount;
    element.classList.toggle('will-be-replaced',!!session.replacementExpected&&!state.added&&!state.replaced);
    element.querySelector('.element-match-state').textContent=matched?'✓ 匹配':'';
    const removal=plannedRemoval(row);element.classList.toggle('is-pending-remove',!!removal);
    element.querySelector('.element-change-state').innerHTML=labels(state,false,session.replacementExpected)+duplicateLabel(duplicateCount)+(removal?'<span class="diff-label is-removed">待删除</span>':'');
    element.querySelector('.element-input').setAttribute('aria-invalid',String(row.value.includes('\0') || (!session.whole && row.value.includes(';'))));
  }
  function plannedRemoval(row){
    if(session.selectionPlan?.valid===false)return null;
    return (session.selectionPlan?.operations||[]).find(op=>op.type==='remove'&&session.originalRows[op.beforeIndex]?.id===row.id&&session.originalRows[op.beforeIndex].value===row.value);
  }
  function candidatePlan(){
    const values=session.appendValues,additions=DemoPlan.build({mode:'-append',actualRaw:serialized(),selectedRaw:values.join(';')});
    const originalPlan=session.selectionPlan;
    if(originalPlan?.valid===false)return {...originalPlan,operations:[]};
    const removals=(originalPlan?.operations||[]).filter(op=>op.type==='remove').flatMap(op=>{
      const original=session.originalRows[op.beforeIndex],row=session.rows.find(row=>row.id===original?.id);
      return row&&row.value===op.value?[{...op,elementId:row.id}]:[];
    });
    return {...additions,operations:[...removals,...additions.operations],issues:originalPlan?.issues||[]};
  }
  function update() {
    const root=session.container,raw=serialized();
    session.currentCounts = countsFor(session.rows);
    session.plan=DemoPlan.buildElementEdit({target:{scope:session.scope,name:session.name},snapshot:session.snapshot||{exists:session.original!==undefined,raw:session.original===undefined?null:session.original,type:session.registryType,revision:0},afterRaw:raw,audit:auditSnapshot()});
    const changed=session.plan.operations.length>0;
    const structureChanged=JSON.stringify(session.rows.map(row=>({id:row.id,value:row.value})))!==JSON.stringify(session.originalRows);
    root.querySelector('#elementTotal').textContent=`${session.rows.length} ${session.whole?'条字符串':'个元素'}`;
    root.querySelector('#elementValidation').textContent=valid()?'':session.whole?'字符串不能包含 NUL。':'一个元素内不能包含分号或 NUL；请将多项内容拆成多个元素。';
    root.querySelector('#applyElements').disabled=!valid()||!session.plan.operations.length;
    root.querySelector('#restoreElements').disabled=!structureChanged;
    root.querySelector('#elementStatus').textContent=changed?'有待应用修改 · 仅更新演示数据':structureChanged?'顺序或结构已调整 · 结果值未变化':'未修改 · 仅更新演示数据';
    root.querySelectorAll('[data-element-id]').forEach(el=>{if(!el.classList.contains('element-row'))return;const row=session.rows.find(row=>row.id===Number(el.dataset.elementId));if(row)paintRow(el,row);});
    const pending = root.querySelector('#pendingElements');
    pending.innerHTML = pendingMarkup(raw,session.appendValues,session.whole,true,session.appendModes,candidatePlan());
    pending.hidden = !pending.innerHTML;
    const editingSession=session;
    pending.querySelectorAll('[data-add-pending]').forEach(button=>{
      button.onclick=()=>adoptPending(Number(button.dataset.addPending),button,editingSession);
    });
    pending.querySelectorAll('[data-remove-pending]').forEach(button=>button.onclick=()=>{
      if(session!==editingSession||!button.isConnected)return;
      const original=session.originalRows[Number(button.dataset.removePending)],row=session.rows.find(row=>row.id===original?.id);
      if(!row||!plannedRemoval(row))return;
      session.rows=session.rows.filter(item=>item.id!==row.id);renderRows(session.rows[0]?.id);
    });
    renderComparison();
  }
  function open(options) {
    dismiss();
    const original=options.raw,rawValues=original===undefined?[]:options.whole?[original]:original.split(';');
    const originalRows=rawValues.map(value=>({id:++sequence,value}));
    const entries=Array.isArray(options.appendEntries)?options.appendEntries:null;
    const appendValues=entries?entries.map(entry=>entry.value):[...(options.appendValues || [])];
    const appendModes=entries?entries.flatMap(entry=>entry.value.split(';').map(()=>entry.mode)):[];
    session={...options,appendValues,appendModes,original,originalRows,originalById:new Map(originalRows.map(row=>[row.id,row])),originalCounts:countsFor(originalRows),currentCounts:new Map(),rows:originalRows.map(row=>({...row})),dragging:null,hoveredId:null,focusedId:null};
    const container=options.container;
    container.innerHTML=`<div class="dialog element-dialog" role="dialog" aria-modal="true" aria-labelledby="dialogTitle">
      <div class="dialoghead"><div><div class="eyebrow">Windows 环境 / ${options.scope==='User'?'用户':'系统'}</div><h3 id="dialogTitle">${escape(options.name)} <span class="element-demo">演示数据</span></h3><p>调整元素顺序或内容，应用后更新本页展示。</p></div><button class="close" id="closeDialog" aria-label="关闭元素编辑器">×</button></div>
      <div class="elements-meta"><span>作用域 <strong>${options.scope==='User'?'用户':'系统'}</strong></span><span>类型 <strong>${escape(options.registryType)}（示例）</strong></span><span id="elementTotal"></span></div>
      <div class="dialogbody elements-body"><div class="elements-toolbar"><span>${options.whole?'整体字符串 · 不拆分元素':'按分号排列 · Alt + ↑ / ↓ 调整顺序'}</span><div class="elements-toolbar-actions"><button class="textbtn" id="restoreElements" disabled title="恢复打开弹窗时的值，不改变页面快照">还原本次编辑</button><button class="btn" id="addElement" ${options.whole?'hidden':''}>＋ 新增元素</button></div></div>
        <div class="change-legend"><span class="legend-group"><span class="legend-heading">变更</span><span class="legend-replaced">替换</span><span class="legend-added">＋ 新增</span><span class="legend-moved">↕ 移动</span><span class="legend-removed">− 删除</span></span><span class="legend-group"><span class="legend-heading">提示</span><span class="legend-duplicate">重复 ×2</span><span class="legend-match">✓ 匹配</span></span></div>
        <div class="element-list" id="elementList"></div><div class="element-validation" id="elementValidation" role="status"></div><div id="pendingElements" hidden></div>
        <details class="element-raw"><summary>查看元素变更对照</summary><p class="comparison-hint">悬停色框可对照同一元素。重复按每栏的原始文本统计，空元素保留；删除项不计入新值。</p><div class="raw-comparison"><div><label>打开时的值</label><pre id="elementOriginal"></pre></div><div><label>编辑后的值 · 含删除标记</label><pre id="elementPreview"></pre></div></div><details class="serialized-result"><summary>实际结果字符串（不含标记或删除项）</summary><pre id="elementSerialized"></pre></details></details>
      </div><div class="dialogfoot element-footer"><span id="elementStatus" role="status">仅更新演示数据</span><button class="btn" id="cancelDialog" title="丢弃弹窗内修改，保留页面快照">取消修改</button><button class="btn primary" id="applyElements" disabled>应用（演示）</button></div></div>`;
    container.classList.add('open');
    bindRelated();
    container.querySelector('#closeDialog').onclick=options.close;container.querySelector('#cancelDialog').onclick=options.close;
    container.querySelector('#addElement').onclick=()=>{session.rows.push({id:++sequence,value:''});renderRows(sequence);};
    container.querySelector('#restoreElements').onclick=()=>{session.rows=session.originalRows.map(row=>({...row}));session.dragging=null;session.hoveredId=null;session.focusedId=null;renderRows(session.rows[0]?.id);if(!session.rows.length)container.querySelector('#addElement').focus();};
    container.querySelector('#applyElements').onclick=()=>{if(!session||!valid()||container.querySelector('#applyElements').disabled)return;const current=session,plan=session.plan;current.close();current.onApply(plan.afterRaw,plan.audit,plan);};
    renderRows();container.querySelector('#closeDialog').focus();
  }
  function adoptPending(index,button,editingSession) {
    if(!session||session!==editingSession||session.whole||!button.isConnected||!session.container.querySelector('#pendingElements').contains(button))return;
    const mode=session.appendModes[index];
    if(!isAppendMode(mode))return;
    const candidate=selectionInfo(serialized(),session.appendValues).missing.find(part=>part.index===index);
    if(!candidate)return;
    const row={id:++sequence,value:candidate.value};
    if(mode==='+append')session.rows.unshift(row);else session.rows.push(row);
    renderRows(row.id);
  }
  function move(id,offset) {
    const index=session.rows.findIndex(row=>row.id===id),target=index+offset;
    if(target<0||target>=session.rows.length)return;
    const [row]=session.rows.splice(index,1);session.rows.splice(target,0,row);renderRows(id);
  }
  function renderRows(focusId) {
    const root=session.container;
    session.hoveredId = null;
    session.focusedId = null;
    root.querySelector('#elementList').innerHTML=session.rows.length?session.rows.map((row,index)=>`<div class="element-row" data-element-id="${row.id}" draggable="${!session.whole}">
      <span class="element-order" title="当前顺序">${index+1}</span><label class="element-input-wrap"><span class="sr-only">第 ${index+1} ${session.whole?'条字符串':'个元素'}</span>${session.whole?`<textarea class="element-input" rows="3" spellcheck="false" data-element-input="${row.id}">${escape(row.value)}</textarea>`:`<input class="element-input" value="${escape(row.value)}" spellcheck="false" placeholder="（空元素）" data-element-input="${row.id}">`}</label>
      <span class="element-states"><span class="element-match-state"></span><span class="element-change-state"></span></span><div class="element-actions" ${session.whole?'hidden':''}><button class="actmini" data-move-up="${row.id}" aria-label="上移第 ${index+1} 个元素" ${index===0?'disabled':''}>↑</button><button class="actmini" data-move-down="${row.id}" aria-label="下移第 ${index+1} 个元素" ${index===session.rows.length-1?'disabled':''}>↓</button><button class="actmini element-delete" data-delete="${row.id}" aria-label="删除第 ${index+1} 个元素">删除</button></div></div>`).join(''):'<div class="empty">列表为空。确认应用将移除变量；保留一个空元素则保存为空值。</div>';
    root.querySelectorAll('[data-element-input]').forEach(input=>{
      const id=Number(input.dataset.elementInput),row=session.rows.find(row=>row.id===id);input.value=row.value;
      input.oninput=()=>{row.value=input.value;update();};
      input.onkeydown=e=>{if(e.altKey&&['ArrowUp','ArrowDown'].includes(e.key)&&!session.whole){e.preventDefault();move(id,e.key==='ArrowUp'?-1:1);}};
    });
    root.querySelectorAll('[data-move-up]').forEach(button=>button.onclick=()=>move(Number(button.dataset.moveUp),-1));
    root.querySelectorAll('[data-move-down]').forEach(button=>button.onclick=()=>move(Number(button.dataset.moveDown),1));
    root.querySelectorAll('[data-delete]').forEach(button=>button.onclick=()=>{const index=session.rows.findIndex(row=>row.id===Number(button.dataset.delete));session.rows.splice(index,1);renderRows(session.rows[Math.min(index,session.rows.length-1)]?.id);if(!session.rows.length)root.querySelector('#addElement').focus();});
    root.querySelectorAll('.element-row').forEach(element=>{
      const id=Number(element.dataset.elementId);
      element.ondragstart=e=>{if(session.whole||e.target.matches('input,textarea,button')){e.preventDefault();return;}session.dragging=id;e.dataTransfer.setData('text/plain',String(id));e.dataTransfer.effectAllowed='move';};
      element.ondragover=e=>{if(session.dragging!==null){e.preventDefault();element.classList.add('drop-target');}};
      element.ondragleave=()=>element.classList.remove('drop-target');
      element.ondrop=e=>{if(session.dragging===null)return;e.preventDefault();const from=session.rows.findIndex(row=>row.id===session.dragging),to=session.rows.findIndex(row=>row.id===id);const [row]=session.rows.splice(from,1);session.rows.splice(to,0,row);session.dragging=null;renderRows(row.id);};
      element.ondragend=()=>{if(session)session.dragging=null;root.querySelectorAll('.drop-target').forEach(row=>row.classList.remove('drop-target'));};
    });
    update();if(focusId)root.querySelector(`[data-element-input="${focusId}"]`)?.focus();
  }
  function dismiss() {
    if (session) {
      session.container.querySelectorAll('.is-related-highlight').forEach(element=>element.classList.remove('is-related-highlight'));
      for (const [type, handler] of Object.entries(session.relatedHandlers || {})) session.container.removeEventListener(type, handler);
    }
    session=null;
  }
  return {highlight,selectionInfo,selectionMarkup,pendingMarkup,comparisonMarkup,bindComparison,open,dismiss};
})();
