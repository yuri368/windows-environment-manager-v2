/* Shared UI component. Reads supplied strings and applies only through the demo callback. */
var WindowsElements = (() => {
  const escape = text => String(text ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let session = null, sequence = 0;
  const normalized = value => value.trim();
  const required = values => new Set(values.flatMap(value => value.split(';')).map(normalized).filter(Boolean));
  function matches(raw, values, whole) {
    return whole ? values.some(value => value === raw) : normalized(raw) !== '' && required(values).has(normalized(raw));
  }
  function highlight(raw, values = [], whole = false) {
    if (raw === undefined) return '<span class="empty-element">（不存在）</span>';
    if (raw === '') return '<span class="empty-element">（空字符串）</span>';
    const parts = whole ? [raw] : raw.split(';');
    return parts.map(part => matches(part, values, whole)
      ? `<mark class="matched-element" title="与准备方案中的元素匹配">${escape(part)}</mark>`
      : escape(part)).join('<span class="element-separator">;</span>');
  }
  function open(options) {
    const original = options.raw;
    session = { ...options, original, rows: (original === undefined ? [] : options.whole ? [original] : original.split(';')).map(value => ({id: ++sequence, value})), dragging: null };
    const container = options.container;
    container.innerHTML = `<div class="dialog element-dialog" role="dialog" aria-modal="true" aria-labelledby="dialogTitle">
      <div class="dialoghead"><div><div class="eyebrow">Windows 环境 / ${escape(options.scope === 'User' ? '用户' : '系统')}</div><h3 id="dialogTitle">${escape(options.name)} <span class="element-demo">演示数据</span></h3><p>调整元素顺序或内容，应用后更新本页展示。</p></div><button class="close" id="closeDialog" aria-label="关闭元素编辑器">×</button></div>
      <div class="elements-meta"><span>作用域 <strong>${options.scope === 'User' ? '用户' : '系统'}</strong></span><span>类型 <strong>${escape(options.registryType)}（示例）</strong></span><span id="elementTotal"></span></div>
      <div class="dialogbody elements-body"><div class="elements-toolbar"><span>${options.whole ? '整体字符串 · 不拆分元素' : '按分号排列 · 拖动或使用箭头调整顺序'}</span><button class="btn" id="addElement" ${options.whole ? 'hidden' : ''}>＋ 新增元素</button></div>
        <div class="element-list" id="elementList"></div>
        <div class="element-validation" id="elementValidation" role="status"></div>
        <details class="element-raw"><summary>查看完整字符串</summary><div class="raw-comparison"><div><label>打开时的值</label><pre id="elementOriginal"></pre></div><div><label>编辑后的值</label><pre id="elementPreview"></pre></div></div></details>
      </div><div class="dialogfoot element-footer"><span id="elementStatus">仅更新演示数据</span><button class="btn" id="cancelDialog">取消</button><button class="btn primary" id="applyElements" disabled>应用（演示）</button></div>
    </div>`;
    container.classList.add('open');
    container.querySelector('#elementOriginal').textContent = original === undefined ? '（不存在）' : original;
    container.querySelector('#closeDialog').onclick = options.close;
    container.querySelector('#cancelDialog').onclick = options.close;
    container.querySelector('#addElement').onclick = () => { session.rows.push({id: ++sequence, value: ''}); renderRows(sequence); };
    container.querySelector('#applyElements').onclick = () => {
      if (!session || !valid()) return;
      const current = session, result = serialized();
      current.close();
      current.onApply(result);
    };
    renderRows();
    container.querySelector('#closeDialog').focus();
  }
  const serialized = () => session.rows.map(row => row.value).join(session.whole ? '' : ';');
  function valid() { return !session.rows.some(row => row.value.includes('\0') || (!session.whole && row.value.includes(';'))); }
  function update() {
    const root = session.container, raw = serialized(), changed = raw !== session.original;
    root.querySelector('#elementTotal').textContent = `${session.rows.length} ${session.whole ? '条字符串' : '个元素'}`;
    root.querySelector('#elementPreview').textContent = raw;
    root.querySelector('#elementValidation').textContent = valid() ? '' : '一个元素内不能包含分号或 NUL；请将多项内容拆成多个元素。';
    root.querySelector('#applyElements').disabled = !valid() || !changed;
    root.querySelector('#elementStatus').textContent = changed ? '有未应用修改 · 仅更新演示数据' : '未修改 · 仅更新演示数据';
  }
  function move(id, offset) {
    const index = session.rows.findIndex(row => row.id === id), target = index + offset;
    if (target < 0 || target >= session.rows.length) return;
    const [row] = session.rows.splice(index, 1);
    session.rows.splice(target, 0, row);
    renderRows(id);
  }
  function renderRows(focusId) {
    const root = session.container, values = session.selectedValues || [];
    root.querySelector('#elementList').innerHTML = session.rows.length ? session.rows.map((row, index) => {
      const matched = matches(row.value, values, session.whole);
      return `<div class="element-row ${matched ? 'is-matched' : ''}" data-element-id="${row.id}" draggable="${!session.whole}">
        <span class="element-order" title="当前顺序">${index + 1}</span>
        <label class="element-input-wrap"><span class="sr-only">第 ${index + 1} ${session.whole ? '条字符串' : '个元素'}</span>${session.whole
          ? `<textarea class="element-input" rows="3" spellcheck="false" data-element-input="${row.id}">${escape(row.value)}</textarea>`
          : `<input class="element-input" value="${escape(row.value)}" spellcheck="false" placeholder="（空元素）" data-element-input="${row.id}">`}</label>
        <span class="element-match-state ${matched ? 'has-match' : ''}" title="${matched ? '与准备方案匹配' : '不属于当前准备方案'}">${matched ? '✓ 匹配' : ''}</span>
        <div class="element-actions" ${session.whole ? 'hidden' : ''}><button class="actmini" data-move-up="${row.id}" aria-label="上移第 ${index+1} 个元素" ${index === 0 ? 'disabled' : ''}>↑</button><button class="actmini" data-move-down="${row.id}" aria-label="下移第 ${index+1} 个元素" ${index === session.rows.length-1 ? 'disabled' : ''}>↓</button><button class="actmini element-delete" data-delete="${row.id}" aria-label="删除第 ${index+1} 个元素">删除</button></div>
      </div>`;
    }).join('') : '<div class="empty">列表为空。可以新增元素；应用空列表会展示为空字符串。</div>';
    root.querySelectorAll('[data-element-input]').forEach(input => {
      const id = Number(input.dataset.elementInput);
      input.oninput = () => {
        const row = session.rows.find(row => row.id === id); row.value = input.value;
        const matched = matches(row.value, values, session.whole), element = input.closest('.element-row');
        element.classList.toggle('is-matched', matched);
        const badge = element.querySelector('.element-match-state');
        badge.textContent = matched ? '✓ 匹配' : ''; badge.classList.toggle('has-match', matched);
        badge.title = matched ? '与准备方案匹配' : '不属于当前准备方案';
        update();
      };
      input.onkeydown = e => { if (e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown') && !session.whole) { e.preventDefault(); move(id, e.key === 'ArrowUp' ? -1 : 1); } };
    });
    root.querySelectorAll('[data-move-up]').forEach(button => button.onclick = () => move(Number(button.dataset.moveUp), -1));
    root.querySelectorAll('[data-move-down]').forEach(button => button.onclick = () => move(Number(button.dataset.moveDown), 1));
    root.querySelectorAll('[data-delete]').forEach(button => button.onclick = () => {
      const index = session.rows.findIndex(row => row.id === Number(button.dataset.delete));
      session.rows.splice(index, 1); renderRows(session.rows[Math.min(index, session.rows.length-1)]?.id);
    });
    root.querySelectorAll('[data-element-id]').forEach(element => {
      const id = Number(element.dataset.elementId);
      element.ondragstart = e => {
        if (session.whole || e.target.matches('input,textarea,button')) { e.preventDefault(); return; }
        session.dragging = id; e.dataTransfer.setData('text/plain', String(id)); e.dataTransfer.effectAllowed = 'move';
      };
      element.ondragover = e => { if (session.dragging !== null) { e.preventDefault(); element.classList.add('drop-target'); } };
      element.ondragleave = () => element.classList.remove('drop-target');
      element.ondrop = e => {
        if (session.dragging === null) return; e.preventDefault();
        const from = session.rows.findIndex(row => row.id === session.dragging), to = session.rows.findIndex(row => row.id === id);
        const [row] = session.rows.splice(from, 1); session.rows.splice(to, 0, row); session.dragging = null; renderRows(row.id);
      };
      element.ondragend = () => { if (session) session.dragging = null; root.querySelectorAll('.drop-target').forEach(row => row.classList.remove('drop-target')); };
    });
    update();
    if (focusId) root.querySelector(`[data-element-input="${focusId}"]`)?.focus();
  }
  return { highlight, open, dismiss() { session = null; } };
})();
