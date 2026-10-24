"""Offline, profile-specific calculations; no deployed or current-chain claim.

Receipt proofs are recomputed. A conditional block calculation additionally needs
the original v3 checker and two independently caller-accepted recorded contexts.
No network, persistent success cache, source refresh or receipt mutation occurs.
"""

import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys

from .errors import DataError
from .inputs import covering
from .proof import same_header, verify_receipt
from traze_evidence.api import verify_packet
from traze_evidence.errors import VerificationError
from traze_evidence import snapshot
from traze_evidence import verify as interval

SCHEMA = 'traze-offline-finality-calculation-v1'
BFG_PROFILE = 'bfg-v2-464bcdf0-reference'
AMA_PROFILE = 'ama-signed-nine-block-model'
SOURCE_POLICY = 'hemi-finality-recorded-view-assumptions-v1'
MAX_RECEIPT_BYTES = 8_000_000
MAX_HEIGHT = 0xFFFFFFFF
REVISION = '464bcdf0af87fdcfd0544532f92ca4e094384574'
_PROFILES = {
    BFG_PROFILE: {
        'id': BFG_PROFILE, 'kind': 'pinned_public_source_arithmetic',
        'revision': REVISION, 'formula': 'effective_confirmations = T - H',
        'threshold': 10, 'threshold_meaning': 'super_finality when T - H >= 10',
        'reference': f'https://github.com/hemilabs/heminetwork/blob/{REVISION}/service/bfg/bfg.go',
        'constant_reference': f'https://github.com/hemilabs/heminetwork/blob/{REVISION}/api/bfgapi/bfgapi.go',
        'whole_bfg_handler_reproduced': False, 'deployed_equivalence_verified': False,
    },
    AMA_PROFILE: {
        'id': AMA_PROFILE, 'kind': 'transcript_described_model',
        'formula': 'signed_counter = T - H - 9',
        'threshold': 9, 'threshold_meaning': 'model_finality_reached when signed_counter >= 0',
        'reference': 'Hemi PoP Q&A transcript, section 2.5',
        'whole_bfg_handler_reproduced': False, 'deployed_equivalence_verified': False,
    },
}


class FinalityError(ValueError):
    """An invalid input or contradiction, never a zero-finality result."""

    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _require(condition, code, message):
    if not condition:
        raise FinalityError(code, message)


def _integer(value, label, minimum=0):
    _require(type(value) is int and minimum <= value <= MAX_HEIGHT,
             'invalid_height', f'{label} must be an integer in the supported uint32 range.')
    return value


def _hash(value, label, hemi=False):
    pattern = r'0x[0-9a-fA-F]{64}' if hemi else r'[0-9a-fA-F]{64}'
    _require(type(value) is str and re.fullmatch(pattern, value) is not None,
             'invalid_identity', f'{label} requires an exact 32-byte display hash.')
    return value.lower()


def _encode(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=True, allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, RecursionError) as exc:
        raise FinalityError('invalid_json', 'Expected finite, bounded JSON evidence.') from exc


def _digest(value):
    return hashlib.sha256(_encode(value)).hexdigest()


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        _require(key not in value, 'duplicate_json_key', 'Duplicate JSON object key.')
        value[key] = item
    return value


def load_receipt(path):
    """Bound a local JSON read; never follow a URL or create state."""
    try:
        with Path(path).open('rb') as handle:
            raw = handle.read(MAX_RECEIPT_BYTES + 1)
        _require(len(raw) <= MAX_RECEIPT_BYTES, 'receipt_size', 'Receipt exceeds the offline input limit.')
        result = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, FinalityError):
            raise
        raise FinalityError('receipt_unreadable', 'Cannot read valid bounded receipt JSON.') from exc
    _require(type(result) is dict, 'receipt_shape', 'Receipt must be a JSON object.')
    return result


def profiles():
    return deepcopy(_PROFILES)


def calculate_heights(publication_height, tip_height, *, profile_id=BFG_PROFILE):
    """Height arithmetic only. It proves neither height, branch nor selection."""
    h = _integer(publication_height, 'Publication height')
    t = _integer(tip_height, 'Bitcoin tip height')
    _require(type(profile_id) is str and profile_id in _PROFILES, 'unsupported_profile', 'Choose a supported named calculation profile.')
    _require(t >= h, 'tip_before_publication', 'The Bitcoin tip precedes the publication.')
    distance = t - h
    value = {'raw_bitcoin_confirmations': distance + 1}
    if profile_id == BFG_PROFILE:
        value.update(effective_confirmations=distance, super_finality=distance >= 10)
    else:
        value.update(signed_counter=distance - 9, model_finality_reached=distance >= 9)
    return value


