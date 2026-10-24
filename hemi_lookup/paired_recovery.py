"""Paired offline snapshots and guarded migration of a bound index and ledger.

Snapshots contain durable state and the original validation reports. Restores
remain acquisition-disabled. Comparing an older snapshot with a newer trusted
one never merges away usage, clocks, evidence or terminal operator stops.
"""

import json
import re
import sqlite3
import time
from contextlib import ExitStack, closing
from pathlib import Path, PurePosixPath
import secrets

from .accounting import KEY, accounting_directory, accounting_state
from .backup import (DATABASES, DISPOSABLE, TRANSIENT, database_check, digest,
                     durable_files, member, quiescent)
from .catalogue import encode, timestamp
from .collection_control import stop_request
from .errors import DataError
from .operating import check_checkpoint, checkpoint, fingerprint, ledger_state
from .storage import PRIOR_REPORTS

SCHEMA = "traze-paired-state-backup-v1"
ROLES = {"index", "ledger", "baselines"}


def directory(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Recovery directories must not traverse symlinks")
    path = path.resolve(strict=True)
    if not path.is_dir():
        raise ValueError("Use an existing recovery directory")
    return path


def new_target(path, sources):
    path = Path(path).absolute()
    if path.exists() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Recovery requires a new destination without symlinks")
    path = path.resolve()
    if any(path.is_relative_to(p) for p in sources):
        raise ValueError("Recovery destination must be outside its sources")
    return path


class Work:
    def __init__(self, max_bytes):
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("Use a positive recovery size limit")
        self.limit, self.total, self.started = max_bytes, 0, time.monotonic()

    def check(self, additional=0):
        if self.total + additional > self.limit or time.monotonic() - self.started > 60:
            raise ValueError("Recovery exceeded its size/time limit")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def closed_ledger(root):
    if not (root / "session.json").is_file():
        raise DataError("missing_accounting", "A paired snapshot requires its original lifetime ledger")
    value = ledger_state(root)
    prior = value["prior_validation_bytes"]
    if (set(prior) - set(PRIOR_REPORTS) or
            any(type(n) is not int or n < 0 for n in prior.values())):
        raise ValueError("Invalid original validation baseline")
    runs = value["runs"]
    first = next((i for i, run in enumerate(runs) if "closed_at" in run), len(runs))
    if (any(not run.get("closed_at") for run in runs[first:]) or
            any(request.get("status") == "reserved" for run in runs for request in run["requests"])):
        raise DataError("session_locked", "Reconcile unclosed source accounting before paired recovery")
    if (any(type(run["received_bytes"]) is not int or run["received_bytes"] < 0 for run in runs)
            or sum(run["received_bytes"] for run in runs) != value["downloaded_bytes"]):
        raise ValueError("Lifetime ledger totals do not match its retained runs")
    return value


def baselines(root, ledger):
    result = []
    for name, amount in sorted(ledger["prior_validation_bytes"].items()):
        path = member(root, name)
        if not path.is_file() or read_json(path).get("http_response_bytes") != amount:
            raise ValueError("Original validation report is missing or changed: " + name)
        result.append(name)
    return result


def normalized_record(table, row, source):
    row = list(row)
    if table == "meta" and row[0] == KEY:
        value = json.loads(row[1])
        if (value.get("index_directory") != source["index_directory"] or
                value.get("ledger_directory") != source["ledger_directory"]):
            raise DataError("accounting_binding_changed", "Snapshot index/ledger identity differs")
        value.update(index_directory="index", ledger_directory="ledger")
        row[1] = encode(value)
    elif table == "collection_services":
        value = json.loads(row[1])
        for field, role in (("index_directory", "index"), ("ledger_directory", "ledger")):
            if field in value:
                if value[field] != source[field]:
                    raise DataError("accounting_binding_changed", "Saved service belongs to a different pair")
                value[field] = role
        row[1] = encode(value)
    return [({"sqlite_blob": v.hex()} if isinstance(v, bytes) else v) for v in row]


def database_fingerprints(path, source, *, normalize=False):
    result = {}
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for name in sorted(names):
            quoted = '"' + name.replace('"', '""') + '"'
            rows = []
            for row in db.execute("SELECT * FROM " + quoted):
                value = normalized_record(name, row, source) if normalize else [
                    ({"sqlite_blob": v.hex()} if isinstance(v, bytes) else v) for v in row]
                rows.append(encode(value))
            result[name] = {"rows": len(rows), "sha256": fingerprint(sorted(rows))}
    return result


def facts(root, source, paths):
    """Check semantic pairing without dereferencing recorded source paths."""
    ledger = closed_ledger(root / "ledger")
    baseline_names = baselines(root / "baselines", ledger)
    if {p for p in paths if p.startswith("baselines/")} != {"baselines/" + n for n in baseline_names}:
        raise ValueError("Paired snapshot baseline membership differs")
    with closing(sqlite3.connect((root / "index/service.sqlite3").as_uri() + "?mode=ro", uri=True)) as db:
        row = db.execute("SELECT value FROM meta WHERE key=?", (KEY,)).fetchone()
        if not row:
            raise ValueError("Paired snapshot requires an explicit index/ledger binding")
        binding = json.loads(row[0])
        if (binding.get("schema") != "traze-index-ledger-v1" or
                binding.get("index_directory") != source["index_directory"] or
                binding.get("ledger_directory") != source["ledger_directory"]):
            raise DataError("accounting_binding_changed", "Snapshot index and ledger are not the declared pair")
        check_checkpoint(ledger, binding["ledger_checkpoint"])
        services = {}
        for identity, data in db.execute("SELECT id,data FROM collection_services"):
            service = json.loads(data)
            normalized_record("collection_services", [identity, data], source)
            check_checkpoint(ledger, service["ledger_checkpoint"])
            if fingerprint(service["policy"]) != service["policy_digest"]:
                raise ValueError("Saved operating policy changed")
            control = stop_request(root / "index", identity)
            if service["state"] == "stopped" and (control is None or any(
                    service.get("stop", {}).get(k) != v for k, v in control.items())):
                raise DataError("collection_control_unavailable", "Stopped service requires its original terminal control")
            services[identity] = {k: service.get(k) for k in (
                "state", "clock", "policy_digest", "next_height", "current_job", "stop")}
    identities = {}
    json_files = {}
    for name in paths:
        parts = PurePosixPath(name).parts
        if len(parts) == 2 and parts[1] in DATABASES:
            identities[name] = database_fingerprints(member(root, name), source,
                                                     normalize=name == "index/service.sqlite3")
        else:
            json_files[name] = digest(member(root, name))
    return {"binding_identity": {k: v for k, v in binding.items()
                                  if k not in ("index_directory", "ledger_directory")},
            "ledger_checkpoint": checkpoint(ledger),
            "counted_response_bytes": ledger["downloaded_bytes"] + sum(ledger["prior_validation_bytes"].values()),
            "services": services, "databases": identities, "durable_json": json_files}


def copy_member(source, target, work, *, snapshot_database=False, expected_size=None):
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if snapshot_database:
        size = source.stat().st_size + sum(p.stat().st_size for p in (Path(str(source) + "-wal"),) if p.exists())
        work.check(size)
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as reader:
            page_size = reader.execute("PRAGMA page_size").fetchone()[0]
            def progress(status, remaining, pages):
                work.check(pages * page_size)
            with closing(sqlite3.connect(target)) as writer:
                reader.backup(writer, pages=256, progress=progress, sleep=.01)
                writer.execute("PRAGMA journal_mode=DELETE")
    else:
        if expected_size is not None and source.stat().st_size != expected_size:
            raise ValueError("Paired snapshot changed size during restore")
        work.check(source.stat().st_size)
        with source.open("rb") as src, target.open("xb") as dst:
            for chunk in iter(lambda: src.read(1024 * 1024), b""):
                if expected_size is not None and dst.tell() + len(chunk) > expected_size:
                    raise ValueError("Paired snapshot grew during restore")
                work.check(dst.tell() + len(chunk))
                dst.write(chunk)
    target.chmod(0o600)
    work.check(target.stat().st_size)
    work.total += target.stat().st_size


def backup_pair(state, destination, *, ledger_directory, prior_root, max_bytes):
    work = Work(max_bytes)
    state, ledger, prior_root = map(directory, (state, ledger_directory, prior_root))
    if (state == ledger or state.is_relative_to(ledger) or ledger.is_relative_to(state)
            or accounting_directory(state) != ledger):
        raise DataError("accounting_binding_changed", "Select the explicit external ledger bound to this index")
    destination = new_target(destination, (state, ledger))
    roots = {"index": state, "ledger": ledger, "baselines": prior_root}
    source = {"index_directory": str(state), "ledger_directory": str(ledger)}
    with ExitStack() as stack:
        for root in sorted((state, ledger), key=str):
            if (root / "restore-guard.json").exists():
                raise DataError("restored_state_requires_review", "A restored copy is not authoritative collection state")
            stack.enter_context(quiescent(root))
        accounting_state(state, prior_root=prior_root)
        saved_ledger = closed_ledger(ledger)
        members = {"index": durable_files(state), "ledger": durable_files(ledger),
                   "baselines": baselines(prior_root, saved_ledger)}
        if "service.sqlite3" not in members["index"]:
            raise ValueError("A maintained index is required")
        paths = [role + "/" + n for role in sorted(ROLES) for n in members[role]]
        if len(paths) > 10000:
            raise ValueError("Paired backup exceeds the file-count limit")
        for role in ("index", "ledger"):
            for name in sorted(set(members[role]) & DATABASES):
                # Keep a read-only snapshot alive until the writer guard closes.
                # Otherwise the last writable connection can checkpoint an idle
                # WAL into the source file merely by closing after this backup.
                pinned = stack.enter_context(closing(sqlite3.connect(
                    (roots[role] / name).as_uri() + "?mode=ro", uri=True)))
                pinned.execute("BEGIN")
                pinned.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
                guard = stack.enter_context(closing(sqlite3.connect(roots[role] / name, timeout=1)))
                if hasattr(sqlite3, "SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE"):
                    guard.setconfig(sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)
                guard.execute("BEGIN IMMEDIATE")
        originals = {p: digest(member(roots[p.split('/')[0]], p.split('/', 1)[1]))
                     for p in paths if p.split('/', 1)[1] not in DATABASES}
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = destination.with_name(destination.name + ".partial-" + secrets.token_hex(4))
        staging.mkdir(mode=0o700)
        entries = []
        for role in sorted(ROLES):
            (staging / role).mkdir(mode=0o700)
            for name in members[role]:
                target = member(staging, role + "/" + name)
                copy_member(member(roots[role], name), target, work,
                            snapshot_database=role != "baselines" and name in DATABASES)
                entries.append({"path": role + "/" + name, "bytes": target.stat().st_size,
                                "sha256": digest(target),
                                "database_counts": database_check(target) if name in DATABASES else None})
        snapshot = facts(staging, source, paths)
        for role in ("index", "ledger"):
            if durable_files(roots[role]) != members[role]:
                raise ValueError("Durable state membership changed during paired backup")
        for path, expected in originals.items():
            role, name = path.split('/', 1)
            if digest(member(roots[role], name)) != expected:
                raise ValueError("Accounting, controls or baselines changed during paired backup")
        manifest = {"schema": SCHEMA, "created_at": timestamp(), "source": source,
                    "snapshot": snapshot, "files": entries, "total_bytes": work.total,
                    "raw_cache_included": False, "runtime_locks_included": False,
                    "qualification": "Consistent stopped-writer snapshot, not zero-loss recovery or permission to resume collection."}
        path = staging / "manifest.json"
        path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        path.chmod(0o600)
        work.check()
    if destination.exists():
        raise ValueError("Backup destination appeared during snapshot")
    staging.rename(destination)
    return {"status": "paired_backup_complete", "manifest_sha256": digest(destination / "manifest.json"),
            "files": len(entries), "bytes": work.total, "seconds": time.monotonic() - work.started,
            "network_requests": 0}


def allowed_member(name):
    parts = PurePosixPath(name).parts
    if len(parts) < 2 or parts[0] not in ROLES:
        return False
    relative = PurePosixPath(*parts[1:])
    if parts[0] == "baselines":
        return len(parts) == 2 and parts[1] in PRIOR_REPORTS
    return (parts[1] not in DISPOSABLE and str(relative) not in TRANSIENT
            and str(relative) != "restore-guard.json"
            and (len(parts) == 2 and parts[1] in DATABASES or relative.suffix == ".json"))


def verify_pair(path, *, expected_manifest_sha256, max_bytes):
    work, path = Work(max_bytes), directory(path)
    manifest_path = member(path, "manifest.json")
    if (not isinstance(expected_manifest_sha256, str) or
            not re.fullmatch(r"[0-9a-f]{64}", expected_manifest_sha256)
            or manifest_path.stat().st_size > 4_000_000
            or digest(manifest_path) != expected_manifest_sha256):
        raise ValueError("Paired manifest differs from its independently retained checksum")
    manifest = read_json(manifest_path)
    if manifest.get("schema") != SCHEMA or not 1 <= len(manifest.get("files", [])) <= 10000:
        raise ValueError("Invalid paired snapshot format")
    if (set(manifest["source"]) != {"index_directory", "ledger_directory"}
            or any(not isinstance(v, str) or not v for v in manifest["source"].values())
            or manifest["source"]["index_directory"] == manifest["source"]["ledger_directory"]):
        raise ValueError("Invalid paired source identities")
    seen = set()
    for entry in manifest["files"]:
        name = entry["path"]
        target = member(path, name)
        if (name in seen or not allowed_member(name) or type(entry["bytes"]) is not int
                or entry["bytes"] < 0 or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])):
            raise ValueError("Invalid or repeated paired snapshot member")
        seen.add(name)
        work.check(entry["bytes"])
        if target.stat().st_size != entry["bytes"] or digest(target) != entry["sha256"]:
            raise ValueError("Paired snapshot member checksum/size mismatch")
        work.total += entry["bytes"]
        if len(PurePosixPath(name).parts) == 2 and PurePosixPath(name).name in DATABASES:
            if database_check(target) != entry["database_counts"]:
                raise ValueError("Paired database counts differ")
    actual = {p.relative_to(path).as_posix() for p in path.rglob("*") if p.is_file()}
    if (any(p.is_symlink() for p in path.rglob("*")) or actual != seen | {"manifest.json"}
            or not {"index/service.sqlite3", "ledger/session.json"} <= seen
            or work.total != manifest["total_bytes"]):
        raise ValueError("Paired snapshot membership or total differs")
    if facts(path, manifest["source"], sorted(seen)) != manifest["snapshot"]:
        raise ValueError("Paired snapshot semantic identity differs")
    work.check()
    return manifest


