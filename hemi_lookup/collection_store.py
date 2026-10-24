"""Durable collection jobs and observations over the existing evidence catalogue."""

import copy
import errno
import json
import sqlite3
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .bitcoin import hex32
from .catalogue import Catalogue, digest, encode, timestamp
from .errors import DataError
from .storage import SAFETY_MARGIN
from .collection_control import runner_status, stop_request
from .accounting import accounting_directory


def epoch(stamp):
    """Read a persisted UTC observation without accepting a timezone-free clock."""
    value = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise DataError("invalid_collection_clock", "Collection timestamps require a timezone")
    return value.timestamp()


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

    @staticmethod
    def held(state):
        """Probe an existing lock without creating files or changing evidence."""
        path = Path(state) / "collection.lock"
        try:
            handle = path.open("r+b")
        except FileNotFoundError:
            return False
        except OSError:
            return None
        with handle:
            try:
                import os
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return False
            except OSError as exc:
                return True if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK) else None

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
            CREATE TABLE IF NOT EXISTS collection_services (id TEXT PRIMARY KEY, data TEXT NOT NULL);
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

    @property
    def ledger_directory(self):
        return accounting_directory(self.state, db=self.db)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def create(self, start, end, *, batch_size=4, max_bytes=40_000_000, max_attempts=20,
               max_requests=600, max_seconds=600, cumulative_limit=100_000_000,
               segment_seconds=60, wall_seconds=7200, poll_seconds=60, backoff_max_seconds=600, service=None):
        values = (start, end, batch_size, max_bytes, max_attempts, max_requests, max_seconds,
                  cumulative_limit, segment_seconds, wall_seconds, poll_seconds, backoff_max_seconds)
        if any(isinstance(n, bool) or not isinstance(n, int) for n in values):
            raise DataError("invalid_collection", "Collection settings must be whole numbers")
        if start < 0 or end < start or end - start + 1 > 200 or not 1 <= batch_size <= 20:
            raise DataError("invalid_collection", "Use a finite range of at most 200 heights and updates of at most 20")
        if min(max_attempts, max_requests, segment_seconds) < 1 or not segment_seconds <= max_seconds <= 600:
            raise DataError("invalid_collection", "Positive limits and at most 600 active seconds per update are required")
        if max_bytes <= SAFETY_MARGIN or cumulative_limit <= SAFETY_MARGIN:
            raise DataError("invalid_collection", "Response allowances must include a safety margin")
        if not 1 <= wall_seconds <= 86400 or not 1 <= poll_seconds <= backoff_max_seconds <= 3600:
            raise DataError("invalid_collection", "Use a finite wall-time allowance of at most one day and positive bounded polling/backoff intervals")
        job = {"id": uuid.uuid4().hex, "created_at": timestamp(), "updated_at": timestamp(),
               "state": "queued", "range": [start, end], "batch_size": batch_size,
               "limits": {"response_bytes": max_bytes, "body_attempts": max_attempts,
                          "requests": max_requests, "active_seconds_per_update": max_seconds,
                          "cumulative_response_bytes": cumulative_limit, "segment_seconds": segment_seconds,
                          "wall_seconds": wall_seconds, "poll_seconds": poll_seconds,
                          "backoff_max_seconds": backoff_max_seconds},
               "items": {}, "associations": {}, "updates": [], "issue": None,
               "clock": {}, "tip_observations": []}
        if service is None:
            self.save(job)
        else:
            # Bind the source-free child and its controller in one transaction.
            # A crash cannot leave acquired work outside the saved service job.
            job.update(operating_service=service["id"], operating_period=service["clock"]["period"])
            inherited = self._successor_work(job, service)
            service["current_job"] = job["id"]
            service["updated_at"] = timestamp()
            with self.db:
                self.db.execute("INSERT INTO collection_jobs VALUES (?,?)", (job["id"], encode(job)))
                self.db.executemany("INSERT INTO collection_metadata VALUES (?,?,?)",
                                    [(job["id"], key, data) for key, data in inherited])
                self.db.execute("UPDATE collection_services SET data=? WHERE id=?", (encode(service), service["id"]))
        return job

    def _successor_work(self, job, service):
        """Carry bounded unfinished work, never old scope, clocks or freshness.

        Only the owning service's immediately retired child is eligible. The
        new child still nominates each Bitcoin height and performs the normal
        uncached Hemi/Bitcoin source rechecks before declaring an update ready.
        Metadata rows retain their original source and observation timestamp.
        """
        previous_id = service.get("last_job")
        if not previous_id or service["state"] in ("stopped", "blocked", "storage_limited"):
            return []
        previous = self.job(previous_id)
        if (previous.get("operating_service") != service["id"]
                or previous["state"] != "period_closed"
                or stop_request(self.state, previous_id)):
            return []
        low = max(job["range"][0], previous["range"][0])
        high = min(job["range"][1], previous["range"][1])
        if low > high:
            return []
        allowed, associations, bitcoin_hashes = set(), {}, {}
        for number in range(low, high + 1):
            item = previous["items"].get(str(number), {})
            if item.get("state") != "processed":
                continue
            bitcoin_hashes[str(number)] = item["hash"]
            if item.get("first_observed_at"):
                job["items"][str(number)] = {"first_observed_at": item["first_observed_at"]}
            for row in self.db.execute("SELECT id,keystone_height FROM publications WHERE block_hash=? AND supported=1", (item["hash"],)):
                height = row["keystone_height"]
                for h in range(max(1, height - 25), height + 1):
                    allowed.add(digest(["eth_getBlockByNumber", [hex(h), False]]))
                allowed.add(digest(["eth_getTransactionByBlockNumberAndIndex", [hex(height), "0x0"]]))
                progress = previous["associations"].get(row["id"], {})
                if progress.get("keystone") != height:
                    continue
                if progress.get("state") == "commitment_checked" or (
                        progress.get("state") == "complete"
                        and progress.get("ancestry_range") == [max(1, height - 25), height]):
                    associations[row["id"]] = copy.deepcopy(progress)
        metadata = [(row["key"], row["data"]) for row in self.db.execute(
            "SELECT key,data FROM collection_metadata WHERE job_id=? ORDER BY key", (previous_id,))
            if row["key"] in allowed]
        if metadata or associations:
            job["associations"] = associations
            job["work_reuse"] = {"from_job": previous_id, "overlap": [low, high],
                                 "bitcoin_hashes": bitcoin_hashes,
                                 "metadata_records": len(metadata),
                                 "complete_associations": sum(a["state"] == "complete" for a in associations.values()),
                                 "partial_associations": sum(a["state"] != "complete" for a in associations.values()),
                                 "prepared_at": timestamp()}
        return metadata

    def service(self, identity):
        row = self.db.execute("SELECT data FROM collection_services WHERE id=?", (identity,)).fetchone()
        if not row:
            raise DataError("unknown_collection_service", "Collection service was not found")
        return json.loads(row[0])

    def save_service(self, service):
        service["updated_at"] = timestamp()
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO collection_services VALUES (?,?)", (service["id"], encode(service)))

    @staticmethod
    def read_service(state, identity):
        path = Path(state) / "service.sqlite3"
        if not path.is_file():
            raise DataError("unknown_collection_service", "Collection service was not found")
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='collection_services'").fetchone():
                raise DataError("unknown_collection_service", "Collection service was not found")
            row = db.execute("SELECT data FROM collection_services WHERE id=?", (identity,)).fetchone()
            if not row:
                raise DataError("unknown_collection_service", "Collection service was not found")
            return json.loads(row[0])

    def start_clock(self, job, started_at=None):
        """Arm once. Waiting, downtime and resume all consume the same window."""
        seconds = job["limits"].get("wall_seconds")
        if seconds is None:
            return  # Earlier finite jobs retain their existing policy.
        clock = job.setdefault("clock", {})
        if clock.get("started_at"):
            if started_at and started_at != clock["started_at"]:
                raise DataError("collection_clock_changed", "A started collection window cannot be reset")
            return
        started_at = started_at or timestamp()
        clock.update(started_at=started_at,
                     expires_at=datetime.fromtimestamp(epoch(started_at) + seconds, timezone.utc).isoformat())
        self.save(job)

    def wall_remaining(self, job):
        clock = job.get("clock", {})
        if not clock.get("expires_at"):
            return float("inf")
        current = time.time()
        if current < epoch(clock["started_at"]) - 1:
            raise DataError("collection_clock_changed", "The current clock precedes the persisted collection window; source work is paused")
        return max(0, epoch(clock["expires_at"]) - current)

    def tip(self, job, height, block_hash, source):
        observation = {"height": height, "hash": block_hash, "source": source, "observed_at": timestamp()}
        observations = job.setdefault("tip_observations", [])
        if observations:
            gap = max(0, epoch(observation["observed_at"]) - epoch(observations[-1]["observed_at"]))
            job["max_tip_observation_gap_seconds"] = max(job.get("max_tip_observation_gap_seconds", 0), gap)
        observations.append(observation)
        for number in range(job["range"][0], min(height, job["range"][1]) + 1):
            item = job["items"].setdefault(str(number), {})
            item.setdefault("first_observed_at", observation["observed_at"])
        self.save(job)
        return observation

    def save(self, job):
        job["updated_at"] = timestamp()
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO collection_jobs VALUES (?,?)", (job["id"], encode(job)))

    def acknowledge_stop(self, identity):
        """Only the runner-lock owner may acknowledge; retain all unfinished work."""
        job = self.job(identity)
        if job["state"] == "stopped":
            return job
        request = stop_request(self.state, identity)
        if request:
            job["stop"] = {**request, "acknowledged_at": timestamp(), "previous_state": job["state"],
                           "previous_issue": copy.deepcopy(job.get("issue"))}
            job.update(state="stopped", issue={"code": "collection_stopped", "message": request["reason"]})
            job["usage"] = self.usage(job)
            self.save(job)
        return job

    def job(self, identity):
        row = self.db.execute("SELECT data FROM collection_jobs WHERE id=?", (identity,)).fetchone()
        if not row:
            raise DataError("unknown_collection", "Collection job was not found")
        return json.loads(row[0])

    @staticmethod
    def read_job(state, identity):
        """Operator preflight must not wait for an evidence writer transaction."""
        path = Path(state) / "service.sqlite3"
        if not path.is_file():
            raise DataError("unknown_collection", "Collection job was not found")
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
            row = db.execute("SELECT data FROM collection_jobs WHERE id=?", (identity,)).fetchone()
            if not row:
                raise DataError("unknown_collection", "Collection job was not found")
            return json.loads(row[0])

    def usage(self, job, update=None):
        path = self.ledger_directory / "session.json"
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
        lock, ledger = self.ledger_directory / "session.lock", self.ledger_directory / "session.json"
        if not lock.exists():
            return
        if not ledger.exists():
            raise DataError("session_locked", "Unidentified session lock; preserve the ledger")
        token = lock.read_text(encoding="utf-8")
        runs = json.loads(ledger.read_text(encoding="utf-8"))["runs"]
        owned = [r for r in runs if r.get("id") == token[:12] and r.get("context", {}).get("collection_job") == job["id"]]
        if len(owned) != 1 or "closed_at" in owned[0]:
            raise DataError("session_locked", "A different source operation owns the session lock")
        if owned[0].get('context', {}).get('collection_index', str(self.state)) != str(self.state):
            raise DataError('session_locked', 'A different index owns this accounting segment')
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


