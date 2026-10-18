'use strict';
// The accepted engine is shared by the UI and the authority process. No input is code.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const root = process.argv[2];
const engineDigest = crypto.createHash('sha256');
const ruleSources = new Map();
for (const [name, source] of [['demo-plan.js',path.join(root,'demo-plan.js')],
  ['config-format.js',path.join(root,'config-format.js')], ['engine_worker.cjs',__filename]]) {
  const bytes=fs.readFileSync(source); ruleSources.set(name,bytes);
  engineDigest.update(name+'\0').update(bytes).update('\0');
}
const engineHash=engineDigest.digest('hex');
const context = vm.createContext({});
for (const file of ['demo-plan.js', 'config-format.js'])
  vm.runInContext(ruleSources.get(file).toString('utf8'), context, { filename: file });
const allowed = new Set(['build', 'buildBatch', 'captureBaseline', 'createRemovalState',
  'syncRemovalState', 'advanceRemovalState', 'buildElementEdit', 'matches', 'selectionChange']);

function advanceTrustedRemovalState({ baseline, state, plan, snapshot }) {
  // Only the authority's verified-write callback uses this adapter. Unknown
  // refreshes still use syncRemovalState with the real opaque revision.
  const engine = context.DemoPlan;
  if (!baseline || !plan || !snapshot) throw new Error('A trusted transition requires baseline, plan and snapshot.');
  const original = state || engine.createRemovalState(baseline);
  const fields = ['exists', 'raw', 'type', 'revision'];
  const sameValue = (left, right) => ['exists', 'raw', 'type'].every(key =>
    Object.hasOwn(left || {}, key) && Object.hasOwn(right || {}, key) && left[key] === right[key]);
  const sameSnapshot = (left, right) => sameValue(left, right)
    && Object.hasOwn(left || {}, 'revision') && Object.hasOwn(right || {}, 'revision')
    && left.revision === right.revision;
  if (!original.snapshot || engine.syncRemovalState({ baseline, state: original, snapshot: original.snapshot }) !== original
      || !sameSnapshot({ ...plan.before, revision: plan.baseRevision }, original.snapshot)
      || Object.hasOwn(plan.before || {}, 'revision') && plan.before.revision !== plan.baseRevision
      || !plan.valid || !Array.isArray(plan.operations) || !sameValue(plan.after, snapshot)) {
    throw new Error('Trusted transition does not match the complete before/after snapshots.');
  }
  const target = baseline.targetKey;
  const sameTarget = value => value && value.scope === target.scope
    && context.ConfigFormat.matchName(value.name) === target.canonicalName;
  if (!sameTarget(plan.targetKey) || (Object.hasOwn(snapshot, 'scope') || Object.hasOwn(snapshot, 'name')) && !sameTarget(snapshot)
      || (Object.hasOwn(plan.before, 'scope') || Object.hasOwn(plan.before, 'name')) && !sameTarget(plan.before)) {
    throw new Error('Trusted transition target does not match its baseline.');
  }
  if (!Object.hasOwn(snapshot, 'revision') || snapshot.revision === null
      || plan.operations.length && snapshot.revision === original.snapshot.revision
      || !plan.operations.length && !sameSnapshot(snapshot, original.snapshot)) {
    throw new Error('Trusted transition requires a verified new revision, or an exact no-op.');
  }
  // Preserve every state/context/audit field. Only revisions are translated;
  // the existing engine still validates raw slices and complete index mappings.
  const clone = value => JSON.parse(JSON.stringify(value));
  const options = clone({ baseline, state: original, plan, snapshot });
  const baselineRevision = sameSnapshot(baseline.snapshot, original.snapshot) ? 0 : -1;
  options.baseline.snapshot.revision = baselineRevision;
  options.state.baselineSnapshot.revision = baselineRevision;
  options.state.snapshot.revision = 0;
  options.plan.baseRevision = 0;
  if (Object.hasOwn(options.plan.before, 'revision')) options.plan.before.revision = 0;
  options.snapshot.revision = plan.operations.length ? 1 : 0;
  const advanced = clone(engine.advanceRemovalState(options));
  advanced.baselineSnapshot = clone(original.baselineSnapshot);
  advanced.snapshot = Object.fromEntries(fields.map(key => [key, snapshot[key]]));
  return advanced;
}

let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => {
  input += chunk;
  if (Buffer.byteLength(input, 'utf8') > 16 * 1024 * 1024) {
    process.stderr.write('Planner request exceeds the size limit.');
    process.exit(2);
  }
});
process.stdin.on('end', () => {
  try {
    const request = JSON.parse(input);
    if (request.expectedEngineHash !== engineHash) throw new Error('Planner resources changed; restart the product before creating a new plan.');
    function execute(call) {
      if (call.method === 'handshake') {
        return { protocol: 1, engineHash };
      }
      if (call.method === 'configErrors') return context.ConfigFormat.errors(call.options);
      if (call.method === 'serializeConfig') return context.ConfigFormat.serialize(call.options);
      if (call.method === 'advanceTrustedRemovalState') return advanceTrustedRemovalState(call.options);
      if (!allowed.has(call.method)) throw new Error('Unsupported planner operation.');
      return context.DemoPlan[call.method](...(call.args || [call.options]));
    }
    const result = Array.isArray(request.calls) ? request.calls.map(execute) : execute(request);
    process.stdout.write(JSON.stringify({ ok: true, result }));
  } catch (error) {
    process.stdout.write(JSON.stringify({ ok: false, error: String(error.message || error) }));
    process.exitCode = 1;
  }
});