def _time(value, label):
    _require(type(value) is str and len(value) <= 128, 'observation_time', f'{label} is missing.')
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise FinalityError('observation_time', f'{label} is malformed.') from exc
    _require(stamp.utcoffset() is not None, 'observation_time', f'{label} requires a timezone.')
    return stamp


def _identity(proof):
    return {'bitcoin_height': proof['bitcoin_height'], 'bitcoin_hash': proof['bitcoin_block_hash'].lower(),
            'keystone_number': proof['checkpoint']['height'], 'txid': proof['txid'].lower(),
            'tx_index': proof['tx_index'], 'vout': proof['vout_index']}


def _bound_header(header, decoded):
    fields = decoded['fields']
    return (int(header['number'], 16) == decoded['number']
            and _hash(header['hash'], 'Hemi header hash', True)[2:] == decoded['hash'].hex()
            and all(header[key].lower() == '0x' + fields[index].hex()
                    for key, index in (('parentHash', 0), ('stateRoot', 3), ('transactionsRoot', 4)))
            and int(header['timestamp'], 16) == int.from_bytes(fields[11], 'big'))


def _coherent_hemi_headers(receipt):
    """Reject incompatible supplied metadata before assuming a shared branch."""
    query = receipt['query']
    _require(type(query.get('covering_checkpoint')) is dict, 'receipt_shape', 'Missing covering-keystone metadata.')
    rows = [query.get('header'), query['covering_checkpoint'].get('header')]
    for proof in receipt['proofs']:
        rows.append(proof['checkpoint'].get('header'))
        if proof['checkpoint'].get('previous_header') is not None:
            rows.append(proof['checkpoint']['previous_header'])
        rows.extend(proof['ancestry'])
    heights, hashes = {}, {}
    for header in rows:
        _require(type(header) is dict, 'receipt_shape', 'Missing supplied Hemi header.')
        for key in ('number', 'timestamp'):
            text = header.get(key)
            _require(type(text) is str and re.fullmatch(r'0x[0-9a-fA-F]{1,16}', text) is not None,
                     'receipt_shape', 'Invalid supplied Hemi height/timestamp.')
        number = _integer(int(header['number'], 16), 'Supplied Hemi number', minimum=1)
        for key in ('hash', 'parentHash', 'stateRoot', 'transactionsRoot'):
            _hash(header.get(key), 'Supplied Hemi ' + key, True)
        digest = header['hash'].lower()
        _require(number not in heights or same_header(heights[number], header),
                 'hemi_branch_conflict', 'One Hemi height has contradictory supplied metadata.')
        _require(digest not in hashes or hashes[digest] == number,
                 'hemi_position_conflict', 'One Hemi hash has contradictory supplied heights.')
        heights[number], hashes[digest] = header, number


