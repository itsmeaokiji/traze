"""Durable finite collection, independent evidence and concurrent read controls."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from collection_fixtures import CollectionSources, ROOT
from hemi_lookup.catalogue import Catalogue
from hemi_lookup.catalogue_lookup import recorded_lookup
from hemi_lookup.collection_store import CollectionStore, RunnerLock, collection_status
from hemi_lookup.collector import Collector
from hemi_lookup.errors import DataError
from hemi_lookup.proof import verify_receipt
from hemi_lookup.storage import Session
from hemi_lookup.storage import atomic_json
from hemi_lookup.web import Jobs


class CollectionTests(unittest.TestCase):
    def setUp(self):
        qa = ROOT / "output/qa-temp-collection"
        qa.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=qa)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / ".hemi_lookup_data"

    def create(self, start=965931, end=965931, **limits):
        with CollectionStore(self.state) as store:
            return store.create(start, end, **limits)["id"]

    def run_job(self, identity, **modes):
        return Collector(self.root, sources_factory=lambda session: CollectionSources(session, **modes)).run(identity)

    def test_new_collection_exports_target_bound_evidence_and_reuses_bodies(self):
        identity = self.create()
        result = self.run_job(identity)
        self.assertEqual(result["items"]["965931"]["state"], "processed")
        self.assertGreater(result["usage"]["requests"], 0)
        packet = recorded_lookup(self.state / "service.sqlite3", 5243119)
        self.assertIsNotNone(packet)
        self.assertGreater(verify_receipt(packet)["proofs_checked"], 0)
        self.assertEqual(packet["proofs"][0]["ancestry"][0]["hash"], packet["query"]["block_hash"])
        with patch.object(CollectionSources, "block_raw", side_effect=AssertionError("Do not redownload processed bodies")):
            self.run_job(identity)
        self.assertEqual(collection_status(self.state)["processed_job_blocks"], 1)

    def test_metadata_outage_is_separate_and_resume_uses_same_downloads(self):
        identity = self.create()
        failed = self.run_job(identity, outage=True)
        self.assertEqual(failed["state"], "incomplete")
        status = collection_status(self.state)
        self.assertEqual(status["processed_blocks"], 1)
        self.assertGreater(status["unresolved_publications"], 0)
        with self.assertRaises(DataError) as missing:
            recorded_lookup(self.state / "service.sqlite3", 5243119)
        self.assertEqual(missing.exception.code, "unresolved_retained_height")
        with patch.object(CollectionSources, "block_raw", side_effect=AssertionError("Metadata repair must not redownload")):
            repaired = self.run_job(identity)
        self.assertIsNotNone(recorded_lookup(self.state / "service.sqlite3", 5243119))
        self.assertEqual(repaired["usage"]["body_attempts"], failed["usage"]["body_attempts"])

    def test_indexed_main_search_works_under_an_active_collection_session(self):
        identity = self.create()
        self.run_job(identity)
        jobs = Jobs(self.root)
        with Session(self.state, self.root, context={"collection_job": "other", "update": 0}), \
             patch.object(jobs, "public_operation", side_effect=AssertionError("Indexed read must stay offline")):
            started = jobs.start("lookup", {"block": 5243119})
            deadline = time.monotonic() + 5
            while jobs.job(started["id"])["state"] == "running" and time.monotonic() < deadline:
                time.sleep(.01)
            finished = jobs.job(started["id"])
            self.assertEqual(finished["state"], "complete", finished)
            self.assertGreater(jobs.receipt(finished["receipt_id"])["verification"]["proofs_checked"], 0)

    def test_source_change_preserves_the_same_proofs_and_warns(self):
        identity = self.create()
        self.run_job(identity)
        before = recorded_lookup(self.state / "service.sqlite3", 5243119)
        Collector(self.root, sources_factory=lambda s: CollectionSources(s, changed=True)).run(identity, recheck=True)
        after = recorded_lookup(self.state / "service.sqlite3", 5243119)
        self.assertEqual([p["txid"] for p in before["proofs"]], [p["txid"] for p in after["proofs"]])
        self.assertTrue(any(i["code"] == "collection_source_view_changed" for i in after["issues"]))
        self.assertFalse(after["context"]["current_view_confirmed"])
        self.assertGreater(verify_receipt(after)["proofs_checked"], 0)

    def test_two_updates_and_tip_catchup_keep_completed_downloads(self):
        identity = self.create(965930, 965931, batch_size=1)
        waiting = self.run_job(identity, tip=965930)
        self.assertEqual(waiting["state"], "waiting")
        self.assertEqual(waiting["items"]["965930"]["state"], "processed")
        self.assertNotIn("965931", waiting["items"])
        next_result = self.run_job(identity, tip=965931)
        self.assertEqual(next_result["items"]["965931"]["state"], "processed")
        self.assertEqual(len(next_result["updates"]), 2)

    def test_hard_process_exit_recovers_owned_lock_without_resetting_allowance(self):
        identity = self.create()
        child = """
