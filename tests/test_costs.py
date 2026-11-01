"""Transaction-cost checks using explicit synthetic vectors and retained evidence."""
from copy import deepcopy
from contextlib import closing
import json
from pathlib import Path
import socket
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from hemi_lookup.bitcoin import Reader, parse_transaction
from hemi_lookup.catalogue import build_catalogue
from hemi_lookup.catalogue import encode
from hemi_lookup.costs import CostService, MAX_MONEY, calculate
from hemi_lookup.errors import DataError, DeadlineError
from hemi_lookup.explorer import Explorer, ReadBudget, MAX_INPUT_BYTES
from hemi_lookup.web import Jobs

ROOT = Path(__file__).resolve().parents[1]


def vector(inputs, values, witness=None):
    """Small synthetic serializations; these vectors are not live chain data."""
    raw = b'\x02\x00\x00\x00' + (b'\x00\x01' if witness is not None else b'')
    raw += bytes([len(inputs)])
    for txid, vout in inputs:
        raw += bytes.fromhex(txid)[::-1] + vout.to_bytes(4, 'little') + b'\x00' + b'\xff' * 4
    raw += bytes([len(values)])
    for amount in values:
        raw += amount.to_bytes(8, 'little') + b'\x01\x51'
    if witness is not None:
        for _ in inputs:
            raw += b'\x01' + bytes([len(witness)]) + witness
    return raw + b'\x00' * 4


def parsed(raw):
    return parse_transaction(Reader(raw))


