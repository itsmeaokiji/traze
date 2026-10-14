// Offline cross-check independent of the Python parser and verifier.
// Checks supplied data; it does not establish fresh chain membership or SPV.
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import assert from 'node:assert/strict';

const sha = b => createHash('sha256').update(b).digest();
const twice = b => sha(sha(b));
const reversed = b => Buffer.from(b).reverse();
const display = b => reversed(b).toString('hex');
function hex(s, length) {
  s = s.replace(/^0x/, '');
  assert(/^(?:[0-9a-fA-F]{2})*$/.test(s), 'Invalid hex');
  const b = Buffer.from(s, 'hex');
  if (length !== undefined) assert.equal(b.length, length);
  return b;
}

function transaction(raw) {
  let pos = 0;
  function take(n) {
    assert(n >= 0 && pos + n <= raw.length, 'Truncated transaction');
    const value = raw.subarray(pos, pos + n); pos += n; return value;
  }
  function compact() {
    const tag = take(1)[0];
    if (tag < 253) return tag;
    const size = tag === 253 ? 2 : tag === 254 ? 4 : 8;
    const b = take(size);
    const v = size === 8 ? b.readBigUInt64LE() : BigInt(b.readUIntLE(0, size));
    assert(v >= BigInt(tag === 253 ? 253 : tag === 254 ? 65536 : 4294967296), 'Noncanonical size');
    assert(v <= BigInt(raw.length), 'Unbounded vector');
    return Number(v);
  }
  const version = take(4);
  const witnessed = raw[pos] === 0 && raw[pos + 1] !== 0;
  if (witnessed) assert.equal(take(2).toString('hex'), '0001', 'Unsupported witness flag');
  const inputsStart = pos, inputs = compact();
  assert(inputs > 0);
  let coinbase = false;
  for (let i = 0; i < inputs; i++) {
    const prev = take(36);
    if (i === 0 && inputs === 1) coinbase = prev.subarray(0, 32).equals(Buffer.alloc(32)) && prev.readUInt32LE(32) === 0xffffffff;
    take(compact()); take(4);
  }
  const outputs = compact(), scripts = [];
  for (let i = 0; i < outputs; i++) { take(8); scripts.push(take(compact())); }
  const baseEnd = pos;
  if (witnessed) for (let i = 0; i < inputs; i++) {
    const items = compact();
    for (let j = 0; j < items; j++) take(compact());
  }
  const locktime = take(4);
  assert.equal(pos, raw.length, 'Trailing transaction bytes');
  return { digest: twice(Buffer.concat([version, raw.subarray(inputsStart, baseEnd), locktime])), scripts, coinbase, witnessed };
}

function firstReturnPush(script) {
  assert.equal(script[0], 0x6a, 'Not OP_RETURN');
  let offset = 2, length = script[1];
  if (length === 76) { length = script.readUInt8(2); offset = 3; }
  else if (length === 77) { length = script.readUInt16LE(2); offset = 4; }
  else if (length === 78) { length = script.readUInt32LE(2); offset = 6; }
  else assert(length <= 75, 'Not a first data push');
  assert(offset + length <= script.length, 'Truncated push');
  return script.subarray(offset, offset + length);
}

function payload(checkpoint) {
  const h = checkpoint.header, attrs = checkpoint.attributes_transaction;
  assert.equal(attrs.type, '0x7e');
  assert.equal(attrs.blockHash, h.hash);
  assert.equal(Number(BigInt(attrs.blockNumber)), checkpoint.height);
  assert.equal(attrs.to.toLowerCase(), '0x4200000000000000000000000000000000000015');
  assert.equal(attrs.from.toLowerCase(), '0xdeaddeaddeaddeaddeaddeaddeaddeaddead0001');
  const input = hex(attrs.input);
  assert.equal(input.length, { '015d8eb9': 260, '440a5e20': 164, '098999be': 176 }[input.subarray(0, 4).toString('hex')]);
  const origin = input.readBigUInt64BE(28);
  assert(origin <= 0xffffffffn);
  assert.equal(Number(origin), checkpoint.ethereum_origin_height);
  assert.equal(Number(BigInt(h.number)), checkpoint.height);
  assert(checkpoint.height >= 25 && checkpoint.height % 25 === 0);
  if (checkpoint.height > 25) assert.equal(Number(BigInt(checkpoint.previous_header.number)), checkpoint.height - 25);
  const prefix = Buffer.alloc(9); prefix[0] = 1;
  prefix.writeUInt32BE(Number(origin), 1); prefix.writeUInt32BE(checkpoint.height, 5);
  return Buffer.concat([prefix, hex(h.parentHash, 32).subarray(0, 11),
    hex(checkpoint.previous_header?.hash ?? '00'.repeat(32), 32).subarray(0, 12),
    hex(h.stateRoot, 32), hex(h.hash, 32).subarray(0, 12)]);
}

