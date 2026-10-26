"""Read-only discovery of local browser receipts; listing never verifies proofs.

Keyset pages use file-save order, not source-observation freshness. The cursor
contains an upper bound so newly saved files do not displace older pages. No
archive, receipt, accounting record or in-memory job is modified by a read.
"""
import base64
import binascii
import json
import re
from pathlib import Path

from .cli import load_receipt
from .errors import DataError
from .explorer import ReadBudget, ReadError

PAGE_SIZE = 10
MAX_FILES = 10_000
FILE_ID = re.compile(r"web-([0-9a-f]{32})\.json\Z")
HASH = re.compile(r"0x[0-9a-f]{64}\Z")
STATUSES = ('found', 'not_found_within_coverage', 'incomplete')


def _key(value):
    return (isinstance(value, list) and len(value) == 2
            and type(value[0]) is int and 0 <= value[0] < 2**64
            and isinstance(value[1], str) and re.fullmatch(r'[0-9a-f]{32}', value[1]))


def _decode(cursor):
    if not isinstance(cursor, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', cursor):
        raise ReadError('invalid_history_cursor', 'Reopen saved history to start again.')
    try:
        value = json.loads(base64.b64decode(cursor + '=' * (-len(cursor) % 4), altchars=b'-_', validate=True))
        if (not isinstance(value, list) or len(value) != 3 or value[0] != 'saved-v1'
                or not _key(value[1]) or not _key(value[2]) or value[2] > value[1]):
            raise ValueError()
        return tuple(value[1]), tuple(value[2])
    except (ValueError, TypeError, UnicodeError, binascii.Error):
        raise ReadError('invalid_history_cursor', 'Reopen saved history to start again.') from None


def _encode(anchor, after):
    raw = json.dumps(['saved-v1', anchor, after], separators=(',', ':')).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def summary(identity, receipt, *, label='Browser result', origin='browser_saved'):
    """Validate display metadata only; a saved outcome is not a verified claim."""
    query = receipt['query']
    number, block_hash = query['block_number'], query.get('requested_block_hash')
    if (number is not None and (type(number) is not int or not 0 < number <= 0xffffffff)
            or block_hash is not None and (not isinstance(block_hash, str) or not HASH.fullmatch(block_hash))
            or receipt['status'] not in STATUSES):
        raise ValueError('Invalid saved metadata')
    mode = receipt.get('mode')
    if mode is not None and (not isinstance(mode, str) or len(mode) > 64):
        raise ValueError('Invalid saved operation')
    completed = receipt.get('completed_at')
    if completed is not None and (not isinstance(completed, str) or len(completed) > 64):
        raise ValueError('Invalid saved date')
    discovery = receipt.get('discovery') or {}
    segment = discovery.get('segment')
    if segment is not None and (type(segment) is not int or not 1 <= segment <= 128):
        raise ValueError('Invalid search segment')
    return {'id': identity, 'label': label, 'origin': origin, 'block_number': number,
            'requested_block_hash': block_hash, 'status': receipt['status'],
            'completed_at': completed, 'mode': mode, 'segment': segment, 'available': True}


def retained_files(state_dir, *, check=None):
    """Enumerate one bounded, deterministic file view for history or query reuse."""
    check = check or ReadBudget()
    directory = Path(state_dir) / 'receipts'
    files = []
    if directory.exists():
        # Bound enumeration as well as metadata decoding. Exceeding the envelope
        # fails explicitly; it must never look like the end of a complete list.
        for count, path in enumerate(directory.iterdir(), 1):
            check()
            if count > MAX_FILES:
                raise ReadError('history_capacity', 'Saved history exceeds this local listing capacity.', 503)
            match = FILE_ID.fullmatch(path.name)
            if not match:
                continue
            try:
                key = (path.lstat().st_mtime_ns, match[1])
            except FileNotFoundError:
                continue  # Removed before this page was read; never invent metadata.
            files.append((key, path))
    files.sort(key=lambda item: item[0], reverse=True)
    check()
    return files


def read_saved(path, state_dir, check):
    directory = Path(state_dir) / 'receipts'
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(directory.resolve()):
        raise ValueError('Unsafe saved path')
    size = path.stat().st_size
    if size > 10_000_000:
        raise ValueError('Oversized saved file')
    check(size)
    value = load_receipt(path)
    check()
    return value


def retained_receipts(state_dir, *, check):
    """One shared read budget; partial enumeration must not imply a cache miss."""
    for key, path in retained_files(state_dir, check=check):
        try:
            value = read_saved(path, state_dir, check)
        except ReadError:
            raise
        except (DataError, OSError, ValueError, KeyError, TypeError):
            continue
        yield path, value


def history_page(state_dir, cursor=None, *, check=None):
    check = check or ReadBudget()
    anchor, after = _decode(cursor) if cursor is not None else (None, None)
    files = [item for item in retained_files(state_dir, check=check)
             if anchor is None or item[0] <= anchor and item[0] < after]
    if anchor is None and files:
        anchor = files[0][0]
    items = []
    for key, path in files[:PAGE_SIZE]:
        identity = key[1]
        try:
            item = summary(identity, read_saved(path, state_dir, check))
        except ReadError:
            raise
        except (DataError, OSError, ValueError, KeyError, TypeError, AttributeError):
            item = {'id': identity, 'label': 'Unavailable saved result', 'origin': 'browser_saved',
                    'block_number': None, 'status': None, 'available': False}
        items.append(item)
    return {'items': items, 'page': {'limit': PAGE_SIZE,
            'next_cursor': _encode(anchor, files[PAGE_SIZE - 1][0]) if len(files) > PAGE_SIZE else None},
            'order': 'saved_file_desc', 'metadata_only': True}
