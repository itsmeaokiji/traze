"""Server-owned packet storage and recomputed first-publication assessments.

Neither receipt flags nor downloaded-packet fields accept the operator policy.
Reading an assessment never constructs a public source client or accounting run.
"""

import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import zipfile

from traze_evidence.history import ASSUMPTIONS, POLICY_ID, PROFILE
from traze_evidence.snapshot import ASSUMPTIONS as SNAPSHOT_ASSUMPTIONS, POLICY_ID as SNAPSHOT_POLICY, SCHEMA as SNAPSHOT_SCHEMA, RESULT_SCHEMA as SNAPSHOT_RESULT_SCHEMA
from traze_evidence.errors import VerificationError
from traze_evidence.crypto import rlp_decode
from traze_evidence import verify as interval_verifier
from traze_evidence.verify import MAX_MANIFEST_BYTES, MAX_TOTAL_BODY_BYTES, MAX_BLOCK_BYTES

from .errors import DataError, DeadlineError
from .packet_cache import PacketResultCache
from .finality_service import FinalityService
from .storage import atomic_json, now

HEX = re.compile(r'[0-9a-f]{64}\Z')
EXPORT_SLOT = threading.BoundedSemaphore(1)
PACKET_RESULTS = PacketResultCache()


class EvidenceBusy(DataError):
    status = 429

    def __init__(self):
        super().__init__('evidence_export_busy', 'A verification-package export is active. Retry shortly.')


def selected_proof(receipt):
    proofs = receipt.get('proofs', [])
    selection = receipt.get('selection', {})
    index = selection.get('current_proof_index')
    if index is None:
        index = selection.get('best_observed_proof_index')
    if type(index) is int and 0 <= index < len(proofs) and proofs[index]['bitcoin_height'] == min(p['bitcoin_height'] for p in proofs):
        return proofs[index]
    return min(proofs, key=lambda p: (p['bitcoin_height'], p['checkpoint']['height'], p['txid'], p['vout_index'])) if proofs else None


def binding(receipt, proof):
    query = receipt['query']
    return {'query': {'number': query['block_number'], 'hash': query['block_hash'].lower()},
            'publication': {'bitcoin_height': proof['bitcoin_height'], 'bitcoin_hash': proof['bitcoin_block_hash'],
                            'keystone_number': proof['checkpoint']['height'], 'txid': proof['txid'],
                            'tx_index': proof['tx_index'], 'vout': proof['vout_index']},
            'profile': PROFILE}