function checkProof(p, query) {
  const data = payload(p.checkpoint);
  assert.equal(data.toString('hex'), p.checkpoint.payload_hex);
  assert.equal(display(twice(data)), p.checkpoint.keystone_id);
  const tx = transaction(hex(p.raw_transaction_hex));
  assert(!tx.coinbase);
  assert.equal(display(tx.digest), p.txid);
  assert(firstReturnPush(tx.scripts[p.vout_index]).equals(Buffer.concat([Buffer.from('HEMI'), data])));
  const header = hex(p.bitcoin_header_hex, 80), digest = twice(header);
  assert.equal(display(digest), p.bitcoin_block_hash);
  const bits = header.readUInt32LE(72), exp = bits >>> 24, word = bits & 0x7fffff;
  assert(!(bits & 0x800000) && word > 0 && exp <= 34, 'Invalid target');
  const target = exp >= 3 ? BigInt(word) << BigInt(8 * (exp - 3)) : BigInt(word) >> BigInt(8 * (3 - exp));
  assert(target > 0n && target < (1n << 256n));
  assert(BigInt('0x' + display(digest)) <= target, 'Header exceeds stated target');
  let root = tx.digest, index = p.tx_index;
  assert(Number.isSafeInteger(index) && index >= 0 && p.merkle_siblings_display_hex.length <= 32);
  for (const sibling of p.merkle_siblings_display_hex) {
    const other = reversed(hex(sibling, 32));
    root = twice(Buffer.concat(index % 2 ? [other, root] : [root, other]));
    index = Math.floor(index / 2);
  }
  assert(root.equals(header.subarray(36, 68)), 'Merkle root mismatch');
  const path = p.ancestry;
  assert(path.length > 0);
  assert.equal(Number(BigInt(path[0].number)), query.block_number);
  for (const key of ['hash', 'parentHash', 'stateRoot', 'transactionsRoot', 'number', 'timestamp']) {
    assert.equal(path[0][key], query.header[key]);
    assert.equal(path.at(-1)[key], p.checkpoint.header[key]);
  }
  assert.equal(path[0].hash, query.block_hash);
  for (let i = 1; i < path.length; i++) {
    assert.equal(BigInt(path[i].number), BigInt(path[i - 1].number) + 1n);
    assert.equal(path[i].parentHash, path[i - 1].hash);
  }
  const cover = Math.ceil(query.block_number / 25) * 25;
  assert.equal(query.covering_keystone, cover);
  assert(p.checkpoint.height >= cover);
  assert.equal(p.relationship, p.checkpoint.height === cover ? 'direct_to_covering_checkpoint' : 'later_checkpoint_support');
  return { txid: p.txid, bitcoin_height: p.bitcoin_height, publishing_checkpoint: p.checkpoint.height,
    ancestry_links: path.length - 1, witness_stripping_required: tx.witnessed, checks: 'passed' };
}

const paths = process.argv.slice(2);
assert(paths.length > 0, 'Pass one or more receipt paths');
const reports = paths.map(path => {
  const raw = readFileSync(path), receipt = JSON.parse(raw);
  assert.equal(receipt.schema, 'hemi-publication-receipt-v1');
  assert.equal(receipt.query.hemi_chain_id, 43111);
  assert.equal(receipt.query.bitcoin_network, 'mainnet');
  assert(receipt.proofs.length > 0, 'No positive proof to cross-check');
  const checks = receipt.proofs.map(p => checkProof(p, receipt.query));
  const alteredRoot = structuredClone(receipt.proofs[0]);
  alteredRoot.merkle_siblings_display_hex[0] = '11'.repeat(32);
  assert.throws(() => checkProof(alteredRoot, receipt.query));
  const alteredState = structuredClone(receipt.proofs[0]);
  alteredState.checkpoint.header.stateRoot = '0x' + '22'.repeat(32);
  assert.throws(() => checkProof(alteredState, receipt.query));
  return { receipt: resolve(path), sha256: sha(raw).toString('hex'), checks,
    tampered_merkle_and_checkpoint_rejected: true };
});
const report = { type: 'independent_offline_node_verification', checked_at: new Date().toISOString(),
  status: 'passed', receipts_checked: reports.length, proofs_checked: reports.reduce((n, r) => n + r.checks.length, 0),
  reports, network_requests: 0, current_chain_membership_checked: false,
  limits: ['No full SPV/difficulty-transition/UTXO checks or independent Hemi header hashing/execution',
    'Bitcoin height is source-reported; supplied-header inclusion does not prove globally earliest publication'] };
mkdirSync(resolve('demo_results'), { recursive: true });
const output = resolve('demo_results', `node_evidence_${Date.now()}.json`);
writeFileSync(output, JSON.stringify(report, null, 2) + '\n', { flag: 'wx' });
console.log(JSON.stringify({ saved: output, status: report.status, receipts_checked: reports.length, proofs_checked: report.proofs_checked }));