def _source_context(receipt, receipt_digest, tip):
    """Check internal recorded consistency, not the truth of unsigned replies."""
    context = receipt.get('context', {})
    _require(type(context) is dict, 'source_context_shape', 'Recorded context must be an object.')
    problems, conflicts = [], []
    observed = context.get('checked_at')
    try:
        checked = _time(observed, 'Recorded source-check time')
        start = _time(receipt.get('started_at'), 'Receipt start time')
        end = _time(receipt.get('completed_at'), 'Receipt completion time')
        _require(start <= checked <= end, 'observation_time', 'Recorded operation times are inconsistent.')
    except FinalityError as exc:
        problems.append({'code': exc.code, 'message': str(exc)})
    if context.get('hemi_chain_id') != 43111 or context.get('hemi_network_same') is not True:
        problems.append({'code': 'network_observation_missing', 'message': 'Recorded Hemi mainnet observation is incomplete.'})
    if context.get('current_view_confirmed') is False:
        problems.append({'code': 'recorded_view_unconfirmed', 'message': 'The receipt explicitly records an unconfirmed source view.'})
    before = receipt.get('bitcoin_tip_before')
    window = 'unavailable'
    if type(before) is not dict:
        problems.append({'code': 'tip_window_missing', 'message': 'The initial recorded Bitcoin tip is missing.'})
    else:
        previous_height = _integer(before.get('height'), 'Initial Bitcoin tip height')
        previous_hash = _hash(before.get('hash'), 'Initial Bitcoin tip hash')
        if previous_height > tip['height'] or (previous_height == tip['height'] and previous_hash != tip['hash']):
            window = 'conflicting'
            conflicts.append({'code': 'tip_window_conflict', 'message': 'Recorded tips decrease or conflict at one height.'})
        else:
            window = 'same_tip' if previous_height == tip['height'] else 'advanced_forward'
    identity = context.get('requested_block_identity')
    if identity is not None:
        _require(type(identity) is dict, 'source_context_shape', 'Invalid requested-block observation.')
        qhash = receipt['query']['block_hash'].lower()
        requested = identity.get('requested_hash')
        current = identity.get('current_hash')
        if (type(requested) is not str or type(current) is not str
                or requested.lower() != qhash or current.lower() != qhash
                or identity.get('status') != 'matches_current_rpc_view'):
            conflicts.append({'code': 'requested_source_conflict', 'message': 'Recorded requested-block identity is conflicting or unresolved.'})
    required = {receipt['query']['block_number'], receipt['query']['covering_keystone']}
    required.update(p['checkpoint']['height'] for p in receipt['proofs'])
    groups = [('hemi_headers', required), ('bitcoin_blocks', {p['bitcoin_height'] for p in receipt['proofs']}),
              ('bitcoin_secondary', {p['bitcoin_height'] for p in receipt['proofs']})]
    for field, wanted in groups:
        rows = context.get(field, [])
        _require(type(rows) is list and len(rows) <= 4096, 'source_context_shape', 'Invalid recorded observation list.')
        for row in rows:
            _require(type(row) is dict, 'source_context_shape', 'Invalid recorded observation row.')
            _integer(row.get('height'), 'Observed height')
            _require(row.get('same') in (True, False, None) and type(row.get('same')) in (bool, type(None)),
                     'source_context_shape', 'Source agreement must be true, false or unknown.')
            if row.get('same') is False:
                conflicts.append({'code': 'recorded_source_conflict', 'message': f'{field} records a changed identity.'})
        if any(not any(row.get('height') == height and row.get('same') is True for row in rows) for height in wanted):
            problems.append({'code': 'source_observation_incomplete', 'message': f'{field} lacks a required positive observation.'})
        if any(row.get('same') is None for row in rows):
            problems.append({'code': 'source_observation_incomplete', 'message': f'{field} contains an unresolved observation.'})
    for issue in receipt.get('issues', []):
        if type(issue) is dict and issue.get('code') in ('source_conflict', 'inconsistent_data', 'wrong_network', 'requested_block_not_current'):
            conflicts.append({'code': 'recorded_source_conflict', 'message': 'The receipt records a conflicting source view.'})
    state = 'conflicting' if conflicts else 'incomplete' if problems else 'recorded_consistent'
    basis = {'schema': 'traze-finality-recorded-inputs-v1', 'receipt_sha256': receipt_digest,
             'query': {'number': receipt['query']['block_number'], 'hash': receipt['query']['block_hash'].lower()},
             'bitcoin_tip': dict(tip), 'initial_bitcoin_tip': deepcopy(before),
             'tip_window': window, 'observed_at': observed, 'recorded_consistency': state}
    return {'status': state, 'sha256': _digest(basis), 'inputs': basis, 'issues': conflicts + problems,
            'policy_id': SOURCE_POLICY, 'accepted_by_caller': False, 'accepted_from_receipt': False,
            'assumptions': [
                'Recorded Bitcoin heights, publication hashes and both tip observations refer to one valid Bitcoin branch, including any forward tip advancement; positions, tip ancestry and chainwork are not independently established.',
                'Recorded Hemi identities and any accepted first-height snapshot describe a coherent valid Hemi branch within the declared domain.',
                'Unsigned source observations are externally trusted historical assertions, not fresh or independently authenticated chain observations.',
            ]}


