"""Bounded local cache and persistent response-data accounting."""

import json
import math
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .errors import BudgetError, DataError, DeadlineError

PRIOR_REPORTS = (
    "HEMI_LIGHTWEIGHT_PROBE_RESULT.json", "HEMI_KNOWN_PUBLICATION_RESULT.json",
    "HEMI_MAINNET_DISCOVERY_RESULT.json", "HEMI_TIMESTAMP_DISCOVERY_5243125_RESULT.json",
    "HEMI_LOOKUP_CASES_RESULT.json",
)
SESSION_LIMIT = 100_000_000
HEADER_RESERVE = 65_536
SAFETY_MARGIN = 1_000_000


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temp.replace(path)


class Session:
    def __init__(self, directory, prior_root=None, max_bytes=40_000_000, max_blocks=20, max_requests=600, max_seconds=600):
        if (isinstance(max_seconds, bool) or not isinstance(max_seconds, (int, float))
                or not math.isfinite(max_seconds) or not 0 < max_seconds <= 600):
            raise DataError("invalid_limits", "Overall lookup time must be positive and at most 600 seconds")
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "session.json"
        self.lock_path = self.directory / "session.lock"
        self.token = uuid.uuid4().hex
        try:
            fd = os.open(str(self.lock_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise DataError("session_locked", "Another lookup may be active. Preserve session.json; see README for interrupted-lock recovery.") from exc
        with os.fdopen(fd, "w") as handle:
            handle.write(self.token)
        self.closed = False
        self.started = time.monotonic()
        self.deadline = self.started + max_seconds
        self.max_seconds = max_seconds
        self.deadline_expired = False
        self._closers = []
        self.max_bytes, self.max_blocks, self.max_requests = max_bytes, max_blocks, max_requests
        try:
            prior = {}
            if prior_root:
                for name in PRIOR_REPORTS:
                    path = Path(prior_root) / name
                    if path.exists():
                        value = json.loads(path.read_text(encoding="utf-8")).get("http_response_bytes")
                        if not isinstance(value, int) or not 0 <= value <= SESSION_LIMIT:
                            raise DataError("invalid_prior_budget", "Prior validation budget is invalid")
                        prior[name] = value
            if self.path.exists():
                self.state = json.loads(self.path.read_text(encoding="utf-8"))
                if self.state.get("schema") != 1 or self.state.get("prior_validation_bytes") != prior:
                    raise DataError("session_changed", "Session/prior budget changed; refusing to reset accounting")
            else:
                self.state = {"schema": 1, "prior_validation_bytes": prior, "downloaded_bytes": 0, "runs": []}
            self.run = {"id": self.token[:12], "started_at": now(), "received_bytes": 0,
                        "body_attempts": 0, "requests": [], "cache_reads": 0,
                        "limits": {"response_bytes": max_bytes, "bitcoin_body_attempts": max_blocks,
                                   "requests": max_requests, "overall_seconds": max_seconds}}
            self.state["runs"].append(self.run)
            self.save()
        except Exception:
            self.close()
            raise

    @property
    def total_bytes(self):
        return sum(self.state["prior_validation_bytes"].values()) + self.state["downloaded_bytes"]

    def save(self):
        atomic_json(self.path, self.state)

    def check_deadline(self):
        if time.monotonic() >= self.deadline:
            self.deadline_expired = True
            raise DeadlineError()

    def remaining_seconds(self):
        self.check_deadline()
        return max(0, self.deadline - time.monotonic())

    def on_close(self, callback):
        self._closers.append(callback)

    def reserve(self, url, ceiling, block=False):
        self.check_deadline()
        if len(self.run["requests"]) >= self.max_requests:
            raise BudgetError("Request limit reached")
        if block and self.run["body_attempts"] >= self.max_blocks:
            raise BudgetError("Bitcoin body-attempt limit reached")
        amount = ceiling + HEADER_RESERVE
        if self.run["received_bytes"] + amount > self.max_bytes - SAFETY_MARGIN:
            raise BudgetError("Per-lookup response budget reached")
        if self.total_bytes + amount > SESSION_LIMIT - SAFETY_MARGIN:
            raise BudgetError("Combined 100 MB session budget reached")
        entry = {"url": url, "status": "reserved", "reserved_bytes": amount}
        self.run["requests"].append(entry)
        self.run["received_bytes"] += amount
        self.state["downloaded_bytes"] += amount
        if block:
            self.run["body_attempts"] += 1
        self.save()  # Interrupted requests retain a conservative reservation.
        return entry

    def charge(self, entry, received, status, error=None, uncertain=False):
        # A terminated read may have bytes in worker/OS buffers that were never
        # reported to the parent. Keep its reservation rather than undercount.
        accounted = max(received, entry["reserved_bytes"]) if uncertain else received
        adjustment = accounted - entry["reserved_bytes"]
        self.run["received_bytes"] += adjustment
        self.state["downloaded_bytes"] += adjustment
        entry.update({"received_bytes": accounted, "status": status})
        if uncertain:
            entry.update({"received_bytes_lower_bound": received, "accounting": "conservative_reservation"})
        if error:
            entry["error"] = error
        self.save()

    def metrics(self):
        return {"run_id": self.run["id"], "response_bytes_this_run": self.run["received_bytes"],
                "response_bytes_session_with_prior_validation": self.total_bytes,
                "bitcoin_body_attempts": self.run["body_attempts"], "cache_body_reads": self.run["cache_reads"],
                "http_requests": len(self.run["requests"]), "limits": self.run["limits"],
                "deadline_expired": self.deadline_expired,
                "conservative_requests": sum(e.get("accounting") == "conservative_reservation" for e in self.run["requests"]),
                "byte_scope": "HTTP bodies/headers plus conservative interrupted reservations; TLS/TCP overhead excluded"}

    def close(self):
        if not self.closed:
            try:
                for callback in self._closers:
                    callback()
            finally:
                if self.lock_path.exists() and self.lock_path.read_text() == self.token:
                    self.lock_path.unlink()
                self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class BlockCache:
    def __init__(self, directory, max_bytes=40_000_000):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes

    def path(self, block_hash):
        from .bitcoin import hex32
        normalized = hex32(block_hash).hex()
        path = self.directory / (normalized + ".bin")
        if not path.resolve().is_relative_to(self.directory):
            raise DataError("unsafe_cache_path", "Cache path escapes its directory")
        return path

    def get(self, block_hash):
        path = self.path(block_hash)
        if not path.exists():
            return None
        if path.stat().st_size > 4_000_000:
            raise DataError("cache_corrupt", "Cached block exceeds the maximum serialized size")
        return path.read_bytes()

    def put(self, block_hash, raw):
        path = self.path(block_hash)
        temp = path.with_suffix(".tmp")
        temp.write_bytes(raw)
        temp.replace(path)
        files = sorted(self.directory.glob("*.bin"), key=lambda item: item.stat().st_mtime)
        total = sum(item.stat().st_size for item in files)
        for old in files:
            if total <= self.max_bytes:
                break
            if not old.resolve().is_relative_to(self.directory):
                raise DataError("unsafe_cache_path", "Refusing to remove a cache path outside its directory")
            if old == path:
                continue
            size = old.stat().st_size
            old.unlink()  # Individual owned cache files only; no recursive removal.
            total -= size
