"""Snapshot-bound conditional assessment; external premises stay caller-owned."""

from datetime import datetime
import hashlib
import json
from urllib.parse import urlsplit

from . import history
from . import verify as interval
from .crypto import rlp_decode
from .errors import VerificationError, require

SCHEMA = 'traze-snapshot-publication-evidence-v3'
RESULT_SCHEMA = 'traze-snapshot-verification-result-v3'
CONTEXT_SCHEMA = 'traze-first-height-snapshot-context-v1'
POLICY_ID = 'hemi-snapshot-first-height-assumptions-v1'
PROFILE = {'id': 'hemi-v1-history-snapshot-first-height-v3',
           'history_profile': history.PROFILE['id'], 'context_schema': CONTEXT_SCHEMA}
PROFILE_BASIS = {
    'id': 'hemi-mainnet-protected-history-reference-v1',
    'execution_revision': '539f4e33c02028c8a4207621cd04fa8102e372a0',
    'derivation_revision': '7c70d2ddd3f3d1ca4710da2e5988fa81bc2304b2',
    'configuration_revision': 'dfd866b7e7bd21839a8140b87a89699ddde7f0c3',
    'history_address': history.HISTORY_ADDRESS, 'history_code_hash': history.HISTORY_CODE_HASH,
    'applicability_basis': 'externally_accepted_operator_premise',
    'applicability_scope': 'all_eligible_same_branch_Q_from_cover_through_snapshot',
    'security_premise': 'typed-sha-keccak-extractor-cycle-hardness-including-zero-output-v1',
}
ASSUMPTIONS = (
    {'id': 'validating_source_branch', 'description': 'The selected validating RPC supplies a coherent valid Hemi mainnet branch containing the recorded requested block, publisher and snapshot. Intervening ancestry and consensus are externally trusted.'},
    {'id': 'historical_profile_applicability', 'description': 'The reviewed execution, rollup derivation, deposit-origin and upgrade regime preserves the fresh-parent history invariant for every eligible same-branch keystone through the snapshot, including those after the found publisher. Historical deployment and execution are not independently certified.'},
    {'id': 'recorded_bitcoin_branch', 'description': 'The retained Bitcoin branch is valid. Its recorded observations are not refreshed by the Hemi snapshot or offline verification.'},
    {'id': 'supported_publication_rules', 'description': 'The declared publication predicate and later-keystone support apply within this snapshot domain. Future snapshots, arbitrary forks and unreviewed upgrades are excluded.'},
    {'id': 'generalized_extractor_cycle_hardness', 'description': 'Constructing the reviewed typed SHA/Keccak extractor commitment cycle, including zero-output self-loops, is computationally infeasible. The finite classical model supplies no measured historical work envelope or numerical confidence.'},
)


