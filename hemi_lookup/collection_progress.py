"""Recorded collection freshness and finite child coverage, without source work."""

import time
from datetime import datetime

from .bitcoin import hex32
from .errors import DataError


def observation_age(value, now):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        seconds = now - parsed.timestamp()
        return round(seconds, 3) if parsed.tzinfo is not None and seconds >= 0 else None
    except (ValueError, TypeError, AttributeError, OverflowError):
        return None


def progress_view(db, service, job, *, now=None):
    now = time.time() if now is None else now
    observations = job.get('tip_observations', []) if job else []
    candidates = [(service or {}).get('latest_tip'), observations[-1] if observations else None, (job or {}).get('final_tip')]
    def order(tip):
        try:
            value = datetime.fromisoformat(tip['observed_at'].replace('Z', '+00:00'))
            return value.timestamp() if value.tzinfo is not None else float('-inf')
        except (ValueError, TypeError, KeyError, AttributeError):
            return float('-inf')
    tip = max((t for t in candidates if isinstance(t, dict)), key=order, default=None)
    observed = None
    if isinstance(tip, dict) and type(tip.get('height')) is int and 0 <= tip['height'] <= 0xffffffff:
        try:
            block_hash = hex32(tip['hash']).hex()
            age = observation_age(tip.get('observed_at'), now)
            observed = {'height': tip['height'], 'hash': block_hash,
                        'observed_at': tip.get('observed_at'), 'age_seconds': age,
                        'clock_valid': age is not None}
        except (DataError, KeyError):
            pass
    start, next_height, ready = None, None, None
    if service:
        start, next_height = service['start_height'], service['next_height']
        if (type(start) is not int or type(next_height) is not int or not 0 <= start <= next_height <= 0x100000000):
            raise DataError('invalid_collection_progress', 'Recorded service cursor requires review')
        anchor = service.get('anchor')
        if anchor and type(anchor.get('height')) is int and start <= anchor['height'] == next_height - 1:
            ready = anchor['height']
    remaining = (max(0, observed['height'] - next_height + 1)
                 if service and observed and observed['clock_valid'] else None)
    details = []
    if job:
        low, high = job['range']
        if type(low) is not int or type(high) is not int or not 0 <= low <= high <= 0xffffffff or high - low + 1 > 200:
            raise DataError('invalid_collection_progress', 'Current child coverage exceeds the finite report domain')
        for height in range(low, high + 1):
            item = job.get('items', {}).get(str(height), {})
            block_hash = item.get('hash')
            body = db.execute('SELECT status FROM blocks WHERE hash=?', (block_hash,)).fetchone() if block_hash else None
            processed = item.get('state') == 'processed' and bool(body and body[0] == 'complete')
            publications = []
            for row in db.execute('SELECT p.id,p.supported,p.keystone_height,EXISTS(SELECT 1 FROM associations a WHERE a.publication_id=p.id) FROM publications p WHERE p.block_hash=? ORDER BY p.id', (block_hash,)) if block_hash else []:
                work = job.get('associations', {}).get(row[0], {})
                publications.append({'id': row[0], 'supported': bool(row[1]), 'keystone_height': row[2],
                                     'association_available': bool(row[3]), 'work_state': work.get('state', 'not_attempted'),
                                     'issue_code': work.get('issue', {}).get('code')})
            update = next((u for u in job.get('updates', []) if u['range'][0] <= height <= u['range'][1]), {})
            checked = processed and update.get('state') == 'complete' and bool(item.get('evidence_ready_at'))
            if job.get('attempt_finished') and job['state'] == 'complete':
                checked = (processed and job.get('bitcoin_rechecks', {}).get(str(height), {}).get('state') == 'matched'
                           and all(not p['supported'] or (p['association_available'] and p['work_state'] == 'complete'
                                   and job.get('hemi_rechecks', {}).get(str(p['keystone_height']), {}).get('state') == 'matched')
                                   for p in publications))
            details.append({'height': height, 'hash': block_hash, 'body_processed': processed,
                            'checked_update_complete': checked, 'update_state': update.get('state', 'not_attempted'),
                            'ready_at': item.get('evidence_ready_at'), 'publications': publications})
    publications = [p for h in details for p in h['publications']]
    supported = [p for p in publications if p['supported']]
    incomplete_bodies = [h['height'] for h in details if not h['body_processed']]
    pending_metadata = sum(not p['association_available'] for p in supported)
    pending_ancestry = sum(p['association_available'] and p['work_state'] != 'complete' for p in supported)
    return {'scope': 'ongoing_service' if service else 'finite_job',
            'observed_bitcoin_tip': observed,
            'service_start_height': start, 'checked_through_height': ready,
            'next_height': next_height, 'remaining_to_observed_tip': remaining,
            'last_ready_at': (service or {}).get('last_ready_at'),
            'controlled_replay': bool((service or {}).get('controlled_replay')),
            'current_job': {'range': job['range'] if job else None, 'required_heights': len(details),
                            'processed_bodies': len(details) - len(incomplete_bodies),
                            'unprocessed_body_heights': incomplete_bodies,
                            'supported_publications': len(supported),
                            'unsupported_publications': len(publications) - len(supported),
                            'pending_metadata': pending_metadata, 'pending_ancestry': pending_ancestry,
                            'checked_update_heights': sum(h['checked_update_complete'] for h in details),
                            'height_accounting': details},
            'qualification': 'Recorded provider tip and service-scoped checked cursor; not current consensus, complete history or a per-target ancestry guarantee.'}


def public_collection_status(state, db):
    from .collection_store import collection_status
    value = collection_status(state, db=db)
    if value is None:
        return None
    keys = ('state', 'kind', 'processed_blocks', 'supported_publications', 'associated_publications',
            'unresolved_publications', 'pending_ancestry', 'source_conflicts', 'updated_at')
    result = {key: value[key] for key in keys if key in value}
    progress = dict(value['progress'])
    progress['current_job'] = {key: item for key, item in progress['current_job'].items() if key != 'height_accounting'}
    result['progress'] = progress
    return result
