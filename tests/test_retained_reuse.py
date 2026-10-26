"""Persisted receipt reuse without a SQLite match, source requests or new ledgers."""
import copy
import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from hemi_lookup.explorer import ReadError
from hemi_lookup.proof import verify_receipt
from hemi_lookup.web import Jobs

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = json.loads((ROOT / 'demo_results/mainnet_5243117.json').read_text(encoding='utf-8'))

class RetainedReuseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='traze_retained_reuse_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        (self.state / 'receipts').mkdir(parents=True)
        self.path = self.save('a' * 32, REFERENCE)
        os.utime(self.path, ns=(1_700_000_000_000_000_000,) * 2)
        for n in range(25):
            self.save(f'{n:032x}', {'query': {'block_number': 100_000 + n}, 'proofs': [], 'status': 'incomplete'})

    def save(self, identity, receipt):
        path = self.state / 'receipts' / f'web-{identity}.json'
        path.write_text(json.dumps(receipt), encoding='utf-8')
        return path

    def hashes(self):
        return {str(p.relative_to(self.state)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.state.rglob('*') if p.is_file()}

    def jobs(self):
        return Jobs(self.root, self.state, mode='discovery')

    def wait(self, jobs, identity):
        end = time.monotonic() + 4
        while time.monotonic() < end:
            value = jobs.job(identity)
            if value['state'] != 'running' and jobs.active is None:
                return value
            time.sleep(.01)
        self.fail('Controlled retained lookup did not finish')

    def test_number_and_hash_reuse_persisted_proof_beyond_twenty_newer_files_after_restart(self):
        before = self.hashes()
        number, block_hash = REFERENCE['query']['block_number'], REFERENCE['query']['block_hash']
        for target in (number, block_hash):
            with self.subTest(target=target), patch('hemi_lookup.web.recorded_lookup', return_value=None), \
                 patch('hemi_lookup.web.Session', side_effect=AssertionError('No source session')):
                jobs = self.jobs()
                self.assertNotIn('a' * 32, jobs.receipts)
                result = jobs.recorded(target)
            self.assertEqual(result['mode'], 'offline_catalogue')
            self.assertEqual(result['completed_at'], REFERENCE['completed_at'])
            self.assertEqual(result['context']['checked_at'], REFERENCE['context']['checked_at'])
            self.assertFalse(result['context']['current_view_confirmed'])
            self.assertEqual(result['resources']['response_bytes_this_run'], 0)
            self.assertEqual(result['resources']['http_requests'], 0)
            self.assertEqual(verify_receipt(result)['proofs_checked'], len(REFERENCE['proofs']))
            self.assertTrue(all(p['current_bitcoin_depth_in_provider_view'] is None for p in result['proofs']))
        self.assertEqual(self.hashes(), before)

    def test_normal_job_uses_retained_result_and_restart_does_not_need_transport(self):
        before = self.hashes()
        with patch('hemi_lookup.web.recorded_lookup', return_value=None), \
             patch('hemi_lookup.web.Jobs.public_operation', side_effect=AssertionError('No source fallback')), \
             patch('hemi_lookup.web.Session', side_effect=AssertionError('No accounting session')):
            jobs = self.jobs()
            pending = jobs.start('lookup', {'block': REFERENCE['query']['block_number']})
            final = self.wait(jobs, pending['id'])
            self.assertEqual(final['state'], 'complete')
            self.assertEqual(jobs.receipt(final['receipt_id'])['receipt']['mode'], 'offline_catalogue')
            restarted = self.jobs()
            self.assertIsNotNone(restarted.recorded(REFERENCE['query']['block_hash']))
        after = self.hashes()
        self.assertTrue(all(after[name] == value for name, value in before.items()))
        self.assertEqual(len(after) - len(before), 1)
        self.assertFalse((self.state / 'session.json').exists())

    def test_reusing_an_already_recorded_result_preserves_its_historical_source_dates(self):
        archived = copy.deepcopy(REFERENCE)
        archived['mode'] = 'offline_catalogue'
        archived['context']['checked_at'] = None
        archived['catalogue'] = {'historical_source_times': ['2026-10-03T21:28:50.832423+08:00'],
                                 'number_resolved_from_retained_view': True, 'retained_note': 'Controlled provenance metadata'}
        self.path.write_text(json.dumps(archived), encoding='utf-8')
        before = self.hashes()
        with patch('hemi_lookup.web.recorded_lookup', return_value=None):
            for target in (REFERENCE['query']['block_number'], REFERENCE['query']['block_hash']):
                result = self.jobs().recorded(target)
                self.assertEqual(result['catalogue']['historical_source_times'], archived['catalogue']['historical_source_times'])
                self.assertEqual(result['catalogue']['retained_note'], archived['catalogue']['retained_note'])
                self.assertFalse(result['context']['current_view_confirmed'])
                self.assertEqual(result['completed_at'], archived['completed_at'])
        self.assertEqual(self.hashes(), before)

    def test_altered_proof_is_rejected_even_with_a_stored_success_status(self):
        altered = copy.deepcopy(REFERENCE)
        altered['proofs'][0]['txid'] = '00' * 32
        self.path.write_text(json.dumps(altered), encoding='utf-8')
        with patch('hemi_lookup.web.recorded_lookup', return_value=None):
            self.assertIsNone(self.jobs().recorded(REFERENCE['query']['block_number']))

    def test_unrelated_receipts_are_not_proof_verified(self):
        with patch('hemi_lookup.web.recorded_lookup', return_value=None), \
             patch('hemi_lookup.web.verify_receipt', side_effect=AssertionError('No unrelated verification')):
            self.assertIsNone(self.jobs().recorded(999999))

    def test_bad_file_is_skipped_but_does_not_hide_a_valid_retained_match(self):
        self.save('b' * 32, {'query': None, 'proofs': [True]})
        path = self.state / 'receipts' / ('web-' + 'c' * 32 + '.json')
        path.write_text('{invalid', encoding='utf-8')
        with patch('hemi_lookup.web.recorded_lookup', return_value=None):
            self.assertIsNotNone(self.jobs().recorded(REFERENCE['query']['block_number']))

    def test_ambiguous_numbers_are_not_silently_assigned_a_retained_hash(self):
        alternate = copy.deepcopy(REFERENCE)
        alternate['query']['block_hash'] = '0x' + 'b' * 64
        self.save('b' * 32, alternate)
        # Controlled selection test only; the altered fixture is not a real fork proof.
        with patch('hemi_lookup.web.recorded_lookup', return_value=None), patch('hemi_lookup.web.verify_receipt'):
            self.assertIsNone(self.jobs().recorded(REFERENCE['query']['block_number']))
            self.assertIsNotNone(self.jobs().recorded(REFERENCE['query']['block_hash']))

    def test_incomplete_archive_read_does_not_fall_through_to_acquisition(self):
        before = self.hashes()
        with patch('hemi_lookup.web.recorded_lookup', return_value=None), \
             patch('hemi_lookup.web.retained_receipts', side_effect=ReadError('read_capacity', 'Controlled retained read bound', 503)), \
             patch('hemi_lookup.web.Jobs.public_operation', side_effect=AssertionError('No fallback on incomplete read')):
            jobs = self.jobs()
            pending = jobs.start('lookup', {'block': REFERENCE['query']['block_number']})
            final = self.wait(jobs, pending['id'])
        self.assertEqual(final['state'], 'error')
        self.assertEqual(final['error']['code'], 'retained_read_capacity')
        self.assertEqual(self.hashes(), before)

    def test_capacity_failure_during_proof_check_is_not_a_cache_miss(self):
        with patch('hemi_lookup.web.recorded_lookup', return_value=None), \
             patch('hemi_lookup.web.verify_receipt', side_effect=ReadError('read_capacity', 'Controlled proof read bound', 503)):
            with self.assertRaises(ReadError) as error:
                self.jobs().recorded(REFERENCE['query']['block_number'])
        self.assertEqual(error.exception.code, 'retained_read_capacity')

    def test_archive_enumeration_bound_fails_explicitly_without_rewriting_files(self):
        before = self.hashes()
        with patch('hemi_lookup.web.recorded_lookup', return_value=None), patch('hemi_lookup.saved_history.MAX_FILES', 20):
            with self.assertRaises(ReadError) as error:
                self.jobs().recorded(REFERENCE['query']['block_number'])
        self.assertEqual(error.exception.code, 'retained_read_capacity')
        self.assertEqual(self.hashes(), before)

if __name__ == '__main__':
    unittest.main()
