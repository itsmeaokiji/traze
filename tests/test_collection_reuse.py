"""Successor progress, fresh views and accounting using retained March data only."""

import copy
import json
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from collection_fixtures import CollectionSources, ROOT
from test_service import Clock, fixture_policy
from hemi_lookup.catalogue_lookup import recorded_lookup
from hemi_lookup.collection_store import CollectionStore
from hemi_lookup.proof import verify_receipt
from hemi_lookup.service import CollectionService


HEIGHT, KEYSTONE = 941750, 4028450


class ReuseTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / "output/qa-temp-reuse"
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / ".hemi_lookup_data"
        self.clock, self.calls, self.cut = Clock(), [], False
        stack = ExitStack()
        stack.enter_context(patch("hemi_lookup.service.time.time", side_effect=lambda: self.clock.value))
        stack.enter_context(patch("hemi_lookup.collector.time.monotonic", side_effect=lambda: self.clock.value))
        for name in ("collection_store.timestamp", "collector.timestamp", "service.timestamp", "collection_control.now"):
            stack.enter_context(patch("hemi_lookup." + name, side_effect=self.clock.stamp))
        stack.enter_context(patch("hemi_lookup.network.HTTP.read", side_effect=AssertionError("No public source calls")))
        self.addCleanup(stack.close)

    def source(self, session, *, partial=False, changed=False, bitcoin_changed=False, bitcoin_replaced=False):
        test = self

        class Tracked(CollectionSources):
            def __init__(self):
                super().__init__(session, tip=HEIGHT, changed=changed)
                self.rpc_calls = []
                self.height_checks = 0

            def rpc(self, method, params):
                if partial and not test.cut and method == "eth_getBlockByNumber" and params[0] == hex(KEYSTONE - 10):
                    test.cut = True
                    test.clock.value += 301
                    self.session.check_deadline()
                result = super().rpc(method, params)
                self.rpc_calls.append((method, copy.deepcopy(params)))
                if changed and method == "eth_getBlockByNumber" and params[0] == hex(KEYSTONE):
                    result["hash"] = "0x" + "22" * 32
                return result

            def btc(self, path, as_json=True):
                result = super().btc(path, as_json)
                if path == "/block-height/" + str(HEIGHT):
                    self.height_checks += 1
                    if bitcoin_replaced or bitcoin_changed and self.height_checks > 1:
                        return "22" * 32
                return result

        source = Tracked()
        self.calls.append(source)
        return source

    def controller(self, **options):
        return CollectionService(self.root, sources_factory=lambda s: self.source(s, **options))

    def create(self, **policy):
        return self.controller().create(HEIGHT, fixture_policy(**policy))["id"]

    def cycle(self, identity, **options):
        return self.controller(**options).run(identity, max_cycles=1, sleep=self.clock.sleep)

    def ledger(self):
        return json.loads((self.state / "session.json").read_text())

    def old_work(self, complete=False, **policy):
        identity = self.create(**policy)
        if complete:
            def event(value):
                if not self.cut and value["stage"] == "association_checked" and value["keystone"] == KEYSTONE:
                    self.cut = True
                    self.clock.value += 301
            first = CollectionService(self.root, sources_factory=self.source, event=event).run(
                identity, max_cycles=1, sleep=self.clock.sleep)
        else:
            first = self.cycle(identity, partial=True)
        self.assertTrue(self.cut)
        self.assertEqual(first["next_height"], HEIGHT)
        with CollectionStore(self.state) as store:
            old = store.job(first["current_job"])
            metadata = dict(store.db.execute("SELECT key,data FROM collection_metadata WHERE job_id=?", (old["id"],)))
        progress = [a for a in old["associations"].values() if a["keystone"] == KEYSTONE]
        self.assertEqual(progress[0]["state"], "complete" if complete else "commitment_checked")
        return identity, old, metadata

    def test_partial_ancestry_continues_after_period_and_controller_restart(self):
        identity, old, metadata = self.old_work()
        before = self.ledger()
        first_sources = len(self.calls)
        result = self.cycle(identity)
        with CollectionStore(self.state) as store:
            new = store.job(result["current_job"])
            previous = store.job(old["id"])
            copied = dict(store.db.execute("SELECT key,data FROM collection_metadata WHERE job_id=?", (new["id"],)))
        self.assertNotEqual(new["id"], old["id"])
        self.assertEqual(new["work_reuse"]["from_job"], old["id"])
        self.assertEqual(new["work_reuse"]["partial_associations"], 1)
        self.assertEqual(previous["state"], "period_closed")
        self.assertEqual(previous["limits"], old["limits"])
        self.assertEqual(previous["clock"]["expires_at"], old["clock"]["expires_at"])
        self.assertTrue(all(copied[key] == value for key, value in metadata.items()))
        new_calls = [call for source in self.calls[first_sources:] for call in source.rpc_calls]
        self.assertNotIn(("eth_getTransactionByBlockNumberAndIndex", [hex(KEYSTONE), "0x0"]), new_calls)
        for number in range(KEYSTONE - 9, KEYSTONE):
            self.assertNotIn(("eth_getBlockByNumber", [hex(number), False]), new_calls)
        self.assertIn(("eth_getBlockByNumber", [hex(KEYSTONE), False]), new_calls)  # Fresh recheck remains.
        completed = [a for a in new["associations"].values() if a["keystone"] == KEYSTONE]
        self.assertEqual(completed[0]["state"], "complete")
        self.assertEqual(sum(len(s.raw_calls) for s in self.calls), 1)
        self.assertEqual(self.ledger()["runs"][:len(before["runs"])], before["runs"])
        packet = recorded_lookup(self.state / "service.sqlite3", KEYSTONE - 25)
        self.assertGreater(verify_receipt(packet)["proofs_checked"], 0)
        self.assertFalse(packet["context"]["current_view_confirmed"])
        self.assertEqual(result["next_height"], HEIGHT)  # Other unresolved associations remain open.
        count = len(self.calls)
        again = self.cycle(identity)
        self.assertEqual(again["current_job"], new["id"])
        self.assertTrue(all(not s.raw_calls for s in self.calls[count:]))

    def test_completed_association_is_carried_without_reacquiring_its_ancestry(self):
        identity, old, metadata = self.old_work(complete=True)
        count = len(self.calls)
        result = self.cycle(identity)
        with CollectionStore(self.state) as store:
            new = store.job(result["current_job"])
            inherited = [a for a in new["associations"].values() if a["keystone"] == KEYSTONE][0]
            original = [a for a in old["associations"].values() if a["keystone"] == KEYSTONE][0]
            copied = dict(store.db.execute("SELECT key,data FROM collection_metadata WHERE job_id=?", (new["id"],)))
        self.assertEqual(new["work_reuse"]["complete_associations"], 1)
        self.assertEqual(inherited, original)
        self.assertTrue(all(copied[k] == v for k, v in metadata.items()))
        calls = [call for source in self.calls[count:] for call in source.rpc_calls
                 if call[0] != "eth_chainId" and KEYSTONE - 25 <= int(call[1][0], 16) <= KEYSTONE]
        self.assertEqual(calls, [("eth_getBlockByNumber", [hex(KEYSTONE), False])])
        self.assertEqual(sum(len(s.raw_calls) for s in self.calls), 1)

    def test_changed_hemi_root_blocks_inherited_work_without_losing_historical_proofs(self):
        identity, old, _ = self.old_work(complete=True)
        before = recorded_lookup(self.state / "service.sqlite3", KEYSTONE - 25)
        result = self.cycle(identity, changed=True)
        self.assertEqual(result["state"], "blocked")
        self.assertEqual(result["issue"]["code"], "source_view_changed")
        self.assertEqual(result["next_height"], HEIGHT)
        after = recorded_lookup(self.state / "service.sqlite3", KEYSTONE - 25)
        self.assertTrue({p["txid"] for p in before["proofs"]}.issubset({p["txid"] for p in after["proofs"]}))
        self.assertTrue(any(i["code"] == "collection_source_view_changed" for i in after["issues"]))
        self.assertGreater(verify_receipt(after)["proofs_checked"], 0)

    def test_changed_bitcoin_height_check_blocks_successor_cursor(self):
        identity, old, _ = self.old_work(complete=True)
        result = self.cycle(identity, bitcoin_changed=True)
        self.assertEqual(result["state"], "blocked")
        self.assertEqual(result["next_height"], HEIGHT)
        packet = recorded_lookup(self.state / "service.sqlite3", KEYSTONE - 25)
        self.assertTrue(any(v["chain"] == "bitcoin" and not v["matches_record"]
                            for v in packet["context"]["collection_source_views"]))
        self.assertGreater(verify_receipt(packet)["proofs_checked"], 0)

    def test_changed_bitcoin_nomination_rejects_carryover_before_another_body(self):
        identity, old, _ = self.old_work(complete=True)
        result = self.cycle(identity, bitcoin_replaced=True)
        self.assertEqual(result["state"], "blocked")
        self.assertEqual(result["issue"]["code"], "source_conflict")
        self.assertEqual(result["next_height"], HEIGHT)
        self.assertEqual(sum(len(s.raw_calls) for s in self.calls), 1)

    def test_reuse_keeps_period_request_caps_and_original_accounting_prefix(self):
        identity, old, _ = self.old_work(requests=80)
        before = self.ledger()
        self.cycle(identity)
        ledger = self.ledger()
        periods = {r["context"]["operating_period"] for r in ledger["runs"]}
        self.assertEqual(periods, {0, 1})
        for period in periods:
            self.assertLessEqual(sum(len(r["requests"]) for r in ledger["runs"]
                                     if r["context"]["operating_period"] == period), 80)
        self.assertEqual(ledger["runs"][:len(before["runs"])], before["runs"])
        self.assertEqual(sum(r["body_attempts"] for r in ledger["runs"]), 1)

    def test_stop_before_successor_is_terminal_and_makes_no_new_source_calls(self):
        identity, old, _ = self.old_work()
        stopped = self.controller().stop(identity)
        ledger, calls = self.ledger(), len(self.calls)
        self.assertEqual(stopped["state"], "stopped")
        self.assertEqual(self.cycle(identity), stopped)
        self.assertEqual(self.ledger(), ledger)
        self.assertEqual(len(self.calls), calls)

    def test_successor_metadata_is_bounded_and_cannot_cross_service_ownership(self):
        identity, old, _ = self.old_work()
        with CollectionStore(self.state) as store:
            store.cache_metadata(old, "eth_chainId", [], "0xa867", "controlled fixture")
        result = self.cycle(identity)
        with CollectionStore(self.state) as store:
            child = store.job(result["current_job"])
            self.assertIsNone(store.metadata(child, "eth_chainId", []))
        other = self.create()
        with CollectionStore(self.state) as store:
            service = store.service(other)
            service.update(last_job=old["id"], clock={"period": 0})
            child = store.create(HEIGHT, HEIGHT, service=service)
            self.assertNotIn("work_reuse", child)
            self.assertEqual(child["associations"], {})

    def test_successor_binding_and_metadata_copy_roll_back_together(self):
        identity, old, _ = self.old_work()
        with CollectionStore(self.state) as store:
            service = store.service(identity)
            retired = store.job(old["id"])
            retired["state"] = "period_closed"
            store.save(retired)
            service.update(last_job=old["id"], current_job=None)
            store.save_service(service)
            count = store.db.execute("SELECT count(*) FROM collection_jobs").fetchone()[0]
            store.db.execute("CREATE TRIGGER reject_copy BEFORE INSERT ON collection_metadata BEGIN SELECT RAISE(ABORT,'controlled commit failure'); END")
            with self.assertRaises(sqlite3.IntegrityError):
                store.create(HEIGHT, HEIGHT, service=service)
            self.assertEqual(store.db.execute("SELECT count(*) FROM collection_jobs").fetchone()[0], count)
            self.assertIsNone(store.service(identity)["current_job"])


if __name__ == "__main__":
    unittest.main()
