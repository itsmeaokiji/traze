"""Offline discovery/recovery controls using retained bodies and supplied views."""

import copy
import json
import socket
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from hash_fixtures import HashSources, ORDINARY, BODY
from hemi_lookup.discovery import receipt_digest
from hemi_lookup.errors import BudgetError, DataError
from hemi_lookup.proof import verify_receipt
from hemi_lookup.storage import Session, HEADER_RESERVE, SAFETY_MARGIN, SESSION_LIMIT
from hemi_lookup.web import Jobs, WebError

ROOT = Path(__file__).resolve().parent.parent
NEGATIVE = json.loads((ROOT / 'demo_results/scoped_no_match.json').read_text(encoding='utf-8'))
PARENT = 'a' * 32


def partial():
    receipt = copy.deepcopy(NEGATIVE)
    receipt['status'] = 'incomplete'
    receipt['coverage']['plan'] = {'mode': 'explicit_range', 'start': 965925, 'initial_end': 965931}
    receipt['coverage']['stop_reason'] = 'body_attempt_budget'
    receipt['coverage']['gaps'] = []
    receipt['coverage']['unresolved_candidates'] = []
    for block in receipt['coverage']['blocks']:
        block['publication_associations_complete'] = True
    return receipt


class DiscoveryRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='traze_discovery_recovery_')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.state = self.root / '.hemi_lookup_data'
        self.jobs = Jobs(self.root, mode='discovery')
        self.guard = patch.object(socket, 'create_connection', side_effect=AssertionError('Public networking forbidden'))
        self.guard.start()
        self.addCleanup(self.guard.stop)

    def store(self, receipt, identity=PARENT, checkpoint=True):
        path = self.state / 'receipts' / f'web-{identity}.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(receipt), encoding='utf-8')
        self.jobs.receipts[identity] = {'path': path, 'label': 'Controlled retained result', 'origin': 'browser_saved'}
        if checkpoint:
            self.jobs.cursors.save(identity, receipt)
        return path

    def wait(self, identity):
        end = time.monotonic() + 8
        while time.monotonic() < end:
            job = self.jobs.job(identity)
            if job['state'] != 'running' and self.jobs.active is None:
                self.assertEqual(job['state'], 'complete', job.get('error'))
                return job
            time.sleep(.01)
        self.fail('Controlled operation did not finish')

    def payload(self):
        preview = self.jobs.receipt(PARENT)['continuation']
        self.assertTrue(preview['available'], preview)
        return {'receipt_id': PARENT, 'receipt_sha256': preview['receipt_sha256'], 'bitcoin_range': preview['bitcoin_range']}

    def test_ordinary_positive_reuses_without_packet_or_any_source_session(self):
        self.store(ORDINARY, checkpoint=False)
        with patch('hemi_lookup.web.Session', side_effect=AssertionError('No source session')), \
             patch('hemi_lookup.web.PublicSources', side_effect=AssertionError('No sources')):
            job = self.jobs.start('lookup', {'block': ORDINARY['query']['block_number']})
            result = self.jobs.receipt(self.wait(job['id'])['receipt_id'])
        receipt = result['receipt']
        self.assertGreater(result['verification']['proofs_checked'], 0)
        self.assertEqual(receipt['mode'], 'offline_catalogue')
        self.assertFalse(receipt['context']['current_view_confirmed'])
        self.assertEqual(receipt['catalogue']['historical_source_times'], [ORDINARY['context']['checked_at']])
        self.assertEqual(receipt['resources']['http_requests'], 0)
        self.assertTrue(all(p['current_bitcoin_depth_in_provider_view'] is None for p in receipt['proofs']))
        self.assertFalse((self.state / 'session.json').exists())

    def test_altered_positive_cannot_bypass_ordinary_verifier(self):
        receipt = copy.deepcopy(ORDINARY)
        receipt['proofs'][0]['raw_transaction_hex'] = '00'
        self.store(receipt, checkpoint=False)
        self.assertIsNone(self.jobs.recorded(receipt['query']['block_number']))
        self.assertFalse((self.state / 'session.json').exists())

    def test_indexed_positive_does_not_extend_optional_assurance_on_read(self):
        with patch('hemi_lookup.web.recorded_lookup', return_value=copy.deepcopy(ORDINARY)), \
             patch('hemi_lookup.web.Session', side_effect=AssertionError('No automatic stronger acquisition')):
            job = self.jobs.start('lookup', {'block': ORDINARY['query']['block_number']})
            self.wait(job['id'])
        self.assertFalse((self.state / 'session.json').exists())

    def test_next_segment_uses_exact_hash_and_retained_body_without_redownload(self):
        before_path = self.store(partial())
        original = before_path.read_bytes()
        cache = self.state / 'blocks'
        cache.mkdir()
        (cache / (ORDINARY['proofs'][0]['bitcoin_block_hash'] + '.bin')).write_bytes(BODY)
        with Session(self.state, self.root) as old_session:
            pass
        ledger_before = json.loads((self.state / 'session.json').read_text())
        source = HashSources(ORDINARY)
        payload = self.payload()
        self.assertEqual(payload['bitcoin_range'], [965931, 965931])
        with patch('hemi_lookup.web.PublicSources', return_value=source):
            job = self.jobs.start('continue', payload)
            wrapper = self.jobs.receipt(self.wait(job['id'])['receipt_id'])
        receipt = wrapper['receipt']
        self.assertEqual(receipt['mode'], 'live_search_continuation')
        self.assertEqual(receipt['query']['requested_block_hash'], ORDINARY['query']['block_hash'])
        self.assertEqual(receipt['status'], 'found')
        self.assertGreater(verify_receipt(receipt)['proofs_checked'], 0)
        self.assertEqual([b['height'] for b in receipt['coverage']['blocks']], [965931])
        self.assertEqual(receipt['resources']['bitcoin_body_attempts'], 0)
        self.assertEqual(receipt['resources']['cache_body_reads'], 1)
        self.assertEqual(before_path.read_bytes(), original)
        ledger = json.loads((self.state / 'session.json').read_text())
        self.assertEqual(ledger['runs'][:-1], ledger_before['runs'])
        self.assertEqual(ledger['downloaded_bytes'], ledger_before['downloaded_bytes'])
        self.assertEqual(ledger['runs'][-1]['limits']['bitcoin_body_attempts'], 20)
        self.assertEqual(ledger['runs'][-1]['limits']['overall_seconds'], 600)
        self.assertEqual(ledger['runs'][-1]['context']['parent_receipt_sha256'], payload['receipt_sha256'])
        self.assertTrue(all(not p.startswith('/block-height/965925') for p in source.bitcoin_calls))
        self.assertFalse((self.state / 'session.lock').exists())
        # A process restart reopens the new result, not the obsolete cursor.
        reloaded = Jobs(self.root, mode='discovery')
        self.assertEqual(reloaded.receipt(PARENT)['continuation']['reason'], 'newer_result_available')
        self.assertEqual(reloaded.receipt(wrapper['id'])['receipt'], receipt)
        with self.assertRaises(WebError) as error:
            reloaded.start('continue', payload)
        self.assertEqual(error.exception.code, 'continuation_unavailable')

    def test_unavailable_source_preserves_progress_and_offers_the_same_next_height(self):
        path = self.store(partial())
        original = path.read_bytes()
        source = HashSources(ORDINARY)
        with patch.object(source, 'rpc', side_effect=DataError('source_unavailable', 'Controlled unavailable RPC')), \
             patch('hemi_lookup.web.PublicSources', return_value=source):
            job = self.jobs.start('continue', self.payload())
            wrapper = self.jobs.receipt(self.wait(job['id'])['receipt_id'])
        self.assertEqual(wrapper['receipt']['status'], 'incomplete')
        self.assertEqual(wrapper['receipt']['proofs'], [])
        self.assertEqual(wrapper['receipt']['coverage']['blocks'], [])
        self.assertEqual(wrapper['continuation']['bitcoin_range'], [965931, 965931])
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse((self.state / 'session.lock').exists())

    def test_cursor_write_failure_keeps_the_saved_evidence_readable(self):
        path = self.store(partial())
        original = path.read_bytes()
        cache = self.state / 'blocks'
        cache.mkdir()
        (cache / (ORDINARY['proofs'][0]['bitcoin_block_hash'] + '.bin')).write_bytes(BODY)
        with patch('hemi_lookup.web.PublicSources', return_value=HashSources(ORDINARY)), \
             patch.object(self.jobs.cursors, 'save', side_effect=OSError('Controlled unavailable cursor storage')):
            job = self.jobs.start('continue', self.payload())
            result = self.wait(job['id'])
        self.assertEqual(self.jobs.receipt(result['receipt_id'])['receipt']['status'], 'found')
        self.assertTrue(any('Evidence saved' in event['message'] for event in result['events']))
        self.assertEqual(path.read_bytes(), original)

    def test_gap_is_retried_without_crossing_already_completed_heights(self):
        receipt = partial()
        receipt['coverage']['gaps'] = [{'height': 965927, 'message': 'Controlled gap'}]
        self.store(receipt)
        self.assertEqual(self.payload()['bitcoin_range'], [965927, 965927])

    def test_candidate_association_limit_does_not_advance_cursor(self):
        receipt = partial()
        receipt['coverage']['blocks'][1]['publication_associations_complete'] = False
        self.store(receipt)
        self.assertEqual(self.payload()['bitcoin_range'], [965926, 965926])

    def test_tampered_range_digest_or_extra_policy_rejected_before_session(self):
        self.store(partial())
        payload = self.payload()
        for field, changed in [('bitcoin_range', [1, 20]), ('receipt_sha256', '0' * 64), ('new_limit', 1000)]:
            with self.subTest(field=field), patch('hemi_lookup.web.Session', side_effect=AssertionError('No session')):
                with self.assertRaises(WebError):
                    self.jobs.start('continue', {**payload, field: changed})
        self.assertFalse((self.state / 'session.json').exists())

    def test_changed_receipt_or_cursor_is_rejected(self):
        path = self.store(partial())
        original = path.read_bytes()
        changed = partial()
        changed['coverage']['blocks'] = []
        path.write_text(json.dumps(changed), encoding='utf-8')
        self.assertEqual(self.jobs.receipt(PARENT)['continuation']['reason'], 'continuation_changed')
        path.write_bytes(original)
        checkpoint = self.jobs.cursors.path(PARENT)
        value = json.loads(checkpoint.read_text())
        value['scope'][1] += 100
        checkpoint.write_text(json.dumps(value), encoding='utf-8')
        self.assertEqual(self.jobs.receipt(PARENT)['continuation']['reason'], 'continuation_changed')

    def test_original_explicit_end_is_not_extended(self):
        receipt = partial()
        receipt['coverage']['plan']['initial_end'] = 965930
        self.store(receipt)
        self.assertEqual(self.jobs.receipt(PARENT)['continuation']['reason'], 'original_extent_processed')

    def test_old_receipt_requires_retained_bytes_and_preview_writes_nothing(self):
        receipt = partial()
        for block in receipt['coverage']['blocks']:
            block.pop('publication_associations_complete')
        self.store(receipt, checkpoint=False)
        self.assertEqual(self.jobs.receipt(PARENT)['continuation']['reason'], 'continuation_cache_unavailable')
        self.assertFalse((self.state / 'discovery-cursors').exists())
        self.assertFalse((self.state / 'session.json').exists())

    def test_known_source_conflict_disables_continuation(self):
        receipt = partial()
        receipt['context']['bitcoin_blocks'][0]['same'] = False
        self.store(receipt)
        self.assertEqual(self.jobs.receipt(PARENT)['continuation']['reason'], 'source_view_changed')

    def test_exhausted_cumulative_allowance_is_not_renewed(self):
        self.store(partial())
        with Session(self.state, self.root) as session:
            session.state['downloaded_bytes'] = SESSION_LIMIT - SAFETY_MARGIN - 1
            session.save()
        before = (self.state / 'session.json').read_bytes()
        preview = self.jobs.receipt(PARENT)['continuation']
        self.assertFalse(preview['available'])
        self.assertEqual(preview['reason'], 'cumulative_response_bytes')
        with self.assertRaises(WebError) as error:
            self.jobs.start('continue', {'receipt_id': PARENT, 'receipt_sha256': receipt_digest(partial()), 'bitcoin_range': [965931, 965931]})
        self.assertEqual(error.exception.code, 'continuation_allowance')
        self.assertEqual((self.state / 'session.json').read_bytes(), before)

    def test_indexed_mode_rejects_continuation_without_state_creation(self):
        self.jobs.mode = 'indexed-only'
        with self.assertRaises(WebError) as error:
            self.jobs.start('continue', {'receipt_id': PARENT})
        self.assertEqual(error.exception.status, 403)
        self.assertFalse(self.state.exists())

    def test_restored_accounting_guard_prevents_continuation(self):
        self.store(partial())
        (self.state / 'restore-guard.json').write_text('{}', encoding='utf-8')
        wrapper = self.jobs.receipt(PARENT)
        self.assertEqual(wrapper['continuation']['reason'], 'restored_state_requires_review')
        with patch('hemi_lookup.web.Session', side_effect=AssertionError('No restored acquisition')):
            with self.assertRaises(WebError) as error:
                self.jobs.start('continue', {'receipt_id': PARENT, 'receipt_sha256': receipt_digest(partial()), 'bitcoin_range': [965931, 965931]})
        self.assertEqual(error.exception.code, 'continuation_allowance')
        self.assertFalse((self.state / 'session.json').exists())

    def test_resource_limit_is_structured_and_no_reservation_is_lost(self):
        for limits, block, expected in [({'max_blocks': 0}, True, 'bitcoin_body_attempts'),
                                        ({'max_requests': 0}, False, 'requests'),
                                        ({'max_bytes': SAFETY_MARGIN + HEADER_RESERVE}, False, 'response_bytes')]:
            with self.subTest(expected=expected), Session(self.state, self.root, **limits) as session:
                with self.assertRaises(BudgetError) as error:
                    session.reserve('https://controlled.invalid', 1, block=block)
                self.assertEqual(error.exception.limit, expected)
                self.assertEqual(session.run['requests'], [])


if __name__ == '__main__':
    unittest.main()