def _first_height(packet, receipt, *, accepted_policy, accepted_context):
    result = {'status': 'not_established', 'issues': [], 'network_requests': 0}
    if packet is None:
        result['issues'].append({'code': 'first_height_packet_missing', 'message': 'A checked version-3 first-height packet is required for block-level reference selection.'})
        return result
    query = receipt['query']
    try:
        root = Path(packet).resolve()
        outer, outer_digest = interval.load_manifest(root)
        _require(type(outer) is dict and outer.get('schema') == snapshot.SCHEMA,
                 'unsupported_first_height_packet', 'This calculation supports version-3 snapshot first-height evidence only.')
        _, history_digest = interval.load_manifest(root / 'history')
        base, base_digest = interval.load_manifest(root / 'history/interval')
        checked = verify_packet(root, expected_hemi_number=query['block_number'], expected_hemi_hash=query['block_hash'],
                                accepted_policy=accepted_policy, accepted_context_sha256=accepted_context)
        _require(checked['manifest_sha256'] == outer_digest and checked['history_manifest_sha256'] == history_digest
                 and checked['interval_evidence']['manifest_sha256'] == base_digest,
                 'packet_changed', 'Captured manifests differ from the checked evidence.')
        result.update(status=checked['status'], manifest_sha256=outer_digest,
                      assessment=checked.get('first_height_assessment'), context=checked.get('context_evidence'),
                      policy=checked.get('policy'), issues=deepcopy(checked.get('issues', [])),
                      publication=deepcopy(checked['interval_evidence']['publication']))
        if checked['status'] != 'verified_conditional_first_height':
            return result
        publication = result['publication']
        matches = [p for p in receipt['proofs'] if _identity(p) == publication]
        _require(bool(matches), 'packet_publication_mismatch', 'The checked first-height publication is absent from the receipt.')
        domain = checked['context_evidence']['domain']
        _require(all(p['checkpoint']['height'] <= domain['last_hemi_block'] for p in receipt['proofs']),
                 'snapshot_domain_mismatch', 'A supplied publisher is outside the checked Hemi snapshot domain.')
        _require(not any(p['bitcoin_height'] < publication['bitcoin_height'] for p in receipt['proofs']),
                 'first_height_contradiction', 'Supplied publication evidence contradicts the conditional first height.')
        headers = {row['number']: interval.decode_header(row) for row in base['hemi_headers']}
        candidates = [query['header'], query['covering_checkpoint']['header']]
        candidates.extend(p['checkpoint']['header'] for p in receipt['proofs'])
        candidates.extend(h for p in receipt['proofs'] for h in p['ancestry'])
        for header in candidates:
            number = int(header['number'], 16)
            if number in headers:
                _require(_bound_header(header, headers[number]), 'packet_header_mismatch',
                         'Receipt metadata differs from an authenticated packet header.')
        result['status'] = 'conditional_reference_verified'
    except VerificationError as exc:
        result.update(status=exc.status, issues=[{'code': exc.code, 'message': str(exc)}])
    except (FinalityError, OSError, KeyError, TypeError, ValueError, RecursionError) as exc:
        result.update(status='invalid', issues=[{'code': getattr(exc, 'code', 'first_height_invalid'), 'message': str(exc)}])
    return result


