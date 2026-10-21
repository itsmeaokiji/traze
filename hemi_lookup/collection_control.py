"""Local operator stop requests and runner observations; never acquire sources.

Signals live outside SQLite transactions so an operator can request a stop while
evidence is being committed. An OS-owned runner lock and a recent matching
heartbeat are both required to report an active collector. Neither implies
fresh blockchain observations or proof validity.
"""

import json
import os
import re
import time
import uuid
from datetime import datetime
from pathlib import Path

from .errors import DataError
from .storage import atomic_json, now

HEARTBEAT_SECONDS = 5
HEARTBEAT_MAX_AGE = 15
CONTROL_POLL_SECONDS = .1


def control_path(state, identity):
    if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{32}", identity):
        raise DataError("invalid_collection", "A saved collection job ID is required")
    return Path(state) / "collection-controls" / (identity + ".json")


def stop_request(state, identity):
    path = control_path(state, identity)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        if (record["job_id"] != identity or record["action"] != "stop"
                or not isinstance(record["reason"], str) or not record["reason"].strip()
                or len(record["reason"]) > 500):
            raise ValueError("Invalid stop request")
        stamp = datetime.fromisoformat(record["requested_at"])
        if stamp.tzinfo is None:
            raise ValueError("Missing timezone")
        return record
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise DataError("collection_control_unavailable", "Operator control could not be read; collection must stop") from exc


def request_stop(state, identity, reason):
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
        raise DataError("invalid_collection", "Use a stop reason of 1–500 characters")
    path = control_path(state, identity)
    existing = stop_request(state, identity)
    if existing:
        return existing
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"job_id": identity, "action": "stop", "reason": reason.strip(), "requested_at": now()}
    atomic_json(path, record)
    return record


class RunnerRecord:
    def __init__(self, state, identity, *, kind="job"):
        self.path = Path(state) / "collection-runner.json"
        self.record = {"job_id": identity, "kind": kind, "run_id": uuid.uuid4().hex,
                       "process_id": os.getpid(), "status": "active", "started_at": now()}
        self.last_write = float("-inf")
        self.touch(force=True)

    def touch(self, force=False):
        current = time.monotonic()
        if force or current - self.last_write >= HEARTBEAT_SECONDS:
            self.record["heartbeat_at"] = now()
            atomic_json(self.path, self.record)
            self.last_write = current

    def close(self):
        # The caller still owns RunnerLock. Do not overwrite an unrelated file.
        current = json.loads(self.path.read_text(encoding="utf-8"))
        if current.get("run_id") != self.record["run_id"]:
            raise DataError("collection_runner_changed", "Runner identity changed; preserve its observations")
        self.record.update(status="inactive", ended_at=now())
        atomic_json(self.path, self.record)


def runner_status(state, identity, lock_held):
    result = {"state": "unavailable", "last_seen_at": None}
    try:
        record = json.loads((Path(state) / "collection-runner.json").read_text(encoding="utf-8"))
        if record["job_id"] != identity:
            return result
        observed = datetime.fromisoformat(record["heartbeat_at"])
        if observed.tzinfo is None:
            return result
        result["last_seen_at"] = record["heartbeat_at"]
        age = time.time() - observed.timestamp()
        if lock_held is True and record["status"] == "active" and 0 <= age <= HEARTBEAT_MAX_AGE:
            result["state"] = "active"
        elif lock_held is False and record["status"] == "inactive":
            result["state"] = "inactive"
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return result
