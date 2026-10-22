"""Bounded process-local reuse of mechanically checked snapshot evidence.

Only captured required bytes and exact caller/rule identities key an entry.
The caller still qualifies receipts and source conflicts on every request.
This is recorded-byte reuse, not a fresh source or canonical-chain observation.
The runtime, source tree and temporary directory are operator-owned.
"""

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import threading
import time

from traze_evidence import snapshot
from traze_evidence import verify as interval
from traze_evidence.history import POLICY_ID as HISTORY_POLICY
from traze_evidence.errors import VerificationError

from .errors import DeadlineError

MAX_ENTRIES = 4
MAX_RESULT_BYTES = 2_000_000
MAX_CAPTURE_BYTES = 3 * interval.MAX_MANIFEST_BYTES + interval.MAX_TOTAL_BODY_BYTES
CHUNK = 65_536
HEX = re.compile(r'[0-9a-f]{64}\Z')


def _json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _rule_files():
    root = Path(__file__).resolve().parents[1]
    return tuple(sorted((root / 'traze_evidence').glob('*.py'))) + (
        Path(__file__).resolve(), root / 'hemi_lookup/assurance.py', root / 'hemi_lookup/evidence_worker.py')


@dataclass(frozen=True)
class _Capture:
    files: tuple
    digest: str