def route_key(receipt, proof):
    return hashlib.sha256(json.dumps(binding(receipt, proof), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def conflict(receipt):
    context = receipt.get('context', {})
    observations = sum((context.get(name, []) for name in ('hemi_headers', 'bitcoin_blocks', 'bitcoin_secondary')), [])
    return (context.get('requested_block_identity', {}).get('status') == 'different_current_rpc_block'
            or any(item.get('same') is False for item in observations)
            or any(item.get('matches_record') is False for item in context.get('collection_source_views', []))
            or any(item.get('code') in ('source_conflict', 'wrong_network', 'inconsistent_data', 'requested_block_not_current',
                                       'collection_source_view_changed', 'conflicting_retained_headers', 'retained_view_conflict')
                   for item in receipt.get('issues', [])))


def check_receipt_headers(packet, checked, receipt, proof):
    """Bind displayed RPC metadata to the full headers the worker authenticated."""
    base = checked.get('interval_evidence')
    if not base:
        return
    inner = packet / 'history/interval' if checked.get('schema') == SNAPSHOT_RESULT_SCHEMA else packet / 'interval'
    manifest, digest = interval_verifier.load_manifest(inner)
    if digest != base['manifest_sha256']:
        raise VerificationError('evidence_snapshot_changed', 'The checked interval manifest changed')
    headers = {row['number']: row for row in manifest['hemi_headers']}
    expected = list(proof['ancestry']) + [receipt['query']['header'], proof['checkpoint']['header']]
    if receipt['query'].get('covering_checkpoint'):
        expected.append(receipt['query']['covering_checkpoint']['header'])
    for metadata in expected:
        number = int(metadata['number'], 16)
        row = headers.get(number)
        if not row or row['hash'] != metadata['hash'].lower():
            raise VerificationError('hemi_receipt_binding', 'Displayed Hemi ancestry differs from the authenticated packet')
        fields = rlp_decode(bytes.fromhex(row['raw']))
        for key, index in (('parentHash', 0), ('stateRoot', 3), ('transactionsRoot', 4)):
            if bytes.fromhex(metadata[key].removeprefix('0x')) != fields[index]:
                raise VerificationError('hemi_receipt_binding', 'Displayed Hemi header fields differ from authenticated bytes')
        if int(metadata['timestamp'], 16) != int.from_bytes(fields[11], 'big'):
            raise VerificationError('hemi_receipt_binding', 'Displayed Hemi timestamp differs from authenticated bytes')


def verify_bounded(packet, number, hemi_hash, policy, *, check=None, accepted_context_sha256=None):
    """Kill the owned checker if the caller's clock expires; never reset it."""
    deadline = time.monotonic() + 20
    def guard():
        if check:
            check()
        if time.monotonic() >= deadline:
            raise DeadlineError('First-publication verification time limit reached')
    guard()
    with tempfile.TemporaryFile() as output:
        child = subprocess.Popen([sys.executable, '-I', '-B', str(Path(__file__).with_name('evidence_worker.py')),
                                  str(packet), str(number), hemi_hash, policy or '-', accepted_context_sha256 or '-'],
                                 stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            while child.poll() is None:
                guard()
                try:
                    child.wait(timeout=.05)
                except subprocess.TimeoutExpired:
                    pass
            guard()
            if child.returncode or output.tell() > 1_000_000:
                raise DataError('verification_unavailable', 'The evidence checker could not finish within its response bounds.')
            output.seek(0)
            return json.loads(output.read(1_000_001))
        finally:
            if child.poll() is None:
                child.kill()
            child.wait()


class EvidenceStore:
    def __init__(self, state_dir, *, policy=POLICY_ID, accepted_context_sha256=None,
                 finality_source_policy=None, accepted_finality_source_context_sha256=None):
        if policy not in (None, POLICY_ID, SNAPSHOT_POLICY):
            raise ValueError('Unsupported first-publication policy')
        if accepted_context_sha256 is not None and (policy != SNAPSHOT_POLICY or
                not isinstance(accepted_context_sha256, str) or not HEX.fullmatch(accepted_context_sha256)):
            raise ValueError('A canonical context checksum requires the snapshot policy')
        self.root = Path(state_dir).resolve() / 'first-publication'
        self.policy = policy
        self.accepted_context_sha256 = accepted_context_sha256
        self.finality = FinalityService(source_policy=finality_source_policy,
            accepted_source_context_sha256=accepted_finality_source_context_sha256)

    def route(self, receipt, proof=None):
        proof = proof or selected_proof(receipt)
        return self.root / route_key(receipt, proof) if proof else None

    def packet(self, receipt, proof=None):
        route = self.route(receipt, proof)
        if route is None:
            return None
        pointer = route / 'current.json'
        current = route
        while current != self.root.parent:
            if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()):
                raise DataError('evidence_path', 'Linked evidence directories are unsupported.')
            current = current.parent
        if pointer.is_symlink():
            raise DataError('evidence_path', 'Linked evidence pointers are unsupported.')
        if not pointer.is_file():
            return route / 'unprepared'
        if pointer.stat().st_size > 256:
            raise DataError('evidence_snapshot_changed', 'Invalid retained evidence pointer.')
        value = json.loads(pointer.read_text(encoding='utf-8'))
        digest = value.get('manifest_sha256', '')
        if not isinstance(digest, str) or not HEX.fullmatch(digest):
            raise DataError('evidence_snapshot_changed', 'Invalid retained evidence identity.')
        packet = route / 'snapshots' / digest / 'packet'
        current = packet
        while current != self.root.parent:
            if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()):
                raise DataError('evidence_path', 'Linked evidence directories are unsupported.')
            current = current.parent
        return packet

    def has_packet(self, receipt):
        packet = self.packet(receipt)
        return bool(packet and (packet / 'manifest.json').is_file())

    def publish(self, receipt, prepared):
        """Publish a newly built snapshot; never replace an existing packet."""
        digest = hashlib.sha256((prepared / 'manifest.json').read_bytes()).hexdigest()
        route = self.route(receipt)
        target = route / 'snapshots' / digest / 'packet'
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            prepared.rename(target)
            (target.parent / 'context.json').write_text(json.dumps({
                'packet_prepared_at': now(),
                'recorded_at': receipt.get('completed_at'),
                'source_checked_at': receipt.get('context', {}).get('checked_at'),
                'fresh_source_check_performed_by_verifier': False,
            }, separators=(',', ':')), encoding='utf-8')
        atomic_json(route / 'current.json', {'manifest_sha256': digest})
        return target

    def assess(self, receipt, *, check=None):
        proof = selected_proof(receipt)
        attempt = receipt.get('first_publication_attempt', {})
        attempt_status = attempt.get('status')
        result = {'status': attempt_status if attempt_status in ('invalid', 'incomplete') else 'not_available', 'query': {
            'number': receipt['query'].get('block_number'), 'hash': receipt['query'].get('block_hash')},
            'publication': None, 'packet_available': False, 'globally_first_proven': False,
            'policy_id': self.policy or POLICY_ID, 'accepted_by_operator': self.policy == POLICY_ID,
            'assumptions': [dict(item) for item in (SNAPSHOT_ASSUMPTIONS if self.policy == SNAPSHOT_POLICY else ASSUMPTIONS)],
            'issues': [{'code': attempt.get('code', 'evidence_not_prepared'),
                        'message': attempt.get('message', 'The stronger evidence package has not been prepared for this publication.')}],
            'recorded_context_only': True}
        if not proof:
            result['issues'] = [{'code': 'publication_unavailable', 'message': 'No applicable publication is available for the stronger check.'}]
            return result
        result.update(binding(receipt, proof))
        try:
            packet = self.packet(receipt, proof)
            if not (packet / 'manifest.json').is_file():
                return result
            manifest, _ = interval_verifier.load_manifest(packet)
            snapshot = manifest.get('schema') == SNAPSHOT_SCHEMA
            policy = self.policy if (self.policy == SNAPSHOT_POLICY) == snapshot else None
            if snapshot:
                checked = PACKET_RESULTS.verify(packet, result['query']['number'], result['query']['hash'], policy,
                    verifier=verify_bounded, check=check, accepted_context_sha256=self.accepted_context_sha256)
            else:
                checked = verify_bounded(packet, result['query']['number'], result['query']['hash'], policy, check=check)
            result.update(status=checked['status'], result=checked, issues=checked.get('issues', []))
            if checked.get('policy'):
                result.update(policy_id=checked['policy']['id'], assumptions=checked['policy']['assumptions'],
                    accepted_by_operator=bool(checked['policy']['accepted_by_caller'] and
                        (not snapshot or checked['policy'].get('context_accepted_by_caller') is True)))
            if checked.get('code'):
                result['issues'] = [{'code': checked['code'], 'message': checked['message']}]
            positive = checked.get('interval_evidence', checked).get('publication')
            if positive and positive != result['publication']:
                raise VerificationError('publication_binding', 'The checked packet belongs to another publication')
            check_receipt_headers(packet, checked, receipt, proof)
            if checked.get('manifest_sha256') and checked['manifest_sha256'] != packet.parent.name:
                raise VerificationError('evidence_snapshot_changed', 'The retained packet no longer matches its immutable snapshot identity')
            result['packet_digest'] = checked.get('manifest_sha256')
            result['packet_available'] = bool(result['packet_digest'] and checked['status'] not in ('invalid',))
            metadata = packet.parent / 'context.json'
            if metadata.is_file() and metadata.stat().st_size <= 4096:
                result['source_context'] = json.loads(metadata.read_text(encoding='utf-8'))
            if conflict(receipt):
                result.update(status='source_conflict', packet_available=False,
                              issues=[{'code': 'source_conflict', 'message': 'Changed or conflicting source identities suspend the stronger assessment. Historical mechanical checks remain separate.'}])
            elif receipt.get('mode') == 'live_metadata_refresh' and receipt.get('context', {}).get('current_view_confirmed') is not True:
                result.update(status='incomplete', packet_available=False,
                              issues=[{'code': 'source_recheck_incomplete', 'message': 'The latest source recheck did not finish; the stronger assessment is suspended.'}])
        except (VerificationError, DataError, OSError, ValueError, KeyError, TypeError) as exc:
            result.update(status=exc.status if isinstance(exc, VerificationError) else 'incomplete', packet_available=False,
                          issues=[{'code': getattr(exc, 'code', 'verification_unavailable'),
                                   'message': str(exc) if isinstance(exc, (VerificationError, DataError)) else 'The retained evidence package could not be checked.'}])
        return result

    def attach(self, wrapper, *, check=None, include_finality=True):
        wrapper['first_publication'] = self.assess(wrapper['receipt'], check=check)
        assessment = wrapper['first_publication']
        if assessment['packet_available']:
            base = ('/api/v1/blocks/' + assessment['query']['hash'] if wrapper.get('record_url')
                    else '/api/receipts/' + wrapper['id'])
            assessment['download_url'] = base + '/first-publication/' + assessment['packet_digest'] + '/download'
        if not include_finality:
            return wrapper
        # Packet flags never authorize this separate calculation. The isolated
        # worker rechecks receipt/packet bytes and exact operator contexts.
        packet = None
        if assessment.get('packet_available'):
            try:
                packet = self.packet(wrapper['receipt'])
            except (DataError, OSError, ValueError):
                pass
        wrapper['finality'] = self.finality.assess(wrapper['receipt'], packet=packet,
            first_policy=self.policy, first_context=self.accepted_context_sha256,
            expected_packet_digest=assessment.get('packet_digest') if packet else None,
            source_conflict=conflict(wrapper['receipt']), check=check)
        return wrapper

    def export(self, receipt, expected_digest, *, check=None):
        if not EXPORT_SLOT.acquire(blocking=False):
            raise EvidenceBusy()
        try:
            return self._export(receipt, expected_digest, check=check)
        finally:
            EXPORT_SLOT.release()

    def _export(self, receipt, expected_digest, *, check=None):
        assessment = self.assess(receipt, check=check)
        if not HEX.fullmatch(expected_digest) or not assessment['packet_available'] or assessment.get('packet_digest') != expected_digest:
            raise DataError('evidence_snapshot_changed', 'The requested evidence snapshot is unavailable or changed. Reload the result.')
        packet = self.packet(receipt)
        # The verifier already checks fixed paths and identities. Export only its
        # exact manifests/body list, never arbitrary directory contents.
        outer = interval_verifier._read_file(packet, 'manifest.json', MAX_MANIFEST_BYTES, 'manifest_missing')
        snapshot = json.loads(outer).get('schema') == SNAPSHOT_SCHEMA
        prefix = 'history/' if snapshot else ''
        history_raw = interval_verifier._read_file(packet, 'history/manifest.json', MAX_MANIFEST_BYTES, 'history_manifest_missing') if snapshot else None
        interval_outer = json.loads(history_raw) if snapshot else json.loads(outer)
        inner = interval_verifier._read_file(packet, prefix + 'interval/manifest.json', MAX_MANIFEST_BYTES, 'interval_manifest_missing')
        manifest = json.loads(inner)
        if (len(outer) > MAX_MANIFEST_BYTES or len(inner) > MAX_MANIFEST_BYTES
                or hashlib.sha256(outer).hexdigest() != expected_digest
                or interval_outer.get('interval_manifest_sha256') != hashlib.sha256(inner).hexdigest()
                or snapshot and (len(history_raw) > MAX_MANIFEST_BYTES or
                    json.loads(outer).get('history_manifest_sha256') != hashlib.sha256(history_raw).hexdigest())):
            raise DataError('evidence_snapshot_changed', 'The evidence snapshot changed during export.')
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_STORED) as archive:
            archive.writestr('packet/manifest.json', outer)
            if snapshot:
                archive.writestr('packet/history/manifest.json', history_raw)
            archive.writestr('packet/' + prefix + 'interval/manifest.json', inner)
            total = 0
            for row in manifest['bitcoin_blocks']:
                if check:
                    check()
                if not HEX.fullmatch(row['hash']):
                    raise DataError('evidence_snapshot_changed', 'Invalid evidence body identity.')
                path = packet / (prefix + 'interval/bitcoin') / (row['hash'] + '.bin')
                if path.is_symlink() or path.stat().st_size > MAX_BLOCK_BYTES:
                    raise DataError('evidence_snapshot_changed', 'The evidence body changed during export.')
                raw = interval_verifier._read_file(packet, prefix + 'interval/bitcoin/' + path.name, MAX_BLOCK_BYTES, 'bitcoin_body_missing')
                total += len(raw)
                if total > MAX_TOTAL_BODY_BYTES or len(raw) != row['size'] or hashlib.sha256(raw).hexdigest() != row['sha256']:
                    raise DataError('evidence_snapshot_changed', 'The evidence body changed during export.')
                archive.writestr('packet/' + prefix + 'interval/bitcoin/' + path.name, raw)
            for module in sorted((Path(__file__).resolve().parents[1] / 'traze_evidence').glob('*.py')):
                archive.writestr('traze_evidence/' + module.name, module.read_bytes())
            policy_help = (f'For the separately conditional snapshot assessment, independently select the context checksum, then add --accept-policy {SNAPSHOT_POLICY} --accept-context <checksum>.\n'
                           if snapshot else f'For the separately conditional assessment, add --accept-policy {POLICY_ID}.\n')
            archive.writestr('README.txt',
                'Traze first-publication evidence\n\nExtract this archive and run from the extracted directory:\n'
                f'python -B -m traze_evidence packet --hemi-block {assessment["query"]["number"]} --hemi-hash {assessment["query"]["hash"]}\n\n'
                + policy_help +
                'Read docs/HISTORY_ASSURANCE.md for assumptions and limits. No live source check is performed.\n')
            root = Path(__file__).resolve().parents[1]
            for name in ('LICENSE', 'docs/HISTORY_ASSURANCE.md', 'docs/ANCHORED_VERIFICATION.md', 'docs/SNAPSHOT_ASSURANCE.md'):
                archive.writestr(name, (root / name).read_bytes())
        return stream.getvalue(), assessment
