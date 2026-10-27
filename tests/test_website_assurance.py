"""Website assurance boundaries using isolated retained-data fixtures."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

from hemi_lookup.assurance import EvidenceStore, POLICY_ID, route_key, verify_bounded
from hemi_lookup.evidence_builder import EvidenceBuilder
from hemi_lookup.evidence_encoding import HEADER_FIELDS, body_row, header_row, transaction_bytes
from hemi_lookup.errors import BudgetError, DataError, DeadlineError
from hemi_lookup.network import PublicSources
from traze_evidence.crypto import keccak256, rlp_encode, transaction_root
from traze_evidence.errors import VerificationError
from test_demo import REFERENCE


class WebsiteAssuranceTests(unittest.TestCase):
    def test_new_packet_records_preparation_separately_and_preserves_existing_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            store = EvidenceStore(folder)
            prepared = Path(folder) / 'prepared'
            prepared.mkdir()
            (prepared / 'manifest.json').write_text('{}', encoding='utf-8')
            receipt = deepcopy(REFERENCE)
            before = deepcopy(receipt)
            with patch('hemi_lookup.assurance.now', return_value='2026-10-20T02:00:00Z'):
                packet = store.publish(receipt, prepared)
            metadata = packet.parent / 'context.json'
            context_bytes = metadata.read_bytes()
            context = json.loads(context_bytes)
            self.assertEqual(context['packet_prepared_at'], '2026-10-20T02:00:00Z')
            self.assertEqual(context['recorded_at'], receipt['completed_at'])
            self.assertEqual(context['source_checked_at'], receipt['context']['checked_at'])
            self.assertFalse(context['fresh_source_check_performed_by_verifier'])
            self.assertEqual(receipt, before)
            prepared.mkdir()
            (prepared / 'manifest.json').write_text('{}', encoding='utf-8')
            with patch('hemi_lookup.assurance.now', side_effect=AssertionError('Existing times must not be rewritten')):
                self.assertEqual(store.publish(receipt, prepared), packet)
            self.assertEqual(metadata.read_bytes(), context_bytes)

    def test_route_identity_binds_query_publication_and_output(self):
        original = deepcopy(REFERENCE)
        first = route_key(original, original['proofs'][0])
        for section, key, value in [('query', 'block_hash', '0x' + '12' * 32),
                                     ('proof', 'txid', '34' * 32), ('proof', 'vout_index', 5),
                                     ('proof', 'tx_index', 1), ('proof', 'bitcoin_block_hash', '56' * 32),
                                     ('proof', 'bitcoin_height', 1)]:
            with self.subTest(key=key):
                changed = deepcopy(original)
                target = changed['query'] if section == 'query' else changed['proofs'][0]
                target[key] = value
                self.assertNotEqual(first, route_key(changed, changed['proofs'][0]))

    def test_legacy_flags_and_packet_policy_cannot_promote_a_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            receipt = deepcopy(REFERENCE)
            receipt['first_publication_attempt'] = {'status': 'verified_conditional_first_height',
                'result': {'assurance': {'globally_first_proven': True}}, 'accepted_policy': POLICY_ID}
            store = EvidenceStore(folder, policy=None)
            result = store.assess(receipt)
            self.assertEqual(result['status'], 'not_available')
            self.assertFalse(result['accepted_by_operator'])
            self.assertFalse(result['globally_first_proven'])
            self.assertFalse(result['packet_available'])
            self.assertFalse(Path(folder, 'first-publication').exists())

    def test_operator_policy_must_be_fixed_and_explicit(self):
        with self.assertRaises(ValueError):
            EvidenceStore('.', policy='any-user-policy')

    def test_failed_preparation_is_visible_without_stronger_success(self):
        with tempfile.TemporaryDirectory() as folder:
            receipt = deepcopy(REFERENCE)
            receipt['first_publication_attempt'] = {'status': 'invalid', 'code': 'history_storage_metadata',
                                                     'message': 'History value differs.'}
            result = EvidenceStore(folder).assess(receipt)
            self.assertEqual(result['status'], 'invalid')
            self.assertEqual(result['issues'][0]['code'], 'history_storage_metadata')
            self.assertFalse(result['packet_available'])

    def test_malformed_snapshot_pointer_is_not_trusted(self):
        with tempfile.TemporaryDirectory() as folder:
            store = EvidenceStore(folder)
            route = store.route(REFERENCE)
            route.mkdir(parents=True)
            (route / 'current.json').write_text(json.dumps({'manifest_sha256': '../../outside'}))
            result = store.assess(REFERENCE)
            self.assertEqual(result['status'], 'incomplete')
            self.assertFalse(result['packet_available'])

    def test_optional_archive_failure_does_not_poison_basic_source_identity(self):
        session = SimpleNamespace(on_close=lambda callback: None, check_deadline=lambda: None)
        sources = PublicSources(session)
        with patch.object(sources, '_rpc_at', side_effect=DataError('source_unavailable', 'Historical state unavailable')) as rpc:
            with self.assertRaises(DataError) as error:
                sources.rpc('eth_getProof', ['0x' + '12' * 20, ['0x1'], '0x2'])
            self.assertEqual(error.exception.code, 'history_source_unavailable')
            self.assertEqual(rpc.call_count, 2)
        self.assertIsNone(sources.hemi.failure)
        self.assertFalse(sources.hemi.switched)

    def test_optional_evidence_cannot_retry_past_budget(self):
        session = SimpleNamespace(on_close=lambda callback: None, check_deadline=lambda: None)
        sources = PublicSources(session)
        with patch.object(sources, '_rpc_at', side_effect=BudgetError('Request limit')) as rpc:
            with self.assertRaises(BudgetError):
                sources.rpc('eth_getProof', ['0x' + '12' * 20, ['0x1'], '0x2'])
            self.assertEqual(rpc.call_count, 1)

    def test_owned_verifier_is_killed_when_the_original_clock_expires(self):
        class Child:
            killed = False
            waited = False
            def poll(self):
                return None
            def wait(self, timeout=None):
                if timeout is not None:
                    raise subprocess.TimeoutExpired('checker', timeout)
                self.waited = True
                return 0
            def kill(self):
                self.killed = True
        child = Child()
        calls = []
        def check():
            calls.append(True)
            if len(calls) == 2:
                raise DeadlineError()
        with patch('hemi_lookup.assurance.subprocess.Popen', return_value=child):
            with self.assertRaises(DeadlineError):
                verify_bounded('.', 25, '0x' + 'ab' * 32, POLICY_ID, check=check)
        self.assertTrue(child.killed and child.waited)

    def test_builder_stops_at_budget_and_preserves_the_publication(self):
        with tempfile.TemporaryDirectory() as folder:
            receipt = deepcopy(REFERENCE)
            engine = SimpleNamespace(sources=SimpleNamespace(rpc=lambda *args: (_ for _ in ()).throw(BudgetError('Request limit'))),
                session=SimpleNamespace(), max_ancestry=256, check_deadline=lambda: None, progress=lambda message: None)
            before = deepcopy(receipt['proofs'])
            result = EvidenceBuilder(engine, EvidenceStore(folder)).prepare(receipt)
            self.assertEqual(result['code'], 'budget_limit')
            self.assertEqual(receipt['proofs'], before)
            self.assertFalse(Path(folder, 'first-publication').exists())

    def test_no_positive_publication_never_starts_evidence_acquisition(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = SimpleNamespace(sources=SimpleNamespace(), session=SimpleNamespace(),
                                      check_deadline=lambda: None)
            result = EvidenceBuilder(engine, EvidenceStore(folder)).prepare({'proofs': []})
            self.assertEqual(result['code'], 'publication_unavailable')
            self.assertFalse(Path(folder, 'first-publication').exists())

    def test_invalid_new_bitcoin_body_is_not_persisted_to_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = SimpleNamespace(get=lambda key: None, put=Mock())
            engine = SimpleNamespace(sources=SimpleNamespace(btc=lambda *args: 'ab' * 32,
                block_raw=lambda *args: (b'invalid body', 'controlled source')),
                session=SimpleNamespace(run={'cache_reads': 0}), cache=cache, check_deadline=lambda: None)
            with self.assertRaises(DataError):
                EvidenceBuilder(engine, EvidenceStore(folder)).bitcoin_body(100)
            cache.put.assert_not_called()

    def test_reusing_saved_evidence_does_not_erase_a_recorded_source_conflict(self):
        from hemi_lookup.web import Jobs
        from hemi_lookup.assurance import conflict
        with tempfile.TemporaryDirectory() as folder:
            receipt = deepcopy(REFERENCE)
            receipt['context']['bitcoin_blocks'] = [{'height': 1, 'same': False}]
            path = Path(folder) / 'saved.json'
            path.write_text(json.dumps(receipt))
            jobs = Jobs(folder, mode='discovery')
            jobs.receipts['a' * 32] = {'path': path, 'label': 'Recorded evidence', 'origin': 'browser_saved'}
            with patch.object(jobs.evidence, 'has_packet', return_value=True):
                result = jobs.recorded(receipt['query']['block_number'])
            self.assertTrue(conflict(result))
            self.assertFalse(result['context']['current_view_confirmed'])

    def test_complete_rpc_header_and_body_are_recomputed(self):
        tx = {'type': '0x7c', 'to': '0x' + '88' * 20, 'gas': '0x1', 'input': '0x1234',
              'hash': '0x' + keccak256(b'\x7c' + rlp_encode([b'\x88' * 20, 1, b'\x12\x34'])).hex()}
        raw_tx = transaction_bytes(tx)
        block = {key: ('0x1' if size is None else '0x' + ('11' * size if size else '')) for key, size in HEADER_FIELDS}
        block['transactionsRoot'] = '0x' + transaction_root([raw_tx]).hex()
        values = [int(block[key], 16) if size is None else bytes.fromhex(block[key][2:]) for key, size in HEADER_FIELDS]
        block['hash'] = '0x' + keccak256(rlp_encode(values)).hex()
        tx.update(blockHash=block['hash'], blockNumber='0x1', transactionIndex='0x0')
        block['transactions'] = [tx]
        self.assertEqual(header_row(block)['number'], 1)
        self.assertEqual(body_row(block)['transactions'], [raw_tx.hex()])
        changed = deepcopy(block)
        changed['transactions'][0]['transactionIndex'] = '0x1'
        with self.assertRaises(VerificationError):
            body_row(changed)
        changed = deepcopy(block)
        changed['gasUsed'] = '0x2'
        with self.assertRaises(VerificationError):
            header_row(changed)

    def test_unknown_header_era_is_incomplete(self):
        with self.assertRaises(VerificationError) as failure:
            header_row({'number': '0x1'})
        self.assertEqual(failure.exception.status, 'incomplete')
