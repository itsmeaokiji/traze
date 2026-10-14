// Independent declared-input binding checks plus the existing Node math checker.
import assert from 'node:assert/strict';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

function binding(r) {
  const q = r.query, kind = q.input_type ?? 'number';
  assert(kind === 'number' || kind === 'hash');
  assert(Number.isSafeInteger(q.block_number) && q.block_number > 0);
  assert.equal(Number(BigInt(q.header.number)), q.block_number);
  const normalized = s => { assert(/^0x[0-9a-f]{64}$/i.test(s)); return s.toLowerCase(); };
  const resolved = normalized(q.block_hash);
  assert.equal(normalized(q.header.hash), resolved);
  if (kind === 'hash') assert.equal(normalized(q.requested_block_hash), resolved);
  else assert.equal(q.requested_block_hash, undefined);
  for (const p of r.proofs) assert.equal(normalized(p.ancestry[0].hash), resolved);
  return { input_type: kind, block_number: q.block_number, requested_hash: q.requested_block_hash ?? null,
    resolved_hash: resolved, declared_input_binding: 'passed' };
}

const paths = process.argv.slice(2);
assert(paths.length > 0, 'Pass positive receipt paths');
const checks = paths.map(path => {
  const raw = readFileSync(path), r = JSON.parse(raw), result = binding(r);
  if (result.input_type === 'hash') {
    const altered = structuredClone(r);
    altered.query.requested_block_hash = '0x' + '44'.repeat(32);
    assert.throws(() => binding(altered));
  }
  return { path: resolve(path), sha256: createHash('sha256').update(raw).digest('hex'), ...result };
});
const child = spawnSync(process.execPath, [fileURLToPath(new URL('./verify-evidence.mjs', import.meta.url)), ...paths], {encoding:'utf8', maxBuffer:1_000_000});
assert.equal(child.status, 0, child.stderr);
const summary = JSON.parse(child.stdout.trim()), math = JSON.parse(readFileSync(summary.saved));
const hashInputCount = checks.filter(c => c.input_type === 'hash').length;
const report = {type:'independent_offline_input_verification', checked_at:new Date().toISOString(),
  status:'passed', input_checks:checks, tampered_requested_hash_rejected:hashInputCount > 0 ? true : null, tampered_hash_inputs_checked:hashInputCount,
  mathematical_verification:math, mathematical_report:summary.saved,
  public_network_requests:0, current_chain_membership_checked:false,
  qualification:'Declared receipt input is checked against provided headers and ancestry; no independent Hemi execution or Bitcoin full-SPV/canonical-chain validation'};
mkdirSync(resolve('demo_results'), { recursive: true });
const output = resolve('demo_results', `node_input_${Date.now()}.json`);
writeFileSync(output, JSON.stringify(report,null,2)+'\n', {flag:'wx'});
console.log(JSON.stringify({saved:output,status:'passed',receipts_checked:math.receipts_checked,proofs_checked:math.proofs_checked}));
