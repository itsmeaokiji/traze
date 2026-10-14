import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const root = fileURLToPath(new URL('../', import.meta.url));
const numberReceipt = join(root, 'demo_results/mainnet_5243117.json');
const hashReceipt = join(root, 'demo_results/milestone3_march_later_hash_20261004T174823173306Z.json');

function isolated(action) {
  const base = resolve(tmpdir());
  const directory = mkdtempSync(join(base, 'traze-verifier-cli-'));
  try { return action(directory); }
  finally {
    const target = resolve(directory);
    assert.equal(dirname(target), base);
    assert(target.startsWith(join(base, 'traze-verifier-cli-')));
    rmSync(target, { recursive: true, force: true });
  }
}

function run(script, receipt, cwd) {
  return spawnSync(process.execPath, [join(root, 'scripts', script), receipt],
    { cwd, encoding: 'utf8', maxBuffer: 1_000_000 });
}

test('number receipt passes math without claiming a requested-hash tamper test', () => isolated(cwd => {
  const child = run('verify-input-binding.mjs', numberReceipt, cwd);
  assert.equal(child.status, 0, child.stderr);
  const report = JSON.parse(readFileSync(JSON.parse(child.stdout).saved, 'utf8'));
  assert.equal(report.mathematical_verification.proofs_checked, 4);
  assert.equal(report.tampered_hash_inputs_checked, 0);
  assert.equal(report.tampered_requested_hash_rejected, null);
}));

test('exact-hash receipt passes math and performs the declared-hash tamper control', () => isolated(cwd => {
  const child = run('verify-input-binding.mjs', hashReceipt, cwd);
  assert.equal(child.status, 0, child.stderr);
  const report = JSON.parse(readFileSync(JSON.parse(child.stdout).saved, 'utf8'));
  assert.equal(report.mathematical_verification.proofs_checked, 6);
  assert.equal(report.tampered_hash_inputs_checked, 1);
  assert.equal(report.tampered_requested_hash_rejected, true);
}));

test('changed declared hash is rejected independently of Python', () => isolated(cwd => {
  const packet = JSON.parse(readFileSync(hashReceipt, 'utf8'));
  packet.query.requested_block_hash = '0x' + '44'.repeat(32);
  const input = join(cwd, 'changed-hash.json');
  writeFileSync(input, JSON.stringify(packet));
  assert.notEqual(run('verify-input-binding.mjs', input, cwd).status, 0);
}));

test('changed Merkle branch is rejected by the independent math checker', () => isolated(cwd => {
  const packet = JSON.parse(readFileSync(numberReceipt, 'utf8'));
  packet.proofs[0].merkle_siblings_display_hex[0] = '44'.repeat(32);
  const input = join(cwd, 'changed-merkle.json');
  writeFileSync(input, JSON.stringify(packet));
  assert.notEqual(run('verify-evidence.mjs', input, cwd).status, 0);
}));

test('a zero-proof packet is not reported as a positive proof check', () => isolated(cwd => {
  const child = run('verify-evidence.mjs', join(root, 'demo_results/scoped_no_match.json'), cwd);
  assert.notEqual(child.status, 0);
  assert.match(child.stderr, /No positive proof/);
}));
