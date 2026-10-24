"""Read-only, per-height accounting for a declared finite backfill.

This describes compiled evidence and recorded source observations. It does not
choose a canonical chain, infer absence outside the declared intervals, or turn
missing association work into a negative publication result.
"""

import json
from pathlib import Path

from .catalogue import Catalogue, timestamp


def _publication(db, row, job, plan):
    progress = job.get('associations', {}).get(row['id'], {})
    available = bool(db.execute('SELECT 1 FROM associations WHERE publication_id=?', (row['id'],)).fetchone())
    supported = bool(row['supported'])
    issue = progress.get('issue')
    if not supported:
        issue = {'code': 'unsupported_format', 'message': 'Candidate is outside the reviewed payload format'}
    elif not available and not issue:
        failed = any(a.get('keystone') == row['keystone_height'] and a.get('issue')
                     for a in job.get('associations', {}).values())
        issue = {'code': 'not_attempted_after_same_height_failure' if failed else 'association_not_completed',
                 'message': 'No checked matching Hemi association was retained for this exact publication'}
    ancestry = ('retained_association' if progress.get('origin') == 'retained_association'
                else 'complete' if progress.get('state') == 'complete' else 'incomplete')
    recheck = job.get('hemi_rechecks', {}).get(str(row['keystone_height']), {'state': 'not_checked'})
    data = json.loads(row['data'])
    return {'id': row['id'], 'txid': row['txid'], 'vout': row['vout'],
            'tx_index': data.get('tx_index'), 'supported': supported,
            'encoded_keystone_height': row['keystone_height'],
            'association_available': available, 'association_work': progress.get('state', 'not_attempted'),
            'ancestry_work': ancestry if supported else 'not_applicable',
            'hemi_source_recheck': recheck if supported and available else {'state': 'not_applicable'},
            'issue': issue, 'was_seed_pending': row['id'] in plan['scope']['seed_pending_publications']}