def collection_status(state, *, db=None):
    """Read-only status; an existing read snapshot can be shared with the API."""
    if db is not None:
        return _collection_status(state, db)
    path = Path(state) / "service.sqlite3"
    if not path.is_file():
        return None
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as connection:
        connection.execute("BEGIN")
        return _collection_status(state, connection)


def _collection_status(state, db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='collection_jobs'").fetchone():
        return None
    row = db.execute("SELECT data FROM collection_jobs ORDER BY json_extract(data,'$.updated_at') DESC, rowid DESC LIMIT 1").fetchone()
    job = json.loads(row[0]) if row else None
    has_services = db.execute("SELECT 1 FROM sqlite_master WHERE name='collection_services'").fetchone()
    service_row = db.execute("SELECT data FROM collection_services ORDER BY json_extract(data,'$.updated_at') DESC, rowid DESC LIMIT 1").fetchone() if has_services else None
    service = json.loads(service_row[0]) if service_row else None
    held = RunnerLock.held(state)
    # A queued record cannot hide the controller that actually owns work.
    try:
        runtime = json.loads((Path(state) / "collection-runner.json").read_text(encoding="utf-8"))
        active_id = runtime["job_id"]
        if runner_status(state, active_id, held)["state"] == "active":
            active = db.execute("SELECT data FROM collection_services WHERE id=?", (active_id,)).fetchone() if has_services else None
            if active:
                service = json.loads(active[0])
                job = None
            else:
                active = db.execute("SELECT data FROM collection_jobs WHERE id=?", (active_id,)).fetchone()
                if active:
                    job, service = json.loads(active[0]), None
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if job and job.get("operating_service") and has_services:
        parent = db.execute("SELECT data FROM collection_services WHERE id=?", (job["operating_service"],)).fetchone()
        if parent:
            parent = json.loads(parent[0])
            if service is None or job["updated_at"] >= service["updated_at"]:
                service = parent
            job = None
    if service and (job is None or service["updated_at"] >= job["updated_at"]):
        record = service
        child_id = service["current_job"] or service["last_job"]
        child = db.execute("SELECT data FROM collection_jobs WHERE id=?", (child_id,)).fetchone() if child_id else None
        job = json.loads(child[0]) if child else None
    else:
        service, record = None, job
    if record is None:
        return None
    runner = runner_status(state, record["id"], held)
    effective_state = record["state"]
    if (service and runner['state'] == 'active' and job and job['state'] in ('running', 'waiting')
            and effective_state not in ('stopped', 'blocked', 'storage_limited')):
        effective_state = job['state']
    active_states = ("running", "waiting", "limited", "incomplete") if service else ("running", "waiting")
    if effective_state in active_states and runner["state"] != "active":
        effective_state = "unavailable"
    request = stop_request(state, record["id"])
    if request and record["state"] != "stopped":
        effective_state = "stopping" if runner["state"] == "active" else "stop_requested"
    supported = db.execute("SELECT COUNT(*) FROM publications WHERE supported=1").fetchone()[0]
    associated = db.execute("SELECT COUNT(DISTINCT publication_id) FROM associations").fetchone()[0]
    processed = db.execute("SELECT COUNT(*) FROM blocks WHERE status='complete'").fetchone()[0]
    result = {"id": record["id"], "kind": "service" if service else "job",
            "state": effective_state, "recorded_state": record["state"],
            "runner": runner, "range": job["range"] if job else None, "stop": record.get("stop"),
            "processed_blocks": processed, "supported_publications": supported,
            "associated_publications": associated, "unresolved_publications": supported - associated,
            "processed_job_blocks": sum(item.get("state") == "processed" for item in job["items"].values()) if job else 0,
            "requested_job_blocks": job["range"][1] - job["range"][0] + 1 if job else 0,
            "updated_at": record["updated_at"], "issue": record["issue"],
            "pending_ancestry": sum(a.get("state") != "complete" for a in job["associations"].values()) if job else 0,
            "source_conflicts": db.execute("SELECT COUNT(*) FROM collection_views v WHERE v.sequence=(SELECT MAX(sequence) FROM collection_views WHERE chain=v.chain AND height=v.height) AND json_extract(v.data,'$.status')!='observed'").fetchone()[0]}
    if service:
        result.update(next_height=service["next_height"], latest_tip=service["latest_tip"])
        messages = {"operating_storage_limit": "Collection is paused for operator storage review. Retained evidence remains available.",
                    "operating_limit": "Collection is waiting for its next configured operating period.",
                    "operating_period_ended": "The operating period ended; collection progress is retained."}
        if result["issue"] and result["issue"]["code"] in messages:
            result["issue"] = {"code": result["issue"]["code"], "message": messages[result["issue"]["code"]]}
    from .collection_progress import progress_view
    result['progress'] = progress_view(db, service, job)
    return result
