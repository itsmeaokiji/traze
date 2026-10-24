"""Bounded, source-free website adapter for the standalone finality calculator."""

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time

from .errors import DataError, DeadlineError
from .finality import MAX_RECEIPT_BYTES, SOURCE_POLICY

SCHEMA = 'traze-web-finality-assessment-v1'
MAX_RESULT_BYTES = 1_500_000
WORK_SECONDS = 3
MAX_WORKERS = 2
WORKERS = threading.BoundedSemaphore(MAX_WORKERS)
HEX = re.compile(r'[0-9a-f]{64}\Z')


def json_bytes(value, limit):
    raw = bytearray()
    for part in json.JSONEncoder(sort_keys=True, separators=(',', ':'), allow_nan=False).iterencode(value):
        raw.extend(part.encode('utf-8'))
        if len(raw) > limit:
            raise DataError('finality_input_size', 'Calculation input exceeds the supported size.')
    return bytes(raw)


def unavailable(code, message, *, status='unavailable'):
    return {'schema': SCHEMA, 'status': status, 'calculation': None, 'comparison': None,
            'issues': [{'code': code, 'message': message}], 'recorded_context_only': True,
            'fresh_source_check_performed': False, 'network_requests': 0}


def _stamp(text):
    if not isinstance(text, str) or len(text) > 128:
        raise ValueError('Missing recorded tip observation time')
    value = datetime.fromisoformat(text.replace('Z', '+00:00'))
    if value.utcoffset() is None:
        raise ValueError('A recorded time requires a timezone')
    return value


def _tip(value):
    return (type(value) is dict and type(value.get('height')) is int and
            0 <= value['height'] <= 0xffffffff and type(value.get('hash')) is str and
            HEX.fullmatch(value['hash']) is not None)


def calculation_inputs(receipt):
    """Select recorded inputs without modifying or refreshing the original receipt.

    Generated indexed packets may borrow a dated Bitcoin tip from their own
    retained observation list. This never upgrades observations to a new Hemi
    target and never permits a conditional block result.
    """
    raw = json_bytes(receipt, MAX_RECEIPT_BYTES)
    original_sha = hashlib.sha256(raw).hexdigest()
    value = json.loads(raw)
    overrides = {}
    binding = {'kind': 'original_receipt', 'original_receipt_sha256': original_sha,
               'receipt_overrides': overrides, 'retained_tip_observation': None}
    if 'bitcoin_tip_after' not in value and value.get('mode') == 'offline_catalogue':
        rows = value.get('coverage', {}).get('historical_observations', [])
        if type(rows) is not list or len(rows) > 128:
            raise DataError('finality_observation_limit', 'Too many retained tip observations for this calculation.')
        candidates, positions = [], {}
        for row in rows:
            source = row.get('observation') if type(row) is dict else None
            if type(source) is not dict or source.get('bitcoin_tip_after') is None:
                continue
            tip, before = source.get('bitcoin_tip_after'), source.get('bitcoin_tip_before')
            if not _tip(tip) or not _tip(before):
                raise DataError('finality_recorded_tip', 'A retained Bitcoin tip is malformed.')
            if before['height'] > tip['height']:
                raise DataError('finality_tip_conflict', 'Retained Bitcoin tips move backwards.')
            for item in (before, tip):
                if item['height'] in positions and positions[item['height']] != item['hash']:
                    raise DataError('finality_tip_conflict', 'Retained Bitcoin tips conflict at one height.')
                positions[item['height']] = item['hash']
            observed = source.get('context', {}).get('checked_at')
            start, checked, end = (_stamp(source.get('started_at')), _stamp(observed), _stamp(source.get('completed_at')))
            if not start <= checked <= end:
                raise DataError('finality_observation_time', 'The retained tip observation has inconsistent times.')
            candidates.append((end, json_bytes(source, MAX_RECEIPT_BYTES), source))
        if not candidates:
            raise DataError('finality_tip_missing', 'No recorded Bitcoin tip is available for this result.')
        # Prefer the latest completed observation, not the largest height.
        chosen = max(candidates, key=lambda item: (item[0], item[1]))[2]
        overrides = {key: deepcopy(chosen[key]) for key in
                     ('started_at', 'completed_at', 'bitcoin_tip_before', 'bitcoin_tip_after')}
        # No observation for another target becomes confirmation for this target.
        overrides['context'] = {**value.get('context', {}), 'current_view_confirmed': False,
                                'checked_at': chosen['context']['checked_at']}
        identity = overrides['context'].get('requested_block_identity')
        if type(identity) is dict and identity.get('status') in ('not_rechecked', 'unavailable'):
            # An explicit absence of a comparison is not a contradictory source
            # reply. Keep it in the original receipt; omit the non-observation
            # from the derived calculator context, which remains unconfirmed.
            overrides['context'].pop('requested_block_identity')
        value.update(overrides)
        binding.update(kind='derived_indexed_input', receipt_overrides=overrides,
            retained_tip_observation={'query': deepcopy(chosen.get('query')),
                'observed_at': chosen['context']['checked_at'], 'completed_at': chosen['completed_at'],
                'sources': deepcopy(chosen.get('sources')), 'qualification':
                'Recorded Bitcoin tip only; original Hemi observation scope is not promoted to this target.'})
    if not _tip(value.get('bitcoin_tip_after')):
        raise DataError('finality_tip_missing', 'A complete recorded Bitcoin tip height and hash are required.')
    binding['calculation_receipt_sha256'] = hashlib.sha256(json_bytes(value, MAX_RECEIPT_BYTES)).hexdigest()
    return value, binding