import os,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from hemi_lookup.collector import Collector
from collection_fixtures import CollectionSources
Collector(Path(sys.argv[1]), sources_factory=CollectionSources, event=lambda e: os._exit(74) if e['stage']=='block_processed' else None).run(sys.argv[2])
"""
        result = subprocess.run([sys.executable, "-B", "-c", child, str(self.root), identity], cwd=ROOT, timeout=30, capture_output=True)
        self.assertEqual(result.returncode, 74, result.stderr)
        before = json.loads((self.state / "session.json").read_text())
        self.assertTrue((self.state / "session.lock").exists())
        with patch.object(CollectionSources, "block_raw", side_effect=AssertionError("Committed body survives a hard exit")):
            resumed = self.run_job(identity)
        after = json.loads((self.state / "session.json").read_text())
        self.assertEqual(after["runs"][:len(before["runs"])], before["runs"])
        self.assertGreaterEqual(resumed["usage"]["active_seconds"], 60)
        self.assertGreater(resumed["usage"]["conservative_segments"], 0)
        self.assertFalse((self.state / "session.lock").exists())
        self.assertIsNotNone(recorded_lookup(self.state / "service.sqlite3", 5243119))

    def test_hard_exit_before_body_commit_rolls_back_and_reuses_verified_cache(self):
        identity = self.create()
        child = """
import os,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from hemi_lookup.collector import Collector
from hemi_lookup.catalogue import Catalogue
from collection_fixtures import CollectionSources
def interrupted(self, block_hash, raw, parsed):
    self.db.execute('BEGIN IMMEDIATE')
    self.db.execute("UPDATE blocks SET status='complete' WHERE hash=?",(block_hash,))
    os._exit(75)
Catalogue._store_body=interrupted
Collector(Path(sys.argv[1]), sources_factory=CollectionSources).run(sys.argv[2])
"""
        killed = subprocess.run([sys.executable, "-B", "-c", child, str(self.root), identity], cwd=ROOT, capture_output=True, timeout=30)
        self.assertEqual(killed.returncode, 75, killed.stderr)
        with CollectionStore(self.state) as store:
            self.assertEqual(store.db.execute("SELECT status FROM blocks").fetchone()[0], "pending")
        with patch.object(CollectionSources, "block_raw", side_effect=AssertionError("Verified cached body survives interrupted commit")):
            self.run_job(identity)
        self.assertGreater(verify_receipt(recorded_lookup(self.state / "service.sqlite3", 5243119))["proofs_checked"], 0)

    def test_tight_job_allowance_cannot_be_reset_by_rerunning(self):
        identity = self.create(max_requests=3)
        first = self.run_job(identity)
        with patch.object(CollectionSources, "rpc", side_effect=AssertionError("No renewed request allowance")):
            second = self.run_job(identity)
        self.assertEqual(second["state"], "limited")
        self.assertEqual(first["usage"] if "usage" in first else None, second.get("usage"))
        with CollectionStore(self.state) as store:
            self.assertEqual(store.usage(store.job(identity))["requests"], 3)

    def test_collector_does_not_remove_another_operations_lock(self):
        identity = self.create()
        with Session(self.state, self.root):
            with self.assertRaises(DataError) as error:
                self.run_job(identity)
            self.assertEqual(error.exception.code, "session_locked")

    def test_atomic_ledger_replace_retries_a_transient_windows_reader_lock(self):
        ledger = self.root / "ledger.json"
        ledger.write_text('{"preserved":true}')
        original = Path.replace
        attempts = []
        def replace(path, destination):
            attempts.append(1)
            if len(attempts) < 3:
                self.assertTrue(json.loads(ledger.read_text())["preserved"])
                raise PermissionError("Controlled transient reader lock")
            return original(path, destination)
        with patch.object(Path, "replace", replace), patch("hemi_lookup.storage.time.sleep"):
            atomic_json(ledger, {"preserved": True, "new": 1})
        self.assertEqual(len(attempts), 3)
        self.assertEqual(json.loads(ledger.read_text())["new"], 1)

    def test_export_survives_eviction_of_the_download_cache(self):
        identity = self.create()
        self.run_job(identity)
        for path in (self.state / "collection_blocks").glob("*.bin"):
            path.unlink()
        with patch("hemi_lookup.catalogue.parse_block", side_effect=AssertionError("No raw-body dependency")):
            packet = recorded_lookup(self.state / "service.sqlite3", 5243119)
        self.assertGreater(verify_receipt(packet)["proofs_checked"], 0)

    def test_snapshot_packet_retains_a_consistent_view_during_new_commits(self):
        identity = self.create()
        self.run_job(identity)
        with Catalogue(self.state / "service.sqlite3", readonly=True) as reader:
            key = reader.resolve_number(5243119)
            before = reader.query(key)
            with CollectionStore(self.state) as writer:
                writer.observe("hemi", 5243125, "22" * 32, "controlled", status="changed")
            unchanged = reader.query(key)
            self.assertEqual(before["context"], unchanged["context"])
        fresh = recorded_lookup(self.state / "service.sqlite3", 5243119)
        self.assertTrue(fresh["issues"])


if __name__ == "__main__":
    unittest.main()
