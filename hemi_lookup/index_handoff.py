"""Durable, bounded offline handoff from immutable browser receipts to the index."""
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
import time

from .catalogue import Catalogue, timestamp
from .collection_store import RunnerLock
from .errors import DataError
from .explorer import ReadBudget, ReadError
from .index_ingestion import ingest, receipt_data
from .storage import atomic_json

ID = re.compile(r'[0-9a-f]{32}\Z')
SHA = re.compile(r'[0-9a-f]{64}\Z')
SCHEMA = 'traze-index-handoff-v1'
MAX_AUTOMATIC_ATTEMPTS = 8


class IndexHandoff:
    def __init__(self, state_dir):
        self.state = Path(state_dir).resolve()
        self.directory = self.state / 'index-updates'
        self.guard = threading.Lock()
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.thread = None

    def receipt_path(self, identity):
        if not isinstance(identity, str) or not ID.fullmatch(identity):
            raise DataError('invalid_index_update', 'Use an exact browser receipt identifier')
        path = self.state / 'receipts' / f'web-{identity}.json'
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(self.state / 'receipts'):
            raise DataError('saved_receipt_unavailable', 'The saved browser receipt is unavailable')
        return path

    def queue_path(self, identity):
        if not isinstance(identity, str) or not ID.fullmatch(identity):
            raise DataError('invalid_index_update', 'Use an exact browser receipt identifier')
        if self.directory.is_symlink() or not self.directory.resolve().is_relative_to(self.state):
            raise DataError('invalid_index_update', 'The local index queue path is unavailable')
        return self.directory / f'{identity}.json'

    def read(self, identity):
        path = self.queue_path(identity)
        if not path.exists():
            return None
        if path.is_symlink() or path.stat().st_size > 16_000:
            raise DataError('invalid_index_update', 'The saved index handoff is invalid')
        value = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(value, dict) or value.get('schema') != SCHEMA or value.get('receipt_id') != identity
                or not isinstance(value.get('receipt_sha256'), str) or not SHA.fullmatch(value['receipt_sha256'])
                or type(value.get('attempts')) is not int or not 0 <= value['attempts'] <= 1_000_000):
            raise DataError('invalid_index_update', 'The saved index handoff does not match its receipt')
        return value

    def active(self):
        # Match Explorer.database exactly. A bad maintained index is never
        # silently replaced by a different catalogue or a new empty database.
        for name in ('service.sqlite3', 'catalogue.sqlite3'):
            path = self.state / name
            if path.exists():
                if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(self.state):
                    raise DataError('index_unavailable', 'The active index path requires operator review')
                return path
        return self.state / 'catalogue.sqlite3'

    def enqueue(self, identity):
        path = self.receipt_path(identity)
        if path.stat().st_size > 10_000_000:
            raise DataError('receipt_too_large', 'Saved receipt exceeds the handoff input bound')
        raw = path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        receipt_data(path, sha, ReadBudget())
        value = self.read(identity)
        if value:
            if value['receipt_sha256'] != sha:
                raise DataError('receipt_changed', 'This immutable saved receipt changed after handoff')
        else:
            value = {'schema': SCHEMA, 'receipt_id': identity, 'receipt_sha256': sha,
                     'state': 'pending', 'reason': 'queued', 'attempts': 0,
                     'created_at': timestamp(), 'updated_at': timestamp()}
            atomic_json(self.queue_path(identity), value)
        self.wake.set()
        return self.status(identity)

    def status(self, identity):
        value = self.read(identity)
        if value is None:
            return {'state': 'not_queued', 'can_retry': True, 'source_requests': 0}
        path = self.receipt_path(identity)
        if path.stat().st_size > 10_000_000 or hashlib.sha256(path.read_bytes()).hexdigest() != value['receipt_sha256']:
            return {'state': 'rejected', 'reason': 'receipt_changed', 'can_retry': False, 'source_requests': 0}
        try:
            active = self.active()
            if active.is_file():
                check = ReadBudget()
                with Catalogue(active, readonly=True, check=check) as catalogue:
                    exists = catalogue.db.execute("SELECT 1 FROM sqlite_master WHERE name='receipt_ingestions'").fetchone()
                    row = catalogue.db.execute('SELECT result FROM receipt_ingestions WHERE receipt_sha256=?',
                                               (value['receipt_sha256'],)).fetchone() if exists else None
                    if row:
                        result = catalogue.read_json(row[0])
                        identities = result.get('publication_ids', [])
                        if (result.get('receipt_sha256') != value['receipt_sha256']
                                or result.get('state') not in ('indexed', 'already_indexed')
                                or not isinstance(identities, list) or len(identities) > 128
                                or any(not isinstance(item, str) or not SHA.fullmatch(item) for item in identities)):
                            raise DataError('index_unavailable', 'The saved index result requires review')
                        if identities:
                            marks = ','.join('?' for _ in identities)
                            found = catalogue.db.execute('SELECT COUNT(DISTINCT p.id) FROM publications p '
                                'JOIN associations a ON a.publication_id=p.id WHERE p.id IN (' + marks + ')', identities).fetchone()[0]
                            if found != len(identities):
                                return {'state': 'pending', 'reason': 'index_unavailable', 'can_retry': True, 'source_requests': 0, 'automatic_retry_pending': False}
                        return {**result, 'can_retry': True}
        except (DataError, OSError, ValueError, sqlite3.Error):
            return {'state': 'pending', 'reason': 'index_unavailable', 'can_retry': True, 'source_requests': 0, 'automatic_retry_pending': False}
        if value['state'] in ('indexed', 'already_indexed'):
            return {'state': 'pending', 'reason': 'active_index_changed', 'can_retry': True,
                    'automatic_retry_pending': False, 'source_requests': 0}
        return {'state': value['state'] if value['state'] == 'rejected' else 'pending',
                'reason': value.get('reason', 'queued'), 'attempts': value['attempts'],
                'can_retry': value['state'] != 'rejected', 'source_requests': 0,
                'automatic_retry_pending': value['state'] == 'pending' and value['attempts'] < MAX_AUTOMATIC_ATTEMPTS
                    and value.get('reason') in ('queued', 'working', 'index_busy', 'ingest_capacity')}

    def process(self, identity, *, manual=False):
        if not self.guard.acquire(blocking=False):
            return
        validated = False
        try:
            value = self.read(identity)
            if value is None or value['state'] == 'rejected':
                return
            if not manual and (value['state'] in ('indexed', 'already_indexed') or value['attempts'] >= MAX_AUTOMATIC_ATTEMPTS):
                return
            value.update(state='pending', reason='working', attempts=value['attempts'] + 1, updated_at=timestamp())
            atomic_json(self.queue_path(identity), value)
            check = ReadBudget()
            # Validate before opening any writable database.
            path = self.receipt_path(identity)
            receipt_data(path, value['receipt_sha256'], check)
            validated = True
            with RunnerLock(self.state):
                active = self.active()
                if active.exists():
                    with Catalogue(active, readonly=True, check=check):
                        pass
                with Catalogue(active, check=check) as catalogue:
                    catalogue.db.execute('PRAGMA busy_timeout=50')
                    result = ingest(catalogue, path, value['receipt_sha256'], self.state / 'blocks', check)
            value.update(state=result['state'], reason='applied', index_name=active.name,
                         result=result, updated_at=timestamp())
        except (DataError, OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
            if 'value' not in locals() or value is None:
                return
            code = getattr(exc, 'code', 'index_unavailable')
            rejected = code in ('receipt_changed', 'invalid_receipt', 'saved_receipt_unavailable', 'receipt_too_large') or code == 'invalid_proof' and not validated
            reason = 'index_busy' if code == 'collector_busy' or isinstance(exc, sqlite3.OperationalError) and 'locked' in str(exc).lower() else 'ingest_capacity' if code == 'read_capacity' else code
            value.update(state='rejected' if rejected else 'pending', reason=reason, updated_at=timestamp())
        finally:
            if 'value' in locals() and value is not None:
                try:
                    atomic_json(self.queue_path(identity), value)
                except (DataError, OSError, ValueError):
                    pass  # The receipt/SQL commit survives a failed queue-status write.
            self.guard.release()

    def retry(self, identity):
        self.enqueue(identity)
        self.process(identity, manual=True)
        return self.status(identity)

    def start(self):
        if self.thread is not None:
            return
        self.thread = threading.Thread(target=self.run, name='traze-local-index-handoff', daemon=True)
        self.thread.start()

    def run(self):
        while not self.stop.is_set():
            self.wake.wait(2)
            self.wake.clear()
            try:
                if not self.directory.is_dir():
                    continue
                for count, path in enumerate(self.directory.iterdir(), 1):
                    if self.stop.is_set() or count > 10_000:
                        break
                    if not ID.fullmatch(path.stem) or path.suffix != '.json':
                        continue
                    value = self.read(path.stem)
                    if (value and value['state'] == 'pending' and value['attempts'] < MAX_AUTOMATIC_ATTEMPTS
                            and value.get('reason') in ('queued', 'working', 'index_busy', 'ingest_capacity')):
                        self.process(path.stem)
            except (DataError, OSError, ValueError, KeyError, TypeError):
                continue

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.thread:
            self.thread.join(7)
