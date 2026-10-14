"""Finite follow windows, observed freshness and restart/backoff controls."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from collection_fixtures import CollectionSources, ROOT
from hemi_lookup.catalogue_lookup import recorded_lookup
from hemi_lookup.collection_store import CollectionStore, RunnerLock, epoch
from hemi_lookup.collector import Collector
from hemi_lookup.errors import DataError
from hemi_lookup.proof import verify_receipt


class Clock:
    def __init__(self):
        self.value = 1792000000.0
        self.waits = []

    def stamp(self):
        return datetime.fromtimestamp(self.value, timezone.utc).isoformat()

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.value += seconds


class FollowTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / 'output/qa-temp-collection-follow'
        directory.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / '.hemi_lookup_data'

    def create(self, start=965931, end=965931, **limits):
        with CollectionStore(self.state) as store:
            return store.create(start, end, batch_size=1, **limits)['id']

    def clocks(self, clock):
        stack = ExitStack()
        stack.enter_context(patch('hemi_lookup.collection_store.time.time', side_effect=lambda: clock.value))
        stack.enter_context(patch('hemi_lookup.collector.time.monotonic', side_effect=lambda: clock.value))
        stack.enter_context(patch('hemi_lookup.collection_store.timestamp', side_effect=clock.stamp))
        stack.enter_context(patch('hemi_lookup.collector.timestamp', side_effect=clock.stamp))
        return stack

    def test_follow_waits_for_new_tip_then_records_newly_processed_empty_block(self):
        clock = Clock()
        with self.clocks(clock):
            identity = self.create(965930, 965930, wall_seconds=180, poll_seconds=60)
            sources = []
            def factory(session):
                source = CollectionSources(session, tip=965929 if not clock.waits else 965930)
                sources.append(source)
                return source
            result = Collector(self.root, sources_factory=factory).follow(identity, sleep=clock.sleep)
        self.assertEqual(result['state'], 'complete')
        self.assertEqual(clock.waits, [60])
        self.assertEqual(len(result['tip_observations']), 2)
        item = result['items']['965930']
        self.assertEqual(item['first_observed_at'], item['evidence_ready_at'])
        self.assertEqual(item['ready_latency_seconds'], 0)
        self.assertEqual(item['supported_publications'], 0)
        self.assertEqual(sum(len(source.raw_calls) for source in sources), 1)
        with CollectionStore(self.state) as store:
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM blocks WHERE status='complete'").fetchone()[0], 1)

    def test_valid_subset_does_not_mark_all_supported_publications_ready(self):
        identity = self.create()
        result = Collector(self.root, sources_factory=CollectionSources).run(identity)
        self.assertEqual(result['state'], 'incomplete')
        self.assertGreater(result['items']['965931']['unresolved_publications'], 0)
        self.assertNotIn('evidence_ready_at', result['items']['965931'])
        self.assertGreater(verify_receipt(recorded_lookup(self.state / 'service.sqlite3', 5243119))['proofs_checked'], 0)

    def test_expired_window_cannot_reset_on_resume_or_contact_a_source(self):
        clock = Clock()
        with self.clocks(clock):
            identity = self.create(wall_seconds=120, poll_seconds=60)
            first = Collector(self.root, sources_factory=lambda s: CollectionSources(s, tip=965930)).run(identity)
            original_clock = dict(first['clock'])
            old_ledger = json.loads((self.state / 'session.json').read_text())
            clock.value += 121
            def refused(session):
                raise AssertionError('An expired window must not instantiate a source')
            result = Collector(self.root, sources_factory=refused).follow(identity, sleep=clock.sleep)
            with CollectionStore(self.state) as store:
                with self.assertRaises(DataError):
                    store.start_clock(result, started_at=clock.stamp())
        self.assertEqual(result['issue']['code'], 'collection_window_expired')
        self.assertEqual(result['clock']['started_at'], original_clock['started_at'])
        self.assertEqual(result['clock']['expires_at'], original_clock['expires_at'])
        self.assertEqual(json.loads((self.state / 'session.json').read_text()), old_ledger)
        self.assertEqual(clock.waits, [])

    def test_waiting_expiry_keeps_completed_blocks_evidence_and_same_window(self):
        clock = Clock()
        with self.clocks(clock):
            identity = self.create(965930, 965932, wall_seconds=120, poll_seconds=60)
            result = Collector(self.root, sources_factory=lambda s: CollectionSources(s, tip=965931)).follow(identity, sleep=clock.sleep)
        self.assertEqual(result['state'], 'limited')
        self.assertEqual(result['issue']['code'], 'collection_window_expired')
        self.assertEqual(clock.waits, [60, 60])
        self.assertEqual(result['items']['965930']['state'], 'processed')
        self.assertEqual(result['items']['965931']['state'], 'processed')
        self.assertNotIn('965932', result['items'])
        self.assertEqual(epoch(result['clock']['expires_at']) - epoch(result['clock']['started_at']), 120)
        self.assertGreater(verify_receipt(recorded_lookup(self.state / 'service.sqlite3', 5243119))['proofs_checked'], 0)
        self.assertFalse((self.state / 'session.lock').exists())

    def test_transient_source_failures_back_off_then_repair_without_new_window(self):
        clock = Clock()
        attempts = []
        class Unavailable(CollectionSources):
            def btc(self, path, as_json=True):
                if path == '/blocks/tip/hash' and len(attempts) < 2:
                    attempts.append(1)
                    return self.charge({}, error=True)
                return super().btc(path, as_json)
        with self.clocks(clock):
            identity = self.create(965930, 965930, wall_seconds=600, poll_seconds=60)
            result = Collector(self.root, sources_factory=Unavailable).follow(identity, sleep=clock.sleep)
        self.assertEqual(result['state'], 'complete')
        self.assertEqual(clock.waits, [120, 240])
        self.assertEqual(epoch(result['clock']['expires_at']) - epoch(result['clock']['started_at']), 600)
        self.assertEqual(result['usage']['body_attempts'], 1)

    def test_saved_backoff_survives_a_follow_process_restart(self):
        clock = Clock()
        class Interrupted(Exception):
            pass
        def stop(seconds):
            raise Interrupted()
        with self.clocks(clock):
            identity = self.create(965930, 965930, wall_seconds=180, poll_seconds=60)
            collector = Collector(self.root, sources_factory=lambda s: CollectionSources(s, tip=965929))
            with self.assertRaises(Interrupted):
                collector.follow(identity, sleep=stop)
            clock.value += 20
            with CollectionStore(self.state) as store:
                original = store.job(identity)['clock']['expires_at']
            result = Collector(self.root, sources_factory=CollectionSources).follow(identity, sleep=clock.sleep)
        self.assertEqual(clock.waits, [40])
        self.assertEqual(result['state'], 'complete')
        self.assertEqual(result['clock']['expires_at'], original)

    def test_wait_keeps_runner_ownership_while_releasing_the_network_lock(self):
        clock = Clock()
        lock_checks = []
        def sleep(seconds):
            with self.assertRaises(DataError) as blocked:
                RunnerLock(self.state)
            self.assertEqual(blocked.exception.code, 'collector_busy')
            self.assertFalse((self.state / 'session.lock').exists())
            lock_checks.append(1)
            clock.sleep(seconds)
        with self.clocks(clock):
            identity = self.create(wall_seconds=60, poll_seconds=60)
            result = Collector(self.root, sources_factory=lambda s: CollectionSources(s, tip=965930)).follow(identity, sleep=sleep)
        self.assertEqual(result['state'], 'limited')
        self.assertEqual(lock_checks, [1])

    def test_process_death_during_wait_preserves_armed_clock_and_releases_runner(self):
        identity = self.create(965930, 965930, wall_seconds=120)
        child = '''
import os,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from collection_fixtures import CollectionSources
from hemi_lookup.collector import Collector
Collector(Path(sys.argv[1]), sources_factory=lambda s: CollectionSources(s,tip=965929)).follow(sys.argv[2],sleep=lambda _:os._exit(76))
'''
        stopped = subprocess.run([sys.executable, '-B', '-c', child, str(self.root), identity], cwd=ROOT, capture_output=True, timeout=30)
        self.assertEqual(stopped.returncode, 76, stopped.stderr)
        with CollectionStore(self.state) as store:
            clock = dict(store.job(identity)['clock'])
        result = Collector(self.root, sources_factory=CollectionSources).run(identity)
        self.assertEqual(result['state'], 'complete')
        self.assertEqual(result['clock']['expires_at'], clock['expires_at'])
        with RunnerLock(self.state):
            pass

    def test_non_transient_metadata_mismatch_does_not_enter_automatic_retry(self):
        identity = self.create()
        waits = []
        result = Collector(self.root, sources_factory=lambda s: CollectionSources(s, changed=True)).follow(identity, sleep=waits.append)
        self.assertEqual(result['state'], 'incomplete')
        self.assertEqual(waits, [])
        self.assertTrue(any(a.get('issue', {}).get('code') == 'payload_mismatch' for a in result['associations'].values()))

    def test_wall_window_clamps_active_session_before_any_later_source_read(self):
        sessions = []
        class SlowChain(CollectionSources):
            def rpc(self, method, params):
                value = super().rpc(method, params)
                if method == 'eth_chainId':
                    sessions.append(self.session.max_seconds)
                    time.sleep(1.05)
                return value
            def btc(self, *args):
                self.session.check_deadline()
                raise AssertionError('No Bitcoin read can begin after the job deadline')
        identity = self.create(wall_seconds=1)
        result = Collector(self.root, sources_factory=SlowChain).run(identity)
        self.assertEqual(result['issue']['code'], 'collection_window_expired')
        self.assertEqual(len(sessions), 1)
        self.assertLessEqual(sessions[0], 1)
        self.assertEqual(result['usage']['requests'], 1)

    def test_zero_update_limit_is_rejected_before_source_work(self):
        identity = self.create()
        with self.assertRaises(DataError):
            Collector(self.root, sources_factory=lambda _: self.fail('No source')).follow(identity, max_updates=0)
        self.assertFalse((self.state / 'session.json').exists())


if __name__ == '__main__':
    unittest.main()
