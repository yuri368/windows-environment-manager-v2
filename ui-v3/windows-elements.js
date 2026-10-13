/* Element editing and annotations are UI-only. Only serialized live rows are applied. */
var WindowsElements = (() => {
  const escape = text => String(text ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let session = null, sequence = 0;
  const normalized = value => value.trim();
  const required = values => new Set(values.flatMap(value => value.split(';')).map(normalized).filter(Boolean));
  const matches = (raw, values, whole) => whole ? values.some(value => value === raw) : normalized(raw) !== '' && required(values).has(normalized(raw));
  function highlight(raw, values = [], whole = false, willReplace = false) {
    if (raw === undefined) return '<span class="empty-element">（不存在）</span>';
    if (raw === '') return '<span class="empty-element">（空字符串）</span>' + (willReplace ? '<span class="diff-label replacement-label">将替换</span>' : '');
    const parts = whole ? [raw] : raw.split(';');
    const content = parts.map(part => matches(part, values, whole)
      ? `<mark class="matched-element" title="与准备方案中的元素匹配">${escape(part)}</mark>` : escape(part)).join('<span class="element-separator">;</span>');
    return willReplace ? `<span class="pending-replacement"><span class="diff-value" data-raw-value="${escape(raw)}">${content}</span><span class="diff-label replacement-label">将替换</span></span>` : content;
  }
  const serialized = () => session.rows.map(row => row.value).join(session.whole ? '' : ';');
  const valid = () => !session.rows.some(row => row.value.includes('\0') || (!session.whole && row.value.includes(';')));
  function stateFor(row) {
    const original = session.originalById.get(row.id);
    if (!original) return {added:true,replaced:false,moved:false};
    const existing = session.rows.filter(item => session.originalById.has(item.id));
    const remaining = session.originalRows.filter(item => session.rows.some(current => current.id === item.id));
    return {added:false,replaced:row.value !== original.value,
      moved:existing.findIndex(item => item.id === row.id) !== remaining.findIndex(item => item.id === row.id)};
  }
  function labels(state, removed = false, planned = false) {
    return (removed ? [['removed','删除']] : [
      ...(state.added ? [['added','新增']] : []), ...(state.replaced ? [['replaced','替换']] : []),
      ...(state.moved ? [['moved','移动']] : []), ...(planned && !state.added && !state.replaced ? [['planned','将替换']] : [])
    ]).map(([kind,text]) => `<span class="diff-label is-${kind}">${text}</span>`).join('');
  }
  function token(row, state, removed = false) {
    const matched = matches(row.value,session.selectedValues || [],session.whole);
    const classes = [removed?'is-removed':'',state.added?'is-added':'',state.replaced?'is-replaced':'',state.moved?'is-moved':'',matched?'is-matched':''].filter(Boolean).join(' ');
    return `<span class="diff-token ${classes}" data-element-id="${row.id}"><span class="diff-value" data-raw-value="${escape(row.value)}">${row.value === '' ? '<span class="empty-element">（空元素）</span>' : escape(row.value)}</span>${labels(state,removed,session.replacementExpected)}</span>`;
  }
  function renderComparison() {
    const root=session.container;
    root.querySelector('#elementOriginal').innerHTML=session.original === undefined ? '<span class="empty-element">（不存在）</span>'
      : session.originalRows.map(original=>{const current=session.rows.find(row=>row.id===original.id);return token(original,current?stateFor(current):{},!current);}).join('<span class="element-separator">;</span>');
    const removed=session.originalRows.filter(original=>!session.rows.some(row=>row.id===original.id));
    const current=session.rows.map(row=>token(row,stateFor(row))).join('<span class="element-separator">;</span>');
    root.querySelector('#elementPreview').innerHTML=(current || '<span class="empty-element">（空字符串）</span>')
      +(removed.length?`<span class="removed-elements"><span class="removed-title">已删除 · 不计入结果</span>${removed.map(row=>token(row,{},true)).join('<span class="element-separator">;</span>')}</span>`:'');
    root.querySelector('#elementSerialized').textContent=serialized();
  }
  function paintRow(element,row) {
    const matched=matches(row.value,session.selectedValues || [],session.whole),state=stateFor(row);
    element.classList.toggle('is-matched',matched);
    for(const flag of ['added','replaced','moved'])element.classList.toggle('is-'+flag,!!state[flag]);
    element.classList.toggle('will-be-replaced',!!session.replacementExpected&&!state.added&&!state.replaced);
    element.querySelector('.element-match-state').textContent=matched?'✓ 匹配':'';
    element.querySelector('.element-change-state').innerHTML=labels(state,false,session.replacementExpected);
    element.querySelector('.element-input').setAttribute('aria-invalid',String(row.value.includes('\0') || (!session.whole && row.value.includes(';'))));
  }
  function update() {
    const root=session.container,raw=serialized(),changed=session.original === undefined?session.rows.length>0:raw!==session.original;
    const structureChanged=JSON.stringify(session.rows.map(row=>({id:row.id,value:row.value})))!==JSON.stringify(session.originalRows);
    root.querySelector('#elementTotal').textContent=`${session.rows.length} ${session.whole?'条字符串':'个元素'}`;
    root.querySelector('#elementValidation').textContent=valid()?'':session.whole?'字符串不能包含 NUL。':'一个元素内不能包含分号或 NUL；请将多项内容拆成多个元素。';
    root.querySelector('#applyElements').disabled=!valid()||!changed;
    root.querySelector('#restoreElements').disabled=!structureChanged;
    root.querySelector('#elementStatus').textContent=changed?'有待应用修改 · 仅更新演示数据':structureChanged?'顺序或结构已调整 · 结果值未变化':'未修改 · 仅更新演示数据';
    root.querySelectorAll('[data-element-id]').forEach(el=>{if(!el.classList.contains('element-row'))return;const row=session.rows.find(row=>row.id===Number(el.dataset.elementId));if(row)paintRow(el,row);});
    renderComparison();
  }
  function open(options) {
    const original=options.raw,rawValues=original===undefined?[]:options.whole?[original]:original.split(';');
    const originalRows=rawValues.map(value=>({id:++sequence,value}));
    session={...options,original,originalRows,originalById:new Map(originalRows.map(row=>[row.id,row])),rows:originalRows.map(row=>({...row})),dragging:null};
    const container=options.container;
    container.innerHTML=`<div class="dialog element-dialog" role="dialog" aria-modal="true" aria-labelledby="dialogTitle">
      <div class="dialoghead"><div><div class="eyebrow">Windows 环境 / ${options.scope==='User'?'用户':'系统'}</div><h3 id="dialogTitle">${escape(options.name)} <span class="element-demo">演示数据</span></h3><p>调整元素顺序或内容，应用后更新本页展示。</p></div><button class="close" id="closeDialog" aria-label="关闭元素编辑器">×</button></div>
      <div class="elements-meta"><span>作用域 <strong>${options.scope==='User'?'用户':'系统'}</strong></span><span>类型 <strong>${escape(options.registryType)}（示例）</strong></span><span id="elementTotal"></span></div>
      <div class="dialogbody elements-body"><div class="elements-toolbar"><span>${options.whole?'整体字符串 · 不拆分元素':'按分号排列 · Alt + ↑ / ↓ 调整顺序'}</span><div class="elements-toolbar-actions"><button class="textbtn" id="restoreElements" disabled title="恢复打开弹窗时的值，不改变页面快照">还原本次编辑</button><button class="btn" id="addElement" ${options.whole?'hidden':''}>＋ 新增元素</button></div></div>
        <div class="change-legend"><span class="legend-match">✓ 匹配</span><span class="legend-replaced">替换</span><span class="legend-added">＋ 新增</span><span class="legend-moved">↕ 移动</span><span class="legend-removed">删除</span></div>
        <div class="element-list" id="elementList"></div><div class="element-validation" id="elementValidation" role="status"></div>
        <details class="element-raw"><summary>查看元素变更对照</summary><div class="raw-comparison"><div><label>打开时的值</label><pre id="elementOriginal"></pre></div><div><label>编辑后的值 · 含删除标记</label><pre id="elementPreview"></pre></div></div><details class="serialized-result"><summary>实际结果字符串（不含标记或删除项）</summary><pre id="elementSerialized"></pre></details></details>
      </div><div class="dialogfoot element-footer"><span id="elementStatus" role="status">仅更新演示数据</span><button class="btn" id="cancelDialog" title="丢弃弹窗内修改，保留页面快照">取消修改</button><button class="btn primary" id="applyElements" disabled>应用（演示）</button></div></div>`;
    container.classList.add('open');
    container.querySelector('#closeDialog').onclick=options.close;container.querySelector('#cancelDialog').onclick=options.close;
    container.querySelector('#addElement').onclick=()=>{session.rows.push({id:++sequence,value:''});renderRows(sequence);};
    container.querySelector('#restoreElements').onclick=()=>{session.rows=session.originalRows.map(row=>({...row}));session.dragging=null;renderRows(session.rows[0]?.id);if(!session.rows.length)container.querySelector('#addElement').focus();};
    container.querySelector('#applyElements').onclick=()=>{if(!session||!valid()||container.querySelector('#applyElements').disabled)return;const current=session,result=serialized();current.close();current.onApply(result);};
    renderRows();container.querySelector('#closeDialog').focus();
  }
  function move(id,offset) {
    const index=session.rows.findIndex(row=>row.id===id),target=index+offset;
    if(target<0||target>=session.rows.length)return;
    const [row]=session.rows.splice(index,1);session.rows.splice(target,0,row);renderRows(id);
  }
  function renderRows(focusId) {
    const root=session.container;
    root.querySelector('#elementList').innerHTML=session.rows.length?session.rows.map((row,index)=>`<div class="element-row" data-element-id="${row.id}" draggable="${!session.whole}">
      <span class="element-order" title="当前顺序">${index+1}</span><label class="element-input-wrap"><span class="sr-only">第 ${index+1} ${session.whole?'条字符串':'个元素'}</span>${session.whole?`<textarea class="element-input" rows="3" spellcheck="false" data-element-input="${row.id}">${escape(row.value)}</textarea>`:`<input class="element-input" value="${escape(row.value)}" spellcheck="false" placeholder="（空元素）" data-element-input="${row.id}">`}</label>
      <span class="element-states"><span class="element-match-state"></span><span class="element-change-state"></span></span><div class="element-actions" ${session.whole?'hidden':''}><button class="actmini" data-move-up="${row.id}" aria-label="上移第 ${index+1} 个元素" ${index===0?'disabled':''}>↑</button><button class="actmini" data-move-down="${row.id}" aria-label="下移第 ${index+1} 个元素" ${index===session.rows.length-1?'disabled':''}>↓</button><button class="actmini element-delete" data-delete="${row.id}" aria-label="删除第 ${index+1} 个元素">删除</button></div></div>`).join(''):'<div class="empty">列表为空。可以新增元素；应用空列表会展示为空字符串。</div>';
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
  return {highlight,open,dismiss(){session=null;}};
})();