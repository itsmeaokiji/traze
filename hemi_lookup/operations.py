"""Local monitoring snapshots. No source clients, sessions or collection writes."""

import argparse
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from .catalogue import timestamp
from .accounting import accounting_directory, accounting_state
from .errors import DataError
from .collection_store import collection_status
from .explorer import Explorer, ReadBudget
from .storage import atomic_json

MONITOR_SCHEMA = "traze-operations-v1"
PUBLIC_FIELDS = ("state", "processed_blocks", "supported_publications",
                 "associated_publications", "unresolved_publications", "updated_at")
STATES = {"queued", "running", "waiting", "complete", "incomplete", "limited",
          "stopped", "blocked", "storage_limited", "unavailable", "stop_requested"}


def age(stamp, current):
    try:
        value = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if value.tzinfo is None:
            return None
        seconds = current - value.timestamp()
        return round(seconds, 3) if seconds >= 0 else None
    except (TypeError, ValueError, AttributeError, OverflowError):
        return None


def public_snapshot(value, *, current=None):
    """Whitelist the small public subset; never expose paths, IDs or raw errors."""
    current = time.time() if current is None else current
    generated = value.get("generated_at")
    elapsed = age(generated, current)
    available = value.get("schema") == MONITOR_SCHEMA and elapsed is not None and elapsed <= 120
    result = {"monitoring": "available" if available else "unavailable",
              "generated_at": generated if elapsed is not None else None,
              "observation_note": "Observation recency is not chain synchronization or current canonical membership.",
              "observations": {}, "collection": None}
    if not available:
        return result
    for chain in ("bitcoin", "hemi"):
        record = value.get("observations", {}).get(chain, {})
        observed_age = age(record.get("observed_at"), current)
        threshold = value.get("observation_max_age_seconds", 1800)
        if type(threshold) is not int or not 60 <= threshold <= 86400:
            threshold = 1800
        result["observations"][chain] = {
            "observed_at": record.get("observed_at") if observed_age is not None else None,
            "recency": "unavailable" if observed_age is None else "stale" if observed_age > threshold else "recent",
            "age_seconds": observed_age}
    raw = value.get("collection")
    if isinstance(raw, dict):
        clean = {key: raw[key] for key in PUBLIC_FIELDS if key in raw and (
            key == "state" and raw[key] in STATES or
            key == "updated_at" and age(raw[key], current) is not None or
            key not in ("state", "updated_at") and type(raw[key]) is int and raw[key] >= 0)}
        clean.setdefault("state", "unavailable")
        result["collection"] = clean
    return result


def inspect_state(state, *, min_free_bytes, observation_max_age_seconds=1800):
    """Operator-only read, including an owned-lock probe and actual disk free."""
    state = Path(state).resolve(strict=True)
    if not state.is_dir() or type(min_free_bytes) is not int or min_free_bytes < 0:
        raise ValueError("Use an existing state directory and a nonnegative free-space threshold")
    if type(observation_max_age_seconds) is not int or not 60 <= observation_max_age_seconds <= 86400:
        raise ValueError("Observation age threshold must be 60-86400 seconds")
    current = time.time()
    result = {"schema": MONITOR_SCHEMA, "generated_at": timestamp(),
              "observation_max_age_seconds": observation_max_age_seconds,
              "checks": {}, "observations": {}, "collection": None}
    disk = shutil.disk_usage(state)
    result["disk"] = {"free_bytes": disk.free, "minimum_free_bytes": min_free_bytes}
    result["checks"]["disk_space"] = disk.free >= min_free_bytes
    result["checks"]["restore_review_clear"] = not (state / "restore-guard.json").exists()
    try:
        ledger_path = accounting_directory(state) / 'session.json'
        if ledger_path.stat().st_size > 16_000_000:
            raise ValueError("Accounting needs a larger monitoring workflow")
        ledger = accounting_state(state)
        if (ledger["schema"] != 1 or type(ledger["downloaded_bytes"]) is not int or ledger["downloaded_bytes"] < 0
                or not isinstance(ledger["runs"], list) or not isinstance(ledger["prior_validation_bytes"], dict)
                or any(type(v) is not int or v < 0 for v in ledger["prior_validation_bytes"].values())):
            raise ValueError("Invalid accounting")
        result["accounting"] = {"counted_response_bytes": ledger["downloaded_bytes"] + sum(ledger["prior_validation_bytes"].values()),
                                "recorded_runs": len(ledger["runs"])}
        result["checks"]["accounting_readable"] = True
    except (OSError, ValueError, KeyError, TypeError, DataError):
        result["checks"]["accounting_readable"] = False
    explorer, budget = Explorer(state), ReadBudget()
    try:
        result["index"] = explorer.read("index", {})
        path, _ = explorer.database()
        with explorer.snapshot(path, budget) as catalogue:
            tables = {row[0] for row in catalogue.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for chain in ("bitcoin", "hemi"):
                row = catalogue.db.execute("SELECT data FROM collection_views WHERE chain=? ORDER BY sequence DESC LIMIT 1", (chain,)).fetchone() if "collection_views" in tables else None
                record = json.loads(row[0]) if row else {}
                result["observations"][chain] = {"observed_at": record.get("observed_at"),
                    "status": record.get("status"), "age_seconds": age(record.get("observed_at"), current)}
            recorded = collection_status(state, db=catalogue.db)
            if recorded:
                result['collection'] = {key: recorded[key] for key in PUBLIC_FIELDS if key in recorded}
                result['collection_progress'] = recorded['progress']
                result['runner'] = recorded['runner']
                result['checks']['collector_not_blocked'] = recorded['state'] not in (
                    'blocked', 'storage_limited', 'unavailable', 'stop_requested', 'stopping')
        budget()
        result["checks"]["index_readable"] = True
        result["checks"]["source_views_observed"] = all(result["observations"][c].get("status") == "observed" for c in ("bitcoin", "hemi"))
    except Exception as exc:
        result["checks"]["index_readable"] = False
        result["index_error"] = getattr(exc, "code", "index_unavailable")
    snapshot = public_snapshot(result)
    result["checks"]["observation_recency"] = all(snapshot["observations"].get(c, {}).get("recency") == "recent" for c in ("bitcoin", "hemi"))
    result["healthy"] = all(result["checks"].values())
    result["public"] = snapshot
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read local operational state; never query chain sources")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--min-free-bytes", type=int, required=True)
    parser.add_argument("--observation-max-age-seconds", type=int, default=1800)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = inspect_state(args.state_dir, min_free_bytes=args.min_free_bytes,
                               observation_max_age_seconds=args.observation_max_age_seconds)
        if args.output:
            atomic_json(args.output, result)
        print(json.dumps(result, indent=2))
        return 0 if result["healthy"] else 2
    except (OSError, ValueError) as exc:
        print(json.dumps({"healthy": False, "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
