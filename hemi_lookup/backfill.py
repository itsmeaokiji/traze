"""Finite historical indexing with one durable scope, clock and original ledger.

The working index is separate from acquisition accounting. Existing collector
body/association checks are reused; no finality or first-publication rule changes.
"""

import argparse
import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .bitcoin import MAX_RAW_BLOCK, hex32
from .catalogue import Catalogue, digest, encode, timestamp
from .collection_store import CollectionStore, RunnerLock, epoch
from .collector import Collector
from .engine import Engine
from .errors import BudgetError, CollectionStopped, DataError, DeadlineError
from .network import PublicSources
from .proof import same_header
from .storage import BlockCache, SAFETY_MARGIN, Session, atomic_json

SCHEMA = 'traze-finite-backfill-v1'


def ledger_usage(state):
    return {'response_bytes': state['downloaded_bytes'] + sum(state['prior_validation_bytes'].values()),
            'runs': len(state['runs'])}


def read_ledger(directory):
    value = json.loads((Path(directory) / 'session.json').read_text(encoding='utf-8'))
    if value.get('schema') != 1:
        raise DataError('invalid_backfill_ledger', 'Use an existing version-1 accounting ledger')
    return value


def legacy_prefix(state):
    """Pin pre-closure-format entries; never rewrite them as closed."""
    first = next((i for i, run in enumerate(state['runs']) if 'closed_at' in run), len(state['runs']))
    if any('closed_at' not in run for run in state['runs'][first:]):
        raise DataError('session_locked', 'A modern accounting segment remains unclosed')
    older = state['runs'][:first]
    if older and (first == len(state['runs']) or any(q.get('status') == 'reserved' for run in older for q in run['requests'])):
        raise DataError('session_locked', 'Legacy accounting needs operator reconciliation before acquisition')
    return [{'id': run['id'], 'started_at': run['started_at']} for run in older]


def check_prefix(state, scope):
    before = scope['ledger_checkpoint']
    count = before['runs']
    if (state['prior_validation_bytes'] != before['prior_validation_bytes']
            or len(state['runs']) < count or digest(state['runs'][:count]) != before['run_prefix_sha256']):
        raise DataError('backfill_ledger_changed', 'Original accounting entries changed; preserve the ledger')
    runs = state['runs'][count:]
    if any(run.get('context', {}).get('backfill') != scope['id'] for run in runs):
        raise DataError('backfill_ledger_changed', 'Another operation changed the baseline; reconcile before continuing')
    if state['downloaded_bytes'] != before['downloaded_bytes'] + sum(run['received_bytes'] for run in runs):
        raise DataError('backfill_ledger_changed', 'Backfill usage does not reconcile with original accounting')
    return {'response_bytes': sum(run['received_bytes'] for run in runs),
            'requests': sum(len(run['requests']) for run in runs),
            'body_attempts': sum(run['body_attempts'] for run in runs),
            'segments': len(runs), 'all_segments_closed': all('closed_at' in run for run in runs)}


def load_plan(directory):
    directory = Path(directory).resolve()
    plan = json.loads((directory / 'backfill.json').read_text(encoding='utf-8'))
    if plan.get('schema') != SCHEMA or plan['scope']['index_directory'] != str(directory):
        raise DataError('invalid_backfill', 'Saved backfill identity/path does not match this working index')
    with Catalogue(directory / 'service.sqlite3', readonly=True) as catalogue:
        rows = dict(catalogue.db.execute("SELECT key,value FROM meta WHERE key IN ('backfill_scope','backfill_clock')"))
    if rows.get('backfill_scope') != digest(plan['scope']):
        raise DataError('backfill_scope_changed', 'Saved ranges, limits or ledger binding changed')
    if rows.get('backfill_clock') != encode(plan.get('clock', {})):
        raise DataError('backfill_clock_changed', 'The original acquisition window cannot be reset')
    return plan