def coverage_report(directory):
    # Lazy import keeps this public report entry point free of a transport client.
    from .backfill import check_prefix, ledger_usage, load_plan, read_ledger
    directory = Path(directory).resolve()
    plan = load_plan(directory)
    scope = plan['scope']
    ledger = read_ledger(scope['ledger_directory'])
    usage = check_prefix(ledger, scope)
    stages, heights, publications, conflicts, links = [], [], [], [], []
    with Catalogue(directory / 'service.sqlite3', readonly=True) as catalogue:
        db = catalogue.db
        db.execute('BEGIN')
        for stage in scope['stages']:
            job = json.loads(db.execute('SELECT data FROM collection_jobs WHERE id=?', (stage['job'],)).fetchone()[0])
            conflicts.extend({'chain': 'hemi', 'height': int(height), 'recheck': value}
                             for height, value in job.get('hemi_rechecks', {}).items()
                             if value.get('state') == 'changed')
            rows, previous = [], None
            for height in range(stage['range'][0], stage['range'][1] + 1):
                item = job.get('items', {}).get(str(height), {})
                expected = stage['pinned'].get(str(height)) or item.get('hash')
                variants = [row[0] for row in db.execute('SELECT DISTINCT hash FROM nominations WHERE height=? ORDER BY hash', (height,))]
                observed = [json.loads(row[0]) for row in db.execute("SELECT data FROM collection_views WHERE chain='bitcoin' AND height=? ORDER BY sequence", (height,))]
                block = db.execute('SELECT * FROM blocks WHERE hash=?', (expected,)).fetchone() if expected else None
                body = json.loads(block['data']) if block and block['data'] else None
                complete = bool(block and block['status'] == 'complete' and body and body.get('full_transaction_root_checked'))
                issue = job.get('body_issues', {}).get(str(height))
                if not complete and not issue:
                    issue = json.loads(block['issue']) if block and block['issue'] else (
                        plan.get('issue') or {'code': 'body_not_completed', 'message': 'This required height has no complete compiled body'})
                checked = job.get('bitcoin_rechecks', {}).get(str(height), {'state': 'not_checked'})
                at_height = [_publication(db, row, job, plan) for row in db.execute(
                    'SELECT * FROM publications WHERE block_hash=? ORDER BY txid,vout', (expected,))] if expected else []
                changed = any(value.get('status') in ('changed', 'source_conflict') for value in observed)
                if changed or len(variants) > 1:
                    conflicts.append({'height': height, 'hashes': variants, 'recorded_source_change': changed})
                link = 'interval_start' if previous is None else 'unavailable'
                if previous is not None and complete and previous['complete']:
                    link = 'matched' if body['previous_hash'] == previous['hash'] else 'conflict'
                    links.append({'height': height, 'previous_height': height - 1, 'state': link})
                    if link == 'conflict':
                        conflicts.append({'height': height, 'reason': 'adjacent_compiled_headers_do_not_link'})
                entry = {'height': height, 'hash': expected, 'retained_hash_variants': variants,
                         'body_state': 'complete' if complete else block['status'] if block else 'not_nominated',
                         'compiled_body': body, 'body_issue': issue, 'job_item_state': item.get('state', 'not_attempted'),
                         'bitcoin_source_recheck': checked, 'source_observations': observed,
                         'previous_header_link': link, 'publications': at_height}
                rows.append(entry); heights.append(entry); publications.extend(at_height)
                previous = {'hash': expected, 'complete': complete}
            stage_complete = bool(job.get('final_tip')) and all(
                h['body_state'] == 'complete' and h['bitcoin_source_recheck']['state'] == 'matched'
                and h['previous_header_link'] != 'conflict' and len(h['retained_hash_variants']) == 1
                and not any(o.get('status') in ('changed', 'source_conflict') for o in h['source_observations'])
                and all(not p['supported'] or (p['association_available'] and p['ancestry_work'] != 'incomplete'
                        and p['hemi_source_recheck']['state'] == 'matched') for p in h['publications'])
                for h in rows)
            stages.append({'kind': stage['kind'], 'range': stage['range'], 'job': stage['job'],
                           'supported_domain_complete': stage_complete,
                           'state': job['state'], 'initial_tip': job.get('initial_tip'),
                           'final_tip': job.get('final_tip'), 'hemi_rechecks': job.get('hemi_rechecks', {}),
                           'required_heights': rows})
        summary = catalogue.summary()
        seed_remaining = [identity for identity in scope['seed_pending_publications'] if not db.execute(
            'SELECT 1 FROM associations WHERE publication_id=?', (identity,)).fetchone()]
    supported = [p for p in publications if p['supported']]
    pending = [p for p in supported if not p['association_available']]
    ancestry_pending = [p for p in supported if p['ancestry_work'] == 'incomplete']
    missing = [h['height'] for h in heights if h['body_state'] != 'complete']
    unchecked = [h['height'] for h in heights if h['bitcoin_source_recheck']['state'] != 'matched']
    unchecked_hemi = sorted({p['encoded_keystone_height'] for p in supported if p['association_available']
                             and p['hemi_source_recheck']['state'] != 'matched'})
    complete = all(s['supported_domain_complete'] for s in stages) and not conflicts
    return {'schema': 'traze-finite-coverage-v1', 'generated_at': timestamp(), 'scope_id': scope['id'],
            'state': plan['state'], 'closed_at': plan.get('closed_at'), 'clock': plan['clock'],
            'terminal_issue': plan.get('issue'), 'declared_intervals': [s['range'] for s in scope['stages']],
            'required_heights': len(heights), 'complete_bodies': len(heights) - len(missing),
            'missing_body_heights': missing, 'supported_publications': len(supported),
            'unsupported_publications': len(publications) - len(supported),
            'available_associations': len(supported) - len(pending),
            'unresolved_associations': len(pending), 'incomplete_ancestry_work': len(ancestry_pending),
            'bitcoin_rechecks_not_matched': unchecked, 'hemi_rechecks_not_matched': unchecked_hemi,
            'source_conflicts': conflicts, 'adjacent_header_checks': links,
            'supported_domain_complete': complete,
            'accounting_complete': bool(plan.get('closed_at')) and usage['all_segments_closed'],
            'seed_pending_before': len(scope['seed_pending_publications']),
            'seed_pending_resolved': len(scope['seed_pending_publications']) - len(seed_remaining),
            'seed_pending_remaining': seed_remaining,
            'new_interval_publications': sum(len(h['publications']) for s in stages if s['kind'] == 'backfill' for h in s['required_heights']),
            'catalogue_summary': summary, 'stages': stages,
            'usage': {**usage, 'cumulative_response_bytes': ledger_usage(ledger)['response_bytes'],
                      'limits': scope['limits'], 'cumulative_response_ceiling': scope['cumulative_response_ceiling'],
                      'original_ledger_prefix_preserved': True,
                      'byte_scope': 'HTTP bodies/headers and conservative reservations; TLS/TCP excluded'},
            'qualifications': [
                'Accounting completeness is not complete acquisition, association or ancestry availability.',
                'Body extraction covers the reviewed HEMI first-data-push parser and supported payload format.',
                'Heights/tips are recorded public-provider observations; adjacent links are not independent consensus validation.',
                'Source checks are recorded observations, not perpetual freshness or provider independence.',
                'Separate intervals do not establish coverage of the heights between them.',
                'No publication absence outside checked bodies, globally-first proof, deployed finality or independent chain execution is asserted.']}
