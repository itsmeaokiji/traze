"""Offline request reuse and window-first execution; no public chain reads.

Synthetic timestamp headers explicitly stub PoW. Planning-only scan rows are
controlled state-machine inputs, not valid new publication or Bitcoin proofs.
Real retained header/positive-proof checks remain in the adjacent suites.
"""

import copy
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hemi_lookup.discovery import DiscoveryCursors, receipt_digest
from hemi_lookup.engine import Engine
from hemi_lookup.errors import BudgetError, DataError, DeadlineError
from hemi_lookup.network import PublicSources
from hemi_lookup.storage import Session
from hemi_lookup.timestamps import TimestampRecovery
from hemi_lookup.web import Jobs
from test_recovery import SyntheticBitcoin
from hash_fixtures import ORDINARY


class OfflineCase(unittest.TestCase):
    def setUp(self):
        guard = patch.object(socket, 'create_connection', side_effect=AssertionError('Public networking forbidden'))
        guard.start()
        self.addCleanup(guard.stop)
        guard = patch('hemi_lookup.transport.ProcessHTTP.read', side_effect=AssertionError('Public transport forbidden'))
        guard.start()
        self.addCleanup(guard.stop)


class TimestampReuseTests(OfflineCase):
    def setUp(self):
        super().setUp()
        guard = patch('hemi_lookup.timestamps.header_pow', return_value=True)
        guard.start()
        self.addCleanup(guard.stop)

    def test_repeated_hint_refreshes_selected_height_and_tip_without_redownloading_headers(self):
        source = SyntheticBitcoin()
        recovery = TimestampRecovery(source.fetch, lambda: None)
        first = recovery.hint(source.base + 510)
        cut = len(source.calls)
        second = recovery.hint(source.base + 510)
        self.assertEqual(first['lookup'], second['lookup'])
        self.assertEqual(len(source.calls) - cut, 3)
        self.assertIn('/block-height/' + str(first['lookup']['height']), source.calls[cut:])
        self.assertEqual(source.calls[-1], '/block-height/100')
        self.assertFalse(any(path.endswith('/header') for path in source.calls[cut:]))
        self.assertFalse(second['global_timestamp_floor_proven'])

    def test_nearby_hints_reuse_linked_headers_but_keep_their_own_controls(self):
        source = SyntheticBitcoin()
        recovery = TimestampRecovery(source.fetch, lambda: None)
        results = [recovery.hint(source.base + t) for t in (510, 520, 530)]
        headers = [p for p in source.calls if p.endswith('/header')]
        self.assertEqual(len(headers), len(set(headers)))
        self.assertEqual(results[0]['lookup']['height'], 53)  # raw times are not monotonic
        for result in results:
            self.assertEqual(len(result['recovery']['mtp_header_control']['supplied_headers']), 11)
            self.assertFalse(result['recovery']['mtp_header_control']['full_spv_or_expected_difficulty_verified'])

    def test_prior_hint_is_revalidated_before_becoming_a_search_bracket(self):
        source = SyntheticBitcoin()
        recovery = TimestampRecovery(source.fetch, lambda: None)
        recovery.seed(53, source.meta[53]['id'])
        cut = len(source.calls)
        result = recovery.hint(source.base + 510)
        self.assertEqual(result['lookup']['height'], 53)
        self.assertTrue(all(step['height'] >= 53 for step in result['recovery']['mtp_steps']))
        self.assertLess(sum(p.startswith('/block-height/') for p in source.calls[cut:]), 12)
        with self.assertRaises(DataError):
            recovery.seed(53, '22' * 32)

    def test_cached_hint_still_rejects_a_changed_selected_height(self):
        source = SyntheticBitcoin()
        changed = False
        def fetch(path, as_json=True):
            if changed and path == '/block-height/53':
                return source.meta[54]['id']
            return source.fetch(path, as_json)
        recovery = TimestampRecovery(fetch, lambda: None)
        recovery.hint(source.base + 510)
        changed = True
        with self.assertRaises(DataError):
            recovery.hint(source.base + 510)

    def test_cached_hint_still_rejects_a_changed_tip_anchor(self):
        source = SyntheticBitcoin()
        changed = False
        def fetch(path, as_json=True):
            if changed and path == '/block-height/100':
                return '22' * 32
            return source.fetch(path, as_json)
        recovery = TimestampRecovery(fetch, lambda: None)
        recovery.hint(source.base + 510)
        changed = True
        with self.assertRaises(DataError):
            recovery.hint(source.base + 510)

    def test_parent_hash_corruption_is_rejected_when_walking_backwards(self):
        source = SyntheticBitcoin()
        def fetch(path, as_json=True):
            raw = source.fetch(path, as_json)
            if path.endswith('/header'):
                return raw[:8] + '22' * 32 + raw[72:]
            return raw
        with self.assertRaises(DataError):
            TimestampRecovery(fetch, lambda: None).hint(source.base + 510)

    def test_cached_neighborhood_median_is_checked_against_supplied_headers(self):
        source = SyntheticBitcoin()
        def fetch(path, as_json=True):
            raw = source.fetch(path, as_json)
            if path.startswith('/blocks/') and path != '/blocks/tip/hash':
                for row in raw:
                    row['mediantime'] += 1
            return raw
        with self.assertRaises(DataError):
            TimestampRecovery(fetch, lambda: None).hint(source.base + 510)

    def test_old_neighborhood_without_median_time_is_not_promoted_to_full_metadata(self):
        source = SyntheticBitcoin()
        def fetch(path, as_json=True):
            raw = source.fetch(path, as_json)
            if path.startswith('/blocks/') and path != '/blocks/tip/hash':
                for row in raw:
                    row.pop('mediantime')
            return raw
        result = TimestampRecovery(fetch, lambda: None).hint(source.base + 510)
        self.assertEqual(result['lookup']['height'], 53)
        self.assertEqual(len(result['recovery']['mtp_header_control']['supplied_headers']), 11)

    def test_a_metadata_batch_cannot_seed_a_height_outside_its_requested_span(self):
        source = SyntheticBitcoin()
        def fetch(path, as_json=True):
            raw = source.fetch(path, as_json)
            if path.startswith('/blocks/') and path != '/blocks/tip/hash':
                raw[0] = copy.deepcopy(source.meta[100])
            return raw
        with self.assertRaises(DataError):
            TimestampRecovery(fetch, lambda: None).hint(source.base + 510)

    def test_header_target_failure_is_not_hidden_by_metadata_reuse(self):
        source = SyntheticBitcoin()
        with patch('hemi_lookup.timestamps.header_pow', return_value=False), self.assertRaises(DataError):
            TimestampRecovery(source.fetch, lambda: None).hint(source.base + 510)

    def test_deadline_stops_even_a_cached_hint_without_another_request(self):
        source = SyntheticBitcoin()
        expired = False
        def check():
            if expired:
                raise DeadlineError()
        recovery = TimestampRecovery(source.fetch, check)
        recovery.hint(source.base + 510)
        cut = len(source.calls)
        expired = True
        with self.assertRaises(DeadlineError):
            recovery.hint(source.base + 510)
        self.assertEqual(len(source.calls), cut)

    def test_primary_then_outage_seeds_only_a_revalidated_pinned_fallback(self):
        source = SyntheticBitcoin()
        primary_calls, messages = [], []
        with tempfile.TemporaryDirectory(prefix='traze_window_source_') as directory, Session(directory) as session:
            sources = PublicSources(session)
            sources.set_progress(messages.append)
            def reader(url, ceiling=262144, rpc=None, block=False):
                if '/timestamp/' in url:
                    primary_calls.append(url)
                    if len(primary_calls) > 1:
                        raise DataError('source_unavailable', 'Controlled TLS outage')
                    meta = source.meta[53]
                    return json.dumps({'height': 53, 'hash': meta['id'], 'timestamp': meta['timestamp']}).encode()
                path = url.split('/api', 1)[1]
                raw = source.fetch(path, not (path.startswith('/block-height/') or path.endswith('/header') or path == '/blocks/tip/hash'))
                return (raw if isinstance(raw, str) else json.dumps(raw)).encode()
            sources.http.read = reader
            sources.timestamp(source.base + 510)
            sources.timestamp(source.base + 520)
            sources.timestamp(source.base + 530)
            self.assertEqual(len(primary_calls), 2)
            self.assertTrue(sources.timestamp_failed)
            self.assertEqual(sources.timestamp_recovery.cache[53]['id'], source.meta[53]['id'])
            self.assertIn('Using the fallback timestamp source', messages)


