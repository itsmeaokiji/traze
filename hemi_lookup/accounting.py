"""Explicit index-to-ledger binding; index reads never create accounting."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from .catalogue import encode, timestamp
from .errors import DataError
from .operating import check_checkpoint, checkpoint, ledger_state

KEY = 'collection_ledger_binding'


def accounting_directory(state, *, db=None):
    state = Path(state).resolve()
    if db is None:
        path = state / 'service.sqlite3'
        if not path.is_file():
            return state
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)) as connection:
            return accounting_directory(state, db=connection)
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='meta'").fetchone():
        return state
    row = db.execute('SELECT value FROM meta WHERE key=?', (KEY,)).fetchone()
    if row is None:
        return state  # Existing combined state remains compatible.
    try:
        binding = json.loads(row[0])
        directory = Path(binding['ledger_directory']).resolve()
        if (binding['schema'] != 'traze-index-ledger-v1'
                or binding['index_directory'] != str(state)
                or str(directory) != binding['ledger_directory']):
            raise ValueError('Index identity or ledger path changed')
        return directory
    except (ValueError, KeyError, TypeError) as exc:
        raise DataError('accounting_binding_changed', 'The saved index/accounting binding requires operator review') from exc


def bind_accounting(store, directory, root):
    """Bind once under the caller's index runner lock, without opening Session."""
    directory = Path(directory).resolve()
    if not (directory / 'session.json').is_file():
        raise DataError('missing_accounting', 'An explicit ledger binding requires an existing lifetime ledger')
    if ((directory / 'session.lock').exists() or (directory / 'restore-guard.json').exists()
            or (store.state / 'session.lock').exists()):
        raise DataError('session_locked', 'Reconcile active/restored accounting before binding an index')
    state = ledger_state(directory, root)
    existing = store.db.execute('SELECT value FROM meta WHERE key=?', (KEY,)).fetchone()
    if existing:
        if accounting_directory(store.state, db=store.db) != directory:
            raise DataError('accounting_binding_changed', 'An index cannot switch to a different lifetime ledger')
        check_checkpoint(state, json.loads(existing[0])['ledger_checkpoint'])
        return json.loads(existing[0])
    # Old entries predate closure fields. Pin them unchanged; a modern unclosed
    # segment is not inferred closed merely because no lock happens to exist.
    first = next((i for i, run in enumerate(state['runs']) if 'closed_at' in run), len(state['runs']))
    if (any('closed_at' not in run for run in state['runs'][first:])
            or first and (first == len(state['runs']) or any(
                request.get('status') == 'reserved' for run in state['runs'][:first] for request in run['requests']))):
        raise DataError('session_locked', 'Unclosed accounting needs operator reconciliation before binding')
    value = {'schema': 'traze-index-ledger-v1', 'created_at': timestamp(),
             'index_directory': str(store.state), 'ledger_directory': str(directory),
             'ledger_checkpoint': checkpoint(state)}
    with store.db:
        store.db.execute('INSERT INTO meta VALUES (?,?)', (KEY, encode(value)))
    return value


def accounting_state(state, *, db=None, prior_root=None):
    """Read and validate the pinned ledger prefix, including before new policies."""
    state = Path(state).resolve()
    if db is None and (state / 'service.sqlite3').is_file():
        with closing(sqlite3.connect((state / 'service.sqlite3').as_uri() + '?mode=ro', uri=True, timeout=1)) as connection:
            return accounting_state(state, db=connection, prior_root=prior_root)
    if db is None:
        return ledger_state(state, prior_root)
    directory = accounting_directory(state, db=db)
    row = (db.execute('SELECT value FROM meta WHERE key=?', (KEY,)).fetchone()
           if db.execute("SELECT 1 FROM sqlite_master WHERE name='meta'").fetchone() else None)
    if row and not (directory / 'session.json').is_file():
        raise DataError('missing_accounting', 'The pinned lifetime ledger must not be recreated')
    value = ledger_state(directory, prior_root)
    if row:
        check_checkpoint(value, json.loads(row[0])['ledger_checkpoint'])
    return value


def validate_service_accounting(store, service, requested=None):
    directory = accounting_directory(store.state, db=store.db)
    if requested is not None and Path(requested).resolve() != directory:
        raise DataError('accounting_binding_changed', 'Requested accounting differs from the saved index binding')
    if (service.get('ledger_directory', str(store.state)) != str(directory)
            or service.get('index_directory', str(store.state)) != str(store.state)):
        raise DataError('accounting_binding_changed', 'The service does not belong to this index/accounting pair')
    if not (directory / 'session.json').is_file() and directory != store.state:
        raise DataError('missing_accounting', 'The bound lifetime ledger is unavailable; it must not be recreated')
    if (directory / 'restore-guard.json').exists():
        raise DataError('restored_state_requires_review', 'Reconcile restored accounting before source acquisition')
    accounting_state(store.state, db=store.db)
    return directory