def prepare(root, index_directory, ledger_directory, seed, ranges, *, repair_ranges=(),
            max_bytes=80_000_000, max_requests=3000, max_attempts=14, wall_seconds=900):
    root, directory, ledger_directory, seed = map(lambda p: Path(p).resolve(),
                                                  (root, index_directory, ledger_directory, seed))
    settings = (max_bytes, max_requests, max_attempts, wall_seconds)
    if (any(type(n) is not int for n in settings) or max_bytes <= SAFETY_MARGIN
            or max_requests < 1 or max_attempts < 0 or not 1 <= wall_seconds <= 86400):
        raise DataError('invalid_backfill', 'Use positive finite limits, including response safety headroom')
    stages, used = [], set()
    for kind, windows in (('repair', repair_ranges), ('backfill', ranges)):
        for low, high in windows:
            if (type(low) is not int or type(high) is not int or not 0 <= low <= high <= 0xffffffff
                    or high - low + 1 > 200 or used.intersection(range(low, high + 1))):
                raise DataError('invalid_backfill', 'Use disjoint finite intervals of at most 200 heights')
            used.update(range(low, high + 1)); stages.append({'kind': kind, 'range': [low, high]})
    if not stages or len(used) > 200:
        raise DataError('invalid_backfill', 'A backfill may contain at most 200 required heights')
    if directory.exists() or seed.is_relative_to(directory) or ledger_directory.is_relative_to(directory):
        raise DataError('invalid_backfill', 'Use a new working index, separate from the seed and original ledger')
    ledger = read_ledger(ledger_directory)
    if (ledger_directory / 'session.lock').exists():
        raise DataError('session_locked', 'Finish/reconcile the existing acquisition session first')
    older = legacy_prefix(ledger)
    seed_sha = hashlib.sha256(seed.read_bytes()).hexdigest()
    with Catalogue(seed, readonly=True) as source:
        if source.db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise DataError('invalid_backfill_seed', 'The seed index failed its integrity check')
        source.db.execute('BEGIN')
        seed_summary = source.summary()
        pending = [row[0] for row in source.db.execute('SELECT id FROM publications p WHERE supported=1 AND NOT EXISTS(SELECT 1 FROM associations a WHERE a.publication_id=p.id) ORDER BY id')]
        retained = [row[0] for row in source.db.execute('SELECT DISTINCT publication_id FROM associations ORDER BY publication_id')]
        for stage in stages:
            stage['pinned'] = {}
            if stage['kind'] == 'repair':
                for height in range(stage['range'][0], stage['range'][1] + 1):
                    rows = source.db.execute("SELECT DISTINCT n.hash FROM nominations n JOIN blocks b ON b.hash=n.hash WHERE n.height=? AND b.status='complete'", (height,)).fetchall()
                    if len(rows) != 1:
                        raise DataError('invalid_backfill_seed', 'Each repair height needs one compiled retained block')
                    stage['pinned'][str(height)] = rows[0][0]
        directory.mkdir(parents=True)
        with closing(sqlite3.connect(directory / 'service.sqlite3')) as destination:
            source.db.backup(destination)
    if hashlib.sha256(seed.read_bytes()).hexdigest() != seed_sha:
        raise DataError('backfill_seed_changed', 'The source index changed during the offline snapshot')
    with CollectionStore(directory) as store:
        for stage in stages:
            job = store.create(*stage['range'], batch_size=1, max_bytes=max_bytes,
                max_attempts=max(1, max_attempts), max_requests=max_requests, max_seconds=600,
                segment_seconds=600, wall_seconds=wall_seconds)
            stage['job'] = job['id']
        scope = {'id': uuid.uuid4().hex, 'root': str(root), 'index_directory': str(directory),
            'ledger_directory': str(ledger_directory), 'seed': str(seed), 'seed_sha256': seed_sha,
            'seed_summary': seed_summary, 'seed_pending_publications': pending,
            'seed_associated_publications': retained, 'stages': stages,
            'limits': {'response_bytes': max_bytes, 'requests': max_requests,
                       'body_attempts': max_attempts, 'wall_seconds': wall_seconds},
            'ledger_checkpoint': {**ledger_usage(ledger), 'downloaded_bytes': ledger['downloaded_bytes'],
                'prior_validation_bytes': ledger['prior_validation_bytes'],
                'legacy_preclosure_entries': older,
                'run_prefix_sha256': digest(ledger['runs'])},
            'cumulative_response_ceiling': ledger_usage(ledger)['response_bytes'] + max_bytes}
        plan = {'schema': SCHEMA, 'created_at': timestamp(), 'scope': scope, 'clock': {},
                'state': 'prepared', 'active_stage': 0, 'issue': None}
        with store.db:
            store.db.execute('INSERT INTO meta VALUES (?,?)', ('backfill_scope', digest(scope)))
            store.db.execute('INSERT INTO meta VALUES (?,?)', ('backfill_clock', encode({})))
        atomic_json(directory / 'backfill.json', plan)
    return plan


