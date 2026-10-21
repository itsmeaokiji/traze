"""Continuous collection checks using retained bodies and controlled clocks only."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from collection_fixtures import CollectionSources, ROOT
from hemi_lookup.catalogue import Catalogue
from hemi_lookup.catalogue_lookup import recorded_lookup
from hemi_lookup.collection_control import request_stop
from hemi_lookup.collection_store import CollectionStore, collection_status
from hemi_lookup.collector import Collector
from hemi_lookup.errors import DataError
from hemi_lookup.operating import OperatingLimit, policy_settings
from hemi_lookup.proof import verify_receipt
from hemi_lookup.service import CollectionService
from hemi_lookup.storage import Session


class Clock:
    def __init__(self):
        self.value = float(int(time.time()))
        self.waits = []

    def stamp(self):
        return datetime.fromtimestamp(self.value, timezone.utc).isoformat()

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.value += seconds


def fixture_policy(**changes):
    # Synthetic test limits; this is not an approved live operating allocation.
    return {"period_seconds": 300, "response_bytes": 30_000_000, "requests": 500,
            "body_attempts": 10, "cache_bytes": 4_000_000, "state_bytes": 100_000_000,
            "job_heights": 1, "poll_seconds": 30, "backoff_max_seconds": 120, **changes}


class ServiceTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / "output/qa-temp-service"
        directory.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / ".hemi_lookup_data"
        self.clock = Clock()
        self.calls = []
        stack = ExitStack()
        stack.enter_context(patch("hemi_lookup.service.time.time", side_effect=lambda: self.clock.value))
        stack.enter_context(patch("hemi_lookup.collector.time.monotonic", side_effect=lambda: self.clock.value))
        for name in ("collection_store.timestamp", "collector.timestamp", "service.timestamp", "collection_control.now"):
            stack.enter_context(patch("hemi_lookup." + name, side_effect=self.clock.stamp))
        stack.enter_context(patch("hemi_lookup.network.HTTP.read", side_effect=AssertionError("No public acquisition in service checks")))
        self.addCleanup(stack.close)

    def source(self, session, **options):
        source = CollectionSources(session, **options)
        self.calls.append(source)
        return source

    def controller(self, **options):
        return CollectionService(self.root, sources_factory=self.source, **options)

    def create(self, start=965928, **policy):
        return self.controller().create(start, fixture_policy(**policy))["id"]

    def run_service(self, identity, cycles=1, **options):
        return self.controller(**options).run(identity, max_cycles=cycles, sleep=self.clock.sleep)

    def ledger(self):
        return json.loads((self.state / "session.json").read_text())

    def test_policy_creation_and_status_have_no_source_or_clock_side_effect(self):
        identity = self.create()
        saved = CollectionStore.read_service(self.state, identity)
        status = collection_status(self.state)
        self.assertEqual(saved["clock"], {})
        self.assertEqual(status["state"], "queued")
        self.assertEqual(status["next_height"], 965928)
        self.assertEqual(status["kind"], "service")
        self.assertNotIn("policy", status)
        self.assertEqual(self.calls, [])
        self.assertFalse((self.state / "session.json").exists())
        for missing in ("response_bytes", "state_bytes", "body_attempts"):
            value = fixture_policy()
            del value[missing]
            with self.assertRaises(DataError):
                policy_settings(value)
        for invalid in ({"requests": True}, {"cache_bytes": 1}, {"period_seconds": 0}, {"unknown": 1}):
            with self.assertRaises(DataError):
                policy_settings(fixture_policy(**invalid))

    def test_service_status_on_legacy_catalogue_is_read_only_and_reports_unknown(self):
        path = self.state / "service.sqlite3"
        with Catalogue(path):
            pass
        before = path.read_bytes()
        with self.assertRaises(DataError) as error:
            CollectionStore.read_service(self.state, "a" * 32)
        self.assertEqual(error.exception.code, "unknown_collection_service")
        self.assertEqual(path.read_bytes(), before)

    def test_automatic_adjacent_jobs_share_clock_and_keep_compiled_evidence_after_eviction(self):
        identity = self.create()
        observed = []
        result = self.run_service(identity, 3, event=lambda _: observed.append(collection_status(self.state)))
        self.assertEqual(result["next_height"], 965931)
        self.assertEqual(result["anchor"]["height"], 965930)
        self.assertEqual(sum(len(s.raw_calls) for s in self.calls), 3)
        self.assertEqual({r["context"]["operating_period"] for r in self.ledger()["runs"]}, {0})
        self.assertEqual(len({r["context"]["collection_job"] for r in self.ledger()["runs"]}), 3)
        cache = self.state / "service-blocks" / identity
        self.assertLessEqual(sum(p.stat().st_size for p in cache.glob("*.bin")), 4_000_000)
        with CollectionStore(self.state) as store:
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM blocks WHERE status='complete'").fetchone()[0], 3)
        self.assertTrue(all(s["runner"]["state"] == "active" for s in observed), observed)
        # Exiting the controlled runner must not advertise ongoing collection.
        self.assertEqual(collection_status(self.state)["state"], "unavailable")

    def test_multiheight_child_advances_one_checked_update_per_cycle(self):
        identity = self.create(job_heights=3)
        first = self.run_service(identity)
        self.assertEqual(first["state"], "running")
        self.assertEqual(first["next_height"], 965929)
        child = first["current_job"]
        self.assertIsNotNone(child)
        result = self.run_service(identity, 2)
        self.assertEqual(result["next_height"], 965931)
        self.assertEqual(result["last_job"], child)
        self.assertEqual(sum(len(s.raw_calls) for s in self.calls), 3)

    def test_future_tip_wait_then_restart_continues_same_saved_child(self):
        identity = self.create(start=965930)
        controller = CollectionService(self.root, sources_factory=lambda s: self.source(s, tip=965929))
        first = controller.run(identity, max_cycles=1, sleep=self.clock.sleep)
        child = CollectionStore.read_job(self.state, first["current_job"])
        clock = dict(first["clock"])
        self.clock.value += 10
        result = self.run_service(identity)
        self.assertEqual(self.clock.waits, [20])
        self.assertEqual(result["next_height"], 965931)
        self.assertEqual(result["last_job"], child["id"])
        self.assertEqual(result["clock"]["armed_at"], clock["armed_at"])
        self.assertEqual(CollectionStore.read_job(self.state, child["id"])["clock"], child["clock"])

    def test_request_limit_is_shared_across_successive_children_and_restart(self):
        identity = self.create(requests=8)
        first = self.run_service(identity)
        self.assertEqual(first["next_height"], 965929)
        before = self.ledger()
        result = self.run_service(identity)
        after = self.ledger()
        requests = sum(len(r["requests"]) for r in after["runs"])
        self.assertEqual(requests, 8)
        self.assertEqual(result["state"], "limited")
        self.assertEqual(after["runs"][:len(before["runs"])], before["runs"])
        self.assertEqual(sum(len(s.raw_calls) for s in self.calls), 1)

    def test_body_limit_renews_only_in_next_period_without_banked_credit(self):
        identity = self.create(body_attempts=1)
        first = self.run_service(identity)
        armed = first["clock"]["armed_at"]
        limited = self.run_service(identity)
        self.assertEqual(limited["state"], "limited")
        old_child = CollectionStore.read_job(self.state, limited["current_job"])
        self.assertEqual(sum(r["body_attempts"] for r in self.ledger()["runs"]), 1)
        self.clock.value = armed + 901
        renewed = self.run_service(identity)
        self.assertEqual(renewed["clock"]["period"], 3)
        self.assertEqual(renewed["next_height"], 965930)
        preserved = CollectionStore.read_job(self.state, old_child["id"])
        self.assertEqual(preserved["range"], old_child["range"])
        self.assertEqual(preserved["limits"], old_child["limits"])
        self.assertEqual(preserved["clock"]["expires_at"], old_child["clock"]["expires_at"])
        self.assertEqual(preserved["state"], "period_closed")
        limited_again = self.run_service(identity)
        self.assertEqual(limited_again["state"], "limited")
        period = [r for r in self.ledger()["runs"] if r["context"]["operating_period"] == 3]
        self.assertEqual(sum(r["body_attempts"] for r in period), 1)

    def test_clock_rollback_and_changed_policy_block_without_source_reads(self):
        identity = self.create()
        first = self.run_service(identity)
        before = self.ledger()
        self.clock.value = first["clock"]["max_seen_time"] - 2
        result = self.run_service(identity)
        self.assertEqual(result["issue"]["code"], "operating_clock_changed")
        self.assertEqual(self.ledger(), before)
        other = self.create(start=965930)
        with CollectionStore(self.state) as store:
            changed = store.service(other)
            changed["policy"]["requests"] += 1
            store.save_service(changed)
        result = self.run_service(other)
        self.assertEqual(result["issue"]["code"], "operating_policy_changed")
        self.assertEqual(self.ledger(), before)

    def test_existing_lifetime_ledger_prefix_is_preserved_and_cannot_be_deleted(self):
        with Session(self.state, self.root) as session:
            entry = session.reserve("https://controlled.invalid/old", 100)
            session.charge(entry, 80, "ok")
        before = self.ledger()
        identity = self.create()
        self.run_service(identity)
        ledger = self.ledger()
        self.assertEqual(ledger["runs"][:len(before["runs"])], before["runs"])
        self.assertGreater(ledger["downloaded_bytes"], before["downloaded_bytes"])
        ledger["runs"].pop(0)
        (self.state / "session.json").write_text(json.dumps(ledger))
        calls = len(self.calls)
        result = self.run_service(identity)
        self.assertEqual(result["state"], "blocked")
        self.assertEqual(result["issue"]["code"], "operating_ledger_changed")
        self.assertEqual(len(self.calls), calls)

    def test_unresolved_associations_hold_the_cursor_while_usable_subset_remains_available(self):
        identity = self.create(start=965931)
        result = self.run_service(identity, 2)
        self.assertEqual(result["next_height"], 965931)
        self.assertEqual(result["state"], "incomplete")
        self.assertEqual(sum(len(s.raw_calls) for s in self.calls), 1)
        packet = recorded_lookup(self.state / "service.sqlite3", 5243119)
        before = verify_receipt(packet)["proofs_checked"]
        self.assertGreater(before, 0)
        for path in (self.state / "service-blocks" / identity).glob("*.bin"):
            path.unlink()
        self.assertEqual(verify_receipt(recorded_lookup(self.state / "service.sqlite3", 5243119))["proofs_checked"], before)
        self.assertGreater(collection_status(self.state)["unresolved_publications"], 0)

    def test_transient_source_outage_has_persistent_backoff_and_no_skipped_height(self):
        identity = self.create()
        class Unavailable(CollectionSources):
            def rpc(self, method, params):
                return self.charge({}, error=True)
        controller = CollectionService(self.root, sources_factory=Unavailable)
        failed = controller.run(identity, max_cycles=1, sleep=self.clock.sleep)
        self.assertEqual(failed["state"], "incomplete")
        self.assertEqual(failed["next_height"], 965928)
        self.clock.value += 10
        repaired = self.run_service(identity)
        self.assertEqual(self.clock.waits, [50])
        self.assertEqual(repaired["next_height"], 965929)

    def test_anchor_source_change_stops_new_jobs_and_keeps_original_evidence(self):
        identity = self.create()
        first = self.run_service(identity)
        anchor = dict(first["anchor"])
        class Changed(CollectionSources):
            def btc(self, path, as_json=True):
                if path == "/block-height/965928":
                    return self.charge("22" * 32)
                return super().btc(path, as_json)
        changed = CollectionService(self.root, sources_factory=Changed).run(identity, max_cycles=1, sleep=self.clock.sleep)
        self.assertEqual(changed["state"], "blocked")
        self.assertEqual(changed["issue"]["code"], "source_conflict")
        self.assertEqual(changed["anchor"], anchor)
        self.assertGreater(collection_status(self.state)["source_conflicts"], 0)
        calls = len(self.ledger()["runs"])
        self.assertEqual(self.run_service(identity), changed)
        self.assertEqual(len(self.ledger()["runs"]), calls)

    def test_cross_child_header_discontinuity_blocks_before_committing_body(self):
        identity = self.create()
        self.run_service(identity)
        # Simulate a stale controller anchor while keeping actual source/body data.
        with CollectionStore(self.state) as store:
            service = store.service(identity)
            service["anchor"]["hash"] = "22" * 32
            store.save_service(service)
        class MatchingStaleAnchor(CollectionSources):
            def btc(self, path, as_json=True):
                if path == "/block-height/965928":
                    return self.charge("22" * 32)
                return super().btc(path, as_json)
        result = CollectionService(self.root, sources_factory=MatchingStaleAnchor).run(identity, max_cycles=1, sleep=self.clock.sleep)
        self.assertEqual(result["issue"]["code"], "source_conflict")
        self.assertEqual(result["next_height"], 965929)
        with CollectionStore(self.state) as store:
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM blocks WHERE status='complete'").fetchone()[0], 1)

    def test_storage_admission_stops_without_source_calls_or_evidence_deletion(self):
        identity = self.create(state_bytes=1)
        result = self.run_service(identity)
        self.assertEqual(result["state"], "storage_limited")
        self.assertEqual(result["next_height"], 965928)
        self.assertEqual(self.calls, [])
        self.assertFalse((self.state / "session.json").exists())
        self.assertEqual(collection_status(self.state)["state"], "storage_limited")

    def test_stop_is_terminal_for_queued_waiting_and_active_child_requests(self):
        identity = self.create()
        stopped = self.controller().stop(identity, "Owner ended this service")
        self.assertEqual(self.run_service(identity), stopped)
        self.assertEqual(self.calls, [])
        identity = self.create(start=965930)
        waiting = CollectionService(self.root, sources_factory=lambda s: self.source(s, tip=965929)).run(identity, max_cycles=1, sleep=self.clock.sleep)
        before = self.ledger()
        request_stop(self.state, waiting["current_job"], "Stop this child")
        result = self.run_service(identity)
        self.assertEqual(result["state"], "stopped")
        self.assertEqual(self.ledger(), before)
        self.assertEqual(self.run_service(identity), result)

    def test_finite_entry_points_cannot_bypass_service_policy(self):
        identity = self.create(start=965930)
        waiting = CollectionService(self.root, sources_factory=lambda s: self.source(s, tip=965929)).run(identity, max_cycles=1, sleep=self.clock.sleep)
        child = waiting["current_job"]
        ledger = self.ledger()
        for method in ("run", "follow"):
            with self.assertRaises(DataError) as error:
                getattr(Collector(self.root), method)(child)
            self.assertEqual(error.exception.code, "service_owned_job")
        self.assertEqual(self.ledger(), ledger)

    def test_stop_during_active_work_cancels_the_controller_and_its_child(self):
        identity = self.create()
        def interrupt(event):
            if event["stage"] == "block_processed":
                saved = CollectionStore.read_service(self.state, identity)
                request_stop(self.state, saved["current_job"], "Stop during active work")
        result = self.run_service(identity, event=interrupt)
        self.assertEqual(result["state"], "stopped")
        self.assertEqual(CollectionStore.read_job(self.state, result["current_job"])["state"], "stopped")
        before = self.ledger()
        self.assertEqual(self.run_service(identity), result)
        self.assertEqual(self.ledger(), before)

    def test_storage_high_water_after_an_update_keeps_committed_block(self):
        identity = self.create(job_heights=3)
        controller = self.controller()
        with patch.object(controller, "_storage", side_effect=[0, 100_000_001]):
            result = controller.run(identity, max_cycles=1, sleep=self.clock.sleep)
        self.assertEqual(result["state"], "storage_limited")
        self.assertEqual(result["next_height"], 965929)
        with CollectionStore(self.state) as store:
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM blocks WHERE status='complete'").fetchone()[0], 1)
        self.assertEqual(sum(len(s.raw_calls) for s in self.calls), 1)
        before = self.ledger()
        self.assertEqual(self.run_service(identity), result)
        self.assertEqual(self.ledger(), before)

    def test_period_boundary_during_admission_waits_instead_of_becoming_blocked(self):
        identity = self.create()
        with patch("hemi_lookup.service.OperatingBudget.remaining_seconds", side_effect=OperatingLimit("Period ended", "operating_period_ended")):
            result = self.run_service(identity)
        self.assertEqual(result["state"], "limited")
        self.assertEqual(result["next_attempt_at"], result["clock"]["armed_at"] + 300)
        self.assertEqual(result["next_height"], 965928)
        self.assertEqual(self.calls, [])

    def test_hard_exit_keeps_atomic_child_binding_conservative_reservations_and_completed_body(self):
        identity = self.create(start=965931)
        code = '''
import os,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from collection_fixtures import CollectionSources
from hemi_lookup.service import CollectionService
CollectionService(Path(sys.argv[1]),sources_factory=CollectionSources,event=lambda e:os._exit(77) if e['stage']=='block_processed' else None).run(sys.argv[2],max_cycles=1)
'''
        child = subprocess.run([sys.executable, "-B", "-c", code, str(self.root), identity], cwd=ROOT, capture_output=True, timeout=30)
        self.assertEqual(child.returncode, 77, child.stderr)
        saved = CollectionStore.read_service(self.state, identity)
        old_job = CollectionStore.read_job(self.state, saved["current_job"])
        ledger = self.ledger()
        self.assertEqual(old_job["items"]["965931"]["state"], "processed")
        self.assertNotIn("closed_at", ledger["runs"][-1])
        # The parent clock was fixed just before child launch in real time.
        self.clock.value = max(self.clock.value, saved["clock"]["armed_at"])
        with patch.object(CollectionSources, "block_raw", side_effect=AssertionError("Committed body must not be downloaded again")):
            result = self.run_service(identity)
        self.assertEqual(result["current_job"], old_job["id"])
        self.assertEqual(self.ledger()["runs"][:len(ledger["runs"])], ledger["runs"])
        self.assertFalse((self.state / "session.lock").exists())
        self.assertGreater(verify_receipt(recorded_lookup(self.state / "service.sqlite3", 5243119))["proofs_checked"], 0)

    def test_hard_exit_can_recover_into_next_period_without_redownloading_a_committed_body(self):
        identity = self.create(start=965931, period_seconds=60)
        code = '''
import os,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from collection_fixtures import CollectionSources
from hemi_lookup.service import CollectionService
CollectionService(Path(sys.argv[1]),sources_factory=CollectionSources,event=lambda e:os._exit(78) if e['stage']=='block_processed' else None).run(sys.argv[2],max_cycles=1)
'''
        child = subprocess.run([sys.executable, "-B", "-c", code, str(self.root), identity], cwd=ROOT, capture_output=True, timeout=30)
        self.assertEqual(child.returncode, 78, child.stderr)
        saved = CollectionStore.read_service(self.state, identity)
        old_child = saved["current_job"]
        self.clock.value = saved["clock"]["armed_at"] + 61
        with patch.object(CollectionSources, "block_raw", side_effect=AssertionError("Committed body must survive period rollover")):
            result = self.run_service(identity)
        self.assertEqual(result["clock"]["period"], 1)
        self.assertNotEqual(result["current_job"], old_child)
        self.assertEqual(CollectionStore.read_job(self.state, old_child)["state"], "period_closed")
        self.assertFalse((self.state / "session.lock").exists())
        self.assertGreater(verify_receipt(recorded_lookup(self.state / "service.sqlite3", 5243119))["proofs_checked"], 0)


if __name__ == "__main__":
    unittest.main()
