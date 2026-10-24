"""Offline finality contracts with historical evidence and labelled controls."""

from copy import deepcopy
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from hemi_lookup import finality
from hemi_lookup.proof import verify_receipt
from traze_evidence.snapshot import POLICY_ID

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/finality/recorded_5200117.json'
PACKET = ROOT / 'examples/snapshot-first-height/packet'
SNAPSHOT_CONTEXT = '840184d1dab42688a714b71deba8938cb97958d48191bda32619586df4b03b34'


def arguments(receipt):
    return {'expected_hemi_number': receipt['query']['block_number'],
            'expected_hemi_hash': receipt['query']['block_hash'],
            'expected_tip_height': receipt['bitcoin_tip_after']['height'],
            'expected_tip_hash': receipt['bitcoin_tip_after']['hash']}


class HeightProfileTests(unittest.TestCase):
    def test_code_traced_vectors_keep_the_two_profiles_distinct(self):
        fixture = json.loads((FIXTURE.parent / 'reference_vectors.json').read_text())
        self.assertIn('not_executed_Go', fixture['comparison_basis'])
        for case in fixture['cases']:
            for profile, key in [(finality.BFG_PROFILE, 'bfg_v2'), (finality.AMA_PROFILE, 'ama_model')]:
                with self.subTest(case=case, profile=profile):
                    result = finality.calculate_heights(case['publication_height'], case['tip_height'], profile_id=profile)
                    self.assertEqual(result, {'raw_bitcoin_confirmations': case['raw_bitcoin_confirmations'], **case[key]})

    def test_invalid_numeric_inputs_do_not_turn_into_zero_finality(self):
        for h, t in [(100, 99), (True, 100), (100, False), (-1, 100), (100.0, 101), (0, 1 << 32)]:
            with self.subTest(h=h, t=t), self.assertRaises(finality.FinalityError):
                finality.calculate_heights(h, t)
        for profile in ['unknown', ['invalid'], None]:
            with self.subTest(profile=profile), self.assertRaises(finality.FinalityError):
                finality.calculate_heights(100, 110, profile_id=profile)

    def test_profile_callers_cannot_poison_subsequent_results(self):
        value = finality.profiles()
        value[finality.BFG_PROFILE]['threshold'] = 0
        self.assertFalse(finality.calculate_heights(100, 109)['super_finality'])
        self.assertEqual(finality.profiles()[finality.BFG_PROFILE]['threshold'], 10)


