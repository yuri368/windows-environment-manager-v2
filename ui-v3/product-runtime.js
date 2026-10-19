/* Transport and durable-operation reconciliation. No environment mutation is computed here. */
'use strict';
window.ProductRuntime = (() => {
  const prefix = '/api/v1', tokenKey = 'envmanager-v3-csrf', pendingKey = 'envmanager-v3-operation';
  let csrf = sessionStorage.getItem(tokenKey) || '', tail = Promise.resolve(), failure = null;
  class ApiError extends Error {
    constructor(status, data) { super(data?.error?.message || `服务请求失败 (${status})`); this.status = status; Object.assign(this, data?.error); this.details = data?.error?.details; }
  }
  async function request(path, {method = 'GET', body, text = false} = {}) {
    const headers = {Accept: text ? 'text/plain' : 'application/json'};
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (method !== 'GET' && csrf) headers['X-CSRF-Token'] = csrf;
    const response = await fetch(prefix + path, {method, headers, credentials: 'same-origin', cache: 'no-store', ...(body === undefined ? {} : {body: JSON.stringify(body)})});
    if (!response.ok) { let data; try { data = await response.json(); } catch {} throw new ApiError(response.status, data); }
    // A newly opened same-origin tab has the HttpOnly cookie, but its own
    // sessionStorage. Only authenticated bootstrap may recover this token.
    if (path === '/bootstrap' && method === 'GET') {
      const token = response.headers?.get('X-CSRF-Token');
      if (token) { csrf = token; sessionStorage.setItem(tokenKey, csrf); }
    }
    return text ? response.text() : response.json();
  }
  async function connect() {
    const fragment = location.hash.slice(1), params = new URLSearchParams(fragment);
    const ticket = params.get('ticket') || (fragment && !fragment.includes('=') ? fragment : null);
    if (ticket) {
      window.history.replaceState(null, '', location.pathname + location.search);
      const data = await request('/session/exchange', {method: 'POST', body: {ticket}});
      csrf = data.csrfToken; sessionStorage.setItem(tokenKey, csrf);
    }
    return request('/bootstrap');
  }
  // Each task observes the revision returned by its predecessor. Failures block
  // planning/saving until an explicit new mutation succeeds; execution is never retried.
  function enqueue(task) {
    const next = tail.then(task);
    tail = next.then(result => { failure = null; return result; }, error => { failure = error; });
    return next;
  }
  async function flush() { await tail; if (failure) throw failure; }
  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  async function reconcile(key, operationId) {
    let result = await request(operationId ? '/operations/' + encodeURIComponent(operationId) : '/operations/by-key/' + encodeURIComponent(key));
    // Only read requests are polled. A disconnected caller can query the same key again.
    for (let i = 0; i < 60 && ['queued','running','pending','executing','prepared','writing','verifying'].includes(result.status); i++) {
      await delay(500);
      result = await request(result.operationId ? '/operations/' + encodeURIComponent(result.operationId) : '/operations/by-key/' + encodeURIComponent(key));
    }
    return result;
  }
  async function execute(plan, reviewToken) {
    if (sessionStorage.getItem(pendingKey)) throw new Error('上次执行结果尚待查询，请先查询原操作。');
    const idempotencyKey = crypto.randomUUID();
    sessionStorage.setItem(pendingKey, idempotencyKey);
    let result;
    try {
      result = await request('/plans/' + encodeURIComponent(plan.planId) + '/execute', {method: 'POST', body: {planId: plan.planId, version: plan.version, digest: plan.digest, reviewToken, idempotencyKey}});
    } catch (error) {
      // HTTP errors also reconcile: the operation may have reached its durable boundary.
      try { result = await reconcile(idempotencyKey); }
      catch { error.operationKey = idempotencyKey; throw error; }
    }
    if (['queued','running','pending','executing','prepared','writing','verifying'].includes(result.status)) result = await reconcile(idempotencyKey, result.operationId);
    settle(result); return result;
  }
  function settle(result) {
    if (['committed','applied_partial_intent','preview_updated','recovery_required','uac_cancelled','cancelled','failed','rejected','compensated','rolled_back','verified'].includes(result.status)) sessionStorage.removeItem(pendingKey);
  }
  async function resume() { const key = sessionStorage.getItem(pendingKey); if (!key) return null; const result = await reconcile(key); settle(result); return result; }
  return {request, connect, enqueue, flush, execute, resume, pendingKey: () => sessionStorage.getItem(pendingKey), ApiError};
})();
