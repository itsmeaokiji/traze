"""Durable collection jobs and observations over the existing evidence catalogue."""

import copy
import json
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

from .bitcoin import hex32
from .catalogue import Catalogue, digest, encode, timestamp
from .errors import DataError
from .storage import SAFETY_MARGIN


class RunnerLock:
    """An OS-owned lock releases on process death; it never resets accounting."""

    def __init__(self, state):
        self.path = Path(state) / "collection.lock"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            import os
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            raise DataError("collector_busy", "Another collector is running") from exc

    def close(self):
        self.handle.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class CollectionStore:
    def __init__(self, state):
        self.state = Path(state).resolve()
        self.path = self.state / "service.sqlite3"
        self.catalogue = Catalogue(self.path)
        self.db = self.catalogue.db
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS collection_jobs (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS collection_metadata (
                job_id TEXT NOT NULL, key TEXT NOT NULL, data TEXT NOT NULL,
                PRIMARY KEY(job_id,key));
            CREATE TABLE IF NOT EXISTS collection_views (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, chain TEXT NOT NULL,
                height INTEGER NOT NULL, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS collection_view_height ON collection_views(chain,height,sequence);
        """)

    def close(self):
        self.catalogue.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def create(self, start, end, *, batch_size=4, max_bytes=40_000_000, max_attempts=20,
               max_requests=600, max_seconds=600, cumulative_limit=100_000_000,
               segment_seconds=60):
        values = (start, end, batch_size, max_bytes, max_attempts, max_requests, max_seconds, cumulative_limit, segment_seconds)
        if any(isinstance(n, bool) or not isinstance(n, int) for n in values):
            raise DataError("invalid_collection", "Collection settings must be whole numbers")
        if start < 0 or end < start or end - start + 1 > 200 or not 1 <= batch_size <= 20:
            raise DataError("invalid_collection", "Use a finite range of at most 200 heights and updates of at most 20")
        if min(max_attempts, max_requests, segment_seconds) < 1 or not segment_seconds <= max_seconds <= 600:
            raise DataError("invalid_collection", "Positive limits and at most 600 active seconds per update are required")
        if max_bytes <= SAFETY_MARGIN or cumulative_limit <= SAFETY_MARGIN:
            raise DataError("invalid_collection", "Response allowances must include a safety margin")
        job = {"id": uuid.uuid4().hex, "created_at": timestamp(), "updated_at": timestamp(),
               "state": "queued", "range": [start, end], "batch_size": batch_size,
               "limits": {"response_bytes": max_bytes, "body_attempts": max_attempts,
                          "requests": max_requests, "active_seconds_per_update": max_seconds,
                          "cumulative_response_bytes": cumulative_limit, "segment_seconds": segment_seconds},
               "items": {}, "associations": {}, "updates": [], "issue": None}
        self.save(job)
        return job

    def save(self, job):
        job["updated_at"] = timestamp()
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO collection_jobs VALUES (?,?)", (job["id"], encode(job)))

    def job(self, identity):
        row = self.db.execute("SELECT data FROM collection_jobs WHERE id=?", (identity,)).fetchone()
        if not row:
            raise DataError("unknown_collection", "Collection job was not found")
        return json.loads(row[0])

    def usage(self, job, update=None):
        path = self.state / "session.json"
        state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"runs": []}
        runs = [r for r in state["runs"] if r.get("context", {}).get("collection_job") == job["id"]]
        times = [r for r in runs if update is None or r["context"]["update"] == update]
        return {"response_bytes": sum(r["received_bytes"] for r in runs),
                "body_attempts": sum(r["body_attempts"] for r in runs),
                "requests": sum(len(r["requests"]) for r in runs),
                "active_seconds": sum(min(r.get("elapsed_seconds", r["limits"]["overall_seconds"]), r["limits"]["overall_seconds"]) for r in times),
                "conservative_segments": sum("closed_at" not in r for r in runs)}

    def recover_session(self, job):
        """Call only while owning RunnerLock; release only this job's stale lock."""
        lock, ledger = self.state / "session.lock", self.state / "session.json"
        if not lock.exists():
            return
        if not ledger.exists():
            raise DataError("session_locked", "Unidentified session lock; preserve the ledger")
        token = lock.read_text(encoding="utf-8")
        runs = json.loads(ledger.read_text(encoding="utf-8"))["runs"]
        owned = [r for r in runs if r.get("id") == token[:12] and r.get("context", {}).get("collection_job") == job["id"]]
        if len(owned) != 1 or "closed_at" in owned[0]:
            raise DataError("session_locked", "A different source operation owns the session lock")
        lock.unlink()

    def metadata(self, job, method, params):
        key = digest([method, params])
        row = self.db.execute("SELECT data FROM collection_metadata WHERE job_id=? AND key=?", (job["id"], key)).fetchone()
        return json.loads(row[0])["value"] if row else None

    def cache_metadata(self, job, method, params, value, source):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO collection_metadata VALUES (?,?,?)",
                            (job["id"], digest([method, params]), encode({"value": value, "method": method, "params": params,
                            "observed_at": timestamp(), "source": source})))

    def metadata_observations(self, job, checkpoint, path):
        requests = [("eth_getBlockByNumber", [hex(int(h["number"], 16)), False]) for h in path + ([checkpoint["previous_header"]] if checkpoint["previous_header"] else [])]
        requests.append(("eth_getTransactionByBlockNumberAndIndex", [hex(checkpoint["height"]), "0x0"]))
        observations = []
        for method, params in requests:
            row = self.db.execute("SELECT data FROM collection_metadata WHERE job_id=? AND key=?", (job["id"], digest([method, params]))).fetchone()
            if row:
                record = json.loads(row[0])
                observations.append({key: record[key] for key in ("method", "params", "observed_at", "source")})
        return observations

    def body_source(self, import_id, source):
        with self.db:
            row = self.db.execute("SELECT observation FROM imports WHERE id=?", (import_id,)).fetchone()
            observation = json.loads(row[0])
            observation["body_source"] = source
            if source and source.startswith("https://"):
                observation["sources"]["bitcoin_apis"] = sorted(set(observation["sources"]["bitcoin_apis"] + [source]))
            self.db.execute("UPDATE imports SET observation=? WHERE id=?", (encode(observation), import_id))

    def observe(self, chain, number, block_hash, source, *, status="observed", header=None):
        observation = {"chain": chain, "height": number, "hash": hex32(block_hash).hex() if block_hash else None,
                       "source": source, "status": status, "observed_at": timestamp()}
        if header:
            observation["header"] = header
        with self.db:
            self.db.execute("INSERT INTO collection_views(chain,height,data) VALUES (?,?,?)", (chain, number, encode(observation)))
        return observation

    def nominate(self, job, number, block_hash, source):
        observation = {"query": {}, "sources": {"bitcoin_apis": [source]},
                       "context": {"checked_at": timestamp(), "current_view_confirmed": False},
                       "qualification": "Recorded collection observation; no fresh target confirmation"}
        identity = digest([job["id"], number, block_hash])
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO imports VALUES (?,?,?,?)", (identity, "Collected Bitcoin block", encode(observation), timestamp()))
            self.db.execute("INSERT OR IGNORE INTO blocks(hash,status) VALUES (?,'pending')", (block_hash,))
            self.db.execute("INSERT OR IGNORE INTO nominations VALUES (?,?,?,?)",
                            (identity, block_hash, number, encode({"height":number,"hash":block_hash,"source":source})))
        return identity

    def template(self, import_id, checkpoint, path, source, observations=()):
        template = {"checkpoint": copy.deepcopy(checkpoint), "ancestry": copy.deepcopy(path)}
        identity = digest(template)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO templates VALUES (?,?,?)", (identity, checkpoint["payload_hex"], encode(template)))
            self.db.execute("INSERT OR IGNORE INTO template_origins VALUES (?,?)", (identity, import_id))
            for header in path + ([checkpoint["previous_header"]] if checkpoint["previous_header"] else []):
                self.catalogue._header(header, import_id)
            row = self.db.execute("SELECT observation FROM imports WHERE id=?", (import_id,)).fetchone()
            observation = json.loads(row[0])
            observation["sources"]["hemi_rpc_used"] = source
            records = {digest(value): value for value in observation.get("metadata_observations", [])}
            records.update({digest(value): value for value in observations})
            observation["metadata_observations"] = list(records.values())
            times = [value["observed_at"] for value in records.values()] + [observation["context"]["checked_at"]]
            observation["observation_interval"] = {"first": min(times), "last": max(times)}
            self.db.execute("UPDATE imports SET observation=? WHERE id=?", (encode(observation), import_id))
        self.catalogue._associate()


def collection_status(state):
    """Read-only UI summary; polling never starts collection or source requests."""
    path = Path(state) / "service.sqlite3"
    if not path.is_file():
        return None
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
        db.execute("BEGIN")
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='collection_jobs'").fetchone():
            return None
        row = db.execute("SELECT data FROM collection_jobs ORDER BY json_extract(data,'$.created_at') DESC LIMIT 1").fetchone()
        if not row:
            return None
        job = json.loads(row[0])
        supported = db.execute("SELECT COUNT(*) FROM publications WHERE supported=1").fetchone()[0]
        associated = db.execute("SELECT COUNT(DISTINCT publication_id) FROM associations").fetchone()[0]
        processed = db.execute("SELECT COUNT(*) FROM blocks WHERE status='complete'").fetchone()[0]
        return {"id": job["id"], "state": job["state"], "range": job["range"],
                "processed_blocks": processed, "supported_publications": supported,
                "associated_publications": associated, "unresolved_publications": supported - associated,
                "processed_job_blocks": sum(item.get("state") == "processed" for item in job["items"].values()),
                "requested_job_blocks": job["range"][1] - job["range"][0] + 1,
                "updated_at": job["updated_at"], "issue": job["issue"],
                "pending_ancestry": sum(a.get("state") != "complete" for a in job["associations"].values()),
                "source_conflicts": db.execute("SELECT COUNT(*) FROM collection_views v WHERE v.sequence=(SELECT MAX(sequence) FROM collection_views WHERE chain=v.chain AND height=v.height) AND json_extract(v.data,'$.status')!='observed'").fetchone()[0]}
