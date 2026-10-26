"""Source-free receipt ingestion. Inclusion evidence is not full-block coverage."""
import copy
import hashlib
import json
from pathlib import Path

from .bitcoin import MAX_RAW_BLOCK, hex32, parse_block
from .catalogue import digest, encode, timestamp
from .errors import DataError
from .proof import verify_proof, verify_receipt

MAX_NOMINATIONS = 512


def publication_id(proof):
    return digest([hex32(proof['bitcoin_block_hash']).hex(), proof['txid'], proof['vout_index']])


def receipt_data(path, expected_sha256, check):
    size = path.stat().st_size
    if size > 10_000_000:
        raise DataError('receipt_too_large', 'Saved receipt exceeds the supported input bound')
    check(size)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise DataError('receipt_changed', 'The saved receipt differs from the queued evidence')
    receipt = json.loads(raw)
    verify_receipt(receipt, check)
    return receipt


def ingest(catalogue, path, expected_sha256, block_directory, check):
    """Commit checked proofs first, then upgrade only bodies actually parsed here.

    Each completed body is durable. Read-budget exhaustion leaves retryable work;
    missing cache files never trigger transport and never fabricate a full scan.
    """
    receipt = receipt_data(path, expected_sha256, check)
    db = catalogue.db
    db.execute('''CREATE TABLE IF NOT EXISTS receipt_ingestions (
        receipt_sha256 TEXT PRIMARY KEY, result TEXT NOT NULL)''')
    db.commit()
    previous = {name: db.execute('SELECT COUNT(*) FROM ' + name).fetchone()[0]
                for name in ('publications', 'associations', 'templates', 'headers')}
    observation = {key: copy.deepcopy(receipt.get(key)) for key in (
        'query', 'sources', 'context', 'started_at', 'completed_at', 'bitcoin_tip_before',
        'bitcoin_tip_after', 'catalogue')}
    observation['qualification'] = 'Original recorded observations; indexing is not a fresh source check'
    nominations = {}
    blocks = receipt.get('coverage', {}).get('blocks', [])
    if not isinstance(blocks, list) or len(blocks) > MAX_NOMINATIONS:
        raise DataError('corpus_too_large', 'Too many nominated blocks for one offline handoff')
    for block in blocks:
        block_hash = hex32(block['hash']).hex()
        number = block['height']
        if type(number) is not int or not 0 <= number <= 0xffffffff:
            raise DataError('invalid_nomination', 'Nominated Bitcoin height must be source-reported and nonnegative')
        nominations[(block_hash, number)] = block
    for proof in receipt['proofs']:
        heights = [proof['bitcoin_height']] if proof.get('bitcoin_height') is not None else proof.get('reported_bitcoin_heights', [])
        if not isinstance(heights, list) or len(heights) > 50:
            raise DataError('invalid_nomination', 'Unsupported reported-height metadata')
        for number in heights:
            if type(number) is not int or not 0 <= number <= 0xffffffff:
                raise DataError('invalid_nomination', 'Unsupported reported Bitcoin height')
            nominations.setdefault((hex32(proof['bitcoin_block_hash']).hex(), number),
                {'height': number, 'hash': proof['bitcoin_block_hash'], 'source': 'provided_publication_proof'})
    if len(nominations) > MAX_NOMINATIONS:
        raise DataError('corpus_too_large', 'Too many distinct nominated blocks for one offline handoff')
    payloads = set()
    with db:
        db.execute('INSERT OR IGNORE INTO imports VALUES (?,?,?,?)',
                   (expected_sha256, path.name, encode(observation), timestamp()))
        if receipt['query'].get('header'):
            catalogue._header(receipt['query']['header'], expected_sha256)
        for (block_hash, number), block in nominations.items():
            check()
            db.execute("INSERT OR IGNORE INTO blocks(hash,status) VALUES (?,'pending')", (block_hash,))
            db.execute('INSERT OR IGNORE INTO nominations VALUES (?,?,?,?)',
                       (expected_sha256, block_hash, number, encode(block)))
        for proof in receipt['proofs']:
            check()
            block_hash = hex32(proof['bitcoin_block_hash']).hex()
            payload = proof['checkpoint']['payload_hex']
            payloads.add(payload)
            db.execute("INSERT OR IGNORE INTO blocks(hash,status) VALUES (?,'proof_only')", (block_hash,))
            db.execute("UPDATE blocks SET status='proof_only',issue=NULL WHERE hash=? AND status IN ('pending','failed')", (block_hash,))
            template = {'checkpoint': proof['checkpoint'], 'ancestry': proof['ancestry']}
            template_id = digest(template)
            db.execute('INSERT OR IGNORE INTO templates VALUES (?,?,?)', (template_id, payload, encode(template)))
            db.execute('INSERT OR IGNORE INTO template_origins VALUES (?,?)', (template_id, expected_sha256))
            for header in proof['ancestry'] + [proof['checkpoint']['previous_header']]:
                if header:
                    catalogue._header(header, expected_sha256)
            record = {key: copy.deepcopy(proof[key]) for key in (
                'bitcoin_block_hash', 'bitcoin_header_hex', 'txid', 'raw_transaction_hex',
                'tx_index', 'vout_index', 'merkle_siblings_display_hex')}
            identity = publication_id(proof)
            db.execute('INSERT OR IGNORE INTO publications VALUES (?,?,?,?,?,?,?,?)',
                       (identity, block_hash, proof['txid'], proof['vout_index'], 1,
                        payload, proof['checkpoint']['height'], encode(record)))
            stored = json.loads(db.execute('SELECT data FROM publications WHERE id=?', (identity,)).fetchone()[0])
            verify_proof({**stored, **template, 'bitcoin_height': proof.get('bitcoin_height')}, check)
            db.execute('INSERT OR IGNORE INTO associations VALUES (?,?)', (identity, template_id))

    directory = Path(block_directory).resolve()
    upgraded = 0
    all_hashes = {key[0] for key in nominations} | {hex32(p['bitcoin_block_hash']).hex() for p in receipt['proofs']}
    for block_hash in sorted(all_hashes):
        check()
        if db.execute('SELECT status FROM blocks WHERE hash=?', (block_hash,)).fetchone()[0] == 'complete':
            continue
        body = directory / (block_hash + '.bin')
        try:
            if body.is_symlink() or not body.is_file() or not body.resolve().is_relative_to(directory):
                continue
            size = body.stat().st_size
            if size > MAX_RAW_BLOCK:
                raise DataError('body_too_large', 'Retained block exceeds the parser bound')
            check(size)
            with body.open('rb') as handle:
                raw = handle.read(MAX_RAW_BLOCK + 1)
            if len(raw) > MAX_RAW_BLOCK:
                raise DataError('body_too_large', 'Retained block grew beyond the parser bound')
            parsed = parse_block(raw, block_hash, check)
            catalogue._store_body(block_hash, raw, parsed)
            payloads.update(c['payload'].hex() for c in parsed['candidates'] if c['supported'])
            upgraded += 1
        except OSError:
            continue
        except DataError as exc:
            if exc.code in ('read_capacity', 'response_capacity'):
                raise
            with db:
                db.execute("UPDATE blocks SET issue=? WHERE hash=? AND status!='complete'",
                           (encode({'code': exc.code, 'message': str(exc)}), block_hash))
    # Reuse only payload-matched, checked metadata. No unrelated pending record is
    # relabelled merely because some Hemi search succeeded.
    for payload in sorted(payloads):
        rows = db.execute('''SELECT p.id,p.data,t.id AS template_id,t.data AS template
            FROM publications p JOIN templates t ON t.payload=p.payload
            WHERE p.supported=1 AND p.payload=? AND NOT EXISTS
            (SELECT 1 FROM associations a WHERE a.publication_id=p.id AND a.template_id=t.id)''', (payload,))
        with db:
            for row in rows:
                check(len(row['data']) + len(row['template']))
                record, template = json.loads(row['data']), json.loads(row['template'])
                verify_proof({**record, **template, 'bitcoin_height': None}, check)
                db.execute('INSERT OR IGNORE INTO associations VALUES (?,?)', (row['id'], row['template_id']))
    counts = {name: db.execute('SELECT COUNT(*) FROM ' + name).fetchone()[0] - previous[name] for name in previous}
    body_counts = {'complete': 0, 'proof_only': 0, 'unavailable': 0}
    for block_hash in all_hashes:
        state = db.execute('SELECT status FROM blocks WHERE hash=?', (block_hash,)).fetchone()[0]
        body_counts[state if state in ('complete', 'proof_only') else 'unavailable'] += 1
    selected = receipt.get('selection', {}).get('current_proof_index')
    if selected is None:
        selected = receipt.get('selection', {}).get('best_observed_proof_index')
    proof = receipt['proofs'][selected if type(selected) is int and 0 <= selected < len(receipt['proofs']) else 0] if receipt['proofs'] else None
    updated = any(counts.values()) or upgraded > 0
    result = {'state': 'indexed' if updated else 'already_indexed', 'receipt_sha256': expected_sha256,
              'publication_count': len({publication_id(p) for p in receipt['proofs']}),
              'publication_ids': sorted({publication_id(p) for p in receipt['proofs']}),
              'added_publications': counts['publications'], 'added_associations': counts['associations'],
              'body_scope': body_counts, 'source_requests': 0, 'source_views_rechecked': False,
              'record_url': '/records/publications/' + publication_id(proof) if proof else None,
              'hemi_record_url': '/records/hemi/' + receipt['query']['block_hash'].lower() if proof and
                  db.execute('SELECT COUNT(DISTINCT height) FROM nominations WHERE hash=?', (hex32(proof['bitcoin_block_hash']).hex(),)).fetchone()[0] == 1 else None}
    check()
    with db:
        db.execute('INSERT OR REPLACE INTO receipt_ingestions VALUES (?,?)', (expected_sha256, encode(result)))
    return result
