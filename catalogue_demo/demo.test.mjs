import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {routeRecords, resultCopy} from './demo.mjs';

const receipt = JSON.parse(readFileSync(new URL('../demo_results/mainnet_5243118_catalogue.json', import.meta.url)));
test('additional-target route starts with the packet target', () => {
  const records = routeRecords(receipt);
  assert.equal(records[0].value, 5243118);
  assert.equal(records[1].label, 'Covering keystone');
  assert.equal(records.length, 3);
  assert.equal(records.at(-1).chain, 'bitcoin');
});
test('later publication changes the route without changing the packet', () => {
  const original = JSON.stringify(receipt);
  const index = receipt.proofs.findIndex(proof => proof.checkpoint.height !== receipt.query.covering_keystone);
  const records = routeRecords(receipt, index);
  assert.equal(records.length, 4);
  assert.equal(records[2].value, receipt.proofs[index].checkpoint.height);
  assert.equal(records[0].value, 5243118);
  assert.equal(JSON.stringify(receipt), original);
});
test('no positive evidence produces no invented route', () => {
  assert.deepEqual(routeRecords({...receipt, proofs: []}), []);
  assert.equal(resultCopy({status:'no_match'}), 'No match in processed scope');
  assert.equal(resultCopy({status:'incomplete'}), 'Evidence incomplete');
});
test('source-reported height conflict remains unresolved in the route', () => {
  const altered = structuredClone(receipt);
  altered.proofs[0].bitcoin_height = null;
  assert.equal(routeRecords(altered).at(-1).value, 'Height unresolved');
});
