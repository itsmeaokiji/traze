"""Quiesced local state backups and new-target, acquisition-disabled restores.

SQLite's backup API includes committed WAL records. No raw-body cache is needed
for compiled evidence. No overwrite, source call or automatic writer resumption.
"""

import argparse
import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path, PurePosixPath

from .catalogue import timestamp
from .collection_store import RunnerLock
from .errors import DataError

SCHEMA = "traze-state-backup-v1"
DATABASES = {"service.sqlite3", "catalogue.sqlite3"}
DISPOSABLE = {"blocks", "collection_blocks", "service-blocks"}
TRANSIENT = {"collection.lock", "session.lock", "collection-runner.json"}


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def member(root, name):
    if not isinstance(name, str):
        raise ValueError("Unsafe backup member")
    part = PurePosixPath(name)
    if (not isinstance(name, str) or "\\" in name or ":" in name or part.is_absolute()
            or not part.parts or any(p in (".", "..") for p in part.parts) or part.as_posix() != name):
        raise ValueError("Unsafe backup member")
    target = Path(root) / name
    if not target.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError("Backup member escapes its directory")
    if any(p.is_symlink() for p in [target, *list(target.parents)[:len(part.parts) - 1]]):
        raise ValueError("Backup members must not be symlinks")
    return target


def durable_files(state):
    result = []
    for parent, dirs, names in os.walk(state, followlinks=False):
        parent = Path(parent)
        for name in dirs[:]:
            path = parent / name
            if path.is_symlink():
                raise ValueError("State directory contains a symlink")
            if parent == state and name in DISPOSABLE:
                dirs.remove(name)
        for name in names:
            path = parent / name
            if path.is_symlink():
                raise ValueError("State files must not be symlinks")
            relative = path.relative_to(state).as_posix()
            if parent == state and (name in TRANSIENT or name.endswith((".sqlite3-wal", ".sqlite3-shm"))):
                continue
            if (parent == state and name in DATABASES) or path.suffix == ".json":
                result.append(relative)
            else:
                raise ValueError("Unclassified durable state file: " + relative)
            if len(result) > 10000:
                raise ValueError("State exceeds the backup file-count limit")
    return sorted(result)


@contextmanager
def quiescent(state):
    """Exclude Traze collector and discovery writers; do not clear stale locks."""
    with RunnerLock(state):
        lock = state / "session.lock"
        token = secrets.token_hex(16)
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise DataError("session_locked", "Quiesce or recover the existing source writer before backup") from None
        with os.fdopen(fd, "w") as handle:
            handle.write(token)
        try:
            yield
        finally:
            if lock.read_text(encoding="utf-8") != token:
                raise DataError("session_changed", "Backup lock ownership changed; preserve state")
            lock.unlink()


def database_check(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)] or db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("Restored SQLite integrity check failed")
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"publications", "associations", "headers", "templates", "meta"}
        if not required.issubset(names):
            raise ValueError("Backup database is not a publication index")
        result = {}
        for table in sorted(required | (names & {"collection_jobs", "collection_services", "collection_metadata"})):
            result[table] = db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
        return result


