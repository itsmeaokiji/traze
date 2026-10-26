"""Operator ceilings preserve accounting and unchanged operation/visitor guards."""
import argparse
import copy
from contextlib import redirect_stdout
import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from hemi_lookup.discovery_allowance import megabytes, validate_ceiling
from hemi_lookup.errors import BudgetError, DataError
from hemi_lookup.storage import Session
from hemi_lookup.web import Jobs, LocalServer, WebError, main
from test_demo import FakeSources, REFERENCE

class DiscoveryAllowanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='traze_discovery_allowance_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        self.state.mkdir()
        self.ledger = self.state / 'session.json'
        self.original = {'schema': 1, 'prior_validation_bytes': {}, 'downloaded_bytes': 65_600_017,
                         'runs': [{'id': 'controlled-closed-run', 'closed_at': '2026-10-01T00:00:00Z',
                                   'received_bytes': 65_600_017, 'requests': [], 'body_attempts': 20}]}
        self.ledger.write_text(json.dumps(self.original), encoding='utf-8')

    def jobs(self, ceiling=100_000_000):
        return Jobs(self.root, self.state, mode='discovery', discovery_limit_bytes=ceiling)

    def test_numeric_validation_rejects_nonfinite_boolean_and_unsafe_limits(self):
        for value in (True, False, None, '200', 200.0, float('nan'), float('inf'), -1, 0, 1_000_000, 2**53):
            with self.subTest(value=value), self.assertRaises(DataError):
                validate_ceiling(value)
        for value in ('', '-1', '1', '0', '2.5', 'nan', 'inf', '9007199255', '1e3', '200 MB'):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                megabytes(value)
        self.assertEqual(megabytes('200'), 200_000_000)
        self.assertEqual(megabytes('2'), 2_000_000)

    def test_default_custom_and_lower_ceiling_recompute_from_same_unchanged_ledger(self):
        before = self.ledger.read_bytes()
        for ceiling, usable in ((100_000_000, 33_399_983), (200_000_000, 133_399_983), (66_000_000, 0)):
            with self.subTest(ceiling=ceiling):
                jobs = self.jobs(ceiling)
                values = jobs.resources()
                self.assertEqual(values['session_limit_bytes'], ceiling)
                self.assertEqual(values['total_response_bytes'], 65_600_017)
                self.assertEqual(values['usable_response_bytes'], usable)
                self.assertEqual(jobs.state()['limits']['session_response_mb'], ceiling / 1_000_000)
        self.assertEqual(self.ledger.read_bytes(), before)
        # Restart without the explicit setting restores the default, never zero usage.
        self.assertEqual(self.jobs().resources()['usable_response_bytes'], 33_399_983)

    def test_prior_validation_and_conservative_charges_are_not_removed_by_configuration(self):
        value = copy.deepcopy(self.original)
        value['prior_validation_bytes'] = {'HEMI_LIGHTWEIGHT_PROBE_RESULT.json': 2_000_000}
        value['runs'][0]['requests'] = [{'accounting': 'conservative_reservation', 'received_bytes': 4_065_536}]
        self.ledger.write_text(json.dumps(value), encoding='utf-8')
        before = self.ledger.read_bytes()
        self.assertEqual(self.jobs(200_000_000).resources()['total_response_bytes'], 67_600_017)
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_empty_allowance_blocks_source_session_but_keeps_saved_evidence_readable(self):
        directory = self.state / 'receipts'; directory.mkdir()
        path = directory / ('web-' + 'a' * 32 + '.json')
        path.write_text(json.dumps(REFERENCE), encoding='utf-8')
        before = self.ledger.read_bytes()
        jobs = self.jobs(66_000_000)
        with patch('hemi_lookup.web.Session', side_effect=AssertionError('No source session')):
            self.assertIsNotNone(jobs.recorded(REFERENCE['query']['block_number']))
            self.assertEqual(jobs.receipt('a' * 32)['verification']['proofs_checked'], len(REFERENCE['proofs']))
            pending = jobs.start('lookup', {'block': 999999})
            final = self.wait(jobs, pending['id'])
        self.assertEqual(final['error']['code'], 'budget_limit')
        self.assertEqual(final['error']['limit'], 'cumulative_response_bytes')
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_higher_ceiling_does_not_remove_active_writer_or_restore_guard(self):
        jobs = self.jobs(200_000_000)
        before = self.ledger.read_bytes()
        lock = self.state / 'session.lock'; lock.write_text('controlled-owner')
        with self.assertRaises(WebError) as error:
            jobs.start('lookup', {'block': 999999})
        self.assertEqual(error.exception.code, 'busy')
        lock.unlink()
        (self.state / 'restore-guard.json').write_text('{}')
        self.assertEqual(jobs.resources()['blocked_reason'], 'restored_state_requires_review')
        with patch('hemi_lookup.web.Session', side_effect=AssertionError('No recovery bypass')):
            pending = jobs.start('lookup', {'block': 999999})
            final = self.wait(jobs, pending['id'])
        self.assertEqual(final['error']['code'], 'restored_state_requires_review')
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_unreadable_ledger_fails_closed_even_with_higher_ceiling(self):
        self.ledger.write_text('{controlled-invalid', encoding='utf-8')
        before = self.ledger.read_bytes()
        jobs = self.jobs(200_000_000)
        self.assertFalse(jobs.resources()['available'])
        with patch('hemi_lookup.web.Session', side_effect=AssertionError('No ledger reset')):
            pending = jobs.start('lookup', {'block': 999999})
            final = self.wait(jobs, pending['id'])
        self.assertEqual(final['error']['code'], 'allowance_unavailable')
        self.assertEqual(self.ledger.read_bytes(), before)

    def wait(self, jobs, identity):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            job = jobs.job(identity)
            if job['state'] != 'running' and jobs.active is None:
                return job
            time.sleep(.01)
        self.fail('Controlled job did not close')

    def test_session_reservation_uses_new_ceiling_without_resetting_old_run_prefix(self):
        value = copy.deepcopy(self.original); value['downloaded_bytes'] = 98_600_000
        self.ledger.write_text(json.dumps(value), encoding='utf-8')
        with Session(self.state, self.root, session_limit=100_000_000) as session:
            with self.assertRaises(BudgetError) as error:
                session.reserve('controlled://metadata', 1_000_000)
            self.assertEqual(error.exception.limit, 'cumulative_response_bytes')
        after_low = json.loads(self.ledger.read_text())
        with Session(self.state, self.root, session_limit=200_000_000, context={'operation': 'controlled_ceiling_test'}) as session:
            entry = session.reserve('controlled://metadata', 1_000_000)
            session.charge(entry, 1234, 'controlled_complete')
            self.assertEqual(session.total_bytes, 98_601_234)
            self.assertEqual(session.max_bytes, 40_000_000)
            self.assertEqual(session.max_blocks, 20)
            self.assertEqual(session.max_requests, 600)
            self.assertEqual(session.max_seconds, 600)
            with self.assertRaises(BudgetError) as error:
                session.reserve('controlled://over-operation-limit', 40_000_000)
            self.assertEqual(error.exception.limit, 'response_bytes')
        after_high = json.loads(self.ledger.read_text())
        self.assertEqual(after_high['runs'][:len(after_low['runs'])], after_low['runs'])
        self.assertEqual(after_high['runs'][0], self.original['runs'][0])
        self.assertEqual(after_high['runs'][-1]['limits']['cumulative_response_bytes'], 200_000_000)

    def test_small_metadata_reservation_can_fit_without_a_full_body_reservation(self):
        value = copy.deepcopy(self.original); value['downloaded_bytes'] = 98_600_000
        self.ledger.write_text(json.dumps(value), encoding='utf-8')
        with Session(self.state, self.root, session_limit=100_000_000) as session:
            entry = session.reserve('controlled://small-metadata', 1024)
            session.charge(entry, 512, 'controlled_complete')
            with self.assertRaises(BudgetError) as error:
                session.reserve('controlled://full-body', 4_000_000, block=True)
            self.assertEqual(error.exception.limit, 'cumulative_response_bytes')

    def test_local_job_passes_configured_ceiling_into_actual_session_and_new_run_limits(self):
        before = json.loads(self.ledger.read_text())
        jobs = self.jobs(200_000_000)
        with patch('hemi_lookup.web.PublicSources', return_value=FakeSources()), patch('hemi_lookup.web.Session', wraps=Session) as session:
            pending = jobs.start('lookup', {'block': 5243117, 'bitcoin_range': [965931, 965931]})
            final = self.wait(jobs, pending['id'])
        self.assertEqual(final['state'], 'complete', final.get('error'))
        self.assertEqual(session.call_args.kwargs['session_limit'], 200_000_000)
        after = json.loads(self.ledger.read_text())
        self.assertEqual(after['runs'][:len(before['runs'])], before['runs'])
        run = after['runs'][-1]
        self.assertEqual(run['limits'], {'response_bytes': 40_000_000, 'bitcoin_body_attempts': 20,
            'requests': 600, 'overall_seconds': 600, 'cumulative_response_bytes': 200_000_000})
        self.assertEqual(run['context']['discovery_ceiling_bytes'], 200_000_000)
        self.assertEqual(after['downloaded_bytes'], before['downloaded_bytes'])

    def test_cli_binds_operator_option_without_starting_acquisition(self):
        before = self.ledger.read_bytes()
        with patch('hemi_lookup.web.LocalServer') as server, redirect_stdout(io.StringIO()):
            server.return_value.server_port = 8765
            self.assertEqual(main(['--mode', 'discovery', '--state-dir', str(self.state), '--discovery-limit-mb', '200']), 0)
            jobs = server.call_args.args[1]
            self.assertEqual(jobs.discovery_limit_bytes, 200_000_000)
            self.assertEqual(jobs.jobs, {})
            server.return_value.server_close.assert_called_once()
        self.assertEqual(self.ledger.read_bytes(), before)
        with patch('hemi_lookup.web.LocalServer') as server, redirect_stdout(io.StringIO()), patch('sys.stderr', io.StringIO()):
            with self.assertRaises(SystemExit):
                main(['--discovery-limit-mb', '200'])
            server.assert_not_called()

    def test_actual_http_visitors_cannot_change_operator_ceiling(self):
        jobs = self.jobs(200_000_000)
        server = LocalServer(0, jobs)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        thread.start()
        before = self.ledger.read_bytes()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            with urlopen(base + '/api/state', timeout=3) as response:
                state = json.loads(response.read())
            self.assertEqual(state['resources']['session_limit_bytes'], 200_000_000)
            for field in ('discovery_limit_bytes', 'discovery_limit_mb', 'session_limit_bytes'):
                request = Request(base + '/api/lookup', data=json.dumps({'block': 999999, field: 999_000_000}).encode(),
                    headers={'Content-Type': 'application/json', 'Origin': base, 'X-Local-Token': server.token})
                with self.assertRaises(HTTPError) as error:
                    urlopen(request, timeout=3)
                self.assertEqual(error.exception.code, 400)
            self.assertEqual(jobs.discovery_limit_bytes, 200_000_000)
            self.assertEqual(jobs.jobs, {})
            self.assertEqual(self.ledger.read_bytes(), before)
        finally:
            server.shutdown(); server.server_close(); thread.join(2)

if __name__ == '__main__':
    unittest.main()