class PlanningSession:
    max_blocks = 20
    def __init__(self, maximum=20):
        self.max_blocks = maximum
        self.run = {'body_attempts': 0, 'cache_reads': 0}
    def metrics(self):
        return {'response_bytes_this_run': 0, 'bitcoin_body_attempts': self.run['body_attempts'],
                'limits': {'response_bytes': 40000000, 'bitcoin_body_attempts': self.max_blocks,
                           'requests': 600, 'overall_seconds': 600}}


class PlanningSources:
    def __init__(self, start=963060, end=963081, tip=963120):
        self.start, self.end, self.tip = start, end, tip
        self.hints = iter((start + 1, start + 2, end - 1))
        self.progress = lambda message: None
    def set_progress(self, callback): self.progress = callback
    def rpc(self, method, params):
        return '0xa867' if method == 'eth_chainId' else 'controlled-offline-planning'
    def btc(self, path, *args):
        return '11' * 32 if path == '/blocks/tip/hash' else {'height': self.tip}
    def timestamp(self, stamp):
        return {'lookup': {'height': next(self.hints)}}


class PlanningEngine(Engine):
    """Exercise the real planner/finish logic with explicitly synthetic scan rows."""
    def __init__(self, *args, gaps=(), cached=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.scanned, self.gaps, self.cached = [], set(gaps), set(cached)
    def resolve_requested(self, value):
        return ORDINARY['query']['block_number'], copy.deepcopy(ORDINARY['query']['header'])
    def checkpoint(self, height): return copy.deepcopy(ORDINARY['query']['covering_checkpoint'])
    def ancestry(self, height): return []
    def scan_height(self, height, cover):
        self.scanned.append(height)
        if height in self.gaps:
            raise DataError('source_unavailable', 'Controlled missing body')
        if height not in self.cached:
            if self.session.run['body_attempts'] >= self.session.max_blocks:
                raise BudgetError('Bitcoin body-attempt limit reached', limit='bitcoin_body_attempts')
            self.session.run['body_attempts'] += 1
        self.receipt['coverage']['blocks'].append({'height': height, 'full_transaction_root_checked': True,
                                                  'publication_associations_complete': True})
    def recheck(self):
        self.progress('Checking source views')
        self.receipt['context'] = {'current_view_confirmed': True}


class WindowFirstTests(OfflineCase):
    def operation(self, count=22, *, maximum=20, gaps=(), cached=(), explicit=None):
        source = PlanningSources(end=963060 + count - 1)
        messages = []
        engine = PlanningEngine(source, PlanningSession(maximum), None, progress=messages.append, gaps=gaps, cached=cached)
        receipt = engine.lookup(5243117, explicit)
        receipt['validation'] = {'scope': 'synthetic_planner_rows', 'live_public_reads': False}
        return receipt, engine, messages

    def test_22_height_estimate_keeps_its_end_but_executes_only_the_first_segment(self):
        receipt, engine, messages = self.operation()
        self.assertEqual(receipt['coverage']['plan']['initial_end'], 963081)
        self.assertEqual(receipt['coverage']['plan']['execution_segment'], [963060, 963079])
        self.assertEqual(engine.scanned, list(range(963060, 963080)))
        self.assertEqual(receipt['resources']['bitcoin_body_attempts'], 20)
        self.assertEqual(receipt['coverage']['stop_reason'], 'search_segment_end')
        self.assertEqual(receipt['status'], 'incomplete')
        self.assertFalse(receipt['selection']['global_earliest_proven'])
        self.assertIn('Estimating the Bitcoin search range', messages)
        self.assertEqual(messages[-1], 'Checking source views')

    def test_short_window_stops_without_silently_extending_and_stays_scoped(self):
        receipt, engine, _ = self.operation(13)
        self.assertEqual(engine.scanned, list(range(963060, 963073)))
        self.assertEqual(receipt['coverage']['stop_reason'], 'estimated_window_end')
        self.assertEqual(receipt['status'], 'not_found_within_coverage')
        self.assertNotIn('extensions', receipt['coverage'])

    def test_lower_body_limit_still_stops_before_completing_the_planned_segment(self):
        receipt, _, _ = self.operation(maximum=3)
        self.assertEqual(receipt['resources']['bitcoin_body_attempts'], 3)
        self.assertEqual(receipt['coverage']['stop_reason'], 'budget_limit')
        self.assertEqual(receipt['coverage']['stop_detail']['bitcoin_height'], 963063)

    def test_cached_heights_use_no_new_body_attempt_and_explicit_ranges_keep_their_scope(self):
        receipt, engine, _ = self.operation(maximum=0, cached=range(963060, 963082), explicit=(963060, 963081))
        self.assertEqual(len(engine.scanned), 22)
        self.assertEqual(receipt['resources']['bitcoin_body_attempts'], 0)
        self.assertEqual(receipt['coverage']['stop_reason'], 'explicit_range_end')
        self.assertNotIn('segment_policy', receipt['coverage']['plan'])

    def store(self, cursors, identity, receipt, parent=None):
        path = cursors.receipt_directory / f'web-{identity}.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(receipt), encoding='utf-8')
        cursors.save(identity, receipt, parent)

    def test_continuation_finishes_the_two_window_heights_before_offering_an_extension(self):
        receipt, _, _ = self.operation()
        parent_id, child_id = 'a' * 32, 'b' * 32
        with tempfile.TemporaryDirectory(prefix='traze_window_cursor_') as directory:
            cursors = DiscoveryCursors(directory)
            self.store(cursors, parent_id, receipt)
            preview = cursors.preview(parent_id, receipt)
            self.assertEqual(preview['bitcoin_range'], [963080, 963081])
            self.assertEqual(preview['search_phase'], 'estimated_window')
            checkpoint = cursors.checkpoint(parent_id, receipt)
            child, _, _ = self.operation(explicit=tuple(preview['bitcoin_range']))
            child['mode'] = 'live_search_continuation'
            child['discovery'] = {'parent_receipt_id': parent_id, 'parent_receipt_sha256': receipt_digest(receipt),
                'root_receipt_id': parent_id, 'original_extent': checkpoint['scope'], 'segment': 2,
                'historical_completed_heights': checkpoint['completed_heights'], 'earlier_coverage_is_historical': True,
                'estimated_window': checkpoint['estimated_window']}
            self.store(cursors, child_id, child, checkpoint)
            after = cursors.preview(child_id, child)
            self.assertEqual(after['bitcoin_range'], [963082, 963101])
            self.assertEqual(after['search_phase'], 'forward_extension')
            self.assertEqual(cursors.preview(parent_id, receipt)['latest_receipt_id'], child_id)

    def test_gap_is_retried_before_later_completed_heights(self):
        receipt, _, _ = self.operation(gaps=[963063])
        with tempfile.TemporaryDirectory(prefix='traze_window_gap_') as directory:
            cursors = DiscoveryCursors(directory)
            self.store(cursors, 'a' * 32, receipt)
            self.assertEqual(cursors.preview('a' * 32, receipt)['bitcoin_range'], [963063, 963063])

    def test_old_timestamp_receipt_keeps_its_original_continuation_behavior_without_writes(self):
        receipt, _, _ = self.operation()
        receipt['coverage']['plan'].pop('segment_policy')
        receipt['coverage']['plan'].pop('execution_segment')
        with tempfile.TemporaryDirectory(prefix='traze_window_legacy_') as directory:
            cursors = DiscoveryCursors(directory)
            self.store(cursors, 'a' * 32, receipt)
            before = {p: p.read_bytes() for p in Path(directory).rglob('*') if p.is_file()}
            preview = cursors.preview('a' * 32, receipt)
            self.assertEqual(preview['bitcoin_range'], [963080, 963099])
            self.assertNotIn('estimated_window', preview)
            self.assertEqual(before, {p: p.read_bytes() for p in Path(directory).rglob('*') if p.is_file()})

    def test_changed_estimated_window_is_not_accepted_by_an_existing_cursor(self):
        receipt, _, _ = self.operation()
        with tempfile.TemporaryDirectory(prefix='traze_window_changed_') as directory:
            cursors = DiscoveryCursors(directory)
            self.store(cursors, 'a' * 32, receipt)
            changed = copy.deepcopy(receipt)
            changed['coverage']['plan']['initial_end'] += 1
            self.assertEqual(cursors.preview('a' * 32, changed)['reason'], 'continuation_changed')

    def test_browser_operation_propagates_the_frozen_window_and_retains_accounting_limits(self):
        receipt, _, _ = self.operation()
        identity = 'a' * 32
        with tempfile.TemporaryDirectory(prefix='traze_window_web_') as directory:
            jobs = Jobs(Path(directory), mode='discovery')
            self.store(jobs.cursors, identity, receipt)
            checkpoint = jobs.cursors.checkpoint(identity, receipt)
            jobs.jobs[identity] = {'events': []}
            with patch('hemi_lookup.web.PublicSources', side_effect=lambda session: PlanningSources()), \
                 patch('hemi_lookup.web.Engine', PlanningEngine):
                child = jobs.public_operation(identity, 'continue', (receipt['query']['block_hash'], checkpoint, [963080, 963081]))
            self.assertEqual(child['discovery']['estimated_window'], [963060, 963081])
            self.assertEqual(child['discovery']['historical_completed_heights'], list(range(963060, 963080)))
            self.assertTrue(child['discovery']['earlier_coverage_is_historical'])
            self.assertEqual(child['resources']['limits']['overall_seconds'], 600)
            self.assertEqual(child['resources']['limits']['response_bytes'], 40000000)
            self.assertEqual(child['resources']['limits']['bitcoin_body_attempts'], 20)
            self.assertEqual(child['resources']['limits']['requests'], 600)
            self.assertEqual(child['coverage']['fully_scanned_intervals'], [[963080, 963081]])
            jobs.cursors.save('b' * 32, child, checkpoint)
            self.assertEqual(jobs.cursors.preview('b' * 32, child)['bitcoin_range'], [963082, 963101])

    def test_a_successor_cannot_move_the_window_even_with_an_updated_local_checksum(self):
        receipt, _, _ = self.operation()
        with tempfile.TemporaryDirectory(prefix='traze_window_lineage_') as directory:
            cursors = DiscoveryCursors(directory)
            self.store(cursors, 'a' * 32, receipt)
            checkpoint = cursors.checkpoint('a' * 32, receipt)
            child, _, _ = self.operation(explicit=(963080, 963081))
            child['mode'] = 'live_search_continuation'
            child['discovery'] = {'parent_receipt_id': 'a' * 32, 'parent_receipt_sha256': receipt_digest(receipt),
                'root_receipt_id': 'a' * 32, 'original_extent': checkpoint['scope'], 'segment': 2,
                'historical_completed_heights': checkpoint['completed_heights'], 'earlier_coverage_is_historical': True,
                'estimated_window': [963060, 963082]}
            with self.assertRaises(DataError):
                self.store(cursors, 'b' * 32, child, checkpoint)
            self.assertFalse(cursors.path('b' * 32).exists())


if __name__ == '__main__':
    unittest.main()
