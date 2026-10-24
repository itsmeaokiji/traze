"""Paired-backup and guarded migration checks; public networking forbidden."""

import copy
import io
import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from hemi_lookup.accounting import accounting_directory, accounting_state, bind_accounting
from hemi_lookup.backup import digest, main
from hemi_lookup.catalogue import build_catalogue, encode
from hemi_lookup.collection_store import CollectionStore, RunnerLock
from hemi_lookup.errors import DataError
from hemi_lookup.explorer import Explorer
from hemi_lookup.proof import verify_receipt
from hemi_lookup.paired_recovery import backup_pair, compare_pairs, restore_pair, verify_pair
from hemi_lookup.service import CollectionService
from hemi_lookup.storage import PRIOR_REPORTS, Session, atomic_json

ROOT = Path(__file__).resolve().parent.parent
LIMIT = 64_000_000


class RecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qa = ROOT / "output/qa-paired-recovery"
        qa.mkdir(parents=True, exist_ok=True)
        cls.fixture_temp = tempfile.TemporaryDirectory(dir=qa)
        cls.addClassCleanup(cls.fixture_temp.cleanup)
        cls.fixture = Path(cls.fixture_temp.name) / "fixture.sqlite3"
        build_catalogue(ROOT, cls.fixture, block_directory=ROOT / "tests/fixtures/bitcoin_blocks")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "output/qa-paired-recovery")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.index, self.ledger = self.root / "index", self.root / "ledger"
        self.index.mkdir()
        shutil.copyfile(self.fixture, self.index / "service.sqlite3")
        atomic_json(self.root / PRIOR_REPORTS[0], {"http_response_bytes": 123})
        with Session(self.ledger, self.root):
            pass
        with CollectionStore(self.index) as store:
            bind_accounting(store, self.ledger, self.root)
        policy = {"period_seconds": 60, "response_bytes": 6_000_000, "requests": 20,
                  "body_attempts": 2, "cache_bytes": 4_000_000, "state_bytes": 30_000_000}
        self.identity = CollectionService(self.root, self.index).create(965928, policy)["id"]
        self.change_service(clock={"armed_at": 1000, "max_seen_time": 1015, "period": 0})
        self.saved, self.restored = self.root / "backup", self.root / "restored"
        self.http = patch("hemi_lookup.network.HTTP.read", side_effect=AssertionError("Public sources forbidden")).start()
        self.addCleanup(patch.stopall)

    def tearDown(self):
        self.http.assert_not_called()

    def change_service(self, **fields):
        with CollectionStore(self.index) as store:
            service = store.service(self.identity)
            service.update(fields)
            store.save_service(service)
        return service

    def save(self, path=None):
        return backup_pair(self.index, path or self.saved, ledger_directory=self.ledger,
                           prior_root=self.root, max_bytes=LIMIT)

    def restore(self, result, destination=None):
        return restore_pair(self.saved, destination or self.restored,
                            expected_manifest_sha256=result["manifest_sha256"], max_bytes=LIMIT)

    def compare(self, old, new, latest):
        return compare_pairs(self.saved, latest, expected_manifest_sha256=old["manifest_sha256"],
                             authoritative_manifest_sha256=new["manifest_sha256"], max_bytes=LIMIT)

    def later_usage(self, amount=2000):
        value = json.loads((self.ledger / "session.json").read_text())
        value["downloaded_bytes"] += amount
        value["runs"].append({"id": "controlled-later-run", "received_bytes": amount,
                              "requests": [], "body_attempts": 0,
                              "closed_at": "2026-10-24T00:00:00+00:00"})
        atomic_json(self.ledger / "session.json", value)

    def test_pair_restore_preserves_proofs_baselines_clocks_and_original_ledger(self):
        CollectionService(self.root, self.index).stop(self.identity, "Preserve this terminal stop")
        before = Explorer(self.index).read("blocks/5243119", {})
        ledger_sha = digest(self.ledger / "session.json")
        service = CollectionStore.read_service(self.index, self.identity)
        result = self.save()
        manifest = verify_pair(self.saved, expected_manifest_sha256=result["manifest_sha256"], max_bytes=LIMIT)
        restored = self.restore(result)
        self.assertEqual(manifest["snapshot"]["counted_response_bytes"], 123)
        self.assertTrue(restored["acquisition_blocked"])
        self.assertEqual(accounting_directory(self.restored / "index"), self.restored / "ledger")
        after = Explorer(self.restored / "index").read("blocks/5243119", {})
        self.assertEqual(after["receipt"]["proofs"], before["receipt"]["proofs"])
        self.assertEqual(verify_receipt(after["receipt"])["proofs_checked"], 4)
        self.assertEqual(digest(self.restored / "ledger/session.json"), ledger_sha)
        self.assertEqual(digest(self.ledger / "session.json"), ledger_sha)
        moved = CollectionStore.read_service(self.restored / "index", self.identity)
        for key in ("state", "stop", "policy", "policy_digest", "clock", "ledger_checkpoint", "next_height"):
            self.assertEqual(moved[key], service[key])
        self.assertEqual(moved["index_directory"], str(self.restored / "index"))
        self.assertEqual(moved["ledger_directory"], str(self.restored / "ledger"))
        self.assertEqual(accounting_state(self.restored / "index", prior_root=self.restored / "baselines")
                         ["prior_validation_bytes"], {PRIOR_REPORTS[0]: 123})

    def test_committed_wal_records_are_snapshotted(self):
        with closing(sqlite3.connect(self.index / "service.sqlite3")) as writer:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("INSERT INTO meta VALUES ('paired-wal-marker','retained')")
            writer.commit()
            self.assertTrue((self.index / "service.sqlite3-wal").exists())
            self.restore(self.save())
        with closing(sqlite3.connect(self.restored / "index/service.sqlite3")) as reader:
            self.assertEqual(reader.execute("SELECT value FROM meta WHERE key='paired-wal-marker'").fetchone()[0], "retained")

    def test_idle_committed_wal_is_preserved_without_source_checkpoint(self):
        dbpath = self.index / "service.sqlite3"
        writer = sqlite3.connect(dbpath)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO meta VALUES ('idle-wal-marker','retained')")
        writer.commit()
        pinned = sqlite3.connect(dbpath.as_uri() + "?mode=ro", uri=True)
        pinned.execute("BEGIN")
        pinned.execute("SELECT COUNT(*) FROM meta").fetchone()
        writer.close()
        pinned.close()
        walpath = Path(str(dbpath) + "-wal")
        self.assertTrue(walpath.exists())
        before = {p: digest(p) for p in (dbpath, walpath)}
        self.save()
        self.assertEqual({p: digest(p) for p in before}, before)
        with closing(sqlite3.connect((self.saved / "index/service.sqlite3").as_uri() + "?mode=ro", uri=True)) as db:
            self.assertEqual(db.execute("SELECT value FROM meta WHERE key='idle-wal-marker'").fetchone()[0], "retained")

    def test_wrong_ledger_is_refused_without_destination(self):
        wrong = self.root / "wrong"
        with Session(wrong, self.root): pass
        with self.assertRaises(DataError):
            backup_pair(self.index, self.saved, ledger_directory=wrong, prior_root=self.root, max_bytes=LIMIT)
        self.assertFalse(self.saved.exists())

    def test_active_index_and_ledger_writers_are_refused(self):
        for directory in (self.index, self.ledger):
            with self.subTest(directory=directory), RunnerLock(directory), self.assertRaises(DataError):
                self.save()
            (directory / "session.lock").write_text("another-owner")
            try:
                with self.assertRaises(DataError): self.save()
                self.assertEqual((directory / "session.lock").read_text(), "another-owner")
            finally:
                (directory / "session.lock").unlink()
        self.assertFalse(self.saved.exists())

    def test_changed_or_missing_original_baseline_is_refused(self):
        path = self.root / PRIOR_REPORTS[0]
        path.write_text('{"http_response_bytes":124}')
        with self.assertRaisesRegex(ValueError, "validation report"): self.save()
        path.unlink()
        with self.assertRaisesRegex(ValueError, "validation report"): self.save()
        self.assertFalse(self.saved.exists())

    def test_reset_or_unclosed_ledger_is_refused(self):
        original = (self.ledger / "session.json").read_bytes()
        value = json.loads(original)
        value["runs"] = []
        atomic_json(self.ledger / "session.json", value)
        with self.assertRaises(DataError): self.save()
        value = json.loads(original)
        value["runs"].append({"id": "unclosed", "received_bytes": 0, "requests": [], "body_attempts": 0})
        atomic_json(self.ledger / "session.json", value)
        with self.assertRaises(DataError): self.save()

    def test_modified_or_missing_member_is_rejected_before_restore(self):
        result = self.save()
        path = self.saved / "ledger/session.json"
        original = path.read_bytes()
        path.write_bytes(original + b" ")
        with self.assertRaisesRegex(ValueError, "checksum/size"): self.restore(result)
        path.unlink()
        with self.assertRaises(OSError): self.restore(result)
        self.assertFalse(self.restored.exists())

    def test_rewritten_manifest_does_not_replace_the_retained_checksum(self):
        result = self.save()
        path = self.saved / "manifest.json"
        value = json.loads(path.read_text())
        value["source"]["ledger_directory"] = "another-ledger"
        atomic_json(path, value)
        with self.assertRaisesRegex(ValueError, "independently retained"): self.restore(result)
        self.assertFalse(self.restored.exists())

    def test_mixed_ledger_from_another_snapshot_is_refused(self):
        result = self.save()
        self.later_usage()
        latest = self.root / "latest"
        self.save(latest)
        shutil.copyfile(latest / "ledger/session.json", self.saved / "ledger/session.json")
        with self.assertRaisesRegex(ValueError, "checksum/size"): self.restore(result)

    def test_duplicate_or_traversal_member_is_refused(self):
        self.save()
        path = self.saved / "manifest.json"
        original = json.loads(path.read_text())
        for bad in ("../outside.json", "index/../../outside.json", "index/session.lock", "C:/outside.json"):
            value = copy.deepcopy(original)
            value["files"][0]["path"] = bad
            atomic_json(path, value)
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                verify_pair(self.saved, expected_manifest_sha256=digest(path), max_bytes=LIMIT)
        value = copy.deepcopy(original)
        value["files"].append(value["files"][0])
        atomic_json(path, value)
        with self.assertRaisesRegex(ValueError, "repeated"):
            verify_pair(self.saved, expected_manifest_sha256=digest(path), max_bytes=LIMIT)

    def test_semantically_wrong_binding_fails_even_with_updated_member_hash(self):
        self.save()
        dbpath = self.saved / "index/service.sqlite3"
        with closing(sqlite3.connect(dbpath)) as db, db:
            row = db.execute("SELECT value FROM meta WHERE key='collection_ledger_binding'").fetchone()
            value = json.loads(row[0]); value["ledger_directory"] = "wrong-pair"
            db.execute("UPDATE meta SET value=? WHERE key='collection_ledger_binding'", (encode(value),))
        manifest = json.loads((self.saved / "manifest.json").read_text())
        entry = next(e for e in manifest["files"] if e["path"] == "index/service.sqlite3")
        entry.update(bytes=dbpath.stat().st_size, sha256=digest(dbpath))
        manifest["total_bytes"] = sum(e["bytes"] for e in manifest["files"])
        atomic_json(self.saved / "manifest.json", manifest)
        with self.assertRaises(DataError):
            verify_pair(self.saved, expected_manifest_sha256=digest(self.saved / "manifest.json"), max_bytes=LIMIT)

    def test_existing_destinations_and_sources_are_never_overwritten(self):
        result = self.save()
        with self.assertRaises(ValueError): self.save()
        with self.assertRaises(ValueError): self.restore(result, self.index)
        with self.assertRaises(ValueError): self.restore(result, self.saved / "nested")

    def test_foreign_recorded_paths_are_migrated_without_accessing_the_old_host(self):
        from hemi_lookup.paired_recovery import facts
        self.save()
        manifest_path = self.saved / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        foreign = {"index_directory": "/previous-host/traze-index",
                   "ledger_directory": "/previous-host/traze-accounting"}
        dbpath = self.saved / "index/service.sqlite3"
        with closing(sqlite3.connect(dbpath)) as db, db:
            binding = json.loads(db.execute("SELECT value FROM meta WHERE key='collection_ledger_binding'").fetchone()[0])
            binding.update(foreign)
            db.execute("UPDATE meta SET value=? WHERE key='collection_ledger_binding'", (encode(binding),))
            for identity, raw in db.execute("SELECT id,data FROM collection_services").fetchall():
                value = json.loads(raw); value.update(foreign)
                db.execute("UPDATE collection_services SET data=? WHERE id=?", (encode(value), identity))
        manifest["source"] = foreign
        entry = next(e for e in manifest["files"] if e["path"] == "index/service.sqlite3")
        entry.update(bytes=dbpath.stat().st_size, sha256=digest(dbpath))
        manifest["total_bytes"] = sum(e["bytes"] for e in manifest["files"])
        manifest["snapshot"] = facts(self.saved, foreign, [e["path"] for e in manifest["files"]])
        atomic_json(manifest_path, manifest)
        result = self.restore({"manifest_sha256": digest(manifest_path)})
        self.assertEqual(accounting_directory(Path(result["index_directory"])), Path(result["ledger_directory"]))
        self.assertEqual(Explorer(Path(result["index_directory"])).read("blocks/5243119", {})["verification"]["proofs_checked"], 4)

    def test_restore_interlocks_block_sources_and_service_changes(self):
        self.restore(self.save())
        before = digest(self.restored / "ledger/session.json")
        with self.assertRaises(DataError): Session(self.restored / "ledger", self.restored / "baselines")
        controller = CollectionService(self.restored / "baselines", self.restored / "index")
        with self.assertRaises(DataError): controller.run(self.identity, max_cycles=1)
        with self.assertRaises(DataError): controller.create(1, {})
        self.assertEqual(digest(self.restored / "ledger/session.json"), before)
        self.assertFalse((self.restored / "ledger/session.lock").exists())
        with self.assertRaises(DataError):
            backup_pair(self.restored / "index", self.root / "restored-as-authority",
                        ledger_directory=self.restored / "ledger", prior_root=self.restored / "baselines", max_bytes=LIMIT)

    def test_growth_after_verification_does_not_publish_a_restore(self):
        result = self.save()
        original = verify_pair
        def changed(*args, **options):
            manifest = original(*args, **options)
            with (self.saved / "ledger/session.json").open("ab") as f: f.write(b"growth")
            return manifest
        with patch("hemi_lookup.paired_recovery.verify_pair", side_effect=changed), self.assertRaisesRegex(ValueError, "changed size"):
            self.restore(result)
        self.assertFalse(self.restored.exists())

    def test_size_and_unclassified_files_refuse_completed_backup(self):
        with self.assertRaises(ValueError):
            backup_pair(self.index, self.saved, ledger_directory=self.ledger, prior_root=self.root, max_bytes=1)
        self.assertFalse(self.saved.exists())
        (self.ledger / "unknown.bin").write_bytes(b"unclassified")
        with self.assertRaisesRegex(ValueError, "Unclassified"): self.save()

    def test_changed_membership_during_snapshot_is_refused(self):
        from hemi_lookup.paired_recovery import copy_member
        def changed(*args, **options):
            result = copy_member(*args, **options)
            atomic_json(self.index / "late-control.json", {"controlled": True})
            return result
        with patch("hemi_lookup.paired_recovery.copy_member", side_effect=changed), self.assertRaisesRegex(ValueError, "membership changed"):
            self.save()
        self.assertFalse(self.saved.exists())

    def test_newer_usage_clock_and_stop_require_fresh_pair_restore(self):
        old = self.save()
        self.restore(old)
        restored_sha = digest(self.restored / "ledger/session.json")
        self.later_usage()
        self.change_service(clock={"armed_at": 1000, "max_seen_time": 1090, "period": 1})
        CollectionService(self.root, self.index).stop(self.identity, "Later terminal stop")
        latest = self.root / "latest"
        new = self.save(latest)
        result = self.compare(old, new, latest)
        self.assertEqual(result["status"], "fresh_snapshot_required")
        self.assertEqual(result["additional_runs"], 1)
        self.assertEqual(result["additional_counted_bytes"], 2000)
        self.assertIn("services", result["changed_components"])
        self.assertEqual(digest(self.restored / "ledger/session.json"), restored_sha)
        fresh = self.root / "fresh-restore"
        restore_pair(latest, fresh, expected_manifest_sha256=new["manifest_sha256"], max_bytes=LIMIT)
        self.assertEqual(digest(fresh / "ledger/session.json"), digest(self.ledger / "session.json"))
        restored = CollectionStore.read_service(fresh / "index", self.identity)
        self.assertEqual(restored["state"], "stopped")
        self.assertEqual(restored["clock"], {"armed_at": 1000, "max_seen_time": 1090, "period": 1})
        self.assertTrue((fresh / "index/collection-controls" / (self.identity + ".json")).is_file())
        self.assertTrue((fresh / "index/restore-guard.json").is_file())

    def test_compare_rejects_accounting_rollback(self):
        original = (self.ledger / "session.json").read_bytes()
        self.later_usage()
        old = self.save()
        (self.ledger / "session.json").write_bytes(original)
        latest = self.root / "rolled-back"
        new = self.save(latest)
        with self.assertRaises(DataError): self.compare(old, new, latest)

    def test_compare_rejects_operating_clock_reset(self):
        old = self.save()
        self.change_service(clock={"armed_at": 999, "max_seen_time": 1015, "period": 0})
        latest = self.root / "reset-clock"
        new = self.save(latest)
        with self.assertRaisesRegex(DataError, "operating clock"): self.compare(old, new, latest)

    def test_missing_terminal_control_cannot_be_snapshotted(self):
        CollectionService(self.root, self.index).stop(self.identity, "Keep this stop")
        (self.index / "collection-controls" / (self.identity + ".json")).unlink()
        with self.assertRaisesRegex(DataError, "terminal control"): self.save()

    def test_matching_snapshot_comparison_does_not_authorize_acquisition(self):
        old = self.save()
        result = self.compare(old, old, self.saved)
        self.assertEqual(result["status"], "matches_authoritative_snapshot")
        self.assertTrue(result["acquisition_blocked"])
        self.assertEqual(result["additional_runs"], 0)

    def test_compare_refuses_removed_stop_even_with_matching_ledger(self):
        CollectionService(self.root, self.index).stop(self.identity, "Cannot be undone by restore")
        old = self.save()
        self.change_service(state="queued", stop=None)
        (self.index / "collection-controls" / (self.identity + ".json")).unlink()
        latest = self.root / "discarded-stop"
        new = self.save(latest)
        with self.assertRaisesRegex(DataError, "terminal stop"):
            self.compare(old, new, latest)

    def test_unavailable_later_authority_keeps_existing_restore_blocked(self):
        old = self.save()
        self.restore(old)
        before = digest(self.restored / "ledger/session.json")
        with self.assertRaises(OSError):
            compare_pairs(self.saved, self.root / "unavailable-authority",
                          expected_manifest_sha256=old["manifest_sha256"],
                          authoritative_manifest_sha256=old["manifest_sha256"], max_bytes=LIMIT)
        self.assertEqual(digest(self.restored / "ledger/session.json"), before)
        for role in ("index", "ledger"):
            self.assertTrue((self.restored / role / "restore-guard.json").is_file())

    def test_pair_cli_routes_preserve_explicit_checksum_and_paths(self):
        def command(*arguments):
            output = io.StringIO()
            with redirect_stdout(output): code = main(list(arguments))
            self.assertEqual(code, 0, output.getvalue())
            return json.loads(output.getvalue())
        saved = command("backup-pair", "--source", str(self.index), "--destination", str(self.saved),
                        "--ledger-dir", str(self.ledger), "--application-root", str(self.root), "--max-bytes", str(LIMIT))
        options = ("--source", str(self.saved), "--expected-manifest-sha256", saved["manifest_sha256"], "--max-bytes", str(LIMIT))
        verified = command("verify-pair", *options)
        self.assertEqual(verified["status"], "paired_backup_verified")
        restored = command("restore-pair", *options, "--destination", str(self.restored))
        self.assertTrue(restored["acquisition_blocked"])
        compared = command("compare-pair", *options, "--authoritative", str(self.saved),
                           "--authoritative-manifest-sha256", saved["manifest_sha256"])
        self.assertEqual(compared["status"], "matches_authoritative_snapshot")


if __name__ == "__main__":
    unittest.main()