class PacketResultCache:
    """One capture operation; at most four entries / two MB of result JSON.

    No packet bodies or success records persist across requests or restarts.
    Busy/disabled reuse and unavailable scratch storage use the original bounded
    verifier. Invalid evidence and changed rules never borrow cached success.
    """

    def __init__(self, *, enabled=True, max_entries=MAX_ENTRIES, max_bytes=MAX_RESULT_BYTES):
        if (type(max_entries) is not int or not 0 < max_entries <= MAX_ENTRIES
                or type(max_bytes) is not int or not 0 < max_bytes <= MAX_RESULT_BYTES):
            raise ValueError('Packet-result limits may only be narrowed.')
        self.max_entries, self.max_bytes = max_entries, max_bytes
        self._entries = OrderedDict()
        self._size = 0
        self._slot = threading.Lock()
        self._files = _rule_files()
        self._invalidated = False
        # Pin at construction/import, before requests. Source updates require a
        # process restart. Missing source disables this optional optimization.
        self._identity = None
        if enabled:
            try:
                self._identity = self._rules(lambda: None)
            except OSError:
                pass

    def _rules(self, guard):
        rows = []
        for path in self._files:
            guard()
            with path.open('rb') as handle:
                raw = handle.read(2_000_001)
            if len(raw) > 2_000_000:
                raise OSError('Verifier source exceeds the source-identity bound.')
            rows.append([str(path), hashlib.sha256(raw).hexdigest()])
        guard()
        return hashlib.sha256(_json_bytes([sys.version, sys.implementation.cache_tag, sys.byteorder, rows])).hexdigest()

    def _check_rules(self, guard):
        if self._invalidated:
            raise VerificationError('packet_rules_changed', 'Verifier identity changed. Restart the service before reuse.', incomplete=True)
        try:
            unchanged = self._rules(guard) == self._identity
        except OSError as exc:
            self._invalidated = True
            raise VerificationError('packet_rules_changed', 'Verifier identity is unavailable. Restart the service before reuse.', incomplete=True) from exc
        if not unchanged:
            self._invalidated = True
            raise VerificationError('packet_rules_changed', 'Verifier rules changed. Restart the service before reuse.', incomplete=True)

    @staticmethod
    def _path(packet, relative):
        path = packet / relative
        for part in (path, *path.parents):
            info = part.lstat()
            if (stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) &
                    getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)):
                raise VerificationError('packet_path', 'Linked/reparse evidence paths are unsupported.')
        if not path.resolve().is_relative_to(packet.resolve()) or not stat.S_ISREG(path.stat().st_mode):
            raise VerificationError('packet_path', 'Evidence must be a regular file inside the packet.')
        return path

    def _read(self, packet, relative, limit, guard):
        guard()
        try:
            path = self._path(packet, relative)
            before = path.stat()
            if before.st_size > limit:
                raise VerificationError('packet_size', 'Evidence exceeds its file bound.')
            pieces, count = [], 0
            with path.open('rb') as handle:
                opened = os.fstat(handle.fileno())
                if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                    raise VerificationError('evidence_snapshot_changed', 'Evidence changed during capture.')
                while True:
                    guard()
                    piece = handle.read(min(CHUNK, limit + 1 - count))
                    if not piece:
                        break
                    count += len(piece)
                    if count > limit:
                        raise VerificationError('packet_size', 'Evidence exceeds its file bound.')
                    pieces.append(piece)
                after = os.fstat(handle.fileno())
            current = self._path(packet, relative).stat()
            common = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
            # Windows/Python 3.12 path-stat and handle-stat may disagree on ctime.
            # Compare it within each API; full bytes, not metadata, key reuse.
            if (common(opened) != common(after) or common(after) != common(current)
                    or before.st_ctime_ns != current.st_ctime_ns or opened.st_ctime_ns != after.st_ctime_ns):
                raise VerificationError('evidence_snapshot_changed', 'Evidence changed during capture.')
            guard()
            return b''.join(pieces)
        except FileNotFoundError as exc:
            raise VerificationError('packet_missing', 'Required evidence is missing.', incomplete=True) from exc

    @staticmethod
    def _manifest(raw):
        try:
            value = json.loads(raw, object_pairs_hook=interval._unique_pairs,
                               parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError, UnicodeError, RecursionError) as exc:
            if isinstance(exc, VerificationError):
                raise
            raise VerificationError('packet_json', 'Invalid captured evidence manifest.') from exc
        if not isinstance(value, dict):
            raise VerificationError('packet_shape', 'Expected an object evidence manifest.')
        return value

    def _capture(self, packet, guard):
        files, total, bodies = [], 0, 0

        def take(relative, limit, *, body=False):
            nonlocal total, bodies
            raw = self._read(packet, relative, limit, guard)
            total += len(raw)
            bodies += len(raw) if body else 0
            if total > MAX_CAPTURE_BYTES or bodies > interval.MAX_TOTAL_BODY_BYTES:
                raise VerificationError('packet_size', 'Evidence exceeds the total capture bound.')
            files.append((relative, raw))
            return raw

        outer = self._manifest(take('manifest.json', interval.MAX_MANIFEST_BYTES))
        if outer.get('schema') != snapshot.SCHEMA:
            raise VerificationError('evidence_snapshot_changed', 'The retained packet format changed during the read.')
        take('history/manifest.json', interval.MAX_MANIFEST_BYTES)
        inner = self._manifest(take('history/interval/manifest.json', interval.MAX_MANIFEST_BYTES))
        blocks = inner.get('bitcoin_blocks')
        if not isinstance(blocks, list) or not 1 <= len(blocks) <= interval.MAX_BITCOIN_BLOCKS:
            raise VerificationError('packet_shape', 'Invalid bounded Bitcoin body list.')
        seen = set()
        for row in blocks:
            guard()
            block_hash = row.get('hash') if isinstance(row, dict) else None
            if not isinstance(block_hash, str) or not HEX.fullmatch(block_hash) or block_hash in seen:
                raise VerificationError('packet_path', 'Invalid/duplicate Bitcoin body identity.')
            seen.add(block_hash)
            take('history/interval/bitcoin/' + block_hash + '.bin', interval.MAX_BLOCK_BYTES, body=True)
        digest = hashlib.sha256(_json_bytes([[path, len(raw), hashlib.sha256(raw).hexdigest()]
                                             for path, raw in sorted(files)])).hexdigest()
        guard()
        return _Capture(tuple(files), digest)

    def verify(self, packet, number, hemi_hash, policy, *, verifier, check=None, accepted_context_sha256=None):
        deadline = time.monotonic() + 20

        def guard():
            if check is not None:
                check()
            if time.monotonic() >= deadline:
                raise DeadlineError('First-publication verification time limit reached')

        def original():
            guard()
            result = verifier(packet, number, hemi_hash, policy, check=guard,
                              accepted_context_sha256=accepted_context_sha256)
            guard()
            if self._identity is not None:
                self._check_rules(guard)
            return result

        guard()
        if self._identity is None:
            return original()
        self._check_rules(guard)
        if not self._slot.acquire(blocking=False):
            # Do full bounded verification instead of waiting or trusting a
            # not-yet-verified result. Existing server reader limits still apply.
            return original()
        try:
            if (type(number) is not int or not 1 <= number <= 0xffffffff
                    or not isinstance(hemi_hash, str) or not re.fullmatch(r'0x[0-9a-f]{64}', hemi_hash)
                    or policy not in (None, HISTORY_POLICY, snapshot.POLICY_ID)
                    or accepted_context_sha256 is not None and
                       (not isinstance(accepted_context_sha256, str) or not HEX.fullmatch(accepted_context_sha256))):
                raise VerificationError('packet_request', 'Invalid exact packet-check request.')
            packet = Path(packet).absolute()
            first = self._capture(packet, guard)
            key = hashlib.sha256(_json_bytes({'packet': first.digest, 'rules': self._identity,
                'number': number, 'hash': hemi_hash, 'policy': policy,
                'accepted_context_sha256': accepted_context_sha256})).hexdigest()
            if key in self._entries:
                if first.digest != self._capture(packet, guard).digest:
                    raise VerificationError('evidence_snapshot_changed', 'The evidence changed while checking.')
                self._check_rules(guard)
                guard()
                raw = self._entries.pop(key)
                self._entries[key] = raw
                return json.loads(raw)

            try:
                with tempfile.TemporaryDirectory(prefix='traze-packet-') as temporary:
                    pinned = Path(temporary) / 'packet'
                    for relative, raw in first.files:
                        guard()
                        path = pinned / relative
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(raw)
                    checked = verifier(pinned, number, hemi_hash, policy, check=guard,
                                       accepted_context_sha256=accepted_context_sha256)
            except OSError:
                # Temporary-storage failure only disables the optimization.
                # Missing/changed required evidence does not take this branch.
                return original()
            if first.digest != self._capture(packet, guard).digest:
                raise VerificationError('evidence_snapshot_changed', 'The evidence changed while checking.')
            self._check_rules(guard)
            raw = _json_bytes(checked)
            guard()
            if (checked.get('status') in ('verified_conditional_first_height', 'verified_snapshot_evidence')
                    and checked.get('schema') == snapshot.RESULT_SCHEMA
                    and checked.get('assurance', {}).get('globally_first_proven') is False
                    and len(raw) <= self.max_bytes):
                while self._entries and (len(self._entries) >= self.max_entries or self._size + len(raw) > self.max_bytes):
                    _, old = self._entries.popitem(last=False)
                    self._size -= len(old)
                self._entries[key] = raw
                self._size += len(raw)
            return json.loads(raw)
        finally:
            self._slot.release()
