"""Controller/index/accounting integration using included data and no network."""

import copy
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from collection_fixtures import CollectionSources, ROOT
from test_service import Clock, fixture_policy
from hemi_lookup.accounting import accounting_directory, bind_accounting
from hemi_lookup.catalogue import Catalogue
from hemi_lookup.catalogue_lookup import recorded_lookup
from hemi_lookup.collection_progress import progress_view
from hemi_lookup.collection_store import CollectionStore, RunnerLock, collection_status
from hemi_lookup.errors import DataError
from hemi_lookup.explorer import Explorer
from hemi_lookup.backup import backup
from hemi_lookup.operations import inspect_state
from hemi_lookup.proof import verify_receipt
from hemi_lookup.service import CollectionService, initialize_index
from hemi_lookup.storage import Session, atomic_json
from hemi_lookup.web import Jobs


class H2IntegrationTests(unittest.TestCase):
    def setUp(self):
        qa = ROOT / 'output/qa-temp-h2'
        qa.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=qa)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.index, self.ledger, self.seed = self.root / 'index', self.root / 'ledger', self.root / 'seed.sqlite3'
        with Catalogue(self.seed): pass
        self.seed_sha = hashlib.sha256(self.seed.read_bytes()).hexdigest()
        with Session(self.ledger, self.root): pass
        self.before = json.loads((self.ledger / 'session.json').read_text())
        initialize_index(self.root, self.index, self.seed, self.ledger)
        self.clock = Clock()
        self.sources = []
        stack = ExitStack()
        stack.enter_context(patch('hemi_lookup.service.time.time', side_effect=lambda: self.clock.value))
        stack.enter_context(patch('hemi_lookup.collector.time.monotonic', side_effect=lambda: self.clock.value))
        for name in ('collection_store.timestamp', 'collector.timestamp', 'service.timestamp', 'collection_control.now'):
            stack.enter_context(patch('hemi_lookup.' + name, side_effect=self.clock.stamp))
        stack.enter_context(patch('hemi_lookup.network.HTTP.read', side_effect=AssertionError('Public source access forbidden')))
        self.addCleanup(stack.close)

    def source(self, session, **options):
        self.assertEqual(session.directory, self.ledger.resolve())
        result = CollectionSources(session, **options)
        self.sources.append(result)
        return result

    def controller(self, **options):
        return CollectionService(self.root, self.index, **options)

    def create(self, start=965928, **limits):
        return self.controller().create(start, fixture_policy(**limits))['id']

    def run_service(self, identity, *, tip=965931, outage=False, cycles=1):
        return self.controller(sources_factory=lambda s: self.source(s, tip=tip, outage=outage)).run(
            identity, max_cycles=cycles, sleep=self.clock.sleep)

    def test_explicit_setup_preserves_seed_ledger_and_does_not_arm_service(self):
        self.assertEqual(accounting_directory(self.index), self.ledger.resolve())
        self.assertFalse((self.index / 'session.json').exists())
        self.assertEqual(json.loads((self.ledger / 'session.json').read_text()), self.before)
        identity = self.create()
        service = CollectionStore.read_service(self.index, identity)
        self.assertEqual(service['clock'], {})
        self.assertEqual(service['ledger_directory'], str(self.ledger.resolve()))
        self.assertEqual(self.sources, [])
        self.assertEqual(hashlib.sha256(self.seed.read_bytes()).hexdigest(), self.seed_sha)

    def test_wait_then_new_height_advances_same_index_without_forking_accounting(self):
        identity = self.create()
        first = self.run_service(identity, tip=965927)
        self.assertEqual(first['state'], 'waiting')
        original_clock = first['clock']['armed_at']
        second = self.run_service(identity, tip=965928)
        self.assertEqual(second['next_height'], 965929)
        self.assertEqual(second['clock']['armed_at'], original_clock)
        self.assertEqual(Explorer(self.index).read('index', {})['counts']['processed_bodies'], 1)
        self.assertFalse((self.index / 'session.json').exists())
        ledger = json.loads((self.ledger / 'session.json').read_text())
        self.assertEqual(ledger['runs'][:len(self.before['runs'])], self.before['runs'])
        self.assertTrue(all('closed_at' in r for r in ledger['runs']))
        self.assertEqual(sum(r['body_attempts'] for r in ledger['runs']), 1)
        status = collection_status(self.index)
        self.assertEqual(status['state'], 'unavailable')  # max_cycles ended; not a live runner.
        self.assertEqual(status['progress']['checked_through_height'], 965928)
        self.assertEqual(status['progress']['remaining_to_observed_tip'], 0)

    def test_publications_become_queryable_while_other_metadata_holds_cursor(self):
        identity = self.create(965931)
        self.run_service(identity)
        packet = recorded_lookup(self.index / 'service.sqlite3', 5243119)
        self.assertGreater(verify_receipt(packet)['proofs_checked'], 0)
        status = collection_status(self.index)
        self.assertEqual(status['next_height'], 965931)
        self.assertIsNone(status['progress']['checked_through_height'])
        work = status['progress']['current_job']
        self.assertEqual(work['processed_bodies'], 1)
        self.assertGreater(work['pending_metadata'], 0)
        self.assertGreater(work['pending_ancestry'], 0)
        self.assertEqual(work['checked_update_heights'], 0)
        self.assertGreater(len(Explorer(self.index).read('publications', {})['items']), 0)

    def test_outage_recovery_reuses_body_and_keeps_original_period(self):
        identity = self.create(965931)
        first = self.run_service(identity, outage=True)
        self.assertEqual(first['state'], 'incomplete')
        second = self.run_service(identity)
        self.assertEqual(second['clock']['armed_at'], first['clock']['armed_at'])
        self.assertGreater(verify_receipt(recorded_lookup(self.index / 'service.sqlite3', 5243119))['proofs_checked'], 0)
        self.assertEqual(sum(len(s.raw_calls) for s in self.sources), 1)
        self.assertFalse((self.ledger / 'session.lock').exists())

    def test_read_api_uses_one_snapshot_and_does_not_leak_binding_paths(self):
        identity = self.create()
        self.run_service(identity, tip=965928)
        before = (self.ledger / 'session.json').read_bytes()
        public = Explorer(self.index).read('index', {})
        raw = json.dumps(public)
        for private in (str(self.root), 'ledger_checkpoint', 'policy_digest', identity):
            self.assertNotIn(private, raw)
        self.assertFalse(public['history_complete'])
        self.assertFalse(public['source_observations_current'])
        self.assertEqual(public['collection']['progress']['checked_through_height'], 965928)
        self.assertNotIn('height_accounting', public['collection']['progress']['current_job'])
        operator = collection_status(self.index)
        self.assertEqual(len(operator['progress']['current_job']['height_accounting']), 1)
        self.assertEqual((self.ledger / 'session.json').read_bytes(), before)

    def test_alternate_or_missing_ledger_cannot_silently_restart_accounting(self):
        identity = self.create()
        other = self.root / 'other'
        with Session(other, self.root): pass
        with self.assertRaises(DataError):
            self.controller(ledger_directory=other).run(identity, max_cycles=1)
        with CollectionStore(self.index) as store, self.assertRaises(DataError):
            bind_accounting(store, other, self.root)
        # A misleading local copy is ignored when the original binding exists.
        atomic_json(self.index / 'session.json', {'schema': 1, 'prior_validation_bytes': {}, 'downloaded_bytes': 0, 'runs': []})
        self.assertEqual(Jobs(self.root, self.index).resources()['total_response_bytes'], 0)
        (self.ledger / 'session.json').unlink()  # Owned temporary test data only.
        with self.assertRaises(DataError): self.run_service(identity)
        self.assertFalse(Jobs(self.root, self.index).resources()['available'])
        self.assertEqual(self.sources, [])

    def test_a_copied_binding_cannot_resume_from_an_unbound_index_path(self):
        identity = self.create()
        copied = self.root / 'copied'; copied.mkdir()
        shutil.copyfile(self.index / 'service.sqlite3', copied / 'service.sqlite3')
        with self.assertRaises(DataError):
            CollectionService(self.root, copied, sources_factory=self.source).run(identity, max_cycles=1)
        self.assertEqual(self.sources, [])

    def test_seed_cannot_replace_its_original_ledger_or_copy_an_active_writer(self):
        other = self.root / 'replacement'
        with Session(other, self.root): pass
        destination = self.root / 'new-index'
        with self.assertRaises(DataError):
            initialize_index(self.root, destination, self.index / 'service.sqlite3', other)
        self.assertFalse(destination.exists())
        with RunnerLock(self.index), self.assertRaises(DataError):
            initialize_index(self.root, destination, self.index / 'service.sqlite3', self.ledger)
        self.assertFalse(destination.exists())
        initialize_index(self.root, destination, self.index / 'service.sqlite3', self.ledger)
        self.assertEqual(accounting_directory(destination), self.ledger.resolve())

    def test_legacy_snapshot_requires_its_full_prefix_in_the_authoritative_ledger(self):
        legacy = self.root / 'legacy'
        legacy.mkdir()
        with Catalogue(self.seed, readonly=True) as source, Catalogue(legacy / 'service.sqlite3') as target:
            source.db.backup(target.db)
        shutil.copyfile(self.ledger / 'session.json', legacy / 'session.json')
        # A later real ledger may extend the earlier read-copy prefix.
        with Session(self.ledger, self.root): pass
        initialize_index(self.root, self.root / 'descendant-index', legacy / 'service.sqlite3', self.ledger)
        unrelated = self.root / 'unrelated'
        with Session(unrelated, self.root): pass
        with self.assertRaises(DataError):
            initialize_index(self.root, self.root / 'invalid-index', legacy / 'service.sqlite3', unrelated)

    def test_foreign_global_lock_is_not_removed_by_index_recovery(self):
        identity = self.create()
        with Session(self.ledger, self.root, context={'collection_job': 'different-owner'}):
            token = (self.ledger / 'session.lock').read_text()
            result = self.run_service(identity)
            self.assertEqual(result['state'], 'blocked')
            self.assertEqual((self.ledger / 'session.lock').read_text(), token)
            self.assertEqual(self.sources, [])

    def test_operator_stop_is_terminal_even_when_the_next_run_omits_ledger_flag(self):
        identity = self.create()
        self.run_service(identity, tip=965927)
        result = self.controller().stop(identity)
        self.assertEqual(result['state'], 'stopped')
        before = (self.ledger / 'session.json').read_bytes()
        self.run_service(identity)
        self.assertEqual(before, (self.ledger / 'session.json').read_bytes())
        self.assertEqual(collection_status(self.index)['state'], 'stopped')

    def test_readers_stay_available_during_an_external_accounting_session(self):
        identity = self.create()
        self.run_service(identity, tip=965928)
        with Session(self.ledger, self.root, context={'collection_job': 'another-index'}):
            self.assertEqual(Explorer(self.index).read('index', {})['counts']['processed_bodies'], 1)
            jobs = Jobs(self.root, self.index, mode='discovery')
            with self.assertRaises(DataError):
                jobs.public_operation('not-started', 'lookup', (5243119, None))

    def test_future_or_missing_observation_time_never_becomes_a_zero_lag_claim(self):
        identity = self.create()
        with CollectionStore(self.index) as store:
            service = store.service(identity)
            service['latest_tip'] = {'height': 965928, 'hash': '11' * 32, 'observed_at': '2099-01-01T00:00:00+00:00'}
            result = progress_view(store.db, service, None, now=self.clock.value)
            self.assertFalse(result['observed_bitcoin_tip']['clock_valid'])
            self.assertIsNone(result['remaining_to_observed_tip'])
            service['latest_tip'] = None
            result = progress_view(store.db, service, None, now=self.clock.value)
            self.assertIsNone(result['observed_bitcoin_tip'])
            self.assertIsNone(result['remaining_to_observed_tip'])

    def test_external_ledger_usage_is_reported_instead_of_a_local_read_copy(self):
        identity = self.create()
        self.run_service(identity, tip=965928)
        with CollectionStore(self.index) as store:
            service = store.service(identity)
            job = store.job(service['last_job'])
            usage = store.usage(job)
        self.assertGreater(usage['requests'], 0)
        self.assertGreater(usage['response_bytes'], 0)
        self.assertEqual(usage['body_attempts'], 1)
        actual = json.loads((self.ledger / 'session.json').read_text())
        self.assertEqual(Jobs(self.root, self.index).resources()['total_response_bytes'], actual['downloaded_bytes'])

    def test_resetting_the_bound_file_cannot_create_a_new_allowance(self):
        identity = self.create()
        atomic_json(self.ledger / 'session.json', {'schema': 1, 'prior_validation_bytes': {}, 'downloaded_bytes': 0, 'runs': []})
        with self.assertRaises(DataError): self.run_service(identity)
        with self.assertRaises(DataError): self.create()
        self.assertFalse(Jobs(self.root, self.index).resources()['available'])
        self.assertEqual(self.sources, [])

    def test_operator_monitor_uses_bound_ledger_and_single_directory_backup_refuses_it(self):
        identity = self.create()
        self.run_service(identity, tip=965928)
        report = inspect_state(self.index, min_free_bytes=0)
        self.assertTrue(report['checks']['accounting_readable'])
        total = json.loads((self.ledger / 'session.json').read_text())['downloaded_bytes']
        self.assertEqual(report['accounting']['counted_response_bytes'], total)
        self.assertEqual(report['collection_progress']['checked_through_height'], 965928)
        self.assertNotIn('collection_progress', report['public'])
        destination = self.root / 'unsafe-backup'
        with self.assertRaises(DataError) as error:
            backup(self.index, destination, max_bytes=100_000_000)
        self.assertEqual(error.exception.code, 'external_accounting_backup_requires_review')
        self.assertFalse(destination.exists())

    def test_real_process_exit_recovers_the_owned_external_session_across_a_period(self):
        identity = self.create(965931, period_seconds=60)
        code = '''
import os,sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path.cwd()/'tests'))
from collection_fixtures import CollectionSources
from hemi_lookup.service import CollectionService
with patch('hemi_lookup.network.HTTP.read',side_effect=AssertionError('No public transport')):
 CollectionService(Path(sys.argv[1]),Path(sys.argv[2]),sources_factory=CollectionSources,
   event=lambda e:os._exit(79) if e['stage']=='block_processed' else None).run(sys.argv[3],max_cycles=1)
'''
        child = subprocess.run([sys.executable, '-B', '-c', code, str(self.root), str(self.index), identity],
                               cwd=ROOT, capture_output=True, timeout=30)
        self.assertEqual(child.returncode, 79, child.stderr)
        saved = CollectionStore.read_service(self.index, identity)
        old_child = saved['current_job']
        prefix = json.loads((self.ledger / 'session.json').read_text())['runs']
        self.assertTrue((self.ledger / 'session.lock').is_file())
        self.assertFalse((self.index / 'session.lock').exists())
        self.clock.value = saved['clock']['armed_at'] + 61
        with patch.object(CollectionSources, 'block_raw', side_effect=AssertionError('No completed-body redownload')):
            result = self.run_service(identity)
        self.assertEqual(result['clock']['period'], 1)
        self.assertEqual(result['clock']['armed_at'], saved['clock']['armed_at'])
        self.assertNotEqual(result['current_job'], old_child)
        self.assertEqual(CollectionStore.read_job(self.index, old_child)['state'], 'period_closed')
        self.assertFalse((self.ledger / 'session.lock').exists())
        after = json.loads((self.ledger / 'session.json').read_text())
        self.assertEqual(after['runs'][:len(prefix)], prefix)
        self.assertEqual(after['runs'][-1]['context']['collection_index'], str(self.index.resolve()))
        self.assertGreater(verify_receipt(recorded_lookup(self.index / 'service.sqlite3', 5243119))['proofs_checked'], 0)
        # Repeating the same explicit binding does not reject a conservatively
        # retained crash segment after its owned lock was recovered.
        with RunnerLock(self.index), CollectionStore(self.index) as store:
            self.assertEqual(bind_accounting(store, self.ledger, self.root)['ledger_directory'], str(self.ledger.resolve()))

    def test_restored_index_interlock_prevents_collector_acquisition(self):
        identity = self.create()
        atomic_json(self.index / 'restore-guard.json', {'reason': 'Controlled restore review'})
        with self.assertRaises(DataError): self.run_service(identity)
        self.assertEqual(self.sources, [])


if __name__ == '__main__': unittest.main()