class CostCalculationTests(unittest.TestCase):
    def setUp(self):
        self.parent = vector([('11' * 32, 0)], [1000, 2000])
        self.parent_id = parsed(self.parent)['txid']
        self.raw = vector([(self.parent_id, 0)], [875])
        self.txid = parsed(self.raw)['txid']
        self.parents = {self.parent_id: self.parent.hex()}

    def cost(self, raw=None, parents=None):
        raw = self.raw if raw is None else raw
        parents = self.parents if parents is None else parents
        return calculate(raw.hex(), parsed(raw)['txid'], parents.get)

    def test_legacy_fee_and_size_use_actual_input_output_values(self):
        result = self.cost()
        self.assertEqual(result['status'], 'calculated')
        self.assertEqual(result['fee_sats'], 125)
        self.assertEqual(result['vsize_vbytes'], len(self.raw))
        self.assertEqual(result['weight_wu'], len(self.raw) * 4)

    def test_segwit_weight_and_rounding_bind_supplied_witness_bytes(self):
        a = vector([(self.parent_id, 0)], [875], b'\x01')
        b = vector([(self.parent_id, 0)], [875], b'\x01' * 17)
        self.assertEqual(parsed(a)['txid'], parsed(b)['txid'])
        ca, cb = self.cost(a), self.cost(b)
        self.assertEqual(ca['weight_wu'], len(self.raw) * 3 + len(a))
        self.assertEqual(ca['vsize_vbytes'], (ca['weight_wu'] + 3) // 4)
        self.assertGreater(cb['vsize_vbytes'], ca['vsize_vbytes'])
        self.assertEqual(ca['fee_sats'], cb['fee_sats'])
        self.assertFalse(ca['witness_commitment_checked'])
        self.assertNotEqual(ca['raw_transaction_sha256'], cb['raw_transaction_sha256'])

    def test_missing_parent_never_becomes_zero_fee(self):
        result = self.cost(parents={})
        self.assertEqual(result['status'], 'unavailable')
        self.assertIsNone(result['fee_sats'])
        self.assertEqual(result['vsize_vbytes'], len(self.raw))

    def test_altered_parent_and_invalid_output_index_are_rejected(self):
        other = vector([('22' * 32, 0)], [1000])
        self.assertEqual(self.cost(parents={self.parent_id: other.hex()})['status'], 'invalid')
        self.assertEqual(self.cost(vector([(self.parent_id, 9)], [10]))['status'], 'invalid')

    def test_negative_fee_duplicate_inputs_and_money_overflow_are_rejected(self):
        for raw in (vector([(self.parent_id, 0)], [1001]),
                    vector([(self.parent_id, 0), (self.parent_id, 0)], [10]),
                    vector([(self.parent_id, 0)], [MAX_MONEY + 1])):
            with self.subTest(raw=raw.hex()):
                result = self.cost(raw)
                self.assertEqual(result['status'], 'invalid')
                self.assertIsNone(result['fee_sats'])

    def test_zero_fee_and_multiple_outputs_are_not_divided_per_publication(self):
        self.assertEqual(self.cost(vector([(self.parent_id, 0)], [1000]))['fee_sats'], 0)
        result = self.cost(vector([(self.parent_id, 0)], [400, 475]))
        self.assertEqual(result['fee_sats'], 125)
        self.assertEqual(result['previous_transactions_checked'], 1)

    def test_shared_parent_distinct_outpoints_count_each_value_once(self):
        result = self.cost(vector([(self.parent_id, 0), (self.parent_id, 1)], [2875]))
        self.assertEqual(result['fee_sats'], 125)
        self.assertEqual(result['previous_transactions_checked'], 1)

    def test_raw_binding_truncation_and_coinbase_have_no_false_fee(self):
        self.assertEqual(calculate(self.raw.hex(), '33' * 32, self.parents.get)['status'], 'invalid')
        self.assertEqual(calculate(self.raw[:-1].hex(), self.txid, self.parents.get)['status'], 'invalid')
        coinbase = vector([('00' * 32, 0xffffffff)], [1000])
        result = self.cost(coinbase)
        self.assertEqual(result['reason'], 'coinbase_fee_not_applicable')
        self.assertIsNone(result['fee_sats'])

    def test_receipt_candidates_bind_size_by_raw_bytes_even_with_shared_txid(self):
        a = vector([(self.parent_id, 0)], [875], b'\x01')
        b = vector([(self.parent_id, 0)], [875], b'\x01' * 17)
        receipt = {'proofs': [{'txid': parsed(raw)['txid'], 'raw_transaction_hex': raw.hex()} for raw in (a, b, self.parent)]}
        before = deepcopy(receipt)
        with tempfile.TemporaryDirectory() as temp:
            result = CostService(temp).assess(receipt)
        self.assertEqual(result['transactions'][0]['fee_sats'], 125)
        self.assertNotEqual(result['transactions'][0]['vsize_vbytes'], result['transactions'][1]['vsize_vbytes'])
        self.assertEqual(receipt, before)

    def test_optional_limits_preserve_size_and_caller_deadline_is_not_swallowed(self):
        with tempfile.TemporaryDirectory() as temp:
            service = CostService(temp)
            receipt = {'proofs': [{'txid': self.txid, 'raw_transaction_hex': self.raw.hex()}]}
            with patch('hemi_lookup.costs.MAX_INPUT_BYTES', 1):
                self.assertEqual(service.assess(receipt)['transactions'][0]['reason'], 'cost_read_limit')
            def expired(*args):
                raise DeadlineError()
            with self.assertRaises(DeadlineError):
                service.assess(receipt, check=expired)


class RetainedCostIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.state = Path(cls.temp.name)
        cls.database = cls.state / 'catalogue.sqlite3'
        build_catalogue(ROOT, cls.database, block_directory=ROOT / 'tests/fixtures/bitcoin_blocks')

    def test_cost_reads_share_existing_decoded_input_and_sql_budgets(self):
        receipt = json.loads((ROOT / 'demo_results/mainnet_5243117.json').read_text())
        budget = ReadBudget()
        CostService(self.state).assess(receipt, check=budget)
        self.assertGreater(budget.bytes, 0)
        self.assertGreater(budget.steps, 0)
        budget = ReadBudget()
        budget.bytes = MAX_INPUT_BYTES - 1
        with self.assertRaises(DataError):
            CostService(self.state).assess(receipt, check=budget)

    def test_actual_indexed_and_saved_costs_and_receipt_preservation(self):
        with patch.object(socket.socket, 'connect', side_effect=AssertionError('No source request')):
            result = Explorer(self.state).read('blocks/5243119', {})
            costs = result['transaction_costs']['transactions']
            cost = costs[0]
            self.assertEqual((cost['fee_sats'], cost['vsize_vbytes']), (125, 283))
            self.assertEqual(result['transaction_costs']['source_requests'], 0)
            detail = Explorer(self.state).read('publications/' + result['receipt']['proofs'][0]['publication_id'], {})
            self.assertEqual(detail['transaction_cost']['fee_sats'], 125)
            jobs = Jobs(ROOT, state_dir=self.state)
            before = (ROOT / 'demo_results/mainnet_5243117.json').read_bytes()
            saved = jobs.receipt('example-ordinary', include_finality=False)
            self.assertEqual(saved['transaction_costs']['transactions'][0]['fee_sats'], 125)
            self.assertEqual((ROOT / 'demo_results/mainnet_5243117.json').read_bytes(), before)
            self.assertNotIn('transaction_costs', saved['receipt'])
            self.assertFalse((self.state / 'session.json').exists())

    def test_old_index_corruption_and_missing_data_do_not_mask_positive_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            shutil.copyfile(self.database, state / 'catalogue.sqlite3')
            with closing(sqlite3.connect(state / 'catalogue.sqlite3')) as database:
                database.execute('DROP INDEX publication_txid')
                database.commit()
            raw = json.loads((ROOT / 'demo_results/mainnet_5243117.json').read_text())
            raw['proofs'] = raw['proofs'][:1]
            self.assertEqual(CostService(state).assess(raw)['transactions'][0]['fee_sats'], 125)
            (state / 'service.sqlite3').write_bytes(b'not a database')
            costs = CostService(state).assess(raw)
            self.assertIsNone(costs['transactions'][0]['fee_sats'])
            self.assertEqual(costs['transactions'][0]['status'], 'unavailable')

    def test_changed_or_removed_parent_data_cannot_reuse_an_old_cost(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            shutil.copyfile(self.database, state / 'catalogue.sqlite3')
            receipt = json.loads((ROOT / 'demo_results/mainnet_5243117.json').read_text())
            receipt['proofs'] = receipt['proofs'][:1]
            service = CostService(state)
            self.assertEqual(service.assess(receipt)['transactions'][0]['fee_sats'], 125)
            target = parsed(bytes.fromhex(receipt['proofs'][0]['raw_transaction_hex']))
            parent = target['inputs'][0]['txid']
            with closing(sqlite3.connect(state / 'catalogue.sqlite3')) as database:
                database.execute('UPDATE publications SET data=? WHERE txid=?',
                    (encode({'raw_transaction_hex': receipt['proofs'][0]['raw_transaction_hex']}), parent))
                database.commit()
            self.assertEqual(service.assess(receipt)['transactions'][0]['status'], 'invalid')
            with closing(sqlite3.connect(state / 'catalogue.sqlite3')) as database:
                database.execute('DELETE FROM publications WHERE txid=?', (parent,))
                database.commit()
            missing = service.assess(receipt)['transactions'][0]
            self.assertEqual(missing['status'], 'unavailable')
            self.assertIsNone(missing['fee_sats'])


if __name__ == '__main__':
    unittest.main()
