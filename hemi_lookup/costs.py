"""Source-free transaction costs from exact raw bytes and retained parent records.

Fees concern the whole transaction. No UTXO, witness-commitment, chain membership
or PoP-reward validation is asserted. Original receipts are never modified.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from .bitcoin import MAX_RAW_BLOCK, Reader, hex32, parse_transaction
from .catalogue import CATALOGUE_SCHEMA
from .errors import DataError

SCHEMA = 'traze-transaction-costs-v1'
MAX_MONEY = 21_000_000 * 100_000_000
MAX_TRANSACTIONS = 32
MAX_PARENTS = 32
MAX_INPUT_BYTES = 2_000_000
MAX_RECORD_BYTES = 500_000
MAX_SECONDS = .25
MAX_SQL_STEPS = 200_000


class CostLimit(Exception):
    pass


class CostInvalid(Exception):
    pass


class CostUnavailable(Exception):
    pass


class CostBudget:
    def __init__(self, external=None):
        self.deadline = time.monotonic() + MAX_SECONDS
        self.bytes = 0
        self.steps = 0
        self.external = external
        self.external_error = None

    def __call__(self, amount=0):
        if self.external:
            self.external(amount)
        self.bytes += amount
        if time.monotonic() >= self.deadline or self.bytes > MAX_INPUT_BYTES or self.steps > MAX_SQL_STEPS:
            raise CostLimit()

    def progress(self):
        self.steps += 1000
        try:
            if self.external and hasattr(self.external, 'progress') and self.external.progress():
                self.external_error = self.external.error
                return 1
            self()
            return 0
        except CostLimit:
            return 1
        except DataError as exc:
            self.external_error = exc
            return 1

    def trace(self, statement):
        self.steps += 1000
        if self.external and hasattr(self.external, 'trace'):
            self.external.trace(statement)


def transaction(raw_hex, expected_txid, check=None):
    if not isinstance(raw_hex, str) or len(raw_hex) > MAX_RAW_BLOCK * 2:
        raise CostInvalid()
    try:
        raw = bytes.fromhex(raw_hex)
        if check:
            check(len(raw))
        reader = Reader(raw, check)
        parsed = parse_transaction(reader)
        if reader.pos != len(raw) or parsed['txid'] != hex32(expected_txid).hex():
            raise CostInvalid()
        return parsed
    except (ValueError, TypeError):
        raise CostInvalid() from None
    except DataError as exc:
        if type(exc) is not DataError:  # Do not swallow the caller's deadline/error.
            raise
        raise CostInvalid() from None


def calculate(raw_hex, txid, parent_reader, check=None):
    """Recompute one fee; parent_reader returns exact raw hex or None by txid."""
    result = {'txid': txid, 'status': 'unavailable', 'reason': 'previous_outputs_unavailable',
              'size_bytes': None, 'weight_wu': None, 'vsize_vbytes': None, 'fee_sats': None,
              'previous_transactions_required': 0, 'previous_transactions_checked': 0,
              'segwit': False, 'witness_commitment_checked': False}
    try:
        tx = transaction(raw_hex, txid, check)
        result.update({key: tx[key] for key in ('size_bytes', 'weight_wu', 'vsize_vbytes', 'segwit')})
        result['raw_transaction_sha256'] = hashlib.sha256(tx['raw']).hexdigest()
        if tx['coinbase']:
            result['reason'] = 'coinbase_fee_not_applicable'
            return result
        outpoints = [(item['txid'], item['vout']) for item in tx['inputs']]
        if len(set(outpoints)) != len(outpoints):
            raise CostInvalid()
        if any(value > MAX_MONEY for value in tx['output_values']) or sum(tx['output_values']) > MAX_MONEY:
            raise CostInvalid()
        parents = {}
        result['previous_transactions_required'] = len({item[0] for item in outpoints})
        if result['previous_transactions_required'] > MAX_PARENTS:
            raise CostLimit()
        for parent_id, _ in outpoints:
            if parent_id in parents:
                continue
            if check:
                check()
            raw = parent_reader(parent_id)
            if raw is None:
                return result
            parent = transaction(raw, parent_id, check)
            if any(value > MAX_MONEY for value in parent['output_values']) or sum(parent['output_values']) > MAX_MONEY:
                raise CostInvalid()
            parents[parent_id] = parent['output_values']
            result['previous_transactions_checked'] += 1
        try:
            total_in = sum(parents[parent_id][vout] for parent_id, vout in outpoints)
        except IndexError:
            raise CostInvalid() from None
        fee = total_in - sum(tx['output_values'])
        if total_in > MAX_MONEY or fee < 0:
            raise CostInvalid()
        result.update(status='calculated', reason=None, fee_sats=fee)
    except CostLimit:
        result.update(status='unavailable', reason='cost_read_limit')
    except CostInvalid:
        result.update(status='invalid', reason='transaction_data_invalid')
    except (CostUnavailable, OSError, sqlite3.Error):
        result.update(status='unavailable', reason='previous_output_records_unavailable')
    return result


class CostService:
    def __init__(self, state_dir):
        self.state_dir = Path(state_dir).resolve()

    def assess(self, receipt, *, check=None):
        budget = CostBudget(check)
        catalogue = None
        database_checked = False
        parent_cache = {}
        results = []
        local = {p['txid']: p['raw_transaction_hex'] for p in receipt.get('proofs', [])
                 if isinstance(p.get('txid'), str) and isinstance(p.get('raw_transaction_hex'), str)}

        def read_parent(txid):
            nonlocal catalogue, database_checked
            budget()
            if txid in parent_cache:
                return parent_cache[txid]
            if len(parent_cache) >= MAX_PARENTS:
                raise CostLimit()
            if txid in local:
                parent_cache[txid] = local[txid]
                return parent_cache[txid]
            if not database_checked:
                database_checked = True
                for name in ('service.sqlite3', 'catalogue.sqlite3'):
                    path = self.state_dir / name
                    if path.exists():
                        # Never fall back from an existing unusable maintained index.
                        if path.is_symlink() or getattr(path, 'is_junction', lambda: False)() or not path.is_file():
                            break
                        catalogue = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.025)
                        catalogue.set_progress_handler(budget.progress, 1000)
                        catalogue.set_trace_callback(budget.trace)
                        if hasattr(catalogue, 'setlimit'):
                            catalogue.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_RECORD_BYTES + 1)
                        catalogue.execute('BEGIN')
                        schema = catalogue.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
                        if not schema or schema[0] != CATALOGUE_SCHEMA:
                            raise CostUnavailable()
                        break
            raw = None
            if catalogue:
                row = catalogue.execute('SELECT data FROM publications WHERE txid=? AND length(data)<=? ORDER BY id LIMIT 1',
                                           (txid, MAX_RECORD_BYTES)).fetchone()
                if row:
                    # TEXT affinity does not exclude BLOB values. Invalid
                    # optional metadata must not block the receipt.
                    if not isinstance(row[0], str):
                        raise CostUnavailable()
                    budget(len(row[0].encode('utf-8')))
                    data = json.loads(row[0])
                    if not isinstance(data, dict):
                        raise CostInvalid()
                    raw = data.get('raw_transaction_hex')
                    if not isinstance(raw, str):
                        raise CostInvalid()
            parent_cache[txid] = raw
            return raw

        def parent_reader(txid):
            try:
                return read_parent(txid)
            except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, RecursionError):
                if budget.external_error:
                    raise budget.external_error
                raise CostUnavailable() from None
            except DataError as exc:
                if type(exc) is not DataError:
                    raise
                raise CostUnavailable() from None

        cache = {}
        try:
            for index, proof in enumerate(receipt.get('proofs', [])[:128]):
                raw, txid = proof.get('raw_transaction_hex'), proof.get('txid')
                key = (txid if isinstance(txid, str) else None, raw if isinstance(raw, str) else None)
                if len(cache) >= MAX_TRANSACTIONS and key not in cache:
                    result = {'txid': txid, 'status': 'unavailable', 'reason': 'cost_read_limit', 'fee_sats': None}
                elif key in cache:
                    result = deepcopy(cache[key])
                else:
                    result = calculate(raw, txid, parent_reader, budget)
                    cache[key] = deepcopy(result)
                result['proof_index'] = index
                results.append(result)
        finally:
            if catalogue:
                catalogue.close()
        return {'schema': SCHEMA, 'transactions': results, 'scope': 'whole_transaction',
                'source_requests': 0, 'source_views_rechecked': False}