def calculate_receipt(receipt, *, expected_hemi_number, expected_hemi_hash, expected_tip_height,
                      expected_tip_hash, profile_id=BFG_PROFILE, selected_publication=None,
                      first_height_packet=None, accepted_first_height_policy=None,
                      accepted_first_context_sha256=None, accepted_source_policy=None,
                      accepted_source_context_sha256=None):
    """Recompute an immutable input capture; caller-owned identities/policies only."""
    raw = _encode(receipt)
    _require(len(raw) <= MAX_RECEIPT_BYTES, 'receipt_size', 'Receipt exceeds the offline input limit.')
    receipt = json.loads(raw)
    number = _integer(expected_hemi_number, 'Expected Hemi number', minimum=1)
    covering(number)
    wanted_hash = _hash(expected_hemi_hash, 'Expected Hemi hash', True)
    tip = {'height': _integer(expected_tip_height, 'Expected Bitcoin tip height'),
           'hash': _hash(expected_tip_hash, 'Expected Bitcoin tip hash')}
    _require(type(profile_id) is str and profile_id in _PROFILES, 'unsupported_profile', 'Choose a supported named calculation profile.')
    _require(type(receipt) is dict and type(receipt.get('query')) is dict, 'receipt_shape', 'Missing receipt query.')
    query = receipt['query']
    _require(type(query.get('block_number')) is int and query['block_number'] == number
             and _hash(query.get('block_hash'), 'Receipt Hemi hash', True) == wanted_hash,
             'query_mismatch', 'Receipt differs from the caller-selected Hemi identity.')
    _require(type(query.get('covering_keystone')) is int and query['covering_keystone'] == covering(number),
             'covering_keystone_mismatch', 'Receipt has no correct covering-keystone identity.')
    _require(type(receipt.get('issues', [])) is list and len(receipt.get('issues', [])) <= 4096
             and all(type(issue) is dict for issue in receipt.get('issues', [])),
             'receipt_shape', 'Invalid recorded issue list.')
    recorded_tip = receipt.get('bitcoin_tip_after')
    _require(type(recorded_tip) is dict and type(recorded_tip.get('height')) is int and recorded_tip == tip,
             'tip_mismatch', 'Recorded tip differs from the caller-selected height/hash.')
    proofs = receipt.get('proofs', [])
    _require(type(proofs) is list and len(proofs) <= 128, 'receipt_shape', 'Invalid or excessive publication proof count.')
    total_links = 0
    positions = {}
    for proof in proofs:
        _require(type(proof) is dict, 'receipt_shape', 'Invalid publication proof.')
        _integer(proof.get('bitcoin_height'), 'Publication height')
        _hash(proof.get('bitcoin_block_hash'), 'Publication Bitcoin hash')
        _hash(proof.get('txid'), 'Publication transaction ID')
        _integer(proof.get('tx_index'), 'Transaction position')
        _integer(proof.get('vout_index'), 'Output position')
        _require(type(proof.get('checkpoint')) is dict, 'receipt_shape', 'Missing publishing keystone.')
        _integer(proof['checkpoint'].get('height'), 'Publishing keystone', minimum=1)
        path = proof.get('ancestry')
        _require(type(path) is list and 1 <= len(path) <= 257, 'ancestry_size', 'Unsupported ancestry size.')
        total_links += len(path)
        _require(total_links <= 4096, 'ancestry_size', 'Receipt exceeds the combined ancestry limit.')
        block_hash = proof['bitcoin_block_hash'].lower()
        height = proof['bitcoin_height']
        _require(block_hash not in positions or positions[block_hash] == height,
                 'bitcoin_position_conflict', 'One Bitcoin block hash has conflicting reported heights.')
        _require(not any(h == height and b != block_hash for b, h in positions.items()),
                 'bitcoin_branch_conflict', 'Competing Bitcoin block hashes occupy one reported height.')
        positions[block_hash] = height
        calculate_heights(height, tip['height'], profile_id=profile_id)
        if height == tip['height']:
            _require(block_hash == tip['hash'], 'tip_branch_conflict', 'Publication at the tip height has a different block hash.')
    _coherent_hemi_headers(receipt)
    try:
        checked = verify_receipt(receipt)
    except (DataError, KeyError, TypeError, ValueError, OverflowError) as exc:
        raise FinalityError(getattr(exc, 'code', 'receipt_invalid'), 'Publication receipt verification failed: ' + str(exc)) from exc
    _require((accepted_source_policy is None) == (accepted_source_context_sha256 is None),
             'source_acceptance_pair', 'Accept both the named source policy and exact source context, or neither.')
    _require(accepted_source_policy is None or accepted_source_policy == SOURCE_POLICY,
             'source_policy', 'Unsupported caller source policy.')
    _require(first_height_packet is not None or (accepted_first_height_policy is None and accepted_first_context_sha256 is None),
             'first_height_packet_required', 'First-height acceptance requires an actual evidence packet.')
    source = _source_context(receipt, hashlib.sha256(raw).hexdigest(), tip)
    if accepted_source_context_sha256 is not None:
        _require(_hash(accepted_source_context_sha256, 'Accepted source context') == source['sha256'],
                 'source_context_mismatch', 'The caller-accepted source context differs from these exact inputs.')
        source['accepted_by_caller'] = True
    calculations = []
    for proof in proofs:
        calculations.append({'publication': _identity(proof),
            'relationship': 'covering_keystone' if proof['checkpoint']['height'] == query['covering_keystone'] else 'later_keystone_support',
            'values': calculate_heights(proof['bitcoin_height'], tip['height'], profile_id=profile_id)})
    calculations.sort(key=lambda p: (p['publication']['bitcoin_height'], p['publication']['keystone_number'],
                                      p['publication']['txid'], p['publication']['vout']))
    selected = None
    if selected_publication is not None:
        selected = next((p for p in calculations if p['publication'] == selected_publication), None)
        _require(selected is not None, 'publication_selection_mismatch', 'Selected exact publication is absent from checked evidence.')
    elif calculations:
        selected = calculations[0]
    first = _first_height(first_height_packet, receipt, accepted_policy=accepted_first_height_policy,
                          accepted_context=accepted_first_context_sha256)
    block = {'status': 'not_established', 'reference_publication': None, 'values': None,
             'issues': list(first['issues']), 'scope': 'recorded_Hemi_snapshot_and_recorded_Bitcoin_view'}
    if not calculations:
        block['issues'].append({'code': 'no_positive_publication', 'message': 'There is no positive publication to calculate.'})
    elif first['status'] == 'conditional_reference_verified':
        if source['status'] != 'recorded_consistent':
            block['issues'].extend(source['issues'])
        elif not source['accepted_by_caller']:
            block['issues'].append({'code': 'source_context_not_accepted', 'message': 'The exact recorded source context and its external premises require caller acceptance.'})
        else:
            block.update(status='calculated_under_accepted_recorded_assumptions',
                         reference_publication=deepcopy(first['publication']),
                         values=calculate_heights(first['publication']['bitcoin_height'], tip['height'], profile_id=profile_id))
    result = {'schema': SCHEMA, 'status': 'conditional_block_calculation' if block['values'] else 'publication_calculations_only' if calculations else 'incomplete',
        'profile': deepcopy(_PROFILES[profile_id]), 'query': {'number': number, 'hash': wanted_hash},
        'bitcoin_tip': tip, 'receipt_sha256': hashlib.sha256(raw).hexdigest(),
        'ordinary_verification': checked, 'publication_calculations': calculations,
        'display_selection': deepcopy(selected), 'display_selection_changes_block_reference': False,
        'source_context': source, 'first_height_evidence': first, 'block_calculation': block,
        'assurance': {'profile_arithmetic_recomputed': bool(calculations), 'fresh_source_check_performed': False,
                      'current_chain_membership_verified': False, 'full_consensus_or_execution_verified': False,
                      'deployed_finality_equivalence_verified': False, 'whole_bfg_handler_reproduced': False,
                      'globally_first_proven': False, 'miner_reward_eligibility_verified': False},
        'network_requests': 0,
        'limits': ['Bitcoin heights and tip membership are source-reported; offline inclusion does not establish global chain position.',
                   'All calculations concern recorded inputs; neither the clock nor opening a result advances them.',
                   'The BFG arithmetic profile does not reproduce its complete RPC handler, descendant batching or early stopping.',
                   'A conditional block model retains every accepted first-height and source-view premise; it is not deployed Hemi protocol equivalence.']}
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='Recompute a named finality model from retained publication evidence offline.')
    parser.add_argument('--receipt', required=True)
    parser.add_argument('--hemi-block', type=int, required=True)
    parser.add_argument('--hemi-hash', required=True)
    parser.add_argument('--tip-height', type=int, required=True)
    parser.add_argument('--tip-hash', required=True)
    parser.add_argument('--profile', choices=tuple(_PROFILES), default=BFG_PROFILE)
    parser.add_argument('--first-height-packet')
    parser.add_argument('--accept-first-height-policy', choices=(snapshot.POLICY_ID,))
    parser.add_argument('--accept-first-context')
    parser.add_argument('--accept-source-policy', choices=(SOURCE_POLICY,))
    parser.add_argument('--accept-source-context')
    args = parser.parse_args(argv)
    try:
        result = calculate_receipt(load_receipt(args.receipt), expected_hemi_number=args.hemi_block,
            expected_hemi_hash=args.hemi_hash, expected_tip_height=args.tip_height, expected_tip_hash=args.tip_hash,
            profile_id=args.profile, first_height_packet=args.first_height_packet,
            accepted_first_height_policy=args.accept_first_height_policy,
            accepted_first_context_sha256=args.accept_first_context,
            accepted_source_policy=args.accept_source_policy,
            accepted_source_context_sha256=args.accept_source_context)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 2 if result['status'] == 'incomplete' else 0
    except (FinalityError, DataError) as exc:
        print(json.dumps({'schema': SCHEMA, 'status': 'invalid', 'code': exc.code,
                          'message': str(exc), 'network_requests': 0}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
