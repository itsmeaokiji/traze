"""Read API boundaries, pagination, identity and concurrent snapshot controls."""

import copy
import hashlib
import json
import shutil
import socket
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from hemi_lookup.catalogue import Catalogue, build_catalogue
from hemi_lookup.catalogue_lookup import recorded_lookup
from hemi_lookup.explorer import Explorer, ReadError, encode_response
from hemi_lookup.proof import verify_receipt
from hemi_lookup.web import Jobs, LocalServer

ROOT = Path(__file__).resolve().parent.parent
NUMBER = 5243119


class ExplorerTests(unittest.TestCase):
    def guarded(self, context):
        result = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        return result

    @classmethod
    def setUpClass(cls):
        qa = ROOT / 'output/qa-explorer'
        qa.mkdir(parents=True, exist_ok=True)
        cls.corpus = tempfile.TemporaryDirectory(dir=qa)
        cls.addClassCleanup(cls.corpus.cleanup)
        cls.compiled = Path(cls.corpus.name) / 'compiled.sqlite3'
        build_catalogue(ROOT, cls.compiled, block_directory=ROOT / 'tests/fixtures/bitcoin_blocks')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'output/qa-explorer')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / '.hemi_lookup_data'
        self.state.mkdir()
        self.database = self.state / 'catalogue.sqlite3'
        shutil.copyfile(self.compiled, self.database)
        self.original = hashlib.sha256(self.database.read_bytes()).hexdigest()
        self.packet = recorded_lookup(self.database, NUMBER)
        self.hash = self.packet['query']['block_hash']
        self.start_server()
        self.addCleanup(self.stop_server)
        self.sources = self.guarded(patch('hemi_lookup.web.PublicSources', side_effect=AssertionError('No source construction')))
        self.sessions = self.guarded(patch('hemi_lookup.web.Session', side_effect=AssertionError('No source session')))
        self.guarded(patch('hemi_lookup.catalogue.parse_block', side_effect=AssertionError('No raw body parsing')))
        connect = socket.socket.connect
        def local_only(sock, address):
            if address[0] not in ('127.0.0.1', 'localhost', '::1'):
                self.fail('External connection attempted: ' + str(address[0]))
            return connect(sock, address)
        self.guarded(patch.object(socket.socket, 'connect', local_only))

    def start_server(self):
        self.server = LocalServer(0, Jobs(self.root))
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)

    def stop_server(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(2)

    def request(self, path, payload=None):
        headers = {} if payload is None else {'Content-Type': 'application/json', 'Origin': self.base, 'X-Local-Token': self.server.token}
        req = Request(self.base + path, data=None if payload is None else json.dumps(payload).encode(), headers=headers)
        try:
            response = urlopen(req, timeout=8)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read()
            return response.status, response.headers, json.loads(raw) if raw and response.headers.get_content_type() == 'application/json' else raw

    def test_number_hash_and_publication_identity_survive_restart_without_writes(self):
        first = self.request('/api/v1/blocks/' + str(NUMBER))[2]
        exact = self.request('/api/v1/blocks/' + self.hash)[2]
        self.assertEqual(first['record_url'], exact['record_url'])
        self.assertEqual(exact['receipt']['query']['requested_block_hash'], self.hash)
        self.assertEqual(len(first['receipt']['proofs'][0]['ancestry']) - 1, 6)
        self.assertEqual(exact['verification']['proofs_checked'], 4)
        self.assertFalse(exact['receipt']['context']['current_view_confirmed'])
        self.assertEqual(self.request(first['record_url'])[0], 200)
        identity = exact['receipt']['proofs'][0]['publication_id']
        detail = self.request('/api/v1/publications/' + identity)[2]
        self.assertEqual(detail['publication']['id'], identity)
        self.assertIn('?publication=' + identity, detail['hemi_records'][0]['record_url'])
        self.assertEqual(self.request(detail['publication']['record_url'])[0], 200)
        self.stop_server(); self.start_server()
        reopened = self.request('/api/v1/blocks/' + self.hash)[2]
        self.assertEqual(reopened['record_url'], exact['record_url'])
        self.assertEqual(reopened['receipt']['proofs'], exact['receipt']['proofs'])
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).hexdigest(), self.original)
        self.assertFalse((self.state / 'receipts').exists())
        self.assertFalse((self.state / 'session.json').exists())
        self.sources.assert_not_called(); self.sessions.assert_not_called()

    def test_keyset_pages_are_disjoint_complete_and_bound_to_filters(self):
        seen = []; cursor = None
        while True:
            query = {'limit': '3'}
            if cursor: query['cursor'] = cursor
            status, _, body = self.request('/api/v1/publications?' + urlencode(query))
            self.assertEqual(status, 200, body)
            self.assertNotIn('raw_transaction_hex', json.dumps(body))
            self.assertNotIn('ancestry', json.dumps(body))
            seen.extend(row['id'] for row in body['items'])
            cursor = body['page']['next_cursor']
            if not cursor: break
        summary = self.request('/api/v1/index')[2]
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(len(seen), summary['counts']['supported_publications'])
        self.assertFalse(summary['history_complete'])
        self.assertIn('not contiguous coverage', summary['coverage_note'])
        first = self.request('/api/v1/publications?limit=1')[2]
        bad = self.request('/api/v1/publications?' + urlencode({'cursor': first['page']['next_cursor'], 'state': 'pending'}))
        self.assertEqual(bad[0], 400)
        self.assertEqual(bad[2]['error']['code'], 'invalid_cursor')

    def test_keystone_grouping_filters_and_association_states(self):
        rows = self.request('/api/v1/keystones?limit=50')[2]['items']
        self.assertTrue(rows)
        self.assertEqual(len({row['keystone_id'] for row in rows}), len(rows))
        all_ids = set()
        for state in ('associated', 'pending'):
            values = self.request('/api/v1/publications?state=' + state + '&limit=50')[2]['items']
            self.assertTrue(values)
            self.assertTrue(all(item['state'] == state for item in values))
            self.assertFalse(all_ids.intersection(item['id'] for item in values))
            all_ids.update(item['id'] for item in values)
        status, _, body = self.request('/api/v1/publications?keystone_height=5243125&bitcoin_height=965931')
        self.assertEqual(status, 200)
        self.assertTrue(body['items'])
        self.assertTrue(all(item['keystone_height'] == 5243125 and item['bitcoin_height'] == 965931 for item in body['items']))

    def test_unknown_and_invalid_inputs_never_trigger_discovery(self):
        for suffix in ('limit=51', 'limit=-1', 'limit=0', 'state=nope', 'keystone_height=true', 'cursor=bad', 'limit=1&limit=2', 'new_lookup=true', 'bitcoin_range=1,2', 'a=1&b=2&c=3&d=4&e=5&f=6&g=7&h=8&i=9'):
            self.assertEqual(self.request('/api/v1/publications?' + suffix)[0], 400, suffix)
        for target in ('12345', '0x' + '44' * 32):
            status, _, body = self.request('/api/v1/blocks/' + target)
            self.assertEqual(status, 404)
            self.assertEqual(body['error']['code'], 'indexed_evidence_unavailable')
        for suffix in ('new_lookup=true', 'bitcoin_range=1,2', 'bfgUrl=https://invalid.example'):
            self.assertEqual(self.request('/api/v1/blocks/' + str(NUMBER) + '?' + suffix)[0], 400)
        for payload in ({'block': NUMBER, 'new_lookup': True}, {'block': NUMBER, 'bitcoin_range': [1, 2]}):
            self.assertEqual(self.request('/api/lookup', payload)[0], 403)
        self.assertEqual(self.request('/api/lookup', {'block': NUMBER})[0], 410)
        self.assertEqual(self.server.jobs.jobs, {})
        self.assertFalse((self.state / 'receipts').exists())

    def test_five_independent_requests_and_bounded_overload(self):
        entered = threading.Barrier(6)
        release = threading.Event()
        original = self.server.explorer._read
        def hold(route, params, budget):
            if route.startswith('blocks/'):
                entered.wait(4)
                if not release.wait(4): raise AssertionError('Controlled release timeout')
            return original(route, params, budget)
        with patch.object(self.server.explorer, '_read', side_effect=hold), ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(self.request, '/api/v1/blocks/' + (str(NUMBER) if i % 2 else self.hash)) for i in range(5)]
            try:
                entered.wait(4)
                status, headers, error = self.request('/api/v1/index')
                self.assertEqual(status, 429)
                self.assertEqual(headers['Retry-After'], '1')
                self.assertEqual(error['error']['code'], 'read_busy')
            finally: release.set()
            for future in futures:
                status, _, wrapper = future.result(7)
                self.assertEqual(status, 200)
                self.assertEqual(verify_receipt(wrapper['receipt'])['proofs_checked'], 4)
        self.assertEqual(self.request('/api/v1/index')[0], 200)
        self.assertIsNone(self.server.jobs.active)
        self.assertFalse((self.state / 'receipts').exists())

    def test_one_slow_reader_does_not_block_another_visitor(self):
        entered, release = threading.Event(), threading.Event()
        original = self.server.explorer._read
        def hold(route, params, budget):
            if route.startswith('blocks/'):
                entered.set()
                if not release.wait(4): raise AssertionError('Controlled release timeout')
            return original(route, params, budget)
        with patch.object(self.server.explorer, '_read', side_effect=hold), ThreadPoolExecutor(max_workers=1) as pool:
            slow = pool.submit(self.request, '/api/v1/blocks/' + self.hash)
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual(self.request('/api/v1/publications?limit=1')[0], 200)
                self.assertFalse(slow.done())
            finally: release.set()
            self.assertEqual(slow.result(7)[0], 200)

    def test_time_work_and_output_budgets_release_capacity(self):
        for key, value in [('READ_SECONDS', -1), ('MAX_INPUT_BYTES', 10), ('MAX_SQL_STEPS', 1), ('MAX_RESPONSE_BYTES', 64)]:
            with patch('hemi_lookup.explorer.' + key, value):
                status, _, error = self.request('/api/v1/blocks/' + self.hash)
                self.assertEqual(status, 503, (key, error))
                self.assertIn(error['error']['code'], ('read_capacity', 'response_capacity'))
            self.assertEqual(self.request('/api/v1/index')[0], 200)

    def test_connection_admission_and_no_private_paths(self):
        for _ in range(16): self.assertTrue(self.server.connections.acquire(blocking=False))
        try:
            status, headers, _ = self.request('/api/v1/index')
            self.assertEqual(status, 503)
            self.assertEqual(headers['Retry-After'], '1')
        finally:
            for _ in range(16): self.server.connections.release()
        for path in ('/records/hemi/../../session.json', '/api/v1/../../session.json', '/api/v1/publications/not-an-id', '/src/ExplorerViews.tsx'):
            self.assertEqual(self.request(path)[0], 404)

    def test_corrupt_maintained_index_does_not_fallback_or_acquire(self):
        (self.state / 'service.sqlite3').write_bytes(b'Not a SQLite database')
        status, _, error = self.request('/api/v1/blocks/' + self.hash)
        self.assertEqual(status, 503)
        self.assertEqual(error['error']['code'], 'index_unavailable')

    def test_conflicting_headers_and_reported_heights_are_qualified(self):
        with Catalogue(self.database) as catalogue:
            row = catalogue.db.execute('SELECT * FROM headers WHERE hash=?', (self.hash[2:],)).fetchone()
            data = json.loads(row['data']); data['timestamp'] = hex(int(data['timestamp'], 16) + 1)
            with catalogue.db:
                catalogue.db.execute('INSERT INTO headers VALUES (?,?,?,?)', (self.hash[2:], 'controlled-variant', row['height'], json.dumps(data)))
        self.assertEqual(self.request('/api/v1/blocks/' + self.hash)[0], 409)
        with Catalogue(self.database) as catalogue:
            publication = catalogue.db.execute('SELECT * FROM publications LIMIT 1').fetchone()
            row = catalogue.db.execute('SELECT * FROM nominations WHERE hash=? LIMIT 1', (publication['block_hash'],)).fetchone()
            with catalogue.db:
                catalogue.db.execute('INSERT INTO nominations VALUES (?,?,?,?)', (row['import_id'], row['hash'], row['height'] + 1, row['observation']))
        detail = self.request('/api/v1/publications/' + publication['id'])[2]
        self.assertTrue(detail['publication']['height_conflict'])
        self.assertIsNone(detail['publication']['bitcoin_height'])
        self.assertEqual(len(detail['reported_bitcoin_heights']), 2)

    def test_packet_uses_one_snapshot_during_a_committing_index_update(self):
        with closing(sqlite3.connect(self.database)) as writer: writer.execute('PRAGMA journal_mode=WAL')
        entered, release = threading.Event(), threading.Event()
        original = Catalogue._scope
        def hold(catalogue, import_ids):
            scope = original(catalogue, import_ids)
            entered.set()
            if not release.wait(4): raise AssertionError('Controlled release timeout')
            return scope
        with patch.object(Catalogue, '_scope', hold), ThreadPoolExecutor(max_workers=1) as pool:
            reading = pool.submit(self.request, '/api/v1/blocks/' + self.hash)
            try:
                self.assertTrue(entered.wait(2))
                with closing(sqlite3.connect(self.database)) as writer, writer:
                    writer.execute('DELETE FROM associations WHERE publication_id=?', (self.packet['proofs'][0]['publication_id'],))
            finally: release.set()
            status, _, wrapper = reading.result(7)
            self.assertEqual(status, 200)
            self.assertEqual(wrapper['verification']['proofs_checked'], 4)
        later = self.request('/api/v1/blocks/' + self.hash)[2]
        self.assertEqual(later['verification']['proofs_checked'], 3)
        self.assertEqual(later['record_url'], wrapper['record_url'])

    def test_tampered_proof_cannot_become_a_verified_response(self):
        with Catalogue(self.database) as catalogue:
            row = catalogue.db.execute('SELECT data FROM publications WHERE id=?', (self.packet['proofs'][0]['publication_id'],)).fetchone()
            data = json.loads(row[0]); data['raw_transaction_hex'] = '00'
            with catalogue.db: catalogue.db.execute('UPDATE publications SET data=? WHERE id=?', (json.dumps(data), self.packet['proofs'][0]['publication_id']))
        status, _, body = self.request('/api/v1/blocks/' + self.hash)
        self.assertEqual(status, 422)
        self.assertNotIn('verification', body)


if __name__ == '__main__':
    unittest.main()
