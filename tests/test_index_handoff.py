"""Offline receipt/index lifecycle controls with actual proof and body fixtures."""
import copy
import hashlib
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import threading

from hemi_lookup.catalogue import Catalogue, DEFAULT_RECEIPTS
from hemi_lookup.catalogue_lookup import recorded_lookup
from hemi_lookup.collection_store import CollectionStore, RunnerLock
from hemi_lookup.errors import DataError
from hemi_lookup.explorer import Explorer, ReadError
from hemi_lookup.index_handoff import IndexHandoff
from hemi_lookup.index_ingestion import publication_id
from hemi_lookup.proof import verify_receipt
from hemi_lookup.web import Jobs, LocalServer

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = json.loads((ROOT / DEFAULT_RECEIPTS[0]).read_text(encoding='utf-8'))
BODIES = ROOT / 'tests/fixtures/bitcoin_blocks'

class IndexHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='traze_index_handoff_')
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / 'state'
        (self.state / 'receipts').mkdir(parents=True)
        self.identity = 'a' * 32
        self.path = self.save(self.identity, REFERENCE)
        self.updates = IndexHandoff(self.state)
        self.addCleanup(self.updates.close)
        self.explorer = Explorer(self.state)

    def save(self, identity, receipt):
        path = self.state / 'receipts' / f'web-{identity}.json'
        path.write_text(json.dumps(receipt), encoding='utf-8')
        return path

    def apply(self, identity=None):
        identity = identity or self.identity
        self.updates.enqueue(identity)
        self.updates.process(identity)
        return self.updates.status(identity)

    def bodies(self, receipt=REFERENCE):
        directory = self.state / 'blocks'; directory.mkdir(exist_ok=True)
        for block in receipt['coverage']['blocks']:
            source = BODIES / (block['hash'] + '.bin')
            if source.is_file():
                shutil.copyfile(source, directory / source.name)

    def test_publication_proof_without_cached_body_is_browsable_but_not_full_scan(self):
        before = self.path.read_bytes()
        with patch('socket.socket.connect', side_effect=AssertionError('No network')):
            result = self.apply()
            page = self.explorer.read('publications', {})
            packet = self.explorer.read('blocks/' + REFERENCE['query']['block_hash'], {})['receipt']
        self.assertEqual(result['state'], 'indexed')
        self.assertEqual(len(page['items']), len({publication_id(p) for p in REFERENCE['proofs']}))
        self.assertTrue(all(p['evidence_scope'] == 'publication_proof' for p in page['items']))
        self.assertEqual(packet['coverage']['fully_scanned_intervals'], [])
        self.assertEqual(verify_receipt(packet)['proofs_checked'], len(REFERENCE['proofs']))
        summary = self.explorer.read('index', {})['counts']
        self.assertEqual(summary['processed_bodies'], 0)
        self.assertEqual(summary['proof_only_blocks'], 1)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse((self.state / 'session.json').exists())

    def test_available_bodies_upgrade_coverage_and_never_downgrade_when_cache_is_missing(self):
        self.bodies()
        first = self.apply()
        self.assertGreater(first['body_scope']['complete'], 0)
        page = self.explorer.read('publications', {})
        self.assertTrue(all(p['evidence_scope'] == 'full_block_scan' for p in page['items']))
        before = self.explorer.read('index', {})['counts']
        for body in (self.state / 'blocks').glob('*.bin'):
            self.assertTrue(body.resolve().is_relative_to(self.state.resolve()))
            body.unlink()
        later = copy.deepcopy(REFERENCE); later['mode'] = 'live_metadata_refresh'
        self.save('b' * 32, later)
        self.apply('b' * 32)
        self.assertEqual(self.explorer.read('index', {})['counts']['processed_bodies'], before['processed_bodies'])
        self.assertTrue(all(p['evidence_scope'] == 'full_block_scan' for p in self.explorer.read('publications', {})['items']))

    def test_repeat_import_and_separate_receipts_do_not_duplicate_publications(self):
        self.apply()
        before = self.explorer.read('index', {})['counts']
        repeated = self.updates.retry(self.identity)
        self.assertEqual(repeated['state'], 'already_indexed')
        self.save('b' * 32, REFERENCE)
        self.apply('b' * 32)
        after = self.explorer.read('index', {})['counts']
        self.assertEqual(after['supported_publications'], before['supported_publications'])
        self.assertEqual(after['associated_publications'], before['associated_publications'])
        self.assertEqual(after['imports'], 1)

    def test_source_recheck_adds_observation_without_faking_new_publication_or_index_freshness(self):
        archived = copy.deepcopy(REFERENCE)
        archived['mode'] = 'offline_catalogue'; archived['context']['checked_at'] = None
        archived['catalogue'] = {'historical_source_times': ['2026-10-02T10:00:00Z']}
        self.path.write_text(json.dumps(archived), encoding='utf-8')
        self.apply()
        later = copy.deepcopy(REFERENCE); later['mode'] = 'live_metadata_refresh'
        later['context']['checked_at'] = '2026-10-03T10:00:00Z'
        self.save('b' * 32, later); self.apply('b' * 32)
        packet = recorded_lookup(self.state / 'catalogue.sqlite3', REFERENCE['query']['block_hash'])
        self.assertEqual(packet['catalogue']['historical_source_times'], ['2026-10-02T10:00:00Z', '2026-10-03T10:00:00Z'])
        self.assertFalse(packet['context']['current_view_confirmed'])
        self.assertEqual(self.explorer.read('index', {})['counts']['supported_publications'], 4)

    def test_no_match_without_bodies_adds_no_phantom_publication(self):
        receipt = json.loads((ROOT / 'demo_results/scoped_no_match.json').read_text())
        self.path.write_text(json.dumps(receipt), encoding='utf-8')
        result = self.apply()
        self.assertEqual(result['publication_count'], 0)
        self.assertIsNone(result['record_url'])
        self.assertEqual(self.explorer.read('publications', {})['items'], [])
        self.assertEqual(self.explorer.read('index', {})['counts']['processed_bodies'], 0)

    def test_altered_proof_is_not_accepted_and_changed_receipt_cannot_rebind_queue(self):
        self.apply()
        before = (self.state / 'catalogue.sqlite3').read_bytes()
        altered = copy.deepcopy(REFERENCE); altered['proofs'][0]['txid'] = '00' * 32
        self.path.write_text(json.dumps(altered), encoding='utf-8')
        self.assertEqual(self.updates.status(self.identity)['state'], 'rejected')
        with self.assertRaises(DataError):
            self.updates.retry(self.identity)
        self.assertEqual((self.state / 'catalogue.sqlite3').read_bytes(), before)

    def test_busy_writer_is_pending_and_resumes_with_a_new_updater(self):
        self.updates.enqueue(self.identity)
        with RunnerLock(self.state):
            self.updates.process(self.identity)
            result = self.updates.status(self.identity)
            self.assertEqual(result['state'], 'pending')
            self.assertEqual(result['reason'], 'index_busy')
        restarted = IndexHandoff(self.state)
        restarted.process(self.identity)
        self.assertEqual(restarted.status(self.identity)['state'], 'indexed')

    def test_maintained_index_is_used_and_corruption_never_falls_back(self):
        with Catalogue(self.state / 'catalogue.sqlite3'):
            pass
        with CollectionStore(self.state):
            pass
        old = (self.state / 'catalogue.sqlite3').read_bytes()
        self.apply()
        self.assertEqual(self.explorer.read('index', {})['index_kind'], 'maintained')
        self.assertEqual((self.state / 'catalogue.sqlite3').read_bytes(), old)
        # A different fixture demonstrates refusal to replace a corrupt active DB.
        second = copy.deepcopy(REFERENCE); second['mode'] = 'live_metadata_refresh'
        self.save('b' * 32, second)
        (self.state / 'service.sqlite3').write_bytes(b'controlled-corrupt-index')
        self.apply('b' * 32)
        self.assertEqual(self.updates.status('b' * 32)['reason'], 'index_unavailable')
        self.assertEqual((self.state / 'service.sqlite3').read_bytes(), b'controlled-corrupt-index')
        self.assertEqual((self.state / 'catalogue.sqlite3').read_bytes(), old)

    def test_commit_survives_a_failed_queue_status_write(self):
        self.updates.enqueue(self.identity)
        from hemi_lookup.storage import atomic_json
        def controlled(path, value):
            if value.get('state') in ('indexed', 'already_indexed'):
                raise OSError('Controlled queue-status write failure')
            return atomic_json(path, value)
        with patch('hemi_lookup.index_handoff.atomic_json', side_effect=controlled):
            self.updates.process(self.identity)
        self.assertEqual(self.updates.read(self.identity)['state'], 'pending')
        self.assertEqual(self.updates.status(self.identity)['state'], 'indexed')
        restarted = IndexHandoff(self.state); restarted.process(self.identity)
        self.assertEqual(restarted.status(self.identity)['state'], 'already_indexed')

    def test_background_worker_recovers_durable_pending_work_without_source_clients(self):
        self.updates.enqueue(self.identity)
        self.updates.start()
        deadline = time.monotonic() + 7
        with patch('socket.socket.connect', side_effect=AssertionError('No network')):
            while time.monotonic() < deadline:
                if self.updates.status(self.identity)['state'] in ('indexed', 'already_indexed'):
                    break
                time.sleep(.03)
            else:
                self.fail('Offline worker did not finish queued evidence')
        self.updates.close()
        self.assertFalse(self.updates.thread.is_alive())

    def test_only_matching_checked_metadata_completes_pending_publications(self):
        other = json.loads((ROOT / DEFAULT_RECEIPTS[1]).read_text(encoding='utf-8'))
        hashes = {REFERENCE['proofs'][0]['bitcoin_block_hash'], other['proofs'][0]['bitcoin_block_hash']}
        seed = copy.deepcopy(REFERENCE)
        seed['proofs'] = []; seed['status'] = 'incomplete'
        seed['selection'] = {'current_proof_index': None, 'best_observed_proof_index': None}
        seed['coverage']['blocks'] = [b for source in (REFERENCE, other) for b in source['coverage']['blocks'] if b['hash'] in hashes]
        self.path.write_text(json.dumps(seed), encoding='utf-8')
        self.bodies(seed); self.apply()
        before = self.explorer.read('index', {})['counts']
        self.assertGreater(before['unassociated_publications'], 0)
        self.save('b' * 32, REFERENCE); self.apply('b' * 32)
        after = self.explorer.read('index', {})['counts']
        self.assertEqual(after['supported_publications'], before['supported_publications'])
        self.assertGreater(after['associated_publications'], before['associated_publications'])
        self.assertGreater(after['unassociated_publications'], 0)
        for proof in REFERENCE['proofs']:
            result = self.explorer.read('publications/' + publication_id(proof), {})
            self.assertEqual(result['publication']['state'], 'associated')
        for proof in other['proofs']:
            if proof['bitcoin_block_hash'] in hashes:
                result = self.explorer.read('publications/' + publication_id(proof), {})
                self.assertEqual(result['publication']['state'], 'pending')

    def test_budget_interruption_keeps_checked_proofs_and_retry_does_not_duplicate(self):
        self.bodies(); self.updates.enqueue(self.identity)
        with patch('hemi_lookup.index_ingestion.parse_block', side_effect=ReadError('read_capacity', 'Controlled local processing limit', 503)):
            self.updates.process(self.identity)
        self.assertEqual(self.updates.status(self.identity)['state'], 'pending')
        self.assertEqual(self.updates.status(self.identity)['reason'], 'ingest_capacity')
        self.assertEqual(len(self.explorer.read('publications', {})['items']), 4)
        self.updates.process(self.identity)
        self.assertIn(self.updates.status(self.identity)['state'], ('indexed', 'already_indexed'))
        ids = [p['id'] for p in self.explorer.read('publications', {})['items']]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreater(self.explorer.read('index', {})['counts']['processed_bodies'], 0)

    def test_automatic_attempts_survive_restart_and_do_not_run_forever(self):
        self.updates.enqueue(self.identity)
        with RunnerLock(self.state):
            for _ in range(10):
                self.updates.process(self.identity)
        self.assertEqual(self.updates.read(self.identity)['attempts'], 8)
        restarted = IndexHandoff(self.state)
        restarted.process(self.identity)
        self.assertEqual(restarted.read(self.identity)['attempts'], 8)
        self.assertFalse(restarted.status(self.identity)['automatic_retry_pending'])
        self.assertIn(restarted.retry(self.identity)['state'], ('indexed', 'already_indexed'))

    def test_changed_active_index_does_not_reuse_a_stale_success_flag(self):
        self.apply()
        old = (self.state / 'catalogue.sqlite3').read_bytes()
        with CollectionStore(self.state):
            pass
        self.assertEqual(self.updates.status(self.identity)['reason'], 'active_index_changed')
        self.updates.retry(self.identity)
        self.assertEqual(self.explorer.read('index', {})['counts']['supported_publications'], 4)
        self.assertEqual(self.explorer.read('index', {})['index_kind'], 'maintained')
        self.assertEqual((self.state / 'catalogue.sqlite3').read_bytes(), old)

    def test_corrupt_cache_cannot_promote_proof_only_record_to_a_full_scan(self):
        directory = self.state / 'blocks'; directory.mkdir()
        for block in REFERENCE['coverage']['blocks']:
            (directory / (block['hash'] + '.bin')).write_bytes(b'controlled invalid block')
        self.apply()
        self.assertEqual(self.explorer.read('index', {})['counts']['processed_bodies'], 0)
        self.assertTrue(all(p['evidence_scope'] == 'publication_proof' for p in self.explorer.read('publications', {})['items']))

    def test_unknown_or_conflicting_reported_height_keeps_proof_without_inventing_position(self):
        for heights in ([], [965930, 965931]):
            with self.subTest(heights=heights):
                receipt = copy.deepcopy(REFERENCE)
                receipt['coverage']['blocks'] = []
                for proof in receipt['proofs']:
                    proof['bitcoin_height'] = None
                    proof['reported_bitcoin_heights'] = heights
                identity = ('c' if not heights else 'd') * 32
                self.save(identity, receipt); result = self.apply(identity)
                self.assertEqual(result['body_scope']['proof_only'], 1)
                self.assertIsNone(result['hemi_record_url'])
                detail = self.explorer.read('publications/' + publication_id(receipt['proofs'][0]), {})
                self.assertIsNone(detail['publication']['bitcoin_height'])
                self.assertEqual(detail['publication']['height_conflict'], bool(heights))
                self.assertEqual(detail['hemi_records'], [])
                self.assertEqual(self.explorer.read('index', {})['counts']['processed_bodies'], 0)

    def test_actual_http_retry_is_local_write_only_and_readonly_mode_cannot_start_it(self):
        def serve(mode):
            jobs = Jobs(self.state.parent, self.state, mode=mode)
            server = LocalServer(0, jobs)
            thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
            thread.start()
            return jobs, server, thread, f'http://127.0.0.1:{server.server_port}'
        jobs, server, thread, base = serve('discovery')
        try:
            def request(payload, token=True):
                req = Request(base + '/api/index-retry', data=json.dumps(payload).encode(),
                    headers={'Content-Type': 'application/json', 'Origin': base, 'X-Local-Token': server.token if token else 'invalid'})
                return urlopen(req, timeout=8)
            with patch('hemi_lookup.web.Session', side_effect=AssertionError('No acquisition')), \
                 patch('hemi_lookup.web.PublicSources', side_effect=AssertionError('No acquisition')):
                with self.assertRaises(HTTPError) as error:
                    request({'receipt_id': self.identity}, False)
                self.assertEqual(error.exception.code, 403)
                with self.assertRaises(HTTPError) as error:
                    request({'receipt_id': self.identity, 'state_dir': '../outside'})
                self.assertEqual(error.exception.code, 400)
                with request({'receipt_id': self.identity}) as response:
                    self.assertIn(json.loads(response.read())['state'], ('pending', 'indexed', 'already_indexed'))
                deadline = time.monotonic() + 6
                while time.monotonic() < deadline and jobs.index_updates.status(self.identity)['state'] == 'pending':
                    time.sleep(.02)
                self.assertIn(jobs.index_updates.status(self.identity)['state'], ('indexed', 'already_indexed'))
            self.assertEqual(jobs.jobs, {})
            self.assertFalse((self.state / 'session.json').exists())
        finally:
            server.shutdown(); server.server_close(); thread.join(2)
        before = (self.state / 'catalogue.sqlite3').read_bytes()
        jobs, server, thread, base = serve('indexed-only')
        try:
            req = Request(base + '/api/index-retry', data=json.dumps({'receipt_id': self.identity}).encode(),
                headers={'Content-Type': 'application/json', 'Origin': base, 'X-Local-Token': server.token})
            with self.assertRaises(HTTPError) as error:
                urlopen(req, timeout=3)
            self.assertEqual(error.exception.code, 403)
            self.assertIsNone(server.index_updates)
        finally:
            server.shutdown(); server.server_close(); thread.join(2)
        self.assertEqual((self.state / 'catalogue.sqlite3').read_bytes(), before)

if __name__ == '__main__':
    unittest.main()