class ReceiptCalculationTests(unittest.TestCase):
    def setUp(self):
        self.receipt = finality.load_receipt(FIXTURE)
        self.attempts = []
        def deny(*args, **kwargs):
            self.attempts.append(True)
            raise AssertionError('Offline finality attempted a source connection')
        for name in ['socket.getaddrinfo', 'socket.create_connection', 'socket.socket.connect', 'socket.socket.connect_ex']:
            guard = patch(name, side_effect=deny)
            guard.start()
            self.addCleanup(guard.stop)
        self.addCleanup(lambda: self.assertEqual(self.attempts, []))

    def calculate(self, receipt=None, **options):
        receipt = self.receipt if receipt is None else receipt
        args = arguments(receipt)
        args.update(options)
        return finality.calculate_receipt(receipt, **args)

    def accepted(self, receipt=None, **options):
        receipt = self.receipt if receipt is None else receipt
        source = self.calculate(receipt)['source_context']['sha256']
        return self.calculate(receipt, first_height_packet=PACKET,
            accepted_first_height_policy=POLICY_ID, accepted_first_context_sha256=SNAPSHOT_CONTEXT,
            accepted_source_policy=finality.SOURCE_POLICY, accepted_source_context_sha256=source, **options)

    def test_actual_direct_and_later_receipts_recompute_all_proofs(self):
        paths = ['demo_results/mainnet_5243117.json', 'demo_results/milestone2_automatic_20261004T170034660491Z.json']
        for name in paths:
            with self.subTest(name=name):
                receipt = finality.load_receipt(ROOT / name)
                receipt['proofs'][0]['current_bitcoin_depth_in_provider_view'] = 1
                before = deepcopy(receipt)
                result = self.calculate(receipt)
                expected = receipt['bitcoin_tip_after']['height'] - receipt['proofs'][0]['bitcoin_height'] + 1
                self.assertEqual(result['ordinary_verification']['proofs_checked'], len(receipt['proofs']))
                self.assertEqual(result['publication_calculations'][0]['values']['raw_bitcoin_confirmations'], expected)
                self.assertEqual(result['status'], 'publication_calculations_only')
                self.assertIsNone(result['block_calculation']['values'])
                self.assertEqual(receipt, before)
        direct = self.calculate(finality.load_receipt(ROOT / paths[0]))
        later = self.calculate(finality.load_receipt(ROOT / paths[1]))
        self.assertIn('covering_keystone', [p['relationship'] for p in direct['publication_calculations']])
        self.assertTrue(all(p['relationship'] == 'later_keystone_support' for p in later['publication_calculations']))

    def test_saved_success_and_selection_flags_cannot_authorize_a_block_result(self):
        self.receipt['selection'] = {'global_earliest_proven': True, 'best_observed_proof_index': 5}
        self.receipt['first_publication'] = {'status': 'verified_conditional_first_height', 'accepted_by_operator': True}
        result = self.calculate()
        self.assertEqual(result['status'], 'publication_calculations_only')
        self.assertFalse(result['assurance']['globally_first_proven'])
        self.assertIsNone(result['block_calculation']['values'])

    def test_wrong_independently_requested_query_is_rejected(self):
        for option in [{'expected_hemi_number': 5200118}, {'expected_hemi_hash': '0x' + '12' * 32}]:
            with self.subTest(option=option), self.assertRaises(finality.FinalityError) as raised:
                self.calculate(**option)
            self.assertEqual(raised.exception.code, 'query_mismatch')

    def test_expected_tip_binds_height_and_hash(self):
        for option in [{'expected_tip_height': 972290}, {'expected_tip_hash': '12' * 32}]:
            with self.subTest(option=option), self.assertRaises(finality.FinalityError) as raised:
                self.calculate(**option)
            self.assertEqual(raised.exception.code, 'tip_mismatch')

    def test_transaction_corruption_cannot_keep_a_saved_validity_flag(self):
        self.receipt['proofs'][0]['txid'] = '12' * 32
        self.receipt['verification'] = {'status': 'passed'}
        with self.assertRaises(finality.FinalityError):
            self.calculate()

    def test_malformed_positions_and_ancestry_are_rejected(self):
        for field, value in [('bitcoin_height', True), ('vout_index', -1), ('tx_index', 2.0)]:
            receipt = deepcopy(self.receipt)
            receipt['proofs'][0][field] = value
            with self.subTest(field=field), self.assertRaises(finality.FinalityError):
                self.calculate(receipt)
        self.receipt['proofs'][0]['ancestry'] *= 3
        with self.assertRaises(finality.FinalityError) as raised:
            self.calculate()
        self.assertEqual(raised.exception.code, 'ancestry_size')

    def test_conflicting_reported_block_positions_are_rejected(self):
        self.receipt['proofs'][0]['bitcoin_height'] += 1
        with self.assertRaises(finality.FinalityError) as raised:
            self.calculate()
        self.assertEqual(raised.exception.code, 'bitcoin_position_conflict')

    def test_competing_hashes_at_one_height_are_rejected(self):
        self.receipt['proofs'][0]['bitcoin_block_hash'] = '12' * 32
        with self.assertRaises(finality.FinalityError) as raised:
            self.calculate()
        self.assertEqual(raised.exception.code, 'bitcoin_branch_conflict')

    def test_tip_before_publication_or_competing_tip_is_rejected(self):
        self.receipt['bitcoin_tip_after']['height'] = 965044
        with self.assertRaises(finality.FinalityError) as raised:
            self.calculate()
        self.assertEqual(raised.exception.code, 'tip_before_publication')
        self.receipt['bitcoin_tip_after']['height'] = 965045
        with self.assertRaises(finality.FinalityError) as raised:
            self.calculate()
        self.assertEqual(raised.exception.code, 'tip_branch_conflict')

    def test_zero_proofs_is_incomplete_not_zero_finality(self):
        self.receipt['proofs'] = []
        result = self.calculate()
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['publication_calculations'], [])
        self.assertIsNone(result['display_selection'])
        self.assertIsNone(result['block_calculation']['values'])

    def test_the_actual_snapshot_cannot_promote_without_first_policy_and_context(self):
        result = self.calculate(first_height_packet=PACKET)
        self.assertEqual(result['first_height_evidence']['status'], 'verified_snapshot_evidence')
        self.assertIsNone(result['block_calculation']['values'])
        self.assertEqual(result['status'], 'publication_calculations_only')

    def test_accepted_first_height_still_requires_separate_recorded_source_acceptance(self):
        result = self.calculate(first_height_packet=PACKET, accepted_first_height_policy=POLICY_ID,
                                accepted_first_context_sha256=SNAPSHOT_CONTEXT)
        self.assertEqual(result['first_height_evidence']['status'], 'conditional_reference_verified')
        self.assertIn('source_context_not_accepted', [p['code'] for p in result['block_calculation']['issues']])
        self.assertIsNone(result['block_calculation']['values'])

    def test_actual_conditional_block_reference_is_independent_of_inspected_transaction(self):
        before = deepcopy(self.receipt)
        other = finality._identity(self.receipt['proofs'][0])
        result = self.accepted(selected_publication=other)
        self.assertEqual(self.receipt, before)
        self.assertEqual(result['status'], 'conditional_block_calculation')
        self.assertEqual(result['display_selection']['publication'], other)
        reference = result['block_calculation']['reference_publication']
        self.assertEqual(reference['txid'], '00331c9acca31ab4533d9b2f10f3e9926e0279af3203c90245049de3387d1900')
        self.assertNotEqual(reference, other)
        self.assertEqual(result['block_calculation']['values'], {'raw_bitcoin_confirmations': 7245,
            'effective_confirmations': 7244, 'super_finality': True})
        self.assertFalse(result['display_selection_changes_block_reference'])
        self.assertTrue(result['assurance']['profile_arithmetic_recomputed'])
        for field in ['current_chain_membership_verified', 'full_consensus_or_execution_verified',
                      'deployed_finality_equivalence_verified', 'globally_first_proven']:
            self.assertFalse(result['assurance'][field])

    def test_accepted_context_does_not_override_recorded_conflict(self):
        self.receipt['context']['bitcoin_secondary'][0]['same'] = False
        self.receipt['context']['current_view_confirmed'] = True
        result = self.accepted()
        self.assertEqual(result['source_context']['status'], 'conflicting')
        self.assertEqual(result['first_height_evidence']['status'], 'conditional_reference_verified')
        self.assertIsNone(result['block_calculation']['values'])

    def test_missing_or_unstable_observations_do_not_promote(self):
        for field in ['bitcoin_secondary', 'hemi_headers']:
            receipt = deepcopy(self.receipt)
            receipt['context'][field] = []
            with self.subTest(field=field):
                result = self.calculate(receipt)
                self.assertEqual(result['source_context']['status'], 'incomplete')
        self.receipt['bitcoin_tip_before'] = {**self.receipt['bitcoin_tip_after'], 'hash': '12' * 32}
        self.assertEqual(self.calculate()['source_context']['status'], 'conflicting')

    def test_forward_tip_advancement_is_recorded_without_claiming_verified_tip_ancestry(self):
        result = self.calculate()
        self.assertEqual(result['source_context']['status'], 'recorded_consistent')
        self.assertEqual(result['source_context']['inputs']['tip_window'], 'advanced_forward')
        self.assertFalse(result['source_context']['accepted_by_caller'])
        self.assertIn('tip ancestry', result['source_context']['assumptions'][0])

    def test_supplied_hemi_header_disagreement_cannot_be_hidden_by_source_success_flags(self):
        self.receipt['proofs'][0]['ancestry'][1]['stateRoot'] = '0x' + '12' * 32
        self.assertEqual(verify_receipt(self.receipt)['proofs_checked'], 6)
        with self.assertRaises(finality.FinalityError) as raised:
            self.calculate()
        self.assertEqual(raised.exception.code, 'hemi_branch_conflict')

    def test_malformed_optional_source_fields_do_not_raise_an_unhandled_exception(self):
        self.receipt['context']['requested_block_identity']['current_hash'] = None
        self.assertEqual(self.calculate()['source_context']['status'], 'conflicting')
        self.receipt['issues'] = None
        with self.assertRaises(finality.FinalityError):
            self.calculate()

    def test_nonobject_packet_and_missing_covering_identity_never_promote(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'manifest.json').write_text('[]', encoding='utf-8')
            result = self.calculate(first_height_packet=directory)
        self.assertEqual(result['first_height_evidence']['status'], 'invalid')
        self.assertIsNone(result['block_calculation']['values'])
        self.receipt['proofs'] = []
        del self.receipt['query']['covering_keystone']
        with self.assertRaises(finality.FinalityError):
            self.calculate()

    def test_missing_timezone_and_inverted_observation_time_are_incomplete(self):
        for value in ['2026-10-20T07:55:17', '2026-10-21T07:55:17+00:00']:
            receipt = deepcopy(self.receipt)
            receipt['context']['checked_at'] = value
            self.assertEqual(self.calculate(receipt)['source_context']['status'], 'incomplete')

    def test_source_policy_is_exact_and_context_cannot_be_reused_for_changed_bytes(self):
        digest = self.calculate()['source_context']['sha256']
        for option in [{'accepted_source_policy': finality.SOURCE_POLICY},
                       {'accepted_source_context_sha256': digest},
                       {'accepted_source_policy': 'unknown', 'accepted_source_context_sha256': digest},
                       {'accepted_source_policy': finality.SOURCE_POLICY, 'accepted_source_context_sha256': '00' * 32}]:
            with self.subTest(option=option), self.assertRaises(finality.FinalityError):
                self.calculate(**option)
        self.receipt['completed_at'] = '2026-10-20T07:56:34+00:00'
        with self.assertRaises(finality.FinalityError) as raised:
            self.calculate(accepted_source_policy=finality.SOURCE_POLICY, accepted_source_context_sha256=digest)
        self.assertEqual(raised.exception.code, 'source_context_mismatch')

    def test_actual_first_packet_is_bound_to_query_and_cannot_be_faked_by_acceptance(self):
        other = finality.load_receipt(ROOT / 'demo_results/mainnet_5243117.json')
        result = self.calculate(other, first_height_packet=PACKET,
            accepted_first_height_policy=POLICY_ID, accepted_first_context_sha256=SNAPSHOT_CONTEXT)
        self.assertEqual(result['first_height_evidence']['status'], 'invalid')
        self.assertIsNone(result['block_calculation']['values'])
        with self.assertRaises(finality.FinalityError):
            self.calculate(accepted_first_height_policy=POLICY_ID)

    def test_full_packet_header_binding_rejects_metadata_the_ordinary_checker_cannot_authenticate(self):
        # Explicitly controlled metadata alteration; no actual chain claim.
        self.receipt['query']['header']['transactionsRoot'] = '0x' + '12' * 32
        for proof in self.receipt['proofs']:
            proof['ancestry'][0]['transactionsRoot'] = '0x' + '12' * 32
        self.assertEqual(verify_receipt(self.receipt)['proofs_checked'], 6)
        result = self.accepted()
        self.assertEqual(result['first_height_evidence']['status'], 'invalid')
        self.assertIn('packet_header_mismatch', [p['code'] for p in result['first_height_evidence']['issues']])
        self.assertIsNone(result['block_calculation']['values'])

    def test_corrupted_required_packet_body_prevents_block_calculation(self):
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / 'packet'
            shutil.copytree(PACKET, copied)
            body = next((copied / 'history/interval/bitcoin').glob('*.bin'))
            with body.open('r+b') as handle:
                handle.seek(100)
                original = handle.read(1)
                handle.seek(100)
                handle.write(bytes([original[0] ^ 1]))
            source = self.calculate()['source_context']['sha256']
            result = self.calculate(first_height_packet=copied,
                accepted_first_height_policy=POLICY_ID, accepted_first_context_sha256=SNAPSHOT_CONTEXT,
                accepted_source_policy=finality.SOURCE_POLICY, accepted_source_context_sha256=source)
        self.assertEqual(result['first_height_evidence']['status'], 'invalid')
        self.assertIsNone(result['block_calculation']['values'])

    def test_api_returns_independent_fresh_objects_and_never_reuses_a_poisoned_result(self):
        result = self.calculate()
        result['profile']['threshold'] = 0
        result['publication_calculations'][0]['values']['effective_confirmations'] = -100
        again = self.calculate()
        self.assertEqual(again['profile']['threshold'], 10)
        self.assertEqual(again['publication_calculations'][0]['values']['effective_confirmations'], 7244)