def run_bounded(payload, *, check=None):
    """Own and reap one child; original caller clock and input budget still apply."""
    if not WORKERS.acquire(blocking=False):
        raise DataError('finality_busy', 'Calculation capacity is busy. The publication evidence remains available.')
    deadline = time.monotonic() + WORK_SECONDS

    def guard():
        if check:
            check()
        if time.monotonic() >= deadline:
            raise DeadlineError('Reference calculation time limit reached')

    try:
        guard()
        raw = json_bytes(payload, MAX_RECEIPT_BYTES + 32_000)
        if check:
            check(len(raw))
        payload['remaining_input_bytes'] = max(0, 32_000_000 - getattr(check, 'bytes', 0))
        raw = json_bytes(payload, MAX_RECEIPT_BYTES + 32_000)
        with tempfile.TemporaryFile() as request, tempfile.TemporaryFile() as response:
            request.write(raw)
            request.seek(0)
            child = subprocess.Popen([sys.executable, '-I', '-B', str(Path(__file__).with_name('finality_worker.py'))],
                stdin=request, stdout=response, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                while child.poll() is None:
                    guard()
                    if response.tell() > MAX_RESULT_BYTES:
                        raise DataError('finality_response_size', 'Calculation response exceeds its size bound.')
                    try:
                        child.wait(timeout=.05)
                    except subprocess.TimeoutExpired:
                        pass
                guard()
                if child.returncode or response.tell() > MAX_RESULT_BYTES:
                    raise DataError('finality_worker', 'The reference calculation could not finish within its bounds.')
                response.seek(0)
                result = json.loads(response.read(MAX_RESULT_BYTES + 1))
                if type(result) is not dict or result.get('schema') != SCHEMA:
                    raise DataError('finality_worker', 'The reference calculation returned an invalid response.')
                if check:
                    check(result.get('accounted_packet_input_bytes', 0))
                return result
            finally:
                if child.poll() is None:
                    child.kill()
                child.wait()
    finally:
        WORKERS.release()


class FinalityService:
    def __init__(self, *, source_policy=None, accepted_source_context_sha256=None):
        if ((source_policy is None) != (accepted_source_context_sha256 is None) or
                source_policy not in (None, SOURCE_POLICY) or
                accepted_source_context_sha256 is not None and
                (type(accepted_source_context_sha256) is not str or not HEX.fullmatch(accepted_source_context_sha256))):
            raise ValueError('Select both the supported finality source policy and exact context checksum, or neither')
        self.source_policy = source_policy
        self.accepted_source_context_sha256 = accepted_source_context_sha256

    def assess(self, receipt, *, packet=None, first_policy=None, first_context=None,
               expected_packet_digest=None, source_conflict=False, check=None):
        if not receipt.get('proofs'):
            return unavailable('finality_publication_missing', 'No positive publication evidence is available to calculate.')
        try:
            value, binding = calculation_inputs(receipt)
            payload = {'receipt': value, 'input_binding': binding,
                'packet': str(packet) if packet else None, 'first_policy': first_policy, 'first_context': first_context,
                'expected_packet_digest': expected_packet_digest,
                'source_policy': self.source_policy, 'source_context': self.accepted_source_context_sha256,
                'source_conflict': bool(source_conflict),
                'allow_block_reference': binding['kind'] == 'original_receipt' and not source_conflict}
            return run_bounded(payload, check=check)
        except (DataError, OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
            code = getattr(exc, 'code', 'finality_input_unavailable')
            return unavailable(code, str(exc) if isinstance(exc, DataError) else
                               'The recorded calculation inputs could not be checked.',
                               status='conflicting' if code == 'finality_tip_conflict' else 'unavailable')
