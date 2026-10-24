"""F2 integration using retained chain evidence and labelled resource controls."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import urlopen

from hemi_lookup import finality_service
from hemi_lookup.assurance import EvidenceStore
from hemi_lookup.catalogue import build_catalogue
from hemi_lookup.catalogue_lookup import recorded_lookup
from hemi_lookup.errors import DeadlineError
from hemi_lookup.explorer import Explorer, ReadBudget
from hemi_lookup.finality import load_receipt, SOURCE_POLICY
from hemi_lookup.public import PublicApplication
from hemi_lookup.web import Jobs, LocalServer
from traze_evidence.snapshot import POLICY_ID

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/finality/recorded_5200117.json'
PACKET = ROOT / 'examples/snapshot-first-height/packet'
FIRST_CONTEXT = '840184d1dab42688a714b71deba8938cb97958d48191bda32619586df4b03b34'
SOURCE_CONTEXT = 'ed69ab5f419d09cc8e7db6c60dbe1cdfea866d7cf3952ecccc1c9973e952bd6c'


def accepted_service(context=SOURCE_CONTEXT):
    return finality_service.FinalityService(source_policy=SOURCE_POLICY, accepted_source_context_sha256=context)


def check_fixture(service=None, receipt=None, **kwargs):
    return (service or accepted_service()).assess(receipt or load_receipt(FIXTURE), packet=PACKET,
        first_policy=POLICY_ID, first_context=FIRST_CONTEXT, check=ReadBudget(), **kwargs)


class FinalityWebsiteTests(unittest.TestCase):
    def setUp(self):
        self.receipt = load_receipt(FIXTURE)
        self.attempts = []
        original = socket.socket.connect
        def local_only(sock, address):
            if address[0] not in ('127.0.0.1', '::1', 'localhost'):
                self.attempts.append(address)
                raise AssertionError('No public-source connection is allowed')
            return original(sock, address)
        guard = patch.object(socket.socket, 'connect', local_only)
        guard.start()
        self.addCleanup(guard.stop)
        self.addCleanup(lambda: self.assertEqual(self.attempts, []))

    def test_actual_worker_matches_offline_numbers_and_does_not_mutate_inputs(self):
        before = deepcopy(self.receipt)
        result = check_fixture(receipt=self.receipt)
        self.assertEqual(result['status'], 'conditional_block_calculation')
        calculation = result['calculation']
        self.assertEqual(calculation['ordinary_verification']['proofs_checked'], 6)
        self.assertEqual(calculation['block_calculation']['values'],
            {'effective_confirmations': 7244, 'raw_bitcoin_confirmations': 7245, 'super_finality': True})
        self.assertEqual(calculation['block_calculation']['reference_publication']['keystone_number'], 5200225)
        self.assertEqual(result['comparison']['block_values']['signed_counter'], 7235)
        self.assertEqual(result['operator_source_acceptance'], 'exact_context_selected')
        self.assertFalse(calculation['assurance']['deployed_finality_equivalence_verified'])
        self.assertFalse(calculation['assurance']['globally_first_proven'])
        self.assertGreater(result['accounted_packet_input_bytes'], 0)
        self.assertEqual(result['network_requests'], 0)
        self.assertEqual(result['denied_network_attempts'], 0)
        self.assertEqual(self.receipt, before)

    def test_no_acceptance_and_wrong_exact_context_preserve_publication_only_results(self):
        for service in (finality_service.FinalityService(), accepted_service('00' * 32)):
            with self.subTest(context=service.accepted_source_context_sha256):
                result = check_fixture(service)
                self.assertEqual(result['status'], 'publication_calculations_only')
                self.assertIsNone(result['calculation']['block_calculation']['values'])
                self.assertEqual(len(result['calculation']['publication_calculations']), 6)

    def test_snapshot_acceptance_is_independent_and_saved_flags_cannot_replace_it(self):
        receipt = deepcopy(self.receipt)
        receipt['finality'] = {'accepted_source_context_sha256': SOURCE_CONTEXT, 'globally_first_proven': True}
        result = accepted_service().assess(receipt, packet=PACKET, first_policy=POLICY_ID,
                                           first_context='00' * 32, check=ReadBudget())
        self.assertEqual(result['status'], 'publication_calculations_only')
        self.assertIsNone(result['calculation']['block_calculation']['values'])

    def test_source_conflict_suspends_block_promotion(self):
        result = check_fixture(source_conflict=True)
        self.assertTrue(result['source_conflict'])
        self.assertEqual(result['status'], 'publication_calculations_only')
        self.assertIsNone(result['calculation']['block_calculation']['values'])

    def test_changed_packet_identity_cannot_reuse_the_displayed_assessment(self):
        result = check_fixture(expected_packet_digest='00' * 32)
        self.assertEqual(result['status'], 'invalid')
        self.assertEqual(result['issues'][0]['code'], 'finality_packet_changed')
        self.assertIsNone(result['calculation'])

    def test_altered_transaction_has_no_numeric_result(self):
        self.receipt['proofs'][0]['txid'] = 'aa' * 32
        result = check_fixture(receipt=self.receipt)
        self.assertEqual(result['status'], 'invalid')
        self.assertIsNone(result['calculation'])

    def test_missing_tip_and_no_positive_proof_are_unknown_not_zero(self):
        for missing in ('bitcoin_tip_after', 'proofs'):
            receipt = deepcopy(self.receipt)
            receipt.pop(missing)
            result = finality_service.FinalityService().assess(receipt)
            self.assertEqual(result['status'], 'unavailable')
            self.assertIsNone(result['calculation'])

    def test_indexed_adapter_preserves_original_scope_and_reconstructable_input(self):
        observation = {key: deepcopy(self.receipt[key]) for key in
                       ('query', 'started_at', 'completed_at', 'context', 'sources', 'bitcoin_tip_before', 'bitcoin_tip_after')}
        generated = deepcopy(self.receipt)
        generated.pop('bitcoin_tip_before'); generated.pop('bitcoin_tip_after')
        generated['mode'] = 'offline_catalogue'
        generated['context'] = {'current_view_confirmed': False,
            'requested_block_identity': {'status': 'not_rechecked', 'requested_hash': generated['query']['block_hash']}}
        generated['coverage'] = {'historical_observations': [{'observation': observation}]}
        before = deepcopy(generated)
        result = check_fixture(receipt=generated)
        self.assertEqual(result['status'], 'publication_calculations_only')
        binding = result['input_binding']
        self.assertEqual(binding['kind'], 'derived_indexed_input')
        restored = deepcopy(generated)
        restored.update(binding['receipt_overrides'])
        digest = hashlib.sha256(finality_service.json_bytes(restored, 8_000_000)).hexdigest()
        self.assertEqual(digest, result['calculation']['receipt_sha256'])
        self.assertFalse(restored['context']['current_view_confirmed'])
        self.assertNotIn('requested_block_identity', restored['context'])
        self.assertEqual(result['calculation']['source_context']['status'], 'incomplete')
        self.assertEqual(binding['retained_tip_observation']['query'], observation['query'])
        self.assertIsNone(result['calculation']['block_calculation']['values'])
        self.assertEqual(generated, before)

    def test_indexed_tip_conflicts_and_missing_observation_time_do_not_guess(self):
        observation = {key: deepcopy(self.receipt[key]) for key in
                       ('query', 'started_at', 'completed_at', 'context', 'sources', 'bitcoin_tip_before', 'bitcoin_tip_after')}
        self.receipt.pop('bitcoin_tip_after'); self.receipt['mode'] = 'offline_catalogue'
        other = deepcopy(observation)
        other['bitcoin_tip_after']['hash'] = 'aa' * 32
        self.receipt['coverage'] = {'historical_observations': [{'observation': observation}, {'observation': other}]}
        result = finality_service.FinalityService().assess(self.receipt)
        self.assertEqual(result['status'], 'conflicting')
        self.assertIsNone(result['calculation'])
        self.receipt['coverage']['historical_observations'] = [{'observation': observation}]
        observation['context'].pop('checked_at')
        self.assertIsNone(finality_service.FinalityService().assess(self.receipt)['calculation'])

    def test_operator_settings_require_a_named_policy_and_exact_context_pair(self):
        for options in ({'source_policy': SOURCE_POLICY}, {'accepted_source_context_sha256': SOURCE_CONTEXT},
                        {'source_policy': 'unknown', 'accepted_source_context_sha256': SOURCE_CONTEXT},
                        {'source_policy': SOURCE_POLICY, 'accepted_source_context_sha256': '../packet'}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                finality_service.FinalityService(**options)

    def test_owned_child_is_killed_and_slot_released_on_original_clock_expiry(self):
        class Child:
            killed = False
            reaped = False
            def poll(self): return None
            def wait(self, timeout=None):
                if timeout is not None: raise subprocess.TimeoutExpired('controlled checker', timeout)
                self.reaped = True
            def kill(self): self.killed = True
        child = Child()
        calls = 0
        def check(amount=0):
            nonlocal calls
            calls += 1
            if calls >= 3: raise DeadlineError()
        with patch.object(finality_service.subprocess, 'Popen', return_value=child):
            with self.assertRaises(DeadlineError):
                finality_service.run_bounded({'receipt': {}}, check=check)
        self.assertTrue(child.killed and child.reaped)
        self.assertTrue(finality_service.WORKERS.acquire(blocking=False))
        finality_service.WORKERS.release()

    def test_capacity_rejection_does_not_start_a_child(self):
        acquired = [finality_service.WORKERS.acquire(blocking=False) for _ in range(finality_service.MAX_WORKERS)]
        try:
            self.assertTrue(all(acquired))
            with patch.object(finality_service.subprocess, 'Popen', side_effect=AssertionError('No child at capacity')):
                self.assertEqual(finality_service.FinalityService().assess(self.receipt)['issues'][0]['code'], 'finality_busy')
        finally:
            for value in acquired:
                if value: finality_service.WORKERS.release()

    def test_actual_expired_calculation_child_is_killed_and_reaped(self):
        children = []
        original = subprocess.Popen
        def capture(*args, **kwargs):
            child = original(*args, **kwargs)
            children.append(child)
            return child
        started = time.monotonic()
        with patch.object(finality_service, 'WORK_SECONDS', .05), \
                patch.object(finality_service.subprocess, 'Popen', side_effect=capture):
            result = finality_service.FinalityService().assess(self.receipt)
        self.assertEqual(result['status'], 'unavailable')
        self.assertIsNone(result['calculation'])
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())
        self.assertIsNotNone(children[0].returncode)
        print(json.dumps({'actual_timeout_control_seconds': round(time.monotonic() - started, 3),
                          'owned_child_reaped': True, 'status': result['status']}))

    def test_packet_reads_respect_original_remaining_input_budget(self):
        budget = ReadBudget()
        budget.bytes = 31_000_000
        result = accepted_service().assess(self.receipt, packet=PACKET, first_policy=POLICY_ID,
                                           first_context=FIRST_CONTEXT, check=budget)
        self.assertIsNone(result['calculation']['block_calculation']['values'] if result['calculation'] else None)
        self.assertLessEqual(budget.bytes, 32_000_000)

    def test_saved_http_result_and_original_receipt_download_remain_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'saved.json'; path.write_text(json.dumps(self.receipt))
            jobs = Jobs(root, assurance_policy=POLICY_ID, accepted_context_sha256=FIRST_CONTEXT,
                finality_source_policy=SOURCE_POLICY, accepted_finality_source_context_sha256=SOURCE_CONTEXT)
            identity = 'a' * 32
            jobs.receipts[identity] = {'path': path, 'label': 'Retained finality evidence', 'origin': 'browser_saved'}
            prepared = root / 'prepared'; shutil.copytree(PACKET, prepared)
            jobs.evidence.publish(self.receipt, prepared)
            server = LocalServer(0, jobs)
            thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
            thread.start()
            try:
                base = 'http://127.0.0.1:' + str(server.server_port)
                with urlopen(base + '/api/receipts/' + identity, timeout=8) as response:
                    result = json.load(response)
                self.assertEqual(result['finality']['status'], 'conditional_block_calculation')
                self.assertEqual(result['receipt'], self.receipt)
                with patch.object(jobs.evidence.finality, 'assess', side_effect=AssertionError('Receipt-only download must not calculate')):
                    with urlopen(base + '/api/receipts/' + identity + '/download', timeout=8) as response:
                        self.assertEqual(json.load(response), self.receipt)
                self.assertFalse((jobs.state_dir / 'session.json').exists())
            finally:
                server.shutdown(); server.server_close(); thread.join(2)


class IndexedFinalityApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.state = Path(cls.temp.name)
        build_catalogue(ROOT, cls.state / 'catalogue.sqlite3', block_directory=ROOT / 'tests/fixtures/bitcoin_blocks')

    def test_actual_number_hash_and_publication_focus_do_not_rewrite_index_or_reference(self):
        path = self.state / 'catalogue.sqlite3'
        before = path.read_bytes()
        explorer = Explorer(self.state)
        first = explorer.read('blocks/5243119', {})
        self.assertEqual(first['finality']['status'], 'publication_calculations_only')
        self.assertEqual(first['finality']['input_binding']['kind'], 'derived_indexed_input')
        self.assertFalse(first['receipt']['context']['current_view_confirmed'])
        self.assertEqual(first['finality']['calculation']['source_context']['status'], 'incomplete')
        exact_hash = first['receipt']['query']['block_hash']
        selected = first['receipt']['proofs'][-1]['publication_id']
        exact = explorer.read('blocks/' + exact_hash, {'publication': selected})
        self.assertEqual(exact['finality']['calculation']['query'], first['finality']['calculation']['query'])
        self.assertEqual(exact['finality']['calculation']['bitcoin_tip'], first['finality']['calculation']['bitcoin_tip'])
        self.assertIsNone(exact['finality']['calculation']['block_calculation']['values'])
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse((self.state / 'session.json').exists())

    def test_wsgi_adapter_returns_same_additive_assessment_and_rejects_client_policy_parameters(self):
        application = PublicApplication(self.state, 'http://127.0.0.1:8888', local_review=True, log=lambda _: None)
        code, raw, mime = application.dispatch('/api/v1/blocks/5243119', {})
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(raw)['finality']['schema'], finality_service.SCHEMA)
        for params in ({'accept_finality_source_context': SOURCE_CONTEXT}, {'tip_height': '972289'}, {'profile': 'unknown'}):
            with self.subTest(params=params), self.assertRaises(Exception) as raised:
                application.dispatch('/api/v1/blocks/5243119', params)
            self.assertEqual(raised.exception.code, 'invalid_filter')
