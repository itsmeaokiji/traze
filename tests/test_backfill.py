"""Finite backfill accounting and evidence controls; no public source calls."""

import copy
import hashlib
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from collection_fixtures import CollectionSources, ROOT
from hemi_lookup.backfill import (Backfill, BackfillBudget, ScopedSources, check_prefix,
                                  load_plan, prepare, read_ledger)
from hemi_lookup.backfill_report import coverage_report
from hemi_lookup.catalogue import Catalogue, DEFAULT_RECEIPTS
from hemi_lookup.catalogue_lookup import recorded_lookup
from hemi_lookup.collection_store import CollectionStore
from hemi_lookup.collector import Collector
from hemi_lookup.engine import Engine
from hemi_lookup.errors import BudgetError, DataError, DeadlineError
from hemi_lookup.proof import verify_receipt
from hemi_lookup.storage import Session, atomic_json


class BackfillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qa = ROOT / 'output/qa-temp-backfill'
        qa.mkdir(parents=True, exist_ok=True)
        cls.corpus = tempfile.TemporaryDirectory(dir=qa)
        cls.seed = Path(cls.corpus.name) / 'seed.sqlite3'
        with Catalogue(cls.seed) as db:
            db.import_receipt(ROOT / DEFAULT_RECEIPTS[0], ROOT / 'tests/fixtures/bitcoin_blocks')

    @classmethod
    def tearDownClass(cls):
        cls.corpus.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'output/qa-temp-backfill')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ledger = self.root / 'ledger'
        self.index = self.root / 'working'
        self.local_seed = self.root / 'seed.sqlite3'
        shutil.copyfile(self.seed, self.local_seed)
        with Session(self.ledger, self.root):
            pass
        self.before = read_ledger(self.ledger)

    def prepare(self, *, ranges=(), repair=((965931, 965931),), **limits):
        return prepare(self.root, self.index, self.ledger, self.local_seed, ranges,
                       repair_ranges=repair, **limits)

    def run_job(self, **modes):
        return Backfill(self.index, sources_factory=lambda s: CollectionSources(s, **modes)).run()

    def test_prepare_report_are_offline_and_preserve_seed_ledger(self):
        before = hashlib.sha256(self.local_seed.read_bytes()).hexdigest()
        with patch('socket.socket', side_effect=AssertionError('No network in prepare/report')):
            plan = self.prepare()
            report = coverage_report(self.index)
        self.assertEqual(plan['clock'], {})
        self.assertEqual(before, hashlib.sha256(self.local_seed.read_bytes()).hexdigest())
        self.assertEqual(self.before, read_ledger(self.ledger))
        self.assertFalse(report['accounting_complete'])
        self.assertFalse(report['supported_domain_complete'])
        self.assertEqual(report['usage']['requests'], 0)

    def test_retained_repair_uses_no_body_and_exports_number_hash_evidence(self):
        self.prepare(max_attempts=0)
        with patch.object(CollectionSources, 'block_raw', side_effect=AssertionError('Repair cannot download')):
            result = self.run_job()
        # The corpus intentionally lacks metadata for three other publications
        # in this body. A usable target packet must not hide those pending rows.
        self.assertFalse(result['supported_domain_complete'])
        self.assertGreater(result['available_associations'], 0)
        self.assertGreater(result['unresolved_associations'], 0)
        self.assertTrue(result['accounting_complete'])
        self.assertEqual(result['usage']['body_attempts'], 0)
        packet = recorded_lookup(self.index / 'service.sqlite3', 5243119)
        self.assertGreater(verify_receipt(packet)['proofs_checked'], 0)
        exact = recorded_lookup(self.index / 'service.sqlite3', packet['query']['block_hash'])
        self.assertEqual(packet['query']['block_hash'], exact['query']['block_hash'])
        self.assertGreater(verify_receipt(exact)['proofs_checked'], 0)
        before = read_ledger(self.ledger)
        with patch.object(CollectionSources, 'rpc', side_effect=AssertionError('Closed scope must not reacquire')):
            self.run_job()
        self.assertEqual(before, read_ledger(self.ledger))

    def test_pending_repair_does_not_confuse_association_with_full_ancestry(self):
        with Catalogue(self.local_seed) as db, db.db:
            db.db.execute('DELETE FROM associations')
            db.db.execute('DELETE FROM template_origins')
            db.db.execute('DELETE FROM templates')
        self.prepare(max_attempts=0)
        result = self.run_job()
        self.assertGreater(result['seed_pending_resolved'], 0)
        self.assertGreater(result['available_associations'], 0)
        self.assertGreater(result['incomplete_ancestry_work'], 0)
        self.assertFalse(result['supported_domain_complete'])
        self.assertTrue(result['accounting_complete'])
        self.assertEqual(result['usage']['body_attempts'], 0)

    def test_outage_preserves_pending_reason_and_completed_bodies(self):
        with Catalogue(self.local_seed) as db, db.db:
            db.db.execute('DELETE FROM associations')
            db.db.execute('DELETE FROM template_origins')
            db.db.execute('DELETE FROM templates')
        self.prepare(max_attempts=0)
        result = self.run_job(outage=True)
        self.assertGreater(result['unresolved_associations'], 0)
        self.assertEqual(result['complete_bodies'], 1)
        self.assertTrue(all(p['issue'] for p in result['stages'][0]['required_heights'][0]['publications']))

    def test_new_interval_downloads_once_and_records_disjoint_coverage(self):
        self.prepare(ranges=((965930, 965930),), repair=())
        # Remove the new interval body from the seed to require a real parser
        # pass using controlled retained raw bytes, rather than a fabricated body.
        with Catalogue(self.index / 'service.sqlite3') as db, db.db:
            row = db.db.execute('SELECT hash FROM nominations WHERE height=965930').fetchone()
            db.db.execute("UPDATE blocks SET status='pending',data=NULL WHERE hash=?", (row[0],))
        result = self.run_job()
        self.assertEqual(result['complete_bodies'], 1)
        self.assertEqual(result['usage']['body_attempts'], 1)
        self.assertTrue(result['usage']['all_segments_closed'])

    def test_stop_before_start_never_arms_clock_or_opens_session(self):
        self.prepare()
        atomic_json(self.index / 'operator-stop.json', {'reason': 'Owner stop'})
        result = self.run_job()
        self.assertEqual(result['state'], 'stopped')
        self.assertEqual(result['clock'], {})
        self.assertEqual(self.before, read_ledger(self.ledger))
        self.assertTrue(result['accounting_complete'])

    def test_stop_during_body_loop_closes_original_session_and_stays_terminal(self):
        self.prepare()
        def stop(event):
            if event.get('stage') == 'block_processed':
                atomic_json(self.index / 'operator-stop.json', {'reason': 'Controlled operator stop'})
        result = Backfill(self.index, sources_factory=CollectionSources, event=stop).run()
        self.assertEqual(result['state'], 'stopped')
        self.assertTrue(result['usage']['all_segments_closed'])
        self.assertFalse((self.ledger / 'session.lock').exists())
        before = read_ledger(self.ledger)
        self.run_job()
        self.assertEqual(before, read_ledger(self.ledger))

    def test_changed_bitcoin_hash_stops_and_preserves_both_views(self):
        plan = self.prepare()
        class Changed(CollectionSources):
            def btc(self, path, as_json=True):
                return self.charge('22' * 32) if path == '/block-height/965931' else super().btc(path, as_json)
        result = Backfill(self.index, sources_factory=Changed).run()
        self.assertEqual(result['state'], 'source_conflict')
        self.assertTrue(result['source_conflicts'])
        self.assertEqual(result['stages'][0]['required_heights'][0]['hash'], plan['scope']['stages'][0]['pinned']['965931'])
        self.assertEqual(result['usage']['body_attempts'], 0)

    def test_changed_hemi_source_blocks_completion(self):
        self.prepare()
        result = self.run_job(changed=True)
        self.assertFalse(result['supported_domain_complete'])
        self.assertEqual(result['state'], 'source_conflict')
        self.assertEqual(result['stages'][0]['hemi_rechecks']['5243125']['state'], 'changed')

    def test_ancestry_source_conflict_stops_before_another_keystone(self):
        with Catalogue(self.local_seed) as db, db.db:
            db.db.execute('DELETE FROM associations')
            db.db.execute('DELETE FROM template_origins')
            db.db.execute('DELETE FROM templates')
        self.prepare()
        with patch.object(Engine, 'checkpoint', side_effect=DataError('source_conflict', 'Controlled contradiction')) as mocked:
            result = self.run_job()
        self.assertEqual(result['state'], 'source_conflict')
        self.assertEqual(mocked.call_count, 1)

    def test_plan_scope_clock_and_ledger_tampering_fail_closed(self):
        plan = self.prepare()
        modified = copy.deepcopy(plan)
        modified['scope']['limits']['requests'] += 1
        atomic_json(self.index / 'backfill.json', modified)
        with self.assertRaises(DataError): load_plan(self.index)
        modified = copy.deepcopy(plan)
        modified['clock'] = {'started_at': '2026-10-01T00:00:00+00:00'}
        atomic_json(self.index / 'backfill.json', modified)
        with self.assertRaises(DataError): load_plan(self.index)
        atomic_json(self.index / 'backfill.json', plan)
        ledger = read_ledger(self.ledger); ledger['runs'][0]['received_bytes'] += 1
        with self.assertRaises(DataError): check_prefix(ledger, plan['scope'])

    def test_aggregate_request_allowance_survives_closed_session_segments(self):
        plan = self.prepare(max_requests=2)
        runner = Backfill(self.index)
        with CollectionStore(self.index) as store: runner._arm(store)
        budget = BackfillBudget(runner.plan, lambda: None)
        for _ in range(2):
            with Session(self.ledger, self.root, session_limit=plan['scope']['cumulative_response_ceiling'],
                         context={'backfill': plan['scope']['id']}, operating_budget=budget) as session:
                entry = session.reserve('https://controlled.invalid/test', 100)
                session.charge(entry, 100, 'ok')
        with Session(self.ledger, self.root, session_limit=plan['scope']['cumulative_response_ceiling'],
                     context={'backfill': plan['scope']['id']}, operating_budget=budget) as session:
            with self.assertRaises(BudgetError): session.reserve('https://controlled.invalid/test', 100)
        self.assertEqual(check_prefix(read_ledger(self.ledger), plan['scope'])['requests'], 2)
        self.assertEqual(load_plan(self.index)['clock'], runner.plan['clock'])

    def test_aggregate_byte_and_body_guards_keep_conservative_headroom(self):
        plan = self.prepare(max_bytes=1_200_000, max_attempts=0)
        runner = Backfill(self.index)
        with CollectionStore(self.index) as store: runner._arm(store)
        budget = BackfillBudget(runner.plan, lambda: None)
        with Session(self.ledger, self.root, session_limit=plan['scope']['cumulative_response_ceiling'],
                     context={'backfill': plan['scope']['id']}, operating_budget=budget) as session:
            with self.assertRaises(BudgetError): session.reserve('https://controlled.invalid/body', 1, True)
            with self.assertRaises(BudgetError): session.reserve('https://controlled.invalid/large', 200_000)
        self.assertEqual(check_prefix(read_ledger(self.ledger), plan['scope'])['requests'], 0)

    def test_original_deadline_cannot_restart_after_downtime(self):
        self.prepare(wall_seconds=1)
        runner = Backfill(self.index)
        with CollectionStore(self.index) as store: runner._arm(store)
        original = runner.plan['clock']
        with patch('hemi_lookup.backfill.time.time', return_value=time.time() + 5):
            result = self.run_job()
        self.assertEqual(result['state'], 'limited')
        self.assertEqual(result['clock'], original)
        self.assertEqual(result['usage']['requests'], 0)

    def test_scope_wrapper_rejects_outside_body_metadata_and_repair_download(self):
        plan = self.prepare()
        with CollectionStore(self.index) as store:
            stage = plan['scope']['stages'][0]; job = store.job(stage['job'])
            source = ScopedSources(None, store, job, stage)
            for operation in (lambda: source.btc('/block-height/965930'),
                              lambda: source.btc('/block/' + '11' * 32),
                              lambda: source.block_raw(next(iter(stage['pinned'].values())), 4_000_000)):
                with self.assertRaises(DataError): operation()

    def test_required_height_accounting_keeps_disjoint_intervals_and_unsupported(self):
        self.prepare(ranges=((965930, 965930),))
        with Catalogue(self.index / 'service.sqlite3') as db, db.db:
            row = db.db.execute('SELECT * FROM publications WHERE block_hash IN (SELECT hash FROM nominations WHERE height=965931) LIMIT 1').fetchone()
            db.db.execute('INSERT INTO publications VALUES (?,?,?,?,0,NULL,NULL,?)',
                          ('unsupported-control', row['block_hash'], row['txid'], 999, row['data']))
        report = coverage_report(self.index)
        self.assertEqual(report['declared_intervals'], [[965931, 965931], [965930, 965930]])
        self.assertEqual(report['required_heights'], 2)
        self.assertEqual(report['unsupported_publications'], 1)
        self.assertFalse(report['supported_domain_complete'])

    def test_segment_exit_reuses_processed_body_and_original_clock(self):
        self.prepare(max_attempts=0)
        original = Collector._body
        segments = []
        def boundary(collector, store, job, height, sources, session, cache):
            original(collector, store, job, height, sources, session, cache)
            if not segments:
                segments.append(session.run['id'])
                raise DeadlineError('Controlled segment boundary after a durable body commit')
        with patch.object(Collector, '_body', boundary):
            result = self.run_job()
        self.assertEqual(result['usage']['segments'], 2)
        self.assertEqual(result['usage']['body_attempts'], 0)
        self.assertEqual(result['complete_bodies'], 1)
        self.assertEqual(result['clock'], load_plan(self.index)['clock'])
        self.assertEqual(read_ledger(self.ledger)['runs'][:len(self.before['runs'])], self.before['runs'])

    def test_wrong_network_stops_before_bitcoin_acquisition(self):
        self.prepare()
        class WrongNetwork(CollectionSources):
            def rpc(self, method, params):
                return self.charge(hex(1)) if method == 'eth_chainId' else super().rpc(method, params)
        result = Backfill(self.index, sources_factory=WrongNetwork).run()
        self.assertEqual(result['terminal_issue']['code'], 'wrong_network')
        self.assertEqual(result['usage']['requests'], 1)
        self.assertEqual(result['usage']['body_attempts'], 0)

    def test_legacy_prefix_is_pinned_but_modern_unclosed_run_is_rejected(self):
        ledger = read_ledger(self.ledger)
        legacy = copy.deepcopy(ledger['runs'][0])
        legacy.pop('closed_at'); legacy.pop('elapsed_seconds')
        legacy['id'] = 'legacy-prefix'
        ledger['runs'].insert(0, legacy)
        atomic_json(self.ledger / 'session.json', ledger)
        plan = self.prepare()
        self.assertEqual(plan['scope']['ledger_checkpoint']['legacy_preclosure_entries'][0]['id'], 'legacy-prefix')
        self.assertEqual(read_ledger(self.ledger), ledger)
        other = self.root / 'other-index'
        ledger['runs'].append(copy.deepcopy(legacy))
        atomic_json(self.ledger / 'session.json', ledger)
        with self.assertRaises(DataError):
            prepare(self.root, other, self.ledger, self.local_seed, (), repair_ranges=((965931, 965931),))


if __name__ == '__main__':
    unittest.main()