class InputAndCliTests(unittest.TestCase):
    def test_duplicate_nonfinite_and_oversized_json_are_rejected(self):
        for content in ['{"schema":1,"schema":2}', '{"value":NaN}', ' ' * (finality.MAX_RECEIPT_BYTES + 1)]:
            with self.subTest(content=content[:40]), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'receipt.json'
                path.write_text(content, encoding='utf-8')
                with self.assertRaises(finality.FinalityError):
                    finality.load_receipt(path)

    def test_actual_cli_is_source_free_and_reports_invalid_identity_as_nonzero(self):
        receipt = finality.load_receipt(FIXTURE)
        args = arguments(receipt)
        command = ['--receipt', str(FIXTURE), '--hemi-block', str(args['expected_hemi_number']),
                   '--hemi-hash', args['expected_hemi_hash'], '--tip-height', str(args['expected_tip_height']),
                   '--tip-hash', args['expected_tip_hash']]
        runner = ('import runpy, socket; from unittest.mock import patch; '
                  'deny=lambda *a,**k: (_ for _ in ()).throw(AssertionError("source attempt")); '
                  'patch("socket.getaddrinfo",side_effect=deny).start(); '
                  'patch("socket.socket.connect",side_effect=deny).start(); '
                  'runpy.run_module("hemi_lookup.finality",run_name="__main__")')
        with tempfile.TemporaryDirectory() as directory:
            for bad in [False, True]:
                supplied = list(command)
                if bad:
                    supplied[supplied.index('--hemi-hash') + 1] = '0x' + '12' * 32
                child = subprocess.run([sys.executable, '-B', '-c', runner, *supplied], cwd=ROOT,
                                       capture_output=True, timeout=20)
                self.assertEqual(child.stderr, b'')
                result = json.loads(child.stdout)
                self.assertEqual(child.returncode, 1 if bad else 0)
                self.assertEqual(result['status'], 'invalid' if bad else 'publication_calculations_only')
                self.assertEqual(result['network_requests'], 0)


if __name__ == '__main__':
    unittest.main()
