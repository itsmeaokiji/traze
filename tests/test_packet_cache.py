"""Prepared-packet invalidation, capacity and original-checker fallback controls.

Small synthetic files exercise cache boundaries; they are not chain evidence.
The separate public-example case runs the actual isolated packet checker.
"""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from hemi_lookup import assurance, packet_cache
from hemi_lookup.errors import DeadlineError
from traze_evidence import snapshot, history
from traze_evidence.crypto import rlp_decode
from traze_evidence.errors import VerificationError
from test_demo import REFERENCE

ROOT = Path(__file__).resolve().parents[1]
NUMBER = 5200117
HASH = '0x2e9a22f7e0f7fa385b80c767ba5ab0e1939b140ea6a00780440933baeb6b48bd'
CONTEXT = '840184d1dab42688a714b71deba8938cb97958d48191bda32619586df4b03b34'


class PacketCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.packet = Path(self.temp.name) / 'packet'
        self.packet.mkdir()
        (self.packet / 'manifest.json').write_text(json.dumps({'schema': snapshot.SCHEMA}))
        inner = self.packet / 'history/interval'
        (inner / 'bitcoin').mkdir(parents=True)
        (self.packet / 'history/manifest.json').write_text('{}')
        (inner / 'manifest.json').write_text(json.dumps({'bitcoin_blocks': [{'hash': 'ab' * 32}]}))
        self.body = inner / 'bitcoin' / ('ab' * 32 + '.bin')
        self.body.write_bytes(b'recorded')
        self.calls = []
        self.cache = packet_cache.PacketResultCache()

    def checker(self, packet, number, block_hash, policy, *, check, accepted_context_sha256):
        check()
        self.calls.append((Path(packet), number, block_hash, policy, accepted_context_sha256))
        body = Path(packet) / self.body.relative_to(self.packet)
        if body.read_bytes() != b'recorded':
            return {'status': 'invalid', 'code': 'controlled_corruption'}
        return {'schema': snapshot.RESULT_SCHEMA, 'status': 'verified_snapshot_evidence',
                'assurance': {'globally_first_proven': False}, 'requested_number': number,
                'hash': block_hash, 'policy': policy, 'context': accepted_context_sha256}

    def verify(self, *, cache=None, number=NUMBER, block_hash=HASH, policy=None, context=None, check=None, verifier=None):
        return (cache or self.cache).verify(self.packet, number, block_hash, policy,
            verifier=verifier or self.checker, check=check, accepted_context_sha256=context)

    def test_first_check_uses_captured_bytes_and_repeat_returns_a_fresh_result(self):
        first = self.verify()
        first['assurance']['globally_first_proven'] = True
        again = self.verify()
        self.assertEqual(len(self.calls), 1)
        self.assertNotEqual(self.calls[0][0], self.packet)
        self.assertFalse(self.calls[0][0].exists())
        self.assertFalse(again['assurance']['globally_first_proven'])

    def test_same_size_corruption_with_restored_mtime_never_hits_or_caches_invalid_result(self):
        self.verify()
        before = self.body.stat()
        self.body.write_bytes(b'altered!')
        os.utime(self.body, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(self.body.stat().st_size, before.st_size)
        self.assertEqual(self.body.stat().st_mtime_ns, before.st_mtime_ns)
        for _ in range(2):
            self.assertEqual(self.verify()['status'], 'invalid')
        self.assertEqual(len(self.calls), 3)

    def test_each_required_manifest_change_forces_a_new_check(self):
        self.verify()
        for name in ('manifest.json', 'history/manifest.json', 'history/interval/manifest.json'):
            path = self.packet / name
            path.write_bytes(path.read_bytes() + b' ')
            self.verify()
        self.assertEqual(len(self.calls), 4)

    def test_request_policy_context_are_distinct_verification_inputs(self):
        for args in ({}, {'number': NUMBER + 1}, {'block_hash': '0x' + '00' * 32},
                     {'policy': history.POLICY_ID}, {'policy': snapshot.POLICY_ID},
                     {'policy': snapshot.POLICY_ID, 'context': CONTEXT}):
            self.verify(**args)
        self.assertEqual(len(self.calls), 6)
        self.assertEqual(self.calls[-1][1:], (NUMBER, HASH, snapshot.POLICY_ID, CONTEXT))

    def test_missing_and_oversized_files_cannot_use_an_existing_success(self):
        self.verify()
        self.body.unlink()
        with self.assertRaises(VerificationError) as error:
            self.verify()
        self.assertEqual(error.exception.status, 'incomplete')
        with self.body.open('wb') as handle:
            handle.truncate(4_000_001)
        with self.assertRaises(VerificationError) as error:
            self.verify()
        self.assertEqual(error.exception.code, 'packet_size')
        self.assertEqual(len(self.calls), 1)

    def test_untrusted_paths_and_duplicate_json_keys_are_rejected(self):
        inner = self.packet / 'history/interval/manifest.json'
        for raw in (b'{"bitcoin_blocks":[],"bitcoin_blocks":[]}',
                    json.dumps({'bitcoin_blocks': [{'hash': '../outside'}]}).encode(),
                    json.dumps({'bitcoin_blocks': [{'hash': 'ab'*32}, {'hash': 'ab'*32}]}).encode()):
            inner.write_bytes(raw)
            with self.assertRaises(VerificationError):
                self.verify()
        self.assertEqual(self.calls, [])

    def test_detected_mutation_during_worker_does_not_publish_a_result(self):
        def changing(*args, **kwargs):
            result = self.checker(*args, **kwargs)
            self.body.write_bytes(b'altered!')
            return result
        with self.assertRaises(VerificationError) as error:
            self.verify(verifier=changing)
        self.assertEqual(error.exception.code, 'evidence_snapshot_changed')
        self.assertFalse(self.cache._entries)

    def test_changed_rules_remain_disabled_until_new_instance(self):
        marker = Path(self.temp.name) / 'identity.txt'
        marker.write_bytes(b'original')
        files = packet_cache._rule_files() + (marker,)
        with patch('hemi_lookup.packet_cache._rule_files', return_value=files):
            cache = packet_cache.PacketResultCache()
            self.verify(cache=cache)
            marker.write_bytes(b'changed')
            with self.assertRaises(VerificationError):
                self.verify(cache=cache)
            marker.write_bytes(b'original')
            with self.assertRaises(VerificationError):
                self.verify(cache=cache)
            fresh = packet_cache.PacketResultCache()
            self.verify(cache=fresh)
        self.assertEqual(len(self.calls), 2)

    def test_existing_clock_expires_before_reuse_and_during_capture(self):
        self.verify()
        def expired():
            raise DeadlineError()
        with self.assertRaises(DeadlineError):
            self.verify(check=expired)
        calls = 0
        def during():
            nonlocal calls
            calls += 1
            if calls == len(self.cache._files) + 14:
                raise DeadlineError()
        with self.assertRaises(DeadlineError):
            self.verify(check=during)
        self.assertEqual(len(self.calls), 1)

    def test_occupied_capture_capacity_runs_original_checker_without_capturing(self):
        self.cache._slot.acquire()
        try:
            with patch.object(self.cache, '_capture', side_effect=AssertionError('No competing capture')):
                result = self.verify()
            self.assertEqual(result['status'], 'verified_snapshot_evidence')
            self.assertEqual(self.calls[0][0], self.packet)
            self.assertFalse(self.cache._entries)
        finally:
            self.cache._slot.release()

    def test_disabled_or_unavailable_temporary_storage_runs_original_checker(self):
        self.verify(cache=packet_cache.PacketResultCache(enabled=False))
        with patch('hemi_lookup.packet_cache.tempfile.TemporaryDirectory', side_effect=OSError('Controlled unavailable scratch')):
            self.verify()
        self.assertTrue(all(row[0] == self.packet for row in self.calls))
        self.assertFalse(self.cache._entries)

    def test_fallback_keeps_the_original_clock(self):
        calls = 0
        def expired_in_worker():
            nonlocal calls
            calls += 1
            if calls == 3:
                raise DeadlineError()
        with self.assertRaises(DeadlineError):
            self.verify(cache=packet_cache.PacketResultCache(enabled=False), check=expired_in_worker)
        self.assertEqual(self.calls, [])

    def test_entry_and_serialized_byte_limits_reverify_evicted_results(self):
        small = packet_cache.PacketResultCache(max_entries=1)
        for number in (NUMBER, NUMBER + 1, NUMBER):
            self.verify(cache=small, number=number)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(len(small._entries), 1)
        value = self.verify()
        limit = len(packet_cache._json_bytes(value)) + 32
        byte_limited = packet_cache.PacketResultCache(max_bytes=limit)
        before = len(self.calls)
        for number in (NUMBER, NUMBER + 1, NUMBER):
            self.verify(cache=byte_limited, number=number)
        self.assertEqual(len(self.calls) - before, 3)
        self.assertLessEqual(byte_limited._size, limit)

    def test_overlarge_result_and_incomplete_result_are_not_cached(self):
        tiny = packet_cache.PacketResultCache(max_bytes=16)
        self.verify(cache=tiny)
        self.verify(cache=tiny)
        self.assertEqual(len(self.calls), 2)
        def incomplete(*args, **kwargs):
            self.calls.append(True)
            return {'status': 'incomplete'}
        for _ in range(2):
            self.verify(verifier=incomplete)
        self.assertEqual(len(self.calls), 4)

    def test_legacy_packets_bypass_snapshot_reuse_in_the_application(self):
        receipt = deepcopy(REFERENCE)
        store = assurance.EvidenceStore(self.temp.name)
        for schema in (history.SCHEMA, 'traze-anchored-publication-evidence-v1'):
            prepared = Path(self.temp.name) / 'prepared'
            prepared.mkdir()
            (prepared / 'manifest.json').write_text(json.dumps({'schema': schema}))
            store.publish(receipt, prepared)
            with patch.object(assurance.PACKET_RESULTS, 'verify', side_effect=AssertionError('Not v3')), \
                 patch('hemi_lookup.assurance.verify_bounded', return_value={'status': 'incomplete'}) as original:
                result = store.assess(receipt)
            self.assertEqual(original.call_count, 1)
            self.assertEqual(result['status'], 'incomplete')


class PublicPacketReuseTests(unittest.TestCase):
    def test_actual_worker_result_reused_without_transferring_source_or_header_qualification(self):
        source = ROOT / 'examples/snapshot-first-height/packet'
        outer = json.loads((source / 'manifest.json').read_text())
        inner = json.loads((source / 'history/interval/manifest.json').read_text())
        headers = {item['number']: item for item in inner['hemi_headers']}
        def metadata(number):
            row = headers[number]
            fields = rlp_decode(bytes.fromhex(row['raw']))
            return {'number': hex(number), 'hash': row['hash'], 'parentHash': '0x' + fields[0].hex(),
                    'stateRoot': '0x' + fields[3].hex(), 'transactionsRoot': '0x' + fields[4].hex(),
                    'timestamp': hex(int.from_bytes(fields[11], 'big'))}
        publication = outer['assessment_context']['publication']
        receipt = {'query': {'block_number': NUMBER, 'block_hash': HASH, 'header': metadata(NUMBER)},
                   'proofs': [{'bitcoin_height': publication['bitcoin_height'], 'bitcoin_block_hash': publication['bitcoin_hash'],
                       'txid': publication['txid'], 'tx_index': publication['tx_index'], 'vout_index': publication['vout'],
                       'checkpoint': {'height': publication['keystone_number'], 'header': metadata(publication['keystone_number'])},
                       'ancestry': []}], 'context': {}, 'issues': []}
        with tempfile.TemporaryDirectory() as folder:
            prepared = Path(folder) / 'prepared'
            shutil.copytree(source, prepared)
            store = assurance.EvidenceStore(folder, policy=snapshot.POLICY_ID, accepted_context_sha256=CONTEXT)
            store.publish(receipt, prepared)
            with patch('hemi_lookup.assurance.PACKET_RESULTS', packet_cache.PacketResultCache()), \
                 patch('hemi_lookup.assurance.verify_bounded', wraps=assurance.verify_bounded) as worker:
                first = store.assess(receipt)
                self.assertEqual(first['status'], 'verified_conditional_first_height')
                again = store.assess(receipt)
                self.assertEqual(again, first)
                self.assertEqual(worker.call_count, 1)
                changed = deepcopy(receipt)
                changed['context']['requested_block_identity'] = {'status': 'different_current_rpc_block'}
                suspended = store.assess(changed)
                self.assertEqual(suspended['status'], 'source_conflict')
                self.assertFalse(suspended['packet_available'])
                changed = deepcopy(receipt)
                changed['query']['header']['timestamp'] = '0x1'
                self.assertEqual(store.assess(changed)['status'], 'invalid')
                self.assertEqual(worker.call_count, 1)
                no_acceptance = assurance.EvidenceStore(folder, policy=None)
                evidence_only = no_acceptance.assess(receipt)
                self.assertEqual(evidence_only['status'], 'verified_snapshot_evidence')
                self.assertFalse(evidence_only['accepted_by_operator'])
                self.assertEqual(worker.call_count, 2)
                self.assertFalse(again['globally_first_proven'])
                self.assertEqual(again['result']['context_evidence']['observed_through'],
                                 first['result']['context_evidence']['observed_through'])


if __name__ == '__main__':
    unittest.main()
