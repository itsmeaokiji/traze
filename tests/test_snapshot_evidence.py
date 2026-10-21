"""Snapshot contract boundaries using explicitly synthetic offline context."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from hemi_lookup.assurance import EvidenceStore

from traze_evidence import verify_packet
from traze_evidence import history, snapshot
from traze_evidence.crypto import rlp_encode, keccak256
from traze_evidence.errors import VerificationError


def fixture():
    headers, blocks = [], {}
    for number in (95, 125):
        fields = [bytes([number]) * 32, b'', b'', bytes([number + 1]) * 32] + [b''] * 7 + [b'\x01'] + [b''] * 9
        raw = rlp_encode(fields)
        row = {'number': number, 'hash': '0x' + keccak256(raw).hex(), 'raw': raw.hex()}
        headers.append(row)
        blocks[number] = {'number': number, 'hash': row['hash'], 'parent_hash': '0x' + fields[0].hex(),
                          'state_root': '0x' + fields[3].hex(), 'timestamp': 1}
    query = {'number': 95, 'hash': headers[0]['hash']}
    publication = {'keystone_number': 125, 'bitcoin_height': 300000, 'bitcoin_hash': '23' * 32,
                   'txid': '45' * 32, 'tx_index': 2, 'vout': 1}
    base = {'query': query, 'publication': publication, 'covering_keystone': 100,
            'anchor_reference': {'hemi_number': 50},
            'verified_interval': {'first_height': 299998, 'last_height': 300000}}
    child = {'query': query, 'status': 'verified_history_evidence', 'interval_evidence': base,
             'history_evidence': {'status': 'verified'}, 'issues': [],
             'assurance': {'first_in_checked_interval': True, 'first_height_under_accepted_assumptions': False,
                           'globally_first_proven': False}}
    packet = {'hemi_headers': headers, 'bitcoin_blocks': [{'hash': '12' * 32}]}
    tip = {'number': 200, 'hash': '0x' + '67' * 32, 'parent_hash': '0x' + '89' * 32,
           'state_root': '0x' + 'ab' * 32, 'timestamp': 2}
    context = {'schema': snapshot.CONTEXT_SCHEMA, 'networks': {'hemi_chain_id': 43111, 'bitcoin': 'mainnet'},
               'history_manifest_sha256': 'cd' * 32, 'query': query, 'publication': publication,
               'covering_keystone': 100, 'anchor': {'hemi_number': 50, 'bitcoin_height': 299998, 'bitcoin_hash': '12' * 32},
               'profile_basis': deepcopy(snapshot.PROFILE_BASIS), 'source_view': {
                   'source': 'https://fixture.invalid/rpc', 'client_version': 'SYNTHETIC OFFLINE FIXTURE',
                   'selection': 'latest_then_number_recheck', 'observed_from': '2026-01-01T00:00:00Z',
                   'observed_through': '2026-01-01T00:00:01Z', 'requested': blocks[95], 'publisher': blocks[125],
                   'snapshot': tip, 'snapshot_recheck': deepcopy(tip)}}
    return context, child, packet


class SnapshotContextTests(unittest.TestCase):
    def test_operator_configuration_and_unprepared_receipts_cannot_self_accept_context(self):
        for policy, context in ((history.POLICY_ID, '00' * 32), (snapshot.POLICY_ID, True),
                                (snapshot.POLICY_ID, '../untrusted')):
            with self.subTest(policy=policy, context=context), self.assertRaises(ValueError):
                EvidenceStore('.', policy=policy, accepted_context_sha256=context)
        with tempfile.TemporaryDirectory() as folder:
            store = EvidenceStore(folder, policy=snapshot.POLICY_ID, accepted_context_sha256='00' * 32)
            checked = store.assess({'query': {'block_number': 1, 'block_hash': '0x'+'12'*32}, 'proofs': [],
                'first_publication_attempt': {'accepted_context_sha256': '00'*32, 'accepted_by_operator': True}})
            self.assertFalse(checked['accepted_by_operator'])
            self.assertEqual(checked['policy_id'], snapshot.POLICY_ID)

    def test_post_p_domain_and_external_guarantees_are_explicit(self):
        context, child, packet = fixture()
        checked = snapshot.check_context(context, 'cd' * 32, child, packet)
        self.assertEqual(checked['domain']['first_hemi_keystone'], 100)
        self.assertEqual(checked['domain']['last_hemi_block'], 200)
        self.assertFalse(checked['historical_execution_certified'])
        self.assertFalse(checked['current_bitcoin_membership_verified'])

    def test_missing_context_is_incomplete(self):
        _, child, packet = fixture()
        with self.assertRaises(VerificationError) as error:
            snapshot.check_context(None, 'cd' * 32, child, packet)
        self.assertEqual(error.exception.status, 'incomplete')

    def test_wrong_network_query_publication_anchor_cover_or_nested_packet_rejected(self):
        for field in ('network', 'query', 'output', 'anchor', 'cover', 'history'):
            context, child, packet = fixture()
            if field == 'network': context['networks']['hemi_chain_id'] = 1
            if field == 'query': context['query'] = dict(context['query'], number=96)
            if field == 'output': context['publication'] = dict(context['publication'], vout=3)
            if field == 'anchor': context['anchor']['bitcoin_hash'] = '00' * 32
            if field == 'cover': context['covering_keystone'] = 125
            if field == 'history': context['history_manifest_sha256'] = 'ef' * 32
            with self.subTest(field=field), self.assertRaises(VerificationError):
                snapshot.check_context(context, 'cd' * 32, child, packet)

    def test_branch_metadata_recheck_and_domain_conflicts_rejected(self):
        for field in ('requested_hash', 'publisher_root', 'tip_recheck', 'domain', 'time', 'selection'):
            context, child, packet = fixture(); view = context['source_view']
            if field == 'requested_hash': view['requested']['hash'] = '0x' + 'ff' * 32
            if field == 'publisher_root': view['publisher']['state_root'] = '0x' + 'ff' * 32
            if field == 'tip_recheck': view['snapshot_recheck']['hash'] = '0x' + 'ff' * 32
            if field == 'domain': view['snapshot']['number'] = view['snapshot_recheck']['number'] = 124
            if field == 'time': view['observed_through'] = '2025-01-01T00:00:00Z'
            if field == 'selection': view['selection'] = 'found_publisher_cutoff'
            with self.subTest(field=field), self.assertRaises(VerificationError):
                snapshot.check_context(context, 'cd' * 32, child, packet)

    def test_unsupported_era_is_incomplete_not_a_claim(self):
        context, child, packet = fixture(); context['profile_basis']['execution_revision'] = 'unknown'
        with self.assertRaises(VerificationError) as error:
            snapshot.check_context(context, 'cd' * 32, child, packet)
        self.assertEqual(error.exception.status, 'incomplete')

    def test_digest_ignores_json_order_but_changes_with_scope(self):
        context, _, _ = fixture(); before = snapshot.context_digest(context)
        self.assertEqual(before, snapshot.context_digest(dict(reversed(list(context.items())))))
        context['source_view']['snapshot']['number'] += 1
        self.assertNotEqual(before, snapshot.context_digest(context))

    def test_boolean_height_and_packet_self_acceptance_fields_rejected(self):
        for field in ('height', 'accepted'):
            context, child, packet = fixture()
            if field == 'height': context['covering_keystone'] = True
            else: context['accepted_by_operator'] = True
            with self.subTest(field=field), self.assertRaises(VerificationError):
                snapshot.check_context(context, 'cd' * 32, child, packet)


class SnapshotPolicyTests(unittest.TestCase):
    def test_new_policy_or_context_cannot_upgrade_a_legacy_packet(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for schema in (history.SCHEMA, 'traze-anchored-publication-evidence-v1'):
                (root / 'manifest.json').write_text(json.dumps({'schema': schema}))
                with self.subTest(schema=schema), self.assertRaises(VerificationError) as error:
                    verify_packet(root, expected_hemi_number=95, expected_hemi_hash='00' * 32,
                                  accepted_policy=snapshot.POLICY_ID, accepted_context_sha256='00' * 32)
                self.assertEqual(error.exception.code, 'snapshot_packet_required')

    def assess(self, policy=None, accepted=None, mutate=None):
        context, child, base_packet = fixture()
        nested_raw = json.dumps({'schema': history.SCHEMA}).encode()
        history_digest = hashlib.sha256(nested_raw).hexdigest()
        context['history_manifest_sha256'] = history_digest
        wanted = snapshot.context_digest(context)
        manifest = {'schema': snapshot.SCHEMA, 'profile': snapshot.PROFILE, 'query': context['query'],
                    'history_manifest_sha256': history_digest, 'assessment_context': context}
        if mutate: mutate(manifest)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root/'history').mkdir()
            (root/'manifest.json').write_text(json.dumps(manifest))
            (root/'history/manifest.json').write_bytes(nested_raw)
            (root/'history/interval').mkdir()
            (root/'history/interval/manifest.json').write_text(json.dumps(base_packet))
            with patch('traze_evidence.snapshot.history.verify_history_packet', return_value=child):
                return verify_packet(root, expected_hemi_number=95, expected_hemi_hash=context['query']['hash'],
                    accepted_policy=policy, accepted_context_sha256=wanted if accepted == 'matching' else accepted)

    def test_both_external_selections_required_and_legacy_policy_does_not_promote(self):
        for policy, accepted in ((None, None), (snapshot.POLICY_ID, None), (history.POLICY_ID, 'matching'), (None, 'matching')):
            checked = self.assess(policy, accepted)
            self.assertEqual(checked['status'], 'verified_snapshot_evidence')
            self.assertFalse(checked['assurance']['first_height_under_accepted_assumptions'])
        checked = self.assess(snapshot.POLICY_ID, 'matching')
        self.assertEqual(checked['status'], 'verified_conditional_first_height')
        self.assertTrue(checked['assurance']['snapshot_bound_under_accepted_assumptions'])
        self.assertFalse(checked['assurance']['globally_first_proven'])
        self.assertFalse(checked['policy']['accepted_from_packet'])

    def test_wrong_external_checksum_or_missing_context_blocks_claim_and_preserves_interval(self):
        checked = self.assess(snapshot.POLICY_ID, '00' * 32)
        self.assertEqual(checked['status'], 'invalid')
        self.assertEqual(checked['issues'][0]['code'], 'snapshot_acceptance_mismatch')
        checked = self.assess(snapshot.POLICY_ID, None, lambda packet: packet.pop('assessment_context'))
        self.assertEqual(checked['status'], 'incomplete')
        self.assertIn('publication', checked['interval_evidence'])

    def test_changed_nested_manifest_and_context_fields_fail_closed(self):
        with self.assertRaises(VerificationError):
            self.assess(mutate=lambda packet: packet.update(history_manifest_sha256='00' * 32))
        checked = self.assess(snapshot.POLICY_ID, 'matching',
            lambda packet: packet['assessment_context']['source_view']['snapshot_recheck'].update(hash='0x'+'00'*32))
        self.assertEqual(checked['status'], 'invalid')


if __name__ == '__main__':
    unittest.main()
