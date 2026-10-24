"""Isolated website calculation entry point; no public-source connections."""

import hashlib
import json
from pathlib import Path
import socket
import sys

NETWORK_ATTEMPTS = 0

def deny(*args, **kwargs):
    global NETWORK_ATTEMPTS
    NETWORK_ATTEMPTS += 1
    raise RuntimeError('Source connections are disabled in the calculation worker')


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    for name in ('getaddrinfo', 'create_connection'):
        setattr(socket, name, deny)
    socket.socket.connect = deny
    socket.socket.connect_ex = deny
    from hemi_lookup import finality
    from hemi_lookup.finality_service import SCHEMA, MAX_RESULT_BYTES, unavailable, json_bytes
    from traze_evidence import verify as interval
    from traze_evidence.snapshot import POLICY_ID

    charged = 0
    try:
        raw = sys.stdin.buffer.read(finality.MAX_RECEIPT_BYTES + 32_001)
        if len(raw) > finality.MAX_RECEIPT_BYTES + 32_000:
            raise ValueError('Calculation request exceeds its limit')
        request = json.loads(raw, object_pairs_hook=finality._pairs)
        receipt = request['receipt']
        remaining = request['remaining_input_bytes']
        if type(remaining) is not int or not 0 <= remaining <= 32_000_000:
            raise ValueError('Invalid remaining input budget')
        read_file = interval._read_file

        def bounded_file(root, relative, limit, code):
            nonlocal charged
            size = (Path(root) / relative).stat().st_size
            reservation = min(size, limit + 1)
            if charged + reservation > remaining:
                raise finality.FinalityError('finality_packet_budget', 'The calculation packet exceeds the remaining read input budget.')
            charged += reservation
            return read_file(root, relative, limit, code)

        interval._read_file = bounded_file
        tip, query = receipt['bitcoin_tip_after'], receipt['query']
        options = {}
        acceptance = 'not_configured'
        if request.get('source_policy') is not None:
            acceptance = 'context_not_selected'
            # A preflight match selects an operator-owned exact context. The
            # original calculator revalidates both bytes and acceptance itself.
            context = finality._source_context(receipt,
                hashlib.sha256(finality._encode(receipt)).hexdigest(), tip)
            if request['allow_block_reference'] and context['sha256'] == request.get('source_context'):
                options.update(accepted_source_policy=request['source_policy'],
                               accepted_source_context_sha256=request['source_context'])
                acceptance = 'exact_context_selected'
        if request.get('packet'):
            options['first_height_packet'] = request['packet']
            if request.get('first_policy') == POLICY_ID:
                options.update(accepted_first_height_policy=POLICY_ID,
                               accepted_first_context_sha256=request.get('first_context'))
        calculation = finality.calculate_receipt(receipt,
            expected_hemi_number=query['block_number'], expected_hemi_hash=query['block_hash'],
            expected_tip_height=tip['height'], expected_tip_hash=tip['hash'], **options)
        if request.get('expected_packet_digest') is not None and calculation['first_height_evidence'].get('manifest_sha256') != request['expected_packet_digest']:
            raise finality.FinalityError('finality_packet_changed', 'The first-height packet changed between the displayed assessment and calculation.')
        # Offline diagnostics may include filesystem exception text. The read
        # API exposes a stable explanation rather than operator-local paths.
        for group in (calculation['first_height_evidence']['issues'], calculation['block_calculation']['issues']):
            for issue in group:
                if issue.get('code') == 'first_height_invalid':
                    issue['message'] = 'The retained first-height packet could not be checked.'
        comparison = {'profile': finality.profiles()[finality.AMA_PROFILE], 'same_checked_inputs': True,
            'publication_calculations': [{'publication': item['publication'], 'values':
                finality.calculate_heights(item['publication']['bitcoin_height'], tip['height'], profile_id=finality.AMA_PROFILE)}
                for item in calculation['publication_calculations']], 'block_values': None}
        if calculation['block_calculation']['values'] is not None:
            comparison['block_values'] = finality.calculate_heights(
                calculation['block_calculation']['reference_publication']['bitcoin_height'], tip['height'],
                profile_id=finality.AMA_PROFILE)
        result = {'schema': SCHEMA, 'status': calculation['status'], 'calculation': calculation, 'comparison': comparison,
            'input_binding': request['input_binding'], 'operator_source_acceptance': acceptance,
            'source_conflict': request.get('source_conflict') is True,
            'issues': [], 'recorded_context_only': True, 'fresh_source_check_performed': False, 'network_requests': 0}
    except Exception as exc:
        result = unavailable(getattr(exc, 'code', 'finality_input_unavailable'),
                             str(exc) if isinstance(exc, finality.FinalityError) else 'The calculation worker could not check these inputs.',
                             status='invalid' if isinstance(exc, finality.FinalityError) else 'unavailable')
    if NETWORK_ATTEMPTS:
        result = unavailable('finality_network_forbidden', 'A source connection was blocked by the calculation worker.')
    result['denied_network_attempts'] = NETWORK_ATTEMPTS
    result['accounted_packet_input_bytes'] = charged
    raw = json_bytes(result, MAX_RESULT_BYTES)
    sys.stdout.buffer.write(raw)


if __name__ == '__main__':
    main()
