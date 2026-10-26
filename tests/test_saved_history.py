"""Source-free retained-receipt discovery, paging and HTTP contract checks."""
import copy
import hashlib
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from hemi_lookup.saved_history import history_page, summary
from hemi_lookup.explorer import ReadError
from hemi_lookup.web import Jobs, LocalServer, WebError

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = json.loads((ROOT / 'demo_results/mainnet_5243117.json').read_text())


class SavedHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='traze_history_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        self.directory = self.state / 'receipts'
        self.directory.mkdir(parents=True)

    def save(self, number, *, receipt=None, mtime=None):
        identity = f'{number:032x}'
        path = self.directory / f'web-{identity}.json'
        path.write_text(json.dumps(REFERENCE if receipt is None else receipt), encoding='utf-8')
        stamp = 1_790_000_000_000_000_000 + number if mtime is None else mtime
        os.utime(path, ns=(stamp, stamp))
        return identity, path

    def hashes(self):
        return {str(p.relative_to(self.state)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.state.rglob('*') if p.is_file()}

    def test_oldest_of_47_is_discoverable_after_restart_and_original_evidence_is_unchanged(self):
        for number in range(1, 48):
            self.save(number)
        before = self.hashes()
        jobs = Jobs(self.root, self.state, mode='discovery')
        self.assertNotIn(f'{1:032x}', [i['id'] for i in jobs.state()['receipts']])
        first = history_page(self.state)
        self.assertEqual(len(first['items']), 10)
        # A fresh Jobs instance uses exactly the same persistent file history.
        jobs = Jobs(self.root, self.state, mode='discovery')
        ids = [i['id'] for i in first['items']]
        cursor = first['page']['next_cursor']
        pages = 1
        while cursor:
            page = history_page(jobs.state_dir, cursor)
            ids += [i['id'] for i in page['items']]
            cursor = page['page']['next_cursor']
            pages += 1
        self.assertEqual(pages, 5)
        self.assertEqual(ids, [f'{n:032x}' for n in range(47, 0, -1)])
        with patch('hemi_lookup.web.Session', side_effect=AssertionError('No acquisition')), \
             patch('hemi_lookup.web.PublicSources', side_effect=AssertionError('No acquisition')):
            wrapper = jobs.receipt(ids[-1])
        self.assertEqual(wrapper['receipt'], REFERENCE)
        self.assertEqual(wrapper['verification']['proofs_checked'], len(REFERENCE['proofs']))
        self.assertEqual(self.hashes(), before)

    def test_new_files_do_not_displace_cursor_pages_and_refresh_includes_them(self):
        for n in range(1, 26):
            self.save(n)
        first = history_page(self.state)
        self.save(26)
        second = history_page(self.state, first['page']['next_cursor'])
        third = history_page(self.state, second['page']['next_cursor'])
        ids = [i['id'] for page in (first, second, third) for i in page['items']]
        self.assertEqual(len(ids), 25)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertNotIn(f'{26:032x}', ids)
        self.assertEqual(history_page(self.state)['items'][0]['id'], f'{26:032x}')

    def test_equal_save_times_have_deterministic_identity_tie_break(self):
        for n in range(1, 24):
            self.save(n, mtime=1_790_000_000_000_000_000)
        page, ids = history_page(self.state), []
        while True:
            ids += [i['id'] for i in page['items']]
            if not page['page']['next_cursor']:
                break
            page = history_page(self.state, page['page']['next_cursor'])
        self.assertEqual(ids, [f'{n:032x}' for n in range(23, 0, -1)])

    def test_missing_archive_and_removed_file_do_not_invent_results(self):
        self.assertEqual(history_page(self.root / 'absent')['items'], [])
        for n in range(1, 13):
            self.save(n)
        first = history_page(self.state)
        (self.directory / f'web-{1:032x}.json').unlink()
        page = history_page(self.state, first['page']['next_cursor'])
        self.assertEqual([i['id'] for i in page['items']], [f'{2:032x}'])
        with self.assertRaises(WebError) as error:
            Jobs(self.root, self.state).receipt(f'{1:032x}')
        self.assertEqual(error.exception.code, 'unknown_receipt')

    def test_unreadable_metadata_oversize_and_directory_are_visible_unavailable_rows(self):
        _, invalid = self.save(1)
        invalid.write_text('{bad', encoding='utf-8')
        self.save(2, receipt={'query': {'block_number': True}, 'status': 'found'})
        self.save(3, receipt=['not a receipt'])
        _, oversized = self.save(4)
        with oversized.open('r+b') as handle:
            handle.truncate(10_000_001)
        (self.directory / f'web-{5:032x}.json').mkdir()
        (self.directory / 'arbitrary-file.json').write_text('{}')
        before = self.hashes()
        page = history_page(self.state)
        self.assertEqual(len(page['items']), 5)
        self.assertTrue(all(not i['available'] and i['status'] is None for i in page['items']))
        self.assertEqual(self.hashes(), before)
        with self.assertRaises(WebError) as error:
            Jobs(self.root, self.state).receipt(f'{1:032x}')
        self.assertEqual(error.exception.code, 'saved_receipt_unavailable')

    def test_symlink_target_is_never_read(self):
        outside = self.root / 'outside.json'
        outside.write_text(json.dumps(REFERENCE))
        path = self.directory / f'web-{1:032x}.json'
        try:
            path.symlink_to(outside)
        except OSError:
            self.skipTest('OS does not allow symlink creation for this process')
        with patch('hemi_lookup.saved_history.load_receipt', side_effect=AssertionError('No symlink reads')):
            self.assertFalse(history_page(self.state)['items'][0]['available'])

    def test_cursor_validation_and_directory_capacity_fail_explicitly(self):
        for cursor in ('', '../path', 'a' * 257, 'e30', 'W10', '!!!!'):
            with self.subTest(cursor=cursor), self.assertRaises(ReadError) as error:
                history_page(self.state, cursor)
            self.assertEqual(error.exception.code, 'invalid_history_cursor')
        self.save(1)
        with patch('hemi_lookup.saved_history.MAX_FILES', 0), self.assertRaises(ReadError) as error:
            history_page(self.state)
        self.assertEqual(error.exception.code, 'history_capacity')
        with patch('hemi_lookup.saved_history.MAX_FILES', 0):
            self.assertFalse(Jobs(self.root, self.state).state()['saved_history_available'])

    def test_symlink_rejection_branch_without_os_symlink_privilege(self):
        self.save(1)
        with patch.object(Path, 'is_symlink', return_value=True), \
             patch('hemi_lookup.saved_history.load_receipt', side_effect=AssertionError('No unsafe reads')):
            self.assertFalse(history_page(self.state)['items'][0]['available'])

    def test_metadata_budget_failure_is_not_swallowed_as_end_of_history(self):
        self.save(1)
        def budget(amount=0):
            if amount:
                raise ReadError('read_capacity', 'Controlled budget failure', 503)
        with self.assertRaises(ReadError) as error:
            history_page(self.state, check=budget)
        self.assertEqual(error.exception.code, 'read_capacity')

    def test_operation_segment_and_original_dates_are_metadata_not_fresh_verification(self):
        receipt = copy.deepcopy(REFERENCE)
        receipt.update(mode='live_search_continuation', completed_at='2026-10-02T12:00:00Z', discovery={'segment': 2})
        self.save(1, receipt=receipt)
        self.save(2, receipt={**receipt, 'mode': 'live_metadata_refresh'})
        with patch('hemi_lookup.web.verify_receipt', side_effect=AssertionError('List must not verify')):
            page = history_page(self.state)
        self.assertEqual(len(page['items']), 2)
        self.assertEqual(page['items'][1]['segment'], 2)
        self.assertEqual(page['items'][0]['mode'], 'live_metadata_refresh')
        self.assertTrue(all(i['completed_at'] == '2026-10-02T12:00:00Z' for i in page['items']))
        self.assertTrue(page['metadata_only'])
        self.assertEqual(summary('example', REFERENCE)['block_number'], 5243117)

    def test_http_history_is_get_only_validates_parameters_and_preserves_files(self):
        for n in range(1, 24):
            self.save(n)
        before = self.hashes()
        jobs = Jobs(self.root, self.state)
        server = LocalServer(0, jobs)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        thread.start()
        try:
            base = f'http://127.0.0.1:{server.server_port}'
            def request(path):
                try:
                    with urlopen(base + path, timeout=3) as reply:
                        return reply.status, json.loads(reply.read())
                except HTTPError as reply:
                    return reply.code, json.loads(reply.read())
            with patch('hemi_lookup.web.Session', side_effect=AssertionError('No source session')), \
                 patch('hemi_lookup.web.PublicSources', side_effect=AssertionError('No public reads')):
                status, first = request('/api/saved-history')
                self.assertEqual(status, 200)
                status, second = request('/api/saved-history?cursor=' + first['page']['next_cursor'])
                self.assertEqual(status, 200)
                self.assertNotEqual(first['items'][0]['id'], second['items'][0]['id'])
                for query in ('?cursor=', '?cursor=bad', '?cursor=a&cursor=b', '?path=secret', '?cursor'):
                    self.assertEqual(request('/api/saved-history' + query)[0], 400)
            self.assertEqual(jobs.jobs, {})
            self.assertEqual(self.hashes(), before)
        finally:
            server.shutdown(); server.server_close(); thread.join(2)

if __name__ == '__main__':
    unittest.main()