class BackfillBudget:
    def __init__(self, plan, stop_check):
        self.plan, self.scope, self.stop_check = plan, plan['scope'], stop_check
        self.end = epoch(plan['clock']['expires_at'])
        self.start = epoch(plan['clock']['started_at'])
        self.monotonic_end = time.monotonic() + max(0, self.end - time.time())

    def remaining_seconds(self):
        self.stop_check()
        if time.time() < self.start - 1:
            raise DataError('backfill_clock_changed', 'Clock moved before the original backfill window')
        return max(0, min(self.end - time.time(), self.monotonic_end - time.monotonic()))

    def check_clock(self):
        if self.remaining_seconds() <= 0:
            raise DeadlineError('The original finite backfill acquisition window ended')

    def prepare(self, session):
        self.check_clock()
        if (str(session.directory) != self.scope['ledger_directory']
                or session.context.get('backfill') != self.scope['id']
                or session.session_limit != self.scope['cumulative_response_ceiling']):
            raise DataError('backfill_scope_changed', 'Source session is not bound to the original ledger/scope')
        usage = check_prefix(session.state, self.scope)
        if not usage['all_segments_closed']:
            raise DataError('session_locked', 'A previous backfill segment remains unclosed')

    def before_request(self, session, amount, block):
        self.check_clock()
        usage = check_prefix(session.state, self.scope)
        limits = self.scope['limits']
        if (usage['response_bytes'] + amount > limits['response_bytes'] - SAFETY_MARGIN
                or usage['requests'] + 1 > limits['requests']
                or usage['body_attempts'] + int(block) > limits['body_attempts']):
            raise BudgetError('Aggregate backfill allowance reached; no renewal is permitted')


class ScopedSources:
    """Admit only nominated historical bodies; record source conflicts explicitly."""
    def __init__(self, source, store, job, stage):
        self.source, self.store, self.job, self.stage = source, store, job, stage
        self.phase = 'acquisition'
        self.tip_hash = None
        self.nominated_hashes = set(stage['pinned'].values()) | {
            item['hash'] for item in job['items'].values() if item.get('hash')}

    @property
    def last_btc_api(self):
        return self.source.last_btc_api

    def provenance(self):
        return self.source.provenance()

    def rpc(self, method, params):
        return self.source.rpc(method, params)

    def btc(self, path, as_json=True):
        if path == '/blocks/tip/hash':
            self.tip_hash = hex32(self.source.btc(path, False)).hex()
            return self.tip_hash
        if path.startswith('/block-height/'):
            height = int(path.rsplit('/', 1)[1]); low, high = self.stage['range']
            if not low <= height <= high:
                raise DataError('backfill_scope_changed', 'Bitcoin lookup escaped the fixed stage interval')
            value = hex32(self.source.btc(path, False)).hex()
            pinned = self.stage['pinned'].get(str(height)) or self.job['items'].get(str(height), {}).get('hash')
            status = 'changed' if pinned and pinned != value else 'observed'
            self.store.observe('bitcoin', height, value, self.last_btc_api, status=status)
            if status == 'changed':
                raise DataError('source_conflict', 'A fixed historical height changed; original evidence is retained')
            self.nominated_hashes.add(value)
            return value
        if path.startswith('/block/'):
            value = path.rsplit('/', 1)[1]
            if value != self.tip_hash and value not in self.nominated_hashes:
                raise DataError('backfill_scope_changed', 'Block metadata was not nominated by this job')
            return self.source.btc(path, as_json)
        raise DataError('backfill_scope_changed', 'Unexpected Bitcoin source operation')

    def block_raw(self, block_hash, maximum):
        permitted = {item.get('hash') for item in self.job['items'].values()}
        if self.stage['kind'] != 'backfill' or block_hash not in permitted or maximum != MAX_RAW_BLOCK:
            raise DataError('source_conflict', 'Repair work cannot download a new or changed Bitcoin body')
        return self.source.block_raw(block_hash, maximum)


class ReuseCache:
    def __init__(self, directory, original, maximum):
        self.owned = BlockCache(directory, max_bytes=maximum)
        self.original = Path(original)

    def get(self, block_hash):
        value = self.owned.get(block_hash)
        if value is not None:
            return value
        file = self.original / (hex32(block_hash).hex() + '.bin')
        if file.is_file():
            if file.stat().st_size > MAX_RAW_BLOCK:
                raise DataError('cache_corrupt', 'Retained Bitcoin body exceeds the parser limit')
            return file.read_bytes()
        return None

    def put(self, block_hash, raw):
        self.owned.put(block_hash, raw)


