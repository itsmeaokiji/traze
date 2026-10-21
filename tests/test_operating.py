"""Period accounting must preserve history and fail before admitting a request."""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from collection_fixtures import ROOT
from hemi_lookup.errors import DataError
from hemi_lookup.operating import OperatingBudget, OperatingLimit, checkpoint, fingerprint, ledger_state, policy_settings
from hemi_lookup.storage import Session


class OperatingTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / "output/qa-temp-operating"
        directory.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        self.now = time.time()
        self.addCleanup(patch.stopall)
        patch("hemi_lookup.operating.time.time", side_effect=lambda: self.now).start()
        policy = policy_settings({"period_seconds": 60, "response_bytes": 6_000_000,
                                  "requests": 2, "body_attempts": 1, "cache_bytes": 4_000_000,
                                  "state_bytes": 20_000_000})
        self.service = {"id": "a" * 32, "policy": policy, "policy_digest": fingerprint(policy),
                        "ledger_checkpoint": checkpoint(ledger_state(self.state)),
                        "clock": {"armed_at": self.now, "period": 0, "max_seen_time": self.now}}

    def session(self, period=0):
        budget = OperatingBudget(self.service, period)
        return Session(self.state, self.root, operating_budget=budget,
                       context={"collection_service": self.service["id"], "operating_period": period,
                                "operating_policy": self.service["policy_digest"]})

    def test_response_headroom_and_period_totals_span_different_sessions(self):
        with self.session() as session:
            entry = session.reserve("https://controlled.invalid/body", 4_000_000, True)
            session.charge(entry, 1_500_000, "ok")
        before = json.loads((self.state / "session.json").read_text())
        with self.session() as session:
            with self.assertRaises(OperatingLimit):
                session.reserve("https://controlled.invalid/second-body", 4_000_000)
            self.assertEqual(session.run["requests"], [])
        after = json.loads((self.state / "session.json").read_text())
        self.assertEqual(after["downloaded_bytes"], before["downloaded_bytes"])
        self.assertEqual(after["runs"][:len(before["runs"])], before["runs"])

    def test_interrupted_body_reservation_remains_charged_on_restart(self):
        with self.session() as session:
            session.reserve("https://controlled.invalid/interrupted", 4_000_000, True)
            reserved = session.run["received_bytes"]
        with self.session() as session:
            with self.assertRaises(OperatingLimit):
                session.reserve("https://controlled.invalid/retry", 10, True)
        ledger = json.loads((self.state / "session.json").read_text())
        self.assertEqual(ledger["downloaded_bytes"], reserved)
        self.assertEqual(ledger["runs"][0]["requests"][0]["status"], "reserved")
        self.assertEqual(sum(r["body_attempts"] for r in ledger["runs"]), 1)

    def test_requests_renew_only_at_next_period_without_lifetime_reset(self):
        for _ in range(2):
            with self.session() as session:
                entry = session.reserve("https://controlled.invalid/read", 100)
                session.charge(entry, 50, "ok")
        with self.session() as session:
            with self.assertRaises(OperatingLimit):
                session.reserve("https://controlled.invalid/third", 100)
        self.now += 60
        with self.session(period=1) as session:
            entry = session.reserve("https://controlled.invalid/new-period", 100)
            session.charge(entry, 50, "ok")
            self.assertEqual(session.total_bytes, 150)
        ledger = json.loads((self.state / "session.json").read_text())
        self.assertEqual(len(ledger["runs"]), 4)
        self.assertEqual(ledger["downloaded_bytes"], 150)

    def test_period_expiry_and_clock_rollback_reject_before_reservation(self):
        with self.session() as session:
            self.now += 60
            with self.assertRaises(OperatingLimit) as ended:
                session.reserve("https://controlled.invalid/late", 100)
            self.assertEqual(ended.exception.code, "operating_period_ended")
            self.assertEqual(session.run["requests"], [])
        self.now -= 61
        with self.assertRaises(DataError) as moved:
            self.session()
        self.assertEqual(moved.exception.code, "operating_clock_changed")
        self.assertFalse((self.state / "session.lock").exists())

    def test_context_or_recorded_period_tampering_cannot_admit_another_session(self):
        budget = OperatingBudget(self.service, 0)
        with self.assertRaises(DataError):
            Session(self.state, self.root, operating_budget=budget, context={})
        with self.session():
            pass
        path = self.state / "session.json"
        ledger = json.loads(path.read_text())
        ledger["runs"][0]["context"]["operating_period"] = 10
        path.write_text(json.dumps(ledger))
        with self.assertRaises(DataError) as changed:
            self.session()
        self.assertEqual(changed.exception.code, "operating_ledger_changed")
        self.assertFalse((self.state / "session.lock").exists())

    def test_explicit_operating_policy_keeps_prior_totals_above_legacy_finite_ceiling(self):
        prior = {"HEMI_LIGHTWEIGHT_PROBE_RESULT.json": 150_000_000}
        for name, amount in prior.items():
            (self.root / name).write_text(json.dumps({"http_response_bytes": amount}))
        self.service["ledger_checkpoint"] = checkpoint(ledger_state(self.state, self.root))
        with self.session() as session:
            entry = session.reserve("https://controlled.invalid/read", 100)
            session.charge(entry, 50, "ok")
            self.assertEqual(session.total_bytes, 150_000_050)
        ledger = json.loads((self.state / "session.json").read_text())
        self.assertEqual(ledger["prior_validation_bytes"], prior)
        with self.assertRaises(DataError):
            Session(self.state, self.root)
        self.assertEqual(json.loads((self.state / "session.json").read_text()), ledger)


if __name__ == "__main__":
    unittest.main()
