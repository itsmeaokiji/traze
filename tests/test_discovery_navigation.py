"""Receipt-bound latest-result reads after cached bodies have been evicted."""
import copy
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hemi_lookup.discovery import SCHEMA, receipt_digest
from hemi_lookup.web import Jobs
from hash_fixtures import MARCH
from test_discovery_recovery import partial

PARENT, CHILD, LATEST = 'a' * 32, 'b' * 32, 'c' * 32


class DiscoveryNavigationTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix='traze_navigation_')
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.state = self.root / '.hemi_lookup_data'
        self.jobs = Jobs(self.root, mode='discovery')
        guard = patch.object(socket, 'create_connection', side_effect=AssertionError('No public networking'))
        guard.start()
        self.addCleanup(guard.stop)
        self.parent = partial()
        self.parent['coverage']['plan']['initial_end'] = 965970
        for block in self.parent['coverage']['blocks']:
            block.pop('publication_associations_complete')
        self.parent_path = self.store(PARENT, self.parent)

    def store(self, identity, receipt):
        path = self.state / 'receipts' / f'web-{identity}.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(receipt), encoding='utf-8')
        self.jobs.receipts[identity] = {'path': path, 'label': 'Controlled cursor fixture', 'origin': 'browser_saved'}
        return path

    def successor(self, parent_id=PARENT, parent=None, identity=CHILD, segment=2):
        parent = parent or self.parent
        historical = (parent.get('discovery', {}).get('historical_completed_heights')
                      or [block['height'] for block in parent['coverage']['blocks']])
        receipt = copy.deepcopy(parent)
        receipt['mode'] = 'live_search_continuation'
        receipt['validation'] = {'scope': 'controlled_saved_progress', 'live_public_reads': False}
        receipt['coverage']['blocks'] = []
        receipt['coverage']['fully_scanned_intervals'] = []
        receipt['coverage']['gaps'] = []
        receipt['coverage']['unresolved_candidates'] = []
        receipt['discovery'] = {'root_receipt_id': PARENT, 'parent_receipt_id': parent_id,
            'parent_receipt_sha256': receipt_digest(parent), 'original_extent': [965925, 965970],
            'historical_completed_heights': historical, 'segment': segment, 'earlier_coverage_is_historical': True}
        path = self.store(identity, receipt)
        checkpoint = {'schema': SCHEMA, 'root_id': PARENT, 'target_hash': parent['query']['block_hash'],
            'scope': [965925, 965970], 'receipt_id': parent_id, 'receipt_sha256': receipt_digest(parent),
            'segment': segment - 1, 'parent_receipt_id': None, 'completed_heights': historical}
        self.jobs.cursors.save(identity, receipt, parent=checkpoint)
        return receipt, path

    def preview(self, identity=PARENT):
        return Jobs(self.root, mode='discovery').receipt(identity)['continuation']

    def test_legacy_parent_opens_valid_successor_without_retained_bodies_or_writes(self):
        self.successor()
        before = {str(path): path.read_bytes() for path in self.state.rglob('*') if path.is_file()}
        with patch('hemi_lookup.web.Session', side_effect=AssertionError('No source session')), \
             patch('hemi_lookup.web.PublicSources', side_effect=AssertionError('No sources')):
            result = self.preview()
            self.assertEqual(result['reason'], 'newer_result_available')
            self.assertEqual(result['latest_receipt_id'], CHILD)
            child = self.preview(CHILD)
        self.assertEqual(child['bitcoin_range'], [965931, 965950])
        self.assertEqual({str(path): path.read_bytes() for path in self.state.rglob('*') if path.is_file()}, before)
        self.assertFalse((self.state / 'blocks').exists())
        self.assertFalse((self.state / 'session.json').exists())

    def test_multiple_successors_are_linked_back_to_the_requested_parent(self):
        child, _ = self.successor()
        self.successor(CHILD, child, LATEST, 3)
        self.assertEqual(self.preview()['latest_receipt_id'], LATEST)
        self.assertEqual(self.preview(CHILD)['latest_receipt_id'], LATEST)
        self.assertEqual(self.preview(LATEST)['bitcoin_range'], [965931, 965950])

    def test_changed_parent_receipt_cannot_redirect_to_its_old_successor(self):
        self.successor()
        changed = copy.deepcopy(self.parent)
        changed['coverage']['stop_reason'] = 'changed'
        self.parent_path.write_text(json.dumps(changed), encoding='utf-8')
        self.assertEqual(self.preview()['reason'], 'continuation_changed')

    def test_changed_successor_receipt_fails_its_checkpoint_binding(self):
        child, path = self.successor()
        child['coverage']['stop_reason'] = 'changed'
        path.write_text(json.dumps(child), encoding='utf-8')
        self.assertEqual(self.preview()['reason'], 'continuation_changed')

    def test_changed_hash_root_extent_or_parent_link_is_rejected(self):
        child, path = self.successor()
        original = path.read_bytes()
        checkpoint_path = self.jobs.cursors.path(CHILD)
        checkpoint_original = checkpoint_path.read_bytes()
        for field, altered in [('root_receipt_id', 'f' * 32), ('original_extent', [965925, 965971]),
                               ('parent_receipt_id', 'e' * 32), ('segment', 4)]:
            with self.subTest(field=field):
                changed = copy.deepcopy(child)
                changed['discovery'][field] = altered
                path.write_text(json.dumps(changed), encoding='utf-8')
                # Even a cursor with a matching receipt hash cannot hide an unrelated lineage.
                checkpoint = json.loads(checkpoint_original)
                checkpoint['receipt_sha256'] = receipt_digest(changed)
                path_cursor_field = {'root_receipt_id': 'root_id', 'original_extent': 'scope',
                                     'parent_receipt_id': 'parent_receipt_id', 'segment': 'segment'}[field]
                checkpoint[path_cursor_field] = altered
                checkpoint_path.write_text(json.dumps(checkpoint), encoding='utf-8')
                self.assertEqual(self.preview()['reason'], 'continuation_changed')
                path.write_bytes(original)
                checkpoint_path.write_bytes(checkpoint_original)
        changed = copy.deepcopy(child)
        changed['query']['block_hash'] = '0x' + '11' * 32
        path.write_text(json.dumps(changed), encoding='utf-8')
        self.assertNotEqual(self.preview().get('reason'), 'newer_result_available')

    def test_missing_corrupt_or_unrelated_latest_pointer_does_not_enable_navigation(self):
        self.successor()
        pointer = self.jobs.cursors.path(PARENT, 'latest.json')
        for raw in ('[]', '{', json.dumps({'receipt_id': '../../escape'}),
                    json.dumps({'receipt_id': 'f' * 32})):
            with self.subTest(raw=raw):
                pointer.write_text(raw, encoding='utf-8')
                self.assertEqual(self.preview()['reason'], 'continuation_changed')

    def test_internally_consistent_different_hemi_target_is_not_a_successor(self):
        child, path = self.successor()
        child['query'] = copy.deepcopy(MARCH['query'])
        path.write_text(json.dumps(child), encoding='utf-8')
        checkpoint_path = self.jobs.cursors.path(CHILD)
        checkpoint = json.loads(checkpoint_path.read_text())
        checkpoint['receipt_sha256'] = receipt_digest(child)
        checkpoint['target_hash'] = child['query']['block_hash']
        checkpoint_path.write_text(json.dumps(checkpoint), encoding='utf-8')
        self.assertEqual(self.preview()['reason'], 'continuation_changed')

    def test_existing_parent_checkpoint_is_not_bypassed_by_latest_navigation(self):
        for block in self.parent['coverage']['blocks']:
            block['publication_associations_complete'] = True
        self.parent_path.write_text(json.dumps(self.parent), encoding='utf-8')
        self.jobs.cursors.save(PARENT, self.parent)
        self.successor()
        checkpoint_path = self.jobs.cursors.path(PARENT)
        checkpoint = json.loads(checkpoint_path.read_text())
        checkpoint['scope'][1] += 1
        checkpoint_path.write_text(json.dumps(checkpoint), encoding='utf-8')
        self.assertEqual(self.preview()['reason'], 'continuation_changed')

    def test_explicitly_unfinished_parent_association_is_not_skipped(self):
        self.parent['coverage']['blocks'][2]['publication_associations_complete'] = False
        self.parent_path.write_text(json.dumps(self.parent), encoding='utf-8')
        self.successor()
        self.assertEqual(self.preview()['reason'], 'continuation_changed')

    def test_incomplete_parent_work_is_not_credited_as_completed_history(self):
        self.parent['coverage']['unresolved_candidates'] = [{'bitcoin_height': 965927}]
        self.parent_path.write_text(json.dumps(self.parent), encoding='utf-8')
        self.successor()
        self.assertEqual(self.preview()['reason'], 'continuation_changed')

    def test_missing_cache_without_successor_still_requires_reconstruction(self):
        self.assertEqual(self.preview()['reason'], 'continuation_cache_unavailable')


if __name__ == '__main__':
    unittest.main()