class Backfill:
    def __init__(self, directory, *, sources_factory=PublicSources, event=None):
        self.directory = Path(directory).resolve()
        self.plan = load_plan(self.directory)
        self.scope = self.plan['scope']
        self.sources_factory = sources_factory
        self.event = event or (lambda value: None)
        self.transports = []

    def save(self):
        atomic_json(self.directory / 'backfill.json', self.plan)

    def check_stop(self):
        if (self.directory / 'operator-stop.json').exists():
            raise CollectionStopped('Operator stopped this finite backfill')

    def emit(self, value):
        self.event({'at': timestamp(), **value})

    def _arm(self, store):
        if self.plan['clock']:
            return
        started = timestamp()
        self.plan['clock'] = {'started_at': started, 'expires_at': datetime.fromtimestamp(
            epoch(started) + self.scope['limits']['wall_seconds'], timezone.utc).isoformat()}
        with store.db:
            store.db.execute("UPDATE meta SET value=? WHERE key='backfill_clock'", (encode(self.plan['clock']),))
        self.save()

    def run(self):
        self.plan = load_plan(self.directory)
        if self.plan.get('closed_at'):
            return self.report()
        with RunnerLock(self.directory), CollectionStore(self.directory) as store:
            self.plan = load_plan(self.directory)
            if self.plan.get('closed_at'):
                return self.report()
            check_prefix(read_ledger(self.scope['ledger_directory']), self.scope)
            if (self.directory / 'operator-stop.json').exists():
                self.plan.update(state='stopped', closed_at=timestamp(),
                                 issue={'code': 'operator_stopped', 'message': 'Stopped before source work'})
                self.save()
                result = self.report()
                atomic_json(self.directory / 'coverage.json', result)
                return result
            if (Path(self.scope['ledger_directory']) / 'session.lock').exists():
                raise DataError('session_locked', 'Another operation owns the original acquisition ledger')
            self._arm(store)
            self.plan['state'] = 'running'; self.save()
            budget = BackfillBudget(self.plan, self.check_stop)
            cache = ReuseCache(self.directory / 'backfill_blocks', Path(self.scope['ledger_directory']) / 'blocks',
                               max(4_000_000, self.scope['limits']['body_attempts'] * MAX_RAW_BLOCK))
            collector = Collector(self.scope['root'], self.directory, event=self.emit, raise_source_conflicts=True)
            try:
                while self.plan['active_stage'] < len(self.scope['stages']):
                    stage = self.scope['stages'][self.plan['active_stage']]
                    job = store.job(stage['job']); budget.check_clock()
                    remaining = check_prefix(read_ledger(self.scope['ledger_directory']), self.scope)
                    limits = self.scope['limits']
                    with Session(self.scope['ledger_directory'], self.scope['root'],
                            max_bytes=limits['response_bytes'] - remaining['response_bytes'],
                            max_requests=max(0, limits['requests'] - remaining['requests']),
                            max_blocks=max(0, limits['body_attempts'] - remaining['body_attempts']),
                            max_seconds=min(600, budget.remaining_seconds()),
                            session_limit=self.scope['cumulative_response_ceiling'],
                            context={'backfill': self.scope['id'], 'collection_job': job['id'], 'stage': stage['kind']},
                            interrupt=self.check_stop, operating_budget=budget) as session:
                        raw_source = self.sources_factory(session)
                        if hasattr(raw_source, 'http'):
                            self.transports.append(raw_source.http.transport)
                        sources = ScopedSources(raw_source, store, job, stage)
                        if int(sources.rpc('eth_chainId', []), 16) != 43111:
                            raise DataError('wrong_network', 'Backfill requires Hemi mainnet')
                        tip_hash = sources.btc('/blocks/tip/hash', False)
                        tip = sources.btc('/block/' + tip_hash)
                        if tip.get('id') != tip_hash or type(tip.get('height')) is not int:
                            raise DataError('invalid_response', 'Invalid observed Bitcoin tip')
                        job.setdefault('initial_tip', {'height': tip['height'], 'hash': tip_hash, 'observed_at': timestamp()})
                        job['state'] = 'running'; store.save(job)
                        low, high = stage['range']
                        try:
                            for height in range(low, high + 1):
                                if job.get('body_issues', {}).get(str(height)):
                                    continue
                                try:
                                    collector._body(store, job, height, sources, session, cache)
                                except BudgetError:
                                    raise
                                except DataError as exc:
                                    if exc.code in ('source_conflict', 'inconsistent_data', 'backfill_scope_changed'):
                                        raise
                                    job.setdefault('body_issues', {})[str(height)] = {'code': exc.code, 'message': str(exc)}
                                    store.save(job)
                            # Retained checked templates remain historical. Do not
                            # reacquire their already-passed reconstruction work.
                            for identity in self.scope['seed_associated_publications']:
                                row = store.db.execute('SELECT p.keystone_height FROM publications p JOIN nominations n ON n.hash=p.block_hash WHERE p.id=? AND n.height BETWEEN ? AND ? LIMIT 1', (identity, low, high)).fetchone()
                                if row:
                                    job['associations'].setdefault(identity, {'state': 'complete', 'keystone': row[0], 'origin': 'retained_association'})
                            store.save(job)
                            collector._associations(store, job, low, high, sources, session)
                            self._recheck(store, job, stage, sources, session)
                            self._tips(store, job)
                            job.update(state='incomplete', completed_at=timestamp(), attempt_finished=True)
                            store.save(job)
                            self.plan['active_stage'] += 1; self.save()
                            self.emit({'stage': 'backfill_stage_finished', 'kind': stage['kind'], 'range': stage['range']})
                        except DeadlineError:
                            store.save(job)
                            if budget.remaining_seconds() <= 0:
                                raise
                            self.emit({'stage': 'backfill_segment_ended', 'range': stage['range']})
                            continue
                self.check_stop()
                self.plan['state'] = 'finished'
            except CollectionStopped as exc:
                self.plan.update(state='stopped', issue={'code': 'operator_stopped', 'message': str(exc)})
            except (DataError, OSError, sqlite3.Error, KeyError, TypeError, ValueError) as exc:
                self.plan.update(state='limited' if isinstance(exc, BudgetError) else 'source_conflict' if getattr(exc, 'code', '') in ('source_conflict', 'inconsistent_data') else 'incomplete',
                    issue={'code': getattr(exc, 'code', 'backfill_error'), 'message': str(exc)})
            except KeyboardInterrupt:
                atomic_json(self.directory / 'operator-stop.json', {'at': timestamp(), 'reason': 'Operator interrupted backfill'})
                self.plan.update(state='stopped', issue={'code': 'operator_stopped', 'message': 'Operator interrupted backfill'})
            finally:
                self.plan['closed_at'] = timestamp(); self.save()
                for stage in self.scope['stages']:
                    saved = store.job(stage['job'])
                    if saved['state'] in ('queued', 'running'):
                        saved.update(state='stopped' if self.plan['state'] == 'stopped' else 'incomplete',
                                     issue=self.plan.get('issue'), ended_at=self.plan['closed_at'])
                        store.save(saved)
        report = self.report()
        # A stage is complete only when its bodies, supported associations,
        # planned ancestry and recorded source rechecks are all accounted for.
        with CollectionStore(self.directory) as store:
            for stage in report['stages']:
                if stage['supported_domain_complete']:
                    saved = store.job(stage['job']); saved['state'] = 'complete'; store.save(saved)
        report = self.report()
        if self.plan['state'] == 'finished':
            self.plan['state'] = 'complete' if report['supported_domain_complete'] else 'incomplete'
            self.save(); report = self.report()
        atomic_json(self.directory / 'coverage.json', report)
        return report

    def _recheck(self, store, job, stage, sources, session):
        for height in range(*[stage['range'][0], stage['range'][1] + 1]):
            if job['items'].get(str(height), {}).get('state') != 'processed':
                continue
            try:
                sources.btc('/block-height/' + str(height), False)
                job.setdefault('bitcoin_rechecks', {})[str(height)] = {'state': 'matched', 'at': timestamp()}
            except BudgetError:
                raise
            except DataError as exc:
                job.setdefault('bitcoin_rechecks', {})[str(height)] = {'state': 'unavailable', 'issue': {'code': exc.code, 'message': str(exc)}}
                if exc.code == 'source_conflict':
                    store.save(job); raise
            store.save(job)
        expected_headers = {}
        for row in store.db.execute('SELECT DISTINCT p.keystone_height,t.data FROM publications p JOIN associations a ON a.publication_id=p.id JOIN templates t ON t.id=a.template_id WHERE p.block_hash IN (SELECT hash FROM nominations WHERE height BETWEEN ? AND ?)', stage['range']).fetchall():
            expected = json.loads(row[1])['checkpoint']['header']
            if row[0] in expected_headers and not same_header(expected_headers[row[0]], expected):
                raise DataError('source_conflict', 'Retained Hemi templates disagree at one height')
            expected_headers[row[0]] = expected
        for height, expected in sorted(expected_headers.items()):
            if str(height) in job.get('hemi_rechecks', {}):
                continue
            try:
                fresh = Engine(sources, session, None).checked_header(sources.rpc('eth_getBlockByNumber', [hex(height), False]), expected_number=height)
                changed = not same_header(expected, fresh)
                store.observe('hemi', height, fresh['hash'], sources.provenance().get('hemi_rpc_used'), status='changed' if changed else 'observed', header=fresh)
                job.setdefault('hemi_rechecks', {})[str(height)] = {'state': 'changed' if changed else 'matched', 'at': timestamp()}
                if changed:
                    raise DataError('source_conflict', 'Associated Hemi header changed during backfill')
            except BudgetError:
                raise
            except DataError as exc:
                if exc.code == 'source_conflict':
                    store.save(job); raise
                job.setdefault('hemi_rechecks', {})[str(height)] = {'state': 'unavailable', 'issue': {'code': exc.code, 'message': str(exc)}}
                store.observe('hemi', height, None, sources.provenance().get('hemi_rpc_used'), status='unavailable')
            store.save(job)
        tip_hash = sources.btc('/blocks/tip/hash', False); tip = sources.btc('/block/' + tip_hash)
        if tip.get('id') != tip_hash or type(tip.get('height')) is not int or tip['height'] < job['initial_tip']['height']:
            raise DataError('source_conflict', 'Bitcoin tip observation became inconsistent')
        if tip['height'] == job['initial_tip']['height'] and tip_hash != job['initial_tip']['hash']:
            raise DataError('source_conflict', 'Bitcoin tips disagree at the same height')
        job['final_tip'] = {'height': tip['height'], 'hash': tip_hash, 'observed_at': timestamp()}; store.save(job)

    def _tips(self, store, job):
        with store.db:
            for item in job['items'].values():
                if item.get('import_id'):
                    row = store.db.execute('SELECT observation FROM imports WHERE id=?', (item['import_id'],)).fetchone()
                    observation = json.loads(row[0])
                    observation.update(bitcoin_tip=job['initial_tip'], bitcoin_tip_after=job['final_tip'])
                    store.db.execute('UPDATE imports SET observation=? WHERE id=?', (encode(observation), item['import_id']))

    def report(self):
        from .backfill_report import coverage_report
        return coverage_report(self.directory)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Finite historical indexing; prepare/report are source-free')
    parser.add_argument('--index-dir', type=Path, required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    create = sub.add_parser('prepare')
    create.add_argument('--seed', type=Path, required=True)
    create.add_argument('--ledger-dir', type=Path, required=True)
    create.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    create.add_argument('--range', nargs=2, type=int, action='append', default=[])
    create.add_argument('--repair-range', nargs=2, type=int, action='append', default=[])
    create.add_argument('--max-bytes', type=int, default=80_000_000)
    create.add_argument('--max-requests', type=int, default=3000)
    create.add_argument('--max-attempts', type=int, default=14)
    create.add_argument('--wall-seconds', type=int, default=900)
    sub.add_parser('run'); sub.add_parser('report'); sub.add_parser('stop')
    args = parser.parse_args(argv)
    if args.command == 'prepare':
        result = prepare(args.root, args.index_dir, args.ledger_dir, args.seed, args.range,
            repair_ranges=args.repair_range, max_bytes=args.max_bytes, max_requests=args.max_requests,
            max_attempts=args.max_attempts, wall_seconds=args.wall_seconds)
    elif args.command == 'run':
        result = Backfill(args.index_dir, event=lambda value: print(encode(value), flush=True)).run()
    elif args.command == 'stop':
        plan = load_plan(args.index_dir)
        if not plan.get('closed_at'):
            atomic_json(args.index_dir / 'operator-stop.json', {'at': timestamp(), 'reason': 'Operator stopped backfill'})
        result = {'state': 'stop_requested' if not plan.get('closed_at') else plan['state']}
    else:
        from .backfill_report import coverage_report
        result = coverage_report(args.index_dir)
    print(json.dumps(result, indent=2))
    return 0