def context_digest(context):
    """Canonical JSON identity, independent of surrounding manifest whitespace."""
    return hashlib.sha256(json.dumps(context, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def _time(value):
    require(isinstance(value, str) and len(value) <= 128, 'snapshot_observation', 'Invalid observation time')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise VerificationError('snapshot_observation', 'Invalid observation time') from exc
    require(result.utcoffset() is not None, 'snapshot_observation', 'Observation time requires a timezone')
    return result


def _block(value):
    interval._object(value, ('number', 'hash', 'parent_hash', 'state_root', 'timestamp'), 'snapshot block')
    interval._integer(value['number'], 'snapshot block number', minimum=1)
    interval._integer(value['timestamp'], 'snapshot block timestamp', minimum=1, maximum=(1 << 64) - 1)
    for field in ('hash', 'parent_hash', 'state_root'):
        interval._hash(value[field], 'snapshot ' + field, hemi=True)
    return value


def check_context(context, history_digest, child, base_packet):
    require(context is not None, 'snapshot_context_missing', 'Snapshot context is missing', incomplete=True)
    interval._object(context, ('schema', 'networks', 'history_manifest_sha256', 'query', 'publication',
                              'covering_keystone', 'anchor', 'source_view', 'profile_basis'), 'assessment context')
    require(context['schema'] == CONTEXT_SCHEMA, 'snapshot_context_schema', 'Unsupported snapshot context', incomplete=True)
    require(interval._same_json(context['networks'], {'hemi_chain_id': 43111, 'bitcoin': 'mainnet'}),
            'snapshot_network', 'Snapshot context uses another network')
    require(context['history_manifest_sha256'] == history_digest,
            'snapshot_history_binding', 'Snapshot context belongs to another history packet')
    base = child['interval_evidence']
    require(interval._same_json(context['query'], base['query']) and
            interval._same_json(context['publication'], base['publication']),
            'snapshot_route_binding', 'Snapshot context belongs to another query or publication')
    cover = interval._integer(context['covering_keystone'], 'context covering keystone', minimum=1)
    require(cover == base['covering_keystone'], 'snapshot_cover_binding', 'Covering keystone differs')
    expected_anchor = {'hemi_number': base['anchor_reference']['hemi_number'],
                       'bitcoin_height': base['verified_interval']['first_height'],
                       'bitcoin_hash': base_packet['bitcoin_blocks'][0]['hash']}
    require(interval._same_json(context['anchor'], expected_anchor),
            'snapshot_anchor_binding', 'Context anchor differs from the checked Bitcoin/Hemi route')
    require(interval._same_json(context['profile_basis'], PROFILE_BASIS),
            'snapshot_profile_unsupported', 'Unsupported execution/derivation/security profile basis', incomplete=True)
    view = interval._object(context['source_view'], ('source', 'client_version', 'observed_from',
        'observed_through', 'selection', 'requested', 'publisher', 'snapshot', 'snapshot_recheck'), 'source view')
    source = view['source']
    require(isinstance(source, str) and len(source) <= 2048, 'snapshot_source', 'Invalid source label')
    try:
        url = urlsplit(source)
        valid = url.scheme == 'https' and bool(url.hostname) and not url.username and not url.password and not url.fragment
    except ValueError:
        valid = False
    require(valid, 'snapshot_source', 'Expected a recorded public HTTPS RPC source')
    require(isinstance(view['client_version'], str) and 0 < len(view['client_version']) <= 1024,
            'snapshot_source', 'Missing client provenance')
    require(view['selection'] == 'latest_then_number_recheck',
            'snapshot_selection', 'Unsupported snapshot selection record')
    require(_time(view['observed_from']) <= _time(view['observed_through']),
            'snapshot_observation', 'Source observation order is inconsistent')
    tip, again = _block(view['snapshot']), _block(view['snapshot_recheck'])
    require(interval._same_json(tip, again), 'snapshot_recheck_conflict', 'Snapshot identity changed at recheck')
    publisher = base['publication']['keystone_number']
    require(tip['number'] >= publisher >= cover, 'snapshot_domain',
            'The selected snapshot excludes the publisher or covering keystone')
    headers = {h['number']: h for h in base_packet['hemi_headers']}
    for field, number in (('requested', base['query']['number']), ('publisher', publisher)):
        observed = _block(view[field])
        row = headers.get(number)
        require(row is not None and observed['number'] == number and observed['hash'] == row['hash'],
                'snapshot_branch_binding', 'Observed requested/publishing identity differs from authenticated evidence')
        fields = rlp_decode(bytes.fromhex(row['raw']))
        require(observed['parent_hash'] == '0x' + fields[0].hex() and
                observed['state_root'] == '0x' + fields[3].hex() and
                observed['timestamp'] == int.from_bytes(fields[11], 'big'),
                'snapshot_header_binding', 'Observed source metadata differs from authenticated header fields')
    require(view['requested']['timestamp'] <= view['publisher']['timestamp'] <= tip['timestamp'],
            'snapshot_domain', 'Snapshot timestamps contradict the branch order')
    if tip['number'] in headers:
        require(tip['hash'] == headers[tip['number']]['hash'], 'snapshot_branch_binding', 'Snapshot conflicts with the supplied prefix')
    return {'status': 'verified_context_binding', 'sha256': context_digest(context),
            'snapshot': dict(tip), 'source': source, 'observed_from': view['observed_from'],
            'observed_through': view['observed_through'], 'profile_basis': dict(PROFILE_BASIS),
            'domain': {'first_hemi_keystone': cover, 'last_hemi_block': tip['number'], 'tip_hash': tip['hash'],
                       'includes_eligible_keystones_after_publisher': True},
            'branch_validity_basis': 'externally_accepted_validating_rpc_coherence',
            'historical_execution_certified': False, 'post_p_ancestry_independently_verified': False,
            'source_authenticity_verified': False, 'current_bitcoin_membership_verified': False,
            'reported_timestamp_is_wall_clock_cutoff': False}


def verify_snapshot_packet(root, packet, manifest_digest, *, expected_hemi_number,
                           expected_hemi_hash, accepted_policy, accepted_context_sha256):
    required = {'schema', 'profile', 'query', 'history_manifest_sha256'}
    require(required <= set(packet) <= required | {'assessment_context'},
            'packet_shape', 'Unsupported fields in snapshot manifest')
    digest = interval._hash(packet['history_manifest_sha256'], 'history manifest checksum')
    raw = interval._read_file(root, 'history/manifest.json', interval.MAX_MANIFEST_BYTES, 'history_manifest_missing')
    require(hashlib.sha256(raw).hexdigest() == digest, 'snapshot_history_checksum', 'Nested history manifest checksum differs')
    nested, nested_digest = interval.load_manifest(root / 'history')
    require(nested_digest == digest and nested.get('schema') == history.SCHEMA,
            'snapshot_history_schema', 'Snapshot wrapper requires an unchanged version-2 history packet')
    child = history.verify_history_packet(root / 'history', nested, digest,
        expected_hemi_number=expected_hemi_number, expected_hemi_hash=expected_hemi_hash, accepted_policy=None)
    require(interval._same_json(packet['query'], child['query']), 'query_mismatch', 'Snapshot query differs from history evidence')
    result = {**child, 'schema': RESULT_SCHEMA, 'verifier_version': '3.0.0', 'profile': PROFILE['id'],
              'manifest_sha256': manifest_digest, 'history_manifest_sha256': digest,
              'first_height_assessment': {'status': 'not_established', 'bitcoin_height': None},
              'context_evidence': {'status': 'not_checked'}, 'issues': list(child['issues']),
              'policy': {'id': POLICY_ID, 'accepted_by_caller': accepted_policy == POLICY_ID,
                         'accepted_from_packet': False, 'context_accepted_by_caller': False,
                         'context_sha256_selected_by_caller': accepted_context_sha256,
                         'assumptions': [dict(item) for item in ASSUMPTIONS],
                         'useful_historical_work_envelope_measured': False, 'numerical_confidence': None,
                         'claimed_system_security_bits': None},
              'scope': {'meaning': 'first_applicable_bitcoin_block_height_in_recorded_snapshot',
                        'recorded_source_context_only': True, 'fresh_source_check_performed': False,
                        'future_unreviewed_upgrades_covered': False, 'restricted_to_found_publisher': False}}
    if child['status'] != 'verified_history_evidence':
        return result
    result['issues'] = []  # The v2 evidence-only policy notice is not a v3 acceptance decision.
    try:
        require(interval._same_json(packet['profile'], PROFILE), 'snapshot_profile_unsupported',
                'Unsupported snapshot profile', incomplete=True)
        base_packet, _ = interval.load_manifest(root / 'history/interval')
        checked = check_context(packet.get('assessment_context'), digest, child, base_packet)
        result['context_evidence'] = checked
        result['scope'].update(checked['domain'])
        if accepted_context_sha256 is not None:
            require(accepted_context_sha256 == checked['sha256'], 'snapshot_acceptance_mismatch',
                    'The checked context differs from the caller-selected context checksum')
            result['policy']['context_accepted_by_caller'] = True
    except VerificationError as exc:
        result.update(status=exc.status, context_evidence={'status': exc.status, 'code': exc.code})
        result['issues'].append({'code': exc.code, 'status': exc.status, 'message': str(exc)})
        return result
    result['status'] = 'verified_snapshot_evidence'
    if accepted_policy != POLICY_ID or not result['policy']['context_accepted_by_caller']:
        result['first_height_assessment']['status'] = 'external_context_or_policy_not_accepted'
        result['issues'].append({'code': 'snapshot_not_accepted', 'status': 'not_accepted',
            'message': 'Snapshot bindings passed. Conditional assessment requires both the new caller policy and the independently selected context checksum.'})
        return result
    result['status'] = 'verified_conditional_first_height'
    result['first_height_assessment'] = {'status': 'conditional',
        'bitcoin_height': child['interval_evidence']['publication']['bitcoin_height'],
        'covering_keystone': child['interval_evidence']['covering_keystone'], 'policy_id': POLICY_ID,
        'context_sha256': checked['sha256'], 'label': 'First applicable publication under snapshot assumptions',
        'qualification': 'Conditional on the caller-accepted validating-source, historical-profile and concrete security premises within the recorded height/hash domain'}
    result['assurance'].update(first_height_under_accepted_assumptions=True,
        snapshot_bound_under_accepted_assumptions=True,
        pre_anchor_absence='computational_inference_under_caller_accepted_snapshot_assumptions')
    return result
