"""Operator lifecycle tests using retained fixtures and a blocked loopback body."""

import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from collection_fixtures import CollectionSources, ROOT
from hemi_lookup.collection_control import RunnerRecord, request_stop
from hemi_lookup.collection_store import CollectionStore, RunnerLock, collection_status
from hemi_lookup.collector import Collector
from hemi_lookup.errors import DataError
from hemi_lookup.network import HTTP
from hemi_lookup.transport import ProcessHTTP


class OperatorTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / 'output/qa-temp-collection-control'
        directory.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / '.hemi_lookup_data'

    def create(self, **limits):
        with CollectionStore(self.state) as store:
            return store.create(965931, 965931, batch_size=1, **limits)['id']

    def test_stop_queued_is_durable_terminal_and_never_creates_a_source_session(self):
        identity = self.create()
        stopped = Collector(self.root).stop(identity, 'Operator ended this scope')
        self.assertEqual(stopped['state'], 'stopped')
        self.assertEqual(stopped['stop']['reason'], 'Operator ended this scope')
        original = json.dumps(stopped, sort_keys=True)
        def prohibited(_):
            raise AssertionError('A stopped scope cannot acquire sources')
        collector = Collector(self.root, sources_factory=prohibited)
        self.assertEqual(json.dumps(collector.run(identity), sort_keys=True), original)
        self.assertEqual(collector.follow(identity), stopped)
        self.assertEqual(collector.stop(identity, 'Must retain original reason'), stopped)
        self.assertEqual(collection_status(self.state)['state'], 'stopped')
        self.assertFalse((self.state / 'session.json').exists())
        self.assertEqual(stopped['clock'], {})

    def test_stop_wakes_a_real_follow_process_during_its_sixty_second_wait(self):
        identity = self.create(wall_seconds=120, poll_seconds=60)
        code = '''
import sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from collection_fixtures import CollectionSources
from hemi_lookup.collector import Collector
Collector(Path(sys.argv[1]),sources_factory=lambda s:CollectionSources(s,tip=965930)).follow(sys.argv[2])
'''
        child = subprocess.Popen([sys.executable, '-B', '-c', code, str(self.root), identity],
                                 cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                status = collection_status(self.state)
                if status['state'] == 'waiting' and status['runner']['state'] == 'active':
                    break
                if child.poll() is not None:
                    self.fail('Controlled follow process exited before its wait')
                time.sleep(.05)
            else:
                self.fail('Controlled follow process did not enter waiting')
            with CollectionStore(self.state) as store:
                before = store.job(identity)
            ledger = (self.state / 'session.json').read_bytes()
            started = time.monotonic()
            command = subprocess.run([sys.executable, '-B', '-m', 'hemi_lookup.collector',
                                      '--state-dir', str(self.state), 'stop', identity,
                                      '--reason', 'Stop from a separate operator terminal'],
                                     cwd=ROOT, capture_output=True, timeout=8)
            self.assertEqual(command.returncode, 0, command.stderr)
            child.communicate(timeout=5)
            self.assertLess(time.monotonic() - started, 5)
            with CollectionStore(self.state) as store:
                after = store.job(identity)
            self.assertEqual(after['state'], 'stopped')
            self.assertEqual(after['clock'], before['clock'])
            self.assertEqual(after['limits'], before['limits'])
            self.assertEqual(after['items'], before['items'])
            self.assertEqual((self.state / 'session.json').read_bytes(), ledger)
            self.assertEqual(collection_status(self.state)['runner']['state'], 'inactive')
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)

    def test_active_body_stop_kills_worker_keeps_reservation_and_no_partial_cache(self):
        entered, release = threading.Event(), threading.Event()
        class Blocked(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Length', '100000')
                self.end_headers()
                self.wfile.write(b'partial body')
                self.wfile.flush()
                entered.set()
                release.wait(8)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Blocked)
        server.daemon_threads = True
        serving = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        serving.start()
        original = ProcessHTTP.read
        workers, results, errors = [], [], []
        def local_read(transport, url, *args, **kwargs):
            workers.append(transport)
            return original(transport, f'http://127.0.0.1:{server.server_port}/body', *args, **kwargs)
        class BlockedSources(CollectionSources):
            def block_raw(self, block_hash, maximum):
                http = HTTP(self.session)
                return http.read('https://blockstream.info/api/controlled-body', 100000, block=True), self.last_btc_api
        identity = self.create(segment_seconds=30)
        def run():
            try:
                results.append(Collector(self.root, sources_factory=BlockedSources).run(identity))
            except BaseException as exc:
                errors.append(exc)
        runner = threading.Thread(target=run, daemon=True)
        try:
            with patch.object(ProcessHTTP, 'read', local_read):
                runner.start()
                self.assertTrue(entered.wait(8), f'Loopback body did not start: results={results}, errors={errors}')
                started = time.monotonic()
                Collector(self.root).stop(identity, 'Stop active body')
                runner.join(5)
                self.assertFalse(runner.is_alive(), 'Stop must interrupt the active body')
                self.assertLess(time.monotonic() - started, 5)
            self.assertEqual(errors, [])
            self.assertEqual(results[0]['state'], 'stopped')
            ledger = json.loads((self.state / 'session.json').read_text())
            requests = ledger['runs'][-1]['requests']
            interrupted = [r for r in requests if r['status'] == 'operator_interrupted']
            self.assertEqual(len(interrupted), 1)
            self.assertEqual(requests[-1], interrupted[0])
            self.assertEqual(interrupted[0]['received_bytes'], interrupted[0]['reserved_bytes'])
            self.assertEqual(interrupted[0]['accounting'], 'conservative_reservation')
            self.assertEqual(ledger['runs'][-1]['body_attempts'], 1)
            self.assertFalse(list((self.state / 'collection_blocks').glob('*.bin')))
            self.assertFalse((self.state / 'session.lock').exists())
            self.assertTrue(workers and all(w.process is None for w in workers))
        finally:
            release.set()
            runner.join(5)
            server.shutdown()
            server.server_close()
            serving.join(2)

    def test_liveness_needs_matching_lock_and_recent_heartbeat(self):
        identity = self.create()
        with CollectionStore(self.state) as store:
            job = store.job(identity); job['state'] = 'waiting'; store.save(job)
        self.assertEqual(collection_status(self.state)['state'], 'unavailable')
        with RunnerLock(self.state):
            record = RunnerRecord(self.state, identity)
            active = collection_status(self.state)
            self.assertEqual(active['state'], 'waiting')
            self.assertEqual(active['runner']['state'], 'active')
            with patch('hemi_lookup.collection_control.time.time', return_value=time.time() + 16):
                self.assertEqual(collection_status(self.state)['state'], 'unavailable')
            # No clean close simulates a crash; a fresh timestamp alone is insufficient.
        inactive = collection_status(self.state)
        self.assertEqual(inactive['state'], 'unavailable')
        self.assertEqual(inactive['recorded_state'], 'waiting')

    def test_unreadable_operator_signal_fails_closed_without_starting_sources(self):
        identity = self.create()
        request_stop(self.state, identity, 'Stop requested')
        path = self.state / 'collection-controls' / (identity + '.json')
        path.write_text('{broken')
        with self.assertRaises(DataError) as error:
            Collector(self.root, sources_factory=lambda _: self.fail('No acquisition')).run(identity)
        self.assertEqual(error.exception.code, 'collection_control_unavailable')
        self.assertFalse((self.state / 'session.json').exists())

    def test_operator_signal_does_not_wait_for_an_evidence_writer_transaction(self):
        identity = self.create()
        with RunnerLock(self.state), CollectionStore(self.state) as writer:
            writer.db.execute('BEGIN IMMEDIATE')
            writer.db.execute('UPDATE collection_jobs SET data=data WHERE id=?', (identity,))
            started = time.monotonic()
            result = Collector(self.root).stop(identity, 'Stop while a write is open')
            self.assertLess(time.monotonic() - started, 1)
            self.assertEqual(result['state'], 'stop_requested')
            self.assertIsNotNone((self.state / 'collection-controls' / (identity + '.json')).read_text())
            writer.db.rollback()
        result = Collector(self.root, sources_factory=lambda _: self.fail('No acquisition')).run(identity)
        self.assertEqual(result['state'], 'stopped')

    def test_status_of_missing_state_does_not_create_files(self):
        missing = self.root / 'missing'
        self.assertIsNone(collection_status(missing))
        self.assertFalse(missing.exists())


if __name__ == '__main__':
    unittest.main()
