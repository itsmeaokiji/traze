"""Offline wrapping of retained history evidence with recorded snapshot context."""

import argparse
import hashlib
import json
from pathlib import Path
import tempfile

from traze_evidence import verify_packet
from traze_evidence import history, snapshot
from traze_evidence import verify as interval
from traze_evidence.errors import require


def wrap_history_packet(source, destination, context, *, expected_hemi_number, expected_hemi_hash):
    """No acquisition or policy acceptance; copy only bounded fixed evidence paths."""
    source, destination = Path(source).resolve(), Path(destination).absolute()
    require(not destination.exists(), 'snapshot_destination_exists', 'Choose a new packet directory')
    outer, digest = interval.load_manifest(source)
    require(outer.get('schema') == history.SCHEMA, 'snapshot_history_schema', 'Expected a version-2 history packet')
    inner, _ = interval.load_manifest(source / 'interval')
    rows = inner.get('bitcoin_blocks')
    require(isinstance(rows, list) and len(rows) <= interval.MAX_BITCOIN_BLOCKS,
            'packet_scope', 'Invalid or oversized Bitcoin body list')
    manifest = {'schema': snapshot.SCHEMA, 'profile': snapshot.PROFILE, 'query': outer['query'],
                'history_manifest_sha256': digest, 'assessment_context': context}
    raw = json.dumps(manifest, separators=(',', ':'), allow_nan=False).encode()
    require(len(raw) <= interval.MAX_MANIFEST_BYTES, 'packet_size', 'Snapshot context is too large')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='snapshot-', dir=destination.parent) as temporary:
        packet = Path(temporary) / 'packet'
        (packet / 'history/interval/bitcoin').mkdir(parents=True)
        (packet / 'manifest.json').write_bytes(raw)
        for relative in ('manifest.json', 'interval/manifest.json'):
            (packet / 'history' / relative).write_bytes(interval._read_file(source, relative,
                interval.MAX_MANIFEST_BYTES, 'history_manifest_missing'))
        total = 0
        for row in rows:
            body_hash = interval._hash(row['hash'], 'Bitcoin body hash')
            relative = 'interval/bitcoin/' + body_hash + '.bin'
            body = interval._read_file(source, relative, interval.MAX_BLOCK_BYTES, 'bitcoin_body_missing')
            total += len(body)
            require(total <= interval.MAX_TOTAL_BODY_BYTES, 'packet_size', 'Bitcoin evidence exceeds total bound')
            (packet / 'history' / relative).write_bytes(body)
        checked = verify_packet(packet, expected_hemi_number=expected_hemi_number, expected_hemi_hash=expected_hemi_hash)
        require(checked['status'] == 'verified_snapshot_evidence', 'snapshot_not_prepared',
                'Retained evidence/context did not pass the snapshot checks')
        packet.rename(destination)
    return {'packet_manifest_sha256': hashlib.sha256(raw).hexdigest(),
            'context_sha256': snapshot.context_digest(context), 'status': checked['status'], 'network_requests': 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Wrap existing history evidence with recorded snapshot context offline')
    parser.add_argument('--history-packet', type=Path, required=True)
    parser.add_argument('--context-file', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--hemi-block', type=int, required=True)
    parser.add_argument('--hemi-hash', required=True)
    args = parser.parse_args(argv)
    with args.context_file.open('rb') as handle:
        raw = handle.read(interval.MAX_MANIFEST_BYTES + 1)
    require(len(raw) <= interval.MAX_MANIFEST_BYTES, 'packet_size', 'Context exceeds manifest bound')
    context = json.loads(raw, object_pairs_hook=interval._unique_pairs)
    result = wrap_history_packet(args.history_packet, args.output, context,
        expected_hemi_number=args.hemi_block, expected_hemi_hash=args.hemi_hash)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