def restore_pair(path, destination, *, expected_manifest_sha256, max_bytes):
    work, path = Work(max_bytes), directory(path)
    destination = new_target(destination, (path,))
    manifest = verify_pair(path, expected_manifest_sha256=expected_manifest_sha256, max_bytes=max_bytes)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(destination.name + ".partial-" + secrets.token_hex(4))
    staging.mkdir(mode=0o700)
    for role in ROLES:
        (staging / role).mkdir(mode=0o700)
    for entry in manifest["files"]:
        target = member(staging, entry["path"])
        copy_member(member(path, entry["path"]), target, work, expected_size=entry["bytes"])
        if digest(target) != entry["sha256"] or target.stat().st_size != entry["bytes"]:
            raise ValueError("Paired snapshot changed during restore")
    paths = [e["path"] for e in manifest["files"]]
    if facts(staging, manifest["source"], paths) != manifest["snapshot"]:
        raise ValueError("Restored pair differs before path migration")
    migrated = {"index_directory": str(destination / "index"),
                "ledger_directory": str(destination / "ledger")}
    guard = {"schema": "traze-restore-review-v1", "restored_at": timestamp(),
             "backup_manifest_sha256": expected_manifest_sha256, "acquisition": "blocked",
             "reason": "Compare with fresh authoritative accounting, controls and clocks before any separate writer activation decision."}
    for role in ("index", "ledger"):
        p = staging / role / "restore-guard.json"
        p.write_text(json.dumps(guard, indent=2) + "\n", encoding="utf-8")
        p.chmod(0o600)
    with closing(sqlite3.connect(staging / "index/service.sqlite3")) as db, db:
        binding = json.loads(db.execute("SELECT value FROM meta WHERE key=?", (KEY,)).fetchone()[0])
        binding.update(migrated)
        db.execute("UPDATE meta SET value=? WHERE key=?", (encode(binding), KEY))
        for identity, data in db.execute("SELECT id,data FROM collection_services").fetchall():
            value = json.loads(data)
            for field, target in migrated.items():
                if field in value:
                    value[field] = target
            db.execute("UPDATE collection_services SET data=? WHERE id=?", (encode(value), identity))
    if facts(staging, migrated, paths) != manifest["snapshot"]:
        raise ValueError("Path migration changed evidence, accounting or operating state")
    origin = {"schema": "traze-paired-restore-origin-v1", "manifest_sha256": expected_manifest_sha256,
              "original_source": manifest["source"], "migrated_source": migrated,
              "original_snapshot": manifest["snapshot"], "acquisition_blocked": True}
    p = staging / "recovery-origin.json"
    p.write_text(json.dumps(origin, indent=2) + "\n", encoding="utf-8")
    p.chmod(0o600)
    work.check()
    if destination.exists():
        raise ValueError("Restore destination appeared during recovery")
    staging.rename(destination)
    return {"status": "paired_restored_read_only", "manifest_sha256": expected_manifest_sha256,
            **migrated, "baseline_root": str(destination / "baselines"), "files": len(paths),
            "bytes": work.total, "seconds": time.monotonic() - work.started,
            "acquisition_blocked": True, "network_requests": 0}


