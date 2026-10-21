"""Committed WAL, accounting, terminal stops and fresh-target restore controls."""

import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from hemi_lookup.backup import backup, restore, verify_backup, digest
from hemi_lookup.catalogue import build_catalogue
from hemi_lookup.collection_store import CollectionStore, RunnerLock
from hemi_lookup.errors import DataError
from hemi_lookup.explorer import Explorer
from hemi_lookup.service import CollectionService
from hemi_lookup.storage import Session

ROOT = Path(__file__).resolve().parent.parent


class BackupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base = ROOT / "output/qa-backup"
        base.mkdir(parents=True, exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(dir=base)
        cls.addClassCleanup(cls.temp.cleanup)
        cls.fixture = Path(cls.temp.name) / "fixture.sqlite3"
        build_catalogue(ROOT, cls.fixture, block_directory=ROOT / "tests/fixtures/bitcoin_blocks")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "output/qa-backup")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        self.state.mkdir()
        shutil.copyfile(self.fixture, self.state / "service.sqlite3")
        with CollectionStore(self.state):
            pass
        self.ledger = {"schema": 1, "prior_validation_bytes": {}, "downloaded_bytes": 12345,
                       "runs": [{"id": "retained-run", "received_bytes": 12345, "requests": [], "body_attempts": 0}]}
        (self.state / "session.json").write_text(json.dumps(self.ledger))
        self.saved = self.root / "backup"
        self.restored = self.root / "restored"

    def save(self):
        return backup(self.state, self.saved, max_bytes=64_000_000)

    def test_wal_restore_preserves_proofs_ledger_and_terminal_stop(self):
        service = CollectionService(self.root, self.state)
        policy = {"period_seconds": 60, "response_bytes": 6_000_000, "requests": 2,
                  "body_attempts": 1, "cache_bytes": 4_000_000, "state_bytes": 30_000_000}
        identity = service.create(965931, policy)["id"]
        stopped = service.stop(identity, "Retain this operator stop")
        original = Explorer(self.state).read("blocks/5243119", {})
        with closing(sqlite3.connect(self.state / "service.sqlite3")) as writer:
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("INSERT INTO meta VALUES ('wal-marker','committed')")
            writer.commit()
            self.assertTrue((self.state / "service.sqlite3-wal").exists())
            self.save()
        manifest = verify_backup(self.saved, max_bytes=64_000_000)
        self.assertFalse(manifest["raw_cache_included"])
        result = restore(self.saved, self.restored, max_bytes=64_000_000)
        self.assertTrue(result["acquisition_blocked"])
        packet = Explorer(self.restored).read("blocks/5243119", {})
        self.assertEqual(packet["verification"]["proofs_checked"], 4)
        self.assertEqual(packet["receipt"]["proofs"], original["receipt"]["proofs"])
        self.assertEqual(json.loads((self.restored / "session.json").read_text()), self.ledger)
        self.assertEqual(CollectionStore.read_service(self.restored, identity), stopped)
        self.assertEqual((self.restored / "collection-controls" / (identity + ".json")).read_bytes(),
                         (self.state / "collection-controls" / (identity + ".json")).read_bytes())
        with closing(sqlite3.connect(self.restored / "service.sqlite3")) as db:
            self.assertEqual(db.execute("SELECT value FROM meta WHERE key='wal-marker'").fetchone()[0], "committed")
        self.assertFalse((self.restored / "collection-runner.json").exists())
        self.assertFalse((self.restored / "session.lock").exists())

    def test_restored_acquisition_interlock_does_not_mutate_accounting(self):
        self.save()
        restore(self.saved, self.restored, max_bytes=64_000_000)
        before = digest(self.restored / "session.json")
        with self.assertRaisesRegex(DataError, "operator reconciliation"):
            Session(self.restored, self.root)
        with self.assertRaises(DataError):
            CollectionService(self.root, self.restored).run("a" * 32, max_cycles=1)
        with self.assertRaises(DataError):
            CollectionService(self.root, self.restored).create(1, {})
        self.assertEqual(digest(self.restored / "session.json"), before)
        self.assertFalse((self.restored / "session.lock").exists())

    def test_backup_refuses_active_collector_or_unresolved_discovery_lock(self):
        with RunnerLock(self.state):
            with self.assertRaises(DataError):
                self.save()
        (self.state / "session.lock").write_text("other-owner")
        with self.assertRaises(DataError):
            self.save()
        self.assertEqual((self.state / "session.lock").read_text(), "other-owner")

    def test_size_limit_and_unknown_files_fail_without_a_complete_backup(self):
        with self.assertRaises(ValueError):
            backup(self.state, self.saved, max_bytes=1)
        self.assertFalse(self.saved.exists())
        (self.state / "unknown.dat").write_text("operator file")
        with self.assertRaisesRegex(ValueError, "Unclassified"):
            self.save()

    def test_disposable_cache_not_needed_for_restored_proofs(self):
        cache = self.state / "service-blocks" / ("a" * 32)
        cache.mkdir(parents=True)
        (cache / ("b" * 64 + ".bin")).write_bytes(b"disposable")
        self.save()
        restore(self.saved, self.restored, max_bytes=64_000_000)
        self.assertFalse((self.restored / "service-blocks").exists())
        self.assertEqual(Explorer(self.restored).read("blocks/5243119", {})["verification"]["proofs_checked"], 4)

    def test_tamper_duplicate_traversal_and_existing_destination_rejected(self):
        self.save()
        manifest_path = self.saved / "manifest.json"
        original = manifest_path.read_bytes()
        manifest = json.loads(original)
        for bad in ("", "../outside.json", "C:/outside.json", "session.lock", "collection-runner.json"):
            corrupt = json.loads(original)
            corrupt["files"][0]["path"] = bad
            manifest_path.write_text(json.dumps(corrupt))
            with self.subTest(bad=bad), self.assertRaises((ValueError, OSError)):
                restore(self.saved, self.restored, max_bytes=64_000_000)
        corrupt = json.loads(original)
        corrupt["files"].append(corrupt["files"][0])
        manifest_path.write_text(json.dumps(corrupt))
        with self.assertRaises(ValueError):
            verify_backup(self.saved, max_bytes=64_000_000)
        manifest_path.write_bytes(original)
        (self.saved / "session.json").write_text("tampered")
        with self.assertRaises(ValueError):
            restore(self.saved, self.restored, max_bytes=64_000_000)
        self.assertFalse(self.restored.exists())
        with self.assertRaises(ValueError):
            self.save()
        with self.assertRaises(ValueError):
            restore(self.saved, self.state, max_bytes=64_000_000)

    def test_backup_growth_after_verification_is_bounded_during_restore(self):
        self.save()
        def changing(directory, **kwargs):
            manifest = verify_backup(directory, **kwargs)
            (self.saved / "session.json").write_bytes(b"x" * 2_000_000)
            return manifest
        with patch("hemi_lookup.backup.verify_backup", side_effect=changing):
            with self.assertRaisesRegex(ValueError, "grew during restore"):
                restore(self.saved, self.restored, max_bytes=64_000_000)
        self.assertFalse(self.restored.exists())
        staging = next(self.root.glob("restored.partial-*"))
        self.assertEqual((staging / "session.json").stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
