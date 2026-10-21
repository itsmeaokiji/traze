"""Prepare versioned first-publication evidence inside the existing lookup job."""

import hashlib
import json
from pathlib import Path
import tempfile

from traze_evidence.errors import VerificationError, require
from traze_evidence.history import HISTORY_ADDRESS, HISTORY_WINDOW, PROFILE, SCHEMA
from traze_evidence.secure_trie import rpc_quantity
from traze_evidence import verify as interval

from .assurance import POLICY_ID, binding, conflict, selected_proof, verify_bounded
from .bitcoin import MAX_RAW_BLOCK, display_hash, parse_block
from .errors import DataError, BudgetError, DeadlineError, CollectionStopped
from .evidence_encoding import body_row, header_row
from .storage import now

ATTRIBUTES_ACCOUNT = '0x' + '88' * 20


class EvidenceBuilder:
    def __init__(self, engine, store):
        self.engine, self.store = engine, store
        self.sources, self.session = engine.sources, engine.session
        self.check = engine.check_deadline
        self.headers = {}

    def block(self, number, full=False, fresh=False):
        self.check()
        data = getattr(self.engine, 'rpc_blocks', {}).get(number)
        if data is None or full or fresh or not all(key in data for key in ('sha3Uncles', 'receiptsRoot', 'requestsHash')):
            data = self.sources.rpc('eth_getBlockByNumber', [hex(number), full])
        row = header_row(data)
        require(row['number'] == number, 'hemi_header_number', 'Unexpected source Hemi block height')
        if number in self.headers:
            require(self.headers[number] == row, 'hemi_header_hash', 'Hemi source identity changed during evidence preparation')
        self.headers[number] = row
        self.check()
        return data

    def locate_anchor(self, cover, publisher):
        """Account nonces locate a candidate only; full bytes authenticate it."""
        lower = max(1, publisher - min(self.engine.max_ancestry + 1, interval.MAX_HEADERS) + 1)
        upper = cover - 1
        require(lower <= upper, 'anchor_outside_envelope', 'No strictly earlier anchor fits the Hemi header limit', incomplete=True)
        def nonce(height):
            self.check()
            return rpc_quantity(self.sources.rpc('eth_getTransactionCount', [ATTRIBUTES_ACCOUNT, hex(height)]), bits=64)
        wanted = nonce(upper)
        floor = nonce(lower - 1)
        require(wanted > floor, 'anchor_unavailable', 'No Bitcoin-attributes update was located inside the supported Hemi lookback', incomplete=True)
        left, right = lower, upper
        while left < right:
            middle = (left + right) // 2
            value = nonce(middle)
            require(floor <= value <= wanted, 'anchor_hint_conflict', 'Bitcoin-attributes nonce hints are inconsistent', incomplete=True)
            if value < wanted:
                left = middle + 1
            else:
                right = middle
        data = self.block(left, True)
        body = body_row(data, self.check)
        matches = [(i, raw) for i, raw in enumerate(body['transactions']) if bytes.fromhex(raw)[0] == 0x7c]
        require(len(matches) == 1, 'anchor_unavailable', 'The located block has no unique supported Bitcoin-attributes transaction', incomplete=True)
        index, raw = matches[0]
        tip = interval.decode_anchor(bytes.fromhex(raw))
        require(any(tip), 'anchor_unavailable', 'The located Bitcoin reference is empty', incomplete=True)
        return left, index, display_hash(tip), body

    def bitcoin_body(self, height, expected_hash=None):
        self.check()
        block_hash = self.sources.btc('/block-height/' + str(height), False)
        require(expected_hash is None or block_hash == expected_hash,
                'source_view_conflict', 'Bitcoin source identity differs from the selected evidence', incomplete=True)
        raw = self.engine.cache.get(block_hash)
        cached = raw is not None
        if cached:
            self.session.run['cache_reads'] += 1
        else:
            raw, _ = self.sources.block_raw(block_hash, MAX_RAW_BLOCK)
        parsed = parse_block(raw, block_hash, self.check)
        if not cached:
            self.engine.cache.put(block_hash, raw)
        self.check()
        return raw, {'height': height, 'hash': block_hash, 'sha256': hashlib.sha256(raw).hexdigest(),
                     'size': len(raw), 'transactions': parsed['tx_count']}

    def prepare(self, receipt):
        proof = selected_proof(receipt)
        if not proof:
            return {'code': 'publication_unavailable', 'message': 'No applicable publication is available for the stronger check.'}
        self.engine.progress('Preparing first-publication evidence…')
        try:
            require(not conflict(receipt), 'source_view_conflict', 'Conflicting source identities prevent stronger evidence preparation', incomplete=True)
            require(receipt.get('context', {}).get('current_view_confirmed') is True,
                    'source_recheck_incomplete', 'Source identities could not be confirmed for evidence preparation', incomplete=True)
            wanted = binding(receipt, proof)
            cover = ((wanted['query']['number'] + 24) // 25) * 25
            publisher = wanted['publication']['keystone_number']
            anchor, anchor_index, anchor_hash, anchor_body = self.locate_anchor(cover, publisher)
            self.engine.progress('Preparing first-publication evidence: authenticating Hemi ancestry…')
            pub_body = body_row(self.block(publisher, True), self.check)
            start = min(anchor, wanted['query']['number'], publisher - 25)
            require(publisher - start + 1 <= min(self.engine.max_ancestry + 1, interval.MAX_HEADERS),
                    'packet_scope', 'The complete Hemi ancestry exceeds the lookup envelope', incomplete=True)
            for number in range(start, publisher + 1):
                if number not in self.headers:
                    self.block(number)
            require(self.headers[wanted['query']['number']]['hash'] == wanted['query']['hash'],
                    'query_mismatch', 'Full Hemi header differs from the requested block')
            meta = self.sources.btc('/block/' + anchor_hash)
            low, high = meta['height'], proof['bitcoin_height']
            require(type(low) is int and meta.get('id') == anchor_hash and low >= interval.MIN_BIP34_HEIGHT
                    and low <= high and high - low + 1 <= interval.MAX_BITCOIN_BLOCKS,
                    'packet_scope', 'The Bitcoin anchor interval is outside the supported envelope', incomplete=True)
            base = {'schema': interval.SCHEMA, 'profile': interval.PROFILE,
                    'networks': {'hemi_chain_id': 43111, 'bitcoin': 'mainnet'}, 'query': wanted['query'],
                    'anchor': {'hemi_number': anchor, 'transaction_index': anchor_index, 'bitcoin_height': low},
                    'publication': {key: value for key, value in wanted['publication'].items() if key != 'bitcoin_hash'},
                    'hemi_headers': [self.headers[n] for n in range(start, publisher + 1)],
                    'hemi_bodies': [anchor_body, pub_body], 'bitcoin_blocks': [], 'observations': []}
            self.store.root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='preparing-', dir=self.store.root) as temporary:
                packet = Path(temporary) / 'packet'
                (packet / 'interval/bitcoin').mkdir(parents=True)
                total = 0
                self.engine.progress(f'Preparing first-publication evidence: checking Bitcoin {low}–{high}…')
                for height in range(low, high + 1):
                    expected = anchor_hash if height == low else proof['bitcoin_block_hash'] if height == high else None
                    raw, row = self.bitcoin_body(height, expected)
                    total += len(raw)
                    require(total <= interval.MAX_TOTAL_BODY_BYTES, 'packet_body_budget', 'The complete Bitcoin interval exceeds the verification envelope', incomplete=True)
                    (packet / 'interval/bitcoin' / (row['hash'] + '.bin')).write_bytes(raw)
                    base['bitcoin_blocks'].append(row)
                    source = getattr(self.sources, 'last_btc_api', None) or receipt.get('sources', {}).get('bitcoin_apis', ['public Bitcoin source'])[0]
                    base['observations'].append({'source': source, 'height': height, 'hash': row['hash'], 'observed_at': now()})
                base_raw = json.dumps(base, separators=(',', ':')).encode()
                require(len(base_raw) <= interval.MAX_MANIFEST_BYTES, 'packet_size', 'The complete Hemi evidence exceeds the manifest envelope', incomplete=True)
                (packet / 'interval/manifest.json').write_bytes(base_raw)
                witnesses, history_issue = [], None
                for number in sorted({cover, publisher}):
                    try:
                        self.check()
                        proof_data = self.sources.rpc('eth_getProof', [HISTORY_ADDRESS, [hex((number - 1) % HISTORY_WINDOW)], hex(number)])
                        source = getattr(self.sources, 'last_evidence_rpc', None) or getattr(getattr(self.sources, 'hemi', None), 'active', None) or receipt.get('sources', {}).get('hemi_rpc_used') or receipt.get('sources', {}).get('hemi_rpc')
                        witnesses.append({'number': number, 'hash': self.headers[number]['hash'], 'proof': proof_data,
                                          'source': source or 'public Hemi RPC', 'observed_at': now()})
                    except (DataError, ValueError, KeyError, TypeError) as exc:
                        if isinstance(exc, (BudgetError, CollectionStopped)):
                            raise
                        history_issue = {'code': getattr(exc, 'code', 'history_witness_unavailable'),
                                         'message': 'A required history-state proof could not be obtained.'}
                        break
                outer = {'schema': SCHEMA, 'profile': PROFILE, 'query': wanted['query'],
                         'interval_manifest_sha256': hashlib.sha256(base_raw).hexdigest(), 'history_witnesses': witnesses}
                outer_raw = json.dumps(outer, separators=(',', ':')).encode()
                require(len(outer_raw) <= interval.MAX_MANIFEST_BYTES, 'packet_size', 'History witnesses exceed the manifest envelope', incomplete=True)
                (packet / 'manifest.json').write_bytes(outer_raw)
                # Re-observe all interval identities and route endpoints. These
                # are source checks only; the verifier does not select consensus.
                for row in base['bitcoin_blocks']:
                    self.check()
                    require(self.sources.btc('/block-height/' + str(row['height']), False) == row['hash'],
                            'source_view_conflict', 'Bitcoin source view changed while preparing evidence', incomplete=True)
                for number in sorted({anchor, wanted['query']['number'], cover, publisher}):
                    self.block(number, fresh=True)
                self.engine.progress('Checking first-publication evidence…')
                checked = verify_bounded(packet, wanted['query']['number'], wanted['query']['hash'],
                    POLICY_ID if self.store.policy == POLICY_ID else None, check=self.check)
                self.check()
                if checked['status'] not in ('invalid',) and checked.get('interval_evidence'):
                    self.store.publish(receipt, packet)
                issue = history_issue or next(iter(checked.get('issues', [])), {})
                return {'code': issue.get('code', checked.get('code', checked['status'])),
                        'message': issue.get('message', checked.get('message', 'The first-publication evidence check completed.')),
                        'status': checked['status'], 'result': checked}
        except CollectionStopped:
            raise
        except (VerificationError, DataError, OSError, ValueError, KeyError, TypeError) as exc:
            return {'status': getattr(exc, 'status', 'incomplete'), 'code': getattr(exc, 'code', 'evidence_preparation_incomplete'),
                    'message': str(exc) if isinstance(exc, (VerificationError, DataError)) else 'The stronger evidence package could not be completed.'}