def compare_pairs(older, authoritative, *, expected_manifest_sha256,
                  authoritative_manifest_sha256, max_bytes):
    """Assess a fresh trusted snapshot; never clear an interlock or merge state."""
    old = verify_pair(older, expected_manifest_sha256=expected_manifest_sha256, max_bytes=max_bytes)
    new = verify_pair(authoritative, expected_manifest_sha256=authoritative_manifest_sha256, max_bytes=max_bytes)
    if old["source"] != new["source"] or old["snapshot"]["binding_identity"] != new["snapshot"]["binding_identity"]:
        raise DataError("accounting_binding_changed", "Compare snapshots of the same authoritative pair")
    ledger = closed_ledger(directory(authoritative) / "ledger")
    check_checkpoint(ledger, old["snapshot"]["ledger_checkpoint"])
    for name, sha in old["snapshot"]["durable_json"].items():
        if "/collection-controls/" in name and new["snapshot"]["durable_json"].get(name) != sha:
            raise DataError("collection_control_unavailable", "Later state must preserve every recorded terminal stop")
    for identity, previous in old["snapshot"]["services"].items():
        current = new["snapshot"]["services"].get(identity)
        if (current is None or current["policy_digest"] != previous["policy_digest"]
                or current["next_height"] < previous["next_height"]
                or previous["state"] == "stopped" and (
                    current["state"] != "stopped" or current["stop"] != previous["stop"])):
            raise DataError("operating_policy_changed", "Later state cannot discard saved services, progress or terminal stops")
        before, after = previous["clock"], current["clock"]
        if before and (not after or after["armed_at"] != before["armed_at"]
                       or after["period"] < before["period"]
                       or after["max_seen_time"] < before["max_seen_time"]):
            raise DataError("operating_clock_changed", "Later state cannot reset an operating clock")
    changed = [key for key in old["snapshot"] if old["snapshot"][key] != new["snapshot"][key]]
    return {"status": "fresh_snapshot_required" if changed else "matches_authoritative_snapshot",
            "changed_components": changed,
            "additional_runs": new["snapshot"]["ledger_checkpoint"]["runs"] - old["snapshot"]["ledger_checkpoint"]["runs"],
            "additional_counted_bytes": new["snapshot"]["counted_response_bytes"] - old["snapshot"]["counted_response_bytes"],
            "older_manifest_sha256": expected_manifest_sha256,
            "authoritative_manifest_sha256": authoritative_manifest_sha256,
            "acquisition_blocked": True, "network_requests": 0,
            "next_action": "Restore the fresh authoritative pair into another new destination" if changed else
                           "Retain the restore interlocks until a separate reviewed operating decision",
            "qualification": "The operator must supply a fresh trusted authoritative snapshot; matching historical copies alone cannot establish freshness."}