def backup(state, destination, *, max_bytes):
    state, destination = Path(state).resolve(strict=True), Path(destination).absolute()
    if type(max_bytes) is not int or max_bytes <= 0 or not state.is_dir():
        raise ValueError("Use an existing state directory and positive backup size limit")
    if destination.exists() or destination.is_symlink() or destination.resolve().is_relative_to(state):
        raise ValueError("Choose a new backup directory outside the state directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(destination.name + ".partial-" + secrets.token_hex(4))
    staging.mkdir(mode=0o700)
    started, entries, total = time.monotonic(), [], 0
    with quiescent(state), ExitStack() as stack:
        files = durable_files(state)
        if not (set(files) & DATABASES):
            raise ValueError("State contains no index")
        # Freeze cooperating and direct SQLite writers while all databases and
        # the append-only accounting/control files are copied together.
        for name in sorted(set(files) & DATABASES):
            guard = stack.enter_context(closing(sqlite3.connect(state / name, timeout=1)))
            guard.execute("BEGIN IMMEDIATE")
        originals = {name: digest(state / name) for name in files if name not in DATABASES}
        for name in files:
            source, target = member(state, name), member(staging, name)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if name in DATABASES:
                source_size = source.stat().st_size + sum(p.stat().st_size for p in (state / (name + "-wal"),) if p.exists())
                if total + source_size > max_bytes:
                    raise ValueError("Backup exceeds its configured size limit")
                with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as size_reader:
                    page_size = size_reader.execute("PRAGMA page_size").fetchone()[0]
                def progress(status, remaining, pages):
                    if time.monotonic() - started > 60 or total + pages * page_size > max_bytes:
                        raise ValueError("SQLite backup exceeded its work limit")
                with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as reader, closing(sqlite3.connect(target)) as writer:
                    reader.backup(writer, pages=256, progress=progress, sleep=.01)
                    writer.execute("PRAGMA journal_mode=DELETE")
                summary = database_check(target)
            else:
                if total + source.stat().st_size > max_bytes:
                    raise ValueError("Backup exceeds its configured size limit")
                with source.open("rb") as src, target.open("xb") as dst:
                    for chunk in iter(lambda: src.read(1024 * 1024), b""):
                        if total + dst.tell() + len(chunk) > max_bytes or time.monotonic() - started > 60:
                            raise ValueError("Backup exceeded its size/time limit during copy")
                        dst.write(chunk)
                summary = None
            target.chmod(0o600)
            total += target.stat().st_size
            if total > max_bytes or time.monotonic() - started > 60:
                raise ValueError("Backup exceeded its size/time limit")
            entries.append({"path": name, "bytes": target.stat().st_size, "sha256": digest(target), "database_counts": summary})
        if durable_files(state) != files or any(digest(state / name) != sha for name, sha in originals.items()):
            raise ValueError("State files changed while being backed up")
    manifest = {"schema": SCHEMA, "created_at": timestamp(), "files": entries, "total_bytes": total,
                "raw_cache_included": False, "runtime_locks_included": False,
                "qualification": "Quiesced SQLite/accounting/control snapshot; not a zero-loss backup or authority to resume acquisition."}
    path = staging / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
    staging.rename(destination)
    return {"status": "backed_up", "manifest_sha256": digest(destination / "manifest.json"), "files": len(entries), "bytes": total}


def verify_backup(directory, *, max_bytes):
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("Use a positive backup size limit")
    directory = Path(directory).resolve(strict=True)
    path = directory / "manifest.json"
    if path.is_symlink() or path.stat().st_size > 4_000_000:
        raise ValueError("Invalid backup manifest")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    entries = manifest.get("files", [])
    if manifest.get("schema") != SCHEMA or not 1 <= len(entries) <= 10000:
        raise ValueError("Invalid backup format")
    seen, total = set(), 0
    for entry in entries:
        name = entry["path"]
        member_path = member(directory, name)
        part = PurePosixPath(name)
        if name in seen or name == "manifest.json" or type(entry["bytes"]) is not int or entry["bytes"] < 0:
            raise ValueError("Invalid or repeated backup member")
        if (part.parts[0] in DISPOSABLE or name in TRANSIENT or
                not (name in DATABASES or part.suffix == ".json")):
            raise ValueError("Backup contains transient or unclassified state")
        seen.add(name)
        total += entry["bytes"]
        if (total > max_bytes or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
                or member_path.stat().st_size != entry["bytes"] or digest(member_path) != entry["sha256"]):
            raise ValueError("Backup file checksum/size mismatch")
        if name in DATABASES and database_check(member_path) != entry["database_counts"]:
            raise ValueError("Backup database counts changed")
    actual = {p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()}
    if any(p.is_symlink() for p in directory.rglob("*")):
        raise ValueError("Backup contains a symlink")
    if actual != seen | {"manifest.json"} or total != manifest["total_bytes"] or not seen & DATABASES:
        raise ValueError("Backup member set/total mismatch")
    return manifest


def restore(directory, destination, *, max_bytes):
    directory, destination = Path(directory).resolve(strict=True), Path(destination).absolute()
    if destination.exists() or destination.is_symlink() or destination.resolve().is_relative_to(directory):
        raise ValueError("Restore requires a new destination outside the backup")
    manifest = verify_backup(directory, max_bytes=max_bytes)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(destination.name + ".partial-" + secrets.token_hex(4))
    staging.mkdir(mode=0o700)
    for entry in manifest["files"]:
        target, source = member(staging, entry["path"]), member(directory, entry["path"])
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with source.open("rb") as src, target.open("xb") as dst:
            for chunk in iter(lambda: src.read(1024 * 1024), b""):
                if dst.tell() + len(chunk) > entry["bytes"]:
                    raise ValueError("Backup grew during restore")
                dst.write(chunk)
        target.chmod(0o600)
        if digest(target) != entry["sha256"]:
            raise ValueError("Backup changed during restore")
        if entry["path"] in DATABASES and database_check(target) != entry["database_counts"]:
            raise ValueError("Restored database mismatch")
    guard = staging / "restore-guard.json"
    guard.write_text(json.dumps({"schema": "traze-restore-review-v1", "restored_at": timestamp(),
        "backup_manifest_sha256": digest(directory / "manifest.json"), "acquisition": "blocked",
        "reason": "Reconcile later ledger usage, stops, clocks and policies with the authoritative state before any writer resumes."}, indent=2) + "\n", encoding="utf-8")
    guard.chmod(0o600)
    staging.rename(destination)
    return {"status": "restored_read_only", "files": len(manifest["files"]), "bytes": manifest["total_bytes"],
            "acquisition_blocked": True, "manifest_sha256": digest(directory / "manifest.json")}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local quiesced backup and fresh-target restore; no acquisition")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("backup", "verify", "restore"):
        item = sub.add_parser(command)
        item.add_argument("--source", type=Path, required=True)
        item.add_argument("--max-bytes", type=int, required=True)
        if command != "verify":
            item.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            result = verify_backup(args.source, max_bytes=args.max_bytes)
        else:
            result = (backup if args.command == "backup" else restore)(args.source, args.destination, max_bytes=args.max_bytes)
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, DataError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
