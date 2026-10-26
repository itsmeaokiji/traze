"""Durable, receipt-bound cursors for explicit bounded browser searches.

Each continuation is a new acquisition segment under the unchanged lifetime
ledger. Earlier coverage remains historical; it is never relabelled as a fresh
combined scan, a non-inclusion proof, or a globally first publication result.
"""

import copy
import hashlib
import json
import re
import time
from pathlib import Path

from .bitcoin import MAX_RAW_BLOCK, hex32, parse_block
from .errors import DataError
from .proof import verify_receipt
from .storage import atomic_json
from .search_plan import SEGMENT_HEIGHTS, estimated_window

SCHEMA = 'traze-discovery-cursor-v1'
IDENTITY = re.compile(r'[0-9a-f]{32}\Z')


def receipt_digest(receipt):
    raw = json.dumps(receipt, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')
    return hashlib.sha256(raw).hexdigest()


def _intervals(heights):
    result = []
    for height in sorted(set(heights)):
        if result and result[-1][1] + 1 == height:
            result[-1][1] = height
        else:
            result.append([height, height])
    return result


class DiscoveryCursors:
    def __init__(self, state_dir):
        self.directory = Path(state_dir) / 'discovery-cursors'
        self.cache_directory = Path(state_dir) / 'blocks'
        self.receipt_directory = Path(state_dir) / 'receipts'

    def legacy_body(self, block_hash):
        path = self.cache_directory / (hex32(block_hash).hex() + '.bin')
        if not path.resolve().is_relative_to(self.cache_directory.resolve()):
            raise DataError('continuation_changed', 'Retained body path changed')
        if not path.is_file():
            return None
        if path.stat().st_size > MAX_RAW_BLOCK:
            raise DataError('cache_corrupt', 'Retained Bitcoin body exceeds its size limit')
        return path.read_bytes()

    def path(self, identity, suffix='json'):
        if not isinstance(identity, str) or not IDENTITY.fullmatch(identity):
            raise DataError('continuation_unavailable', 'Only local browser searches can be continued')
        path = self.directory / f'{identity}.{suffix}'
        if not path.resolve().is_relative_to(self.directory.resolve()):
            raise DataError('continuation_changed', 'Search cursor path changed')
        return path

    def completed(self, receipt, *, legacy=False):
        coverage = receipt['coverage']
        pending = {v.get('height', v.get('bitcoin_height')) for key in ('gaps', 'unresolved_candidates')
                   for v in coverage.get(key, [])}
        result = []
        if not receipt['query'].get('covering_checkpoint'):
            return []
        cover = receipt['query']['covering_checkpoint']['height']
        for block in coverage.get('blocks', []):
            height = block['height']
            if height in pending or block.get('full_transaction_root_checked') is not True:
                continue
            complete = block.get('publication_associations_complete')
            if complete is True:
                result.append(height)
            elif complete is None and legacy:
                # Old negative receipts have no durable candidate-completion
                # marker. Reparse retained bytes before using their scan cursor.
                raw = self.legacy_body(block['hash'])
                if raw is None:
                    raise DataError('continuation_cache_unavailable', 'Retained Bitcoin bodies are needed to validate this older search cursor')
                parsed = parse_block(raw, block['hash'])
                if parsed['tx_count'] != block['transaction_count'] or parsed['header'].hex() != block['header_hex']:
                    raise DataError('continuation_changed', 'Older scan metadata differs from retained Bitcoin data')
                if all(item['supported'] and (item['l2_height'] < cover or item['l2_height'] % 25)
                       for item in parsed['candidates']):
                    result.append(height)
                # Any potentially applicable older candidate is retried, even
                # if a historical success/absence flag claims it was handled.
        return result

    def checkpoint(self, identity, receipt):
        path = self.path(identity)
        digest = receipt_digest(receipt)
        if path.is_file():
            if path.stat().st_size > 100_000:
                raise DataError('continuation_changed', 'Search checkpoint exceeds its supported size')
            value = json.loads(path.read_text(encoding='utf-8'))
            if (not isinstance(value, dict) or value.get('schema') != SCHEMA or value.get('receipt_id') != identity
                    or value.get('receipt_sha256') != digest
                    or value.get('target_hash') != receipt['query'].get('block_hash')):
                raise DataError('continuation_changed', 'The saved search or its cursor changed; acquisition was not started')
            discovery = receipt.get('discovery')
            if discovery:
                expected = sorted(set(discovery['historical_completed_heights'] + self.completed(receipt)))
                valid = (value['scope'] == discovery['original_extent'] and value['root_id'] == discovery['root_receipt_id']
                         and value['parent_receipt_id'] == discovery['parent_receipt_id']
                         and value['segment'] == discovery['segment'])
            else:
                plan = receipt['coverage']['plan']
                end = plan['initial_end'] if plan['mode'] == 'explicit_range' else receipt['bitcoin_tip_before']['height']
                expected = self.completed(receipt)
                valid = value['scope'] == [plan['start'], end] and value['root_id'] == identity and value['segment'] == 1
            if (not valid or value['completed_heights'] != expected
                    or value.get('estimated_window') != estimated_window(receipt)):
                raise DataError('continuation_changed', 'Search extent or completed-height cursor changed')
            return value
        if receipt.get('mode') != 'live_lookup' or receipt.get('proofs') or receipt.get('validation'):
            raise DataError('continuation_unavailable', 'This result is not an unfinished ordinary browser discovery')
        plan = receipt.get('coverage', {}).get('plan', {})
        start = plan.get('start')
        end = (plan.get('initial_end') if plan.get('mode') == 'explicit_range'
               else receipt.get('bitcoin_tip_before', {}).get('height'))
        if (plan.get('mode') not in ('explicit_range', 'timestamp_guided')
                or type(start) is not int or type(end) is not int or not 0 <= start <= end
                or not receipt['query'].get('header') or not receipt['query'].get('covering_checkpoint')):
            raise DataError('continuation_unavailable', 'A resolved input and original Bitcoin search extent are required')
        window = estimated_window(receipt)
        return {'schema': SCHEMA, 'receipt_id': identity, 'receipt_sha256': digest,
                'root_id': identity, 'target_hash': receipt['query']['block_hash'],
                'scope': [start, end], 'segment': 1, 'parent_receipt_id': None,
                'completed_heights': self.completed(receipt, legacy=True),
                **({'estimated_window': window} if window else {})}

    def scope(self, identity, receipt):
        """Read frozen identity/extent metadata without reconstructing old bodies."""
        discovery = receipt.get('discovery')
        if discovery:
            root, scope, segment = discovery['root_receipt_id'], discovery['original_extent'], discovery['segment']
            if receipt.get('mode') != 'live_search_continuation':
                raise DataError('continuation_changed', 'Successor is not a saved search segment')
        else:
            plan = receipt['coverage']['plan']
            end = plan['initial_end'] if plan['mode'] == 'explicit_range' else receipt['bitcoin_tip_before']['height']
            root, scope, segment = identity, [plan['start'], end], 1
            if receipt.get('mode') != 'live_lookup' or plan['mode'] not in ('explicit_range', 'timestamp_guided'):
                raise DataError('continuation_changed', 'Original search extent is unavailable')
        self.path(root)
        if (not isinstance(scope, list) or len(scope) != 2 or any(type(h) is not int for h in scope)
                or not 0 <= scope[0] <= scope[1] or type(segment) is not int or not 1 <= segment <= 128):
            raise DataError('continuation_changed', 'Saved search scope or segment is outside its supported bounds')
        hex32(receipt['query']['block_hash'])
        estimated_window(receipt)
        return root, scope, segment

    def parent_link(self, identity, receipt, checkpoint, child):
        """Validate the successor's historical progress without making it fresh."""
        link = child['discovery']
        _, _, segment = self.scope(identity, receipt)
        if (link['parent_receipt_id'] != identity or link['parent_receipt_sha256'] != receipt_digest(receipt)
                or link['segment'] != segment + 1 or estimated_window(child) != estimated_window(receipt)):
            raise DataError('continuation_changed', 'Successor no longer belongs to the requested saved result')
        historical = link['historical_completed_heights']
        if (not isinstance(historical, list) or len(historical) > 3000
                or any(type(h) is not int for h in historical) or historical != sorted(set(historical))):
            raise DataError('continuation_changed', 'Successor historical progress is invalid')
        if checkpoint is not None:
            valid = historical == checkpoint['completed_heights']
        else:
            # A validated child checkpoint already records earlier local work.
            # Bind it to the exact old receipt and reject known unfinished work;
            # do not require evicted bytes merely to open that saved child.
            pending = {item.get('height', item.get('bitcoin_height'))
                       for key in ('gaps', 'unresolved_candidates') for item in receipt['coverage'].get(key, [])}
            eligible = {block['height'] for block in receipt['coverage']['blocks']
                        if block.get('full_transaction_root_checked') is True
                        and block.get('publication_associations_complete') is not False
                        and block['height'] not in pending}
            valid = set(historical).issubset(eligible)
        if not valid:
            raise DataError('continuation_changed', 'Successor credits unfinished or changed parent progress')

    def successor(self, identity, receipt):
        """Resolve an authenticated saved-result chain before a legacy reparse.

        This returns a read action only. The selected segment's own checkpoint
        still controls acquisition; prior scan records are never refreshed here.
        """
        root = receipt.get('discovery', {}).get('root_receipt_id', identity)
        pointer = self.path(root, 'latest.json')
        if not pointer.is_file():
            return None
        deadline, used = time.monotonic() + 5, 0

        def check():
            if time.monotonic() >= deadline:
                raise DataError('continuation_changed', 'Saved-result navigation exceeded its read bound')

        def read(path, maximum):
            nonlocal used
            check()
            size = path.stat().st_size
            used += size
            if size > maximum or used > 32_000_000:
                raise DataError('continuation_changed', 'Saved-result navigation exceeds its input bound')
            value = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(value, dict):
                raise DataError('continuation_changed', 'Saved navigation record is not an object')
            return value

        successor = read(pointer, 4096)['receipt_id']
        self.path(successor)
        if successor == identity:
            return None
        root, scope, segment = self.scope(identity, receipt)
        parent_checkpoint = self.checkpoint(identity, receipt) if self.path(identity).is_file() else None
        current, child, seen = successor, None, set()
        while current != identity:
            if current in seen or len(seen) >= 127:
                raise DataError('continuation_changed', 'Saved-result navigation is cyclic or too long')
            seen.add(current)
            self.path(current)
            path = self.receipt_directory / f'web-{current}.json'
            if not path.resolve().is_relative_to(self.receipt_directory.resolve()):
                raise DataError('continuation_changed', 'Successor receipt path changed')
            saved = read(path, 10_000_000)
            verify_receipt(saved, check)
            saved_root, saved_scope, saved_segment = self.scope(current, saved)
            if (saved_root != root or saved_scope != scope or saved_segment <= segment
                    or saved['query']['block_hash'] != receipt['query']['block_hash']
                    or estimated_window(saved) != estimated_window(receipt)):
                raise DataError('continuation_changed', 'Latest result has a different target, root or extent')
            checkpoint = self.checkpoint(current, saved)
            heights = checkpoint['completed_heights']
            if (not isinstance(heights, list) or len(heights) > 3000
                    or any(type(h) is not int or not scope[0] <= h <= scope[1] for h in heights)):
                raise DataError('continuation_changed', 'Successor progress exceeds its frozen extent')
            if child is not None:
                self.parent_link(current, saved, checkpoint, child)
            child, current = saved, saved['discovery']['parent_receipt_id']
        self.parent_link(identity, receipt, parent_checkpoint, child)
        check()
        return successor

    def preview(self, identity, receipt):
        try:
            self.path(identity)
            if any(item.get('same') is False for group in ('hemi_headers', 'bitcoin_blocks', 'bitcoin_secondary')
                   for item in receipt.get('context', {}).get(group, [])) or receipt.get('context', {}).get(
                       'requested_block_identity', {}).get('status') == 'different_current_rpc_block':
                return {'available': False, 'reason': 'source_view_changed'}
            try:
                successor = self.successor(identity, receipt)
            except (DataError, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
                raise DataError('continuation_changed', 'The saved successor could not be validated') from exc
            if successor:
                return {'available': False, 'reason': 'newer_result_available', 'latest_receipt_id': successor}
            checkpoint = self.checkpoint(identity, receipt)
            if receipt.get('proofs'):
                return {'available': False, 'reason': 'matching_evidence_available'}
            start, end = checkpoint['scope']
            completed = set(checkpoint['completed_heights'])
            if (len(completed) > 3000 or type(checkpoint['segment']) is not int or checkpoint['segment'] > 128
                    or any(type(h) is not int or not start <= h <= end for h in completed)):
                raise DataError('continuation_changed', 'The local search cursor is outside its supported bounds')
            next_height = start
            while next_height in completed:
                next_height += 1
            common = {'receipt_sha256': checkpoint['receipt_sha256'], 'original_extent': [start, end],
                      'historical_scanned_intervals': _intervals(completed),
                      'segment': checkpoint['segment'], 'parent_receipt_id': checkpoint.get('parent_receipt_id')}
            if next_height > end:
                return {**common, 'available': False, 'reason': 'original_extent_processed'}
            if checkpoint['segment'] >= 128:
                return {**common, 'available': False, 'reason': 'segment_history_limit'}
            high = min(next_height + SEGMENT_HEIGHTS - 1, end)
            window = checkpoint.get('estimated_window')
            if window:
                common['estimated_window'] = list(window)
                common['search_phase'] = 'estimated_window' if next_height <= window[1] else 'forward_extension'
                if next_height <= window[1]:
                    high = min(high, window[1])
            # Stop before an already processed height. A gap retry must not
            # redownload later completed bodies merely to cross the gap.
            high = min([h - 1 for h in completed if next_height < h <= high] or [high])
            return {**common, 'available': True, 'reason': 'next_segment',
                    'bitcoin_range': [next_height, high]}
        except (DataError, OSError, ValueError, KeyError, TypeError) as exc:
            return {'available': False, 'reason': getattr(exc, 'code', 'continuation_changed')}

    def save(self, identity, receipt, parent=None):
        if parent is None:
            # New engine receipts carry an association-completion marker. No
            # legacy body reparse is necessary when storing their checkpoint.
            if receipt.get('mode') != 'live_lookup' or receipt.get('proofs'):
                return
            plan = receipt.get('coverage', {}).get('plan', {})
            end = (plan.get('initial_end') if plan.get('mode') == 'explicit_range'
                   else receipt.get('bitcoin_tip_before', {}).get('height'))
            if type(plan.get('start')) is not int or type(end) is not int:
                return
            value = {'schema': SCHEMA, 'receipt_id': identity, 'receipt_sha256': receipt_digest(receipt),
                     'root_id': identity, 'target_hash': receipt['query']['block_hash'],
                     'scope': [plan['start'], end], 'segment': 1, 'parent_receipt_id': None,
                     'completed_heights': self.completed(receipt)}
            window = estimated_window(receipt)
            if window:
                value['estimated_window'] = window
        else:
            value = copy.deepcopy(parent)
            if parent.get('estimated_window') != estimated_window(receipt):
                raise DataError('continuation_changed', 'Continuation changed its estimated window')
            value.update({'receipt_id': identity, 'receipt_sha256': receipt_digest(receipt),
                          'segment': parent['segment'] + 1, 'parent_receipt_id': parent['receipt_id'],
                          'completed_heights': sorted(set(parent['completed_heights'] + self.completed(receipt)))})
        path = self.path(identity)
        if path.exists():
            raise DataError('continuation_changed', 'Refusing to replace an existing search checkpoint')
        atomic_json(path, value)
        atomic_json(self.path(value['root_id'], 'latest.json'), {'receipt_id': identity})
