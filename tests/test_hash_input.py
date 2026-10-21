"""Exact input identity controls; provider changes are simulated, not live reorgs."""

import copy
import io
import json
import tempfile
import threading
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

from hemi_lookup.cli import main, parser
from hemi_lookup.engine import Engine
from hemi_lookup.errors import DataError, BudgetError, DeadlineError
from hemi_lookup.inputs import block_input
from hemi_lookup.proof import verify_receipt
from hemi_lookup.storage import BlockCache, Session
from hemi_lookup.network import HTTP
from hemi_lookup.web import Jobs, LocalServer
from hash_fixtures import REFERENCE, ORDINARY, MARCH, BODY, HashSources, FixtureCache, ControlledSession, hash_receipt
import test_web as web_controls


class InputTests(unittest.TestCase):
    def test_numbers_and_exact_case_normalized_hash(self):
        digest = REFERENCE["query"]["block_hash"]
        for value in (5243125, "5243125", hex(5243125)):
            self.assertEqual(block_input(value), 5243125)
        self.assertEqual(block_input(" " + digest.upper() + " "), digest)

    def test_invalid_inputs_do_not_create_cli_session(self):
        for value in (True, False, 0, -1, 1.5, "0xg1", "0x" + "a" * 63, "0x" + "a" * 65, [], {}, "", 0xffffffff):
            with self.subTest(value=value), self.assertRaises(DataError): block_input(value)
        with patch("hemi_lookup.cli.Session") as session, redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["lookup", "0x" + "a" * 63])
        session.assert_not_called()

    def test_cli_parser_supports_both_inputs_and_zero_body_limit(self):
        digest = REFERENCE["query"]["block_hash"]
        self.assertEqual(parser().parse_args(["lookup", digest]).block, digest)
        self.assertEqual(parser().parse_args(["lookup", "5243125"]).block, 5243125)
        self.assertEqual(parser().parse_args(["lookup", digest, "--max-blocks", "0"]).max_blocks, 0)

    def test_cli_unresolved_hash_is_saved_readable_and_offline_verifiable(self):
        with tempfile.TemporaryDirectory(prefix="hemi_hash_cli_") as directory:
            root = Path(directory)
            output, errors = io.StringIO(), io.StringIO()
            digest = REFERENCE["query"]["block_hash"]
            with patch("hemi_lookup.cli.PublicSources", side_effect=lambda session: HashSources(mode="hash_unavailable")), redirect_stdout(output), redirect_stderr(errors):
                code = main(["lookup", digest, "--state-dir", str(root), "--max-blocks", "0"])
            self.assertEqual(code, 2)
            path = next((root / "receipts").glob("hash-*.json"))
            receipt = json.loads(path.read_text())
            self.assertIsNone(receipt["query"]["block_number"])
            self.assertEqual(receipt["query"]["requested_block_hash"], digest)
            self.assertEqual(verify_receipt(receipt)["status"], "no_positive_proof_to_verify")
            self.assertIn("Requested Hemi block hash:", output.getvalue())
            self.assertFalse((root / "session.lock").exists())


class HashEngineTests(unittest.TestCase):
    def lookup(self, receipt=REFERENCE, mode="baseline", target=None):
        sources = HashSources(receipt, mode)
        target = receipt["query"]["block_hash"] if target is None else target
        result = Engine(sources, ControlledSession(), FixtureCache()).lookup(target, (965931, 965931))
        return result, sources

    def test_number_and_hash_lookup_same_actual_direct_proofs(self):
        number, _ = self.lookup(target=5243125)
        hashed, _ = self.lookup()
        self.assertEqual(number["status"], "found")
        self.assertEqual(hashed["status"], "found")
        self.assertEqual(number["proofs"], hashed["proofs"])
        self.assertEqual(hashed["query"]["requested_block_hash"], REFERENCE["query"]["block_hash"])
        self.assertEqual(verify_receipt(hashed)["proofs_checked"], 4)
        self.assertEqual(hashed["resources"]["bitcoin_body_attempts"], 0)

    def test_ordinary_hash_retains_requested_block_and_cover(self):
        result, _ = self.lookup(ORDINARY)
        self.assertEqual(result["status"], "found")
        self.assertEqual(result["query"]["block_number"], 5243117)
        self.assertEqual(result["query"]["covering_keystone"], 5243125)
        self.assertEqual(result["proofs"][0]["ancestry"][0]["hash"], ORDINARY["query"]["block_hash"])
        self.assertEqual(len(result["proofs"][0]["ancestry"]) - 1, 8)

    def test_unavailable_hash_preserved_without_guessed_number(self):
        result, sources = self.lookup(mode="hash_unavailable")
        self.assertIsNone(result["query"]["block_number"])
        self.assertEqual(result["query"]["requested_block_hash"], REFERENCE["query"]["block_hash"])
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["context"]["requested_block_identity"]["status"], "unavailable")
        self.assertEqual(sources.bitcoin_calls, [])
        self.assertEqual(verify_receipt(result)["proofs_checked"], 0)
        with self.assertRaises(DataError): Engine(sources, ControlledSession(), None).refresh(result)

    def test_hash_response_cannot_substitute_another_header(self):
        result, sources = self.lookup(mode="hash_response_changed")
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["query"]["block_hash"], REFERENCE["query"]["block_hash"])
        self.assertIsNone(result["query"]["block_number"])
        self.assertEqual(sources.bitcoin_calls, [])

    def test_different_current_block_stops_before_bitcoin_search(self):
        result, sources = self.lookup(mode="canonical_changed")
        self.assertEqual(result["context"]["requested_block_identity"]["status"], "different_current_rpc_block")
        self.assertEqual(result["query"]["block_hash"], REFERENCE["query"]["block_hash"])
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(sources.bitcoin_calls, [])
        self.assertEqual(result["proofs"], [])

    def test_same_hash_header_conflict_is_unconfirmed(self):
        result, sources = self.lookup(mode="root_conflict")
        self.assertEqual(result["context"]["requested_block_identity"]["status"], "unavailable")
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(sources.bitcoin_calls, [])

    def test_resolved_hash_with_missing_canonical_header_is_unavailable(self):
        result, sources = self.lookup(mode="number_unavailable")
        self.assertEqual(result["query"]["block_number"], 5243125)
        self.assertEqual(result["context"]["requested_block_identity"]["status"], "unavailable")
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(sources.bitcoin_calls, [])
        self.assertEqual(verify_receipt(result)["proofs_checked"], 0)

    def test_mid_lookup_change_preserves_proofs_and_withholds_selection(self):
        result, _ = self.lookup(mode="changed_after_scan")
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["context"]["requested_block_identity"]["status"], "different_current_rpc_block")
        self.assertIsNone(result["selection"]["current_proof_index"])
        self.assertIsNone(result["proofs"][0]["current_bitcoin_depth_in_provider_view"])
        self.assertEqual(verify_receipt(result)["proofs_checked"], 4)

    def test_wrong_network_cannot_resolve_requested_hash(self):
        result, sources = self.lookup(mode="wrong_network")
        self.assertEqual(result["status"], "incomplete")
        self.assertIsNone(result["query"]["block_number"])
        self.assertFalse(any(method == "eth_getBlockByHash" for method, params in sources.calls))
        self.assertEqual(sources.bitcoin_calls, [])
        self.assertEqual(result["context"]["requested_block_identity"]["status"], "unavailable")

    def test_hash_refresh_keeps_actual_later_support_and_original_input(self):
        receipt = hash_receipt(MARCH)
        result = Engine(HashSources(MARCH), ControlledSession(), None).refresh(receipt)
        self.assertEqual(result["status"], "found")
        self.assertEqual(result["query"], receipt["query"])
        self.assertEqual(result["proofs"][0]["relationship"], "later_checkpoint_support")
        self.assertEqual(verify_receipt(result)["proofs_checked"], 6)

    def test_hash_refresh_changed_unavailable_or_wrong_network_retains_identity(self):
        receipt = hash_receipt()
        for mode, status in (("canonical_changed", "different_current_rpc_block"), ("number_unavailable", "unavailable"), ("wrong_network", "unavailable")):
            with self.subTest(mode=mode):
                before = copy.deepcopy(receipt)
                result = Engine(HashSources(mode=mode), ControlledSession(), None).refresh(receipt)
                self.assertEqual(receipt, before)
                self.assertEqual(result["query"], receipt["query"])
                self.assertEqual(result["status"], "incomplete")
                self.assertEqual(result["context"]["requested_block_identity"]["status"], status)
                self.assertIsNone(result["selection"]["current_proof_index"])
                self.assertEqual(verify_receipt(result)["proofs_checked"], 4)

    def test_requested_hash_tampering_and_identity_downgrade_rejected(self):
        for change in ("hash", "kind", "number", "unresolved"):
            receipt = hash_receipt()
            if change == "hash": receipt["query"]["requested_block_hash"] = "0x" + "44" * 32
            elif change == "kind": receipt["query"]["input_type"] = "number"
            elif change == "number": receipt["query"]["block_number"] += 1
            else: receipt["query"]["block_number"] = None
            with self.subTest(change=change), self.assertRaises(DataError): verify_receipt(receipt)

    def test_timeout_during_hash_identity_check_remains_unconfirmed(self):
        class ExpiringSession(ControlledSession):
            expired = False
            def check_deadline(self):
                if self.expired: raise DeadlineError()
        session = ExpiringSession()
        class ExpiringSources(HashSources):
            def rpc(self, method, params):
                result = super().rpc(method, params)
                if method == "eth_getBlockByNumber": session.expired = True
                return result
        sources = ExpiringSources()
        result = Engine(sources, session, FixtureCache()).lookup(REFERENCE["query"]["block_hash"], (965931, 965931))
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["termination"]["reason"], "time_budget_exhausted")
        self.assertEqual(result["context"]["requested_block_identity"]["status"], "unavailable")
        self.assertEqual(sources.bitcoin_calls, [])
        self.assertEqual(verify_receipt(result)["proofs_checked"], 0)


class MetadataLimitTests(unittest.TestCase):
    def test_zero_body_session_rejects_before_transport_or_reservation(self):
        with tempfile.TemporaryDirectory(prefix="hemi_hash_zero_bodies_") as directory:
            with Session(directory, max_bytes=3_000_000, max_blocks=0) as session:
                client = HTTP(session)
                with patch.object(client.transport, "read") as transport, self.assertRaises(BudgetError):
                    client.read("https://blockstream.info/api/block/" + "11" * 32 + "/raw", 4_000_000, block=True)
                transport.assert_not_called()
                self.assertEqual(session.run["requests"], [])
                self.assertEqual(session.run["body_attempts"], 0)




class HashWebTests(unittest.TestCase):
    request = web_controls.WebTests.request
    wait_job = web_controls.WebTests.wait_job
    stop_server = web_controls.WebTests.stop_server

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="hemi_hash_browser_")
        self.root = Path(temp.name)
        self.addCleanup(temp.cleanup)
        samples = self.root / "demo_results"
        samples.mkdir()
        (samples / "mainnet_5243117.json").write_text(json.dumps(ORDINARY))
        self.jobs = Jobs(self.root, mode="discovery")
        BlockCache(self.jobs.state_dir / "blocks").put(REFERENCE["proofs"][0]["bitcoin_block_hash"], BODY)
        self.server = LocalServer(0, self.jobs)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def test_hash_lookup_download_and_refresh_preserve_input(self):
        digest = REFERENCE["query"]["block_hash"]
        with patch("hemi_lookup.web.PublicSources", side_effect=lambda session: HashSources()):
            status, _, raw = self.request("/api/lookup", {"block": digest, "bitcoin_range": [965931, 965931]})
            self.assertEqual(status, 202)
            job = self.wait_job(json.loads(raw)["id"])
            self.assertEqual(job["state"], "complete")
            wrapper = self.jobs.receipt(job["receipt_id"])
            self.assertEqual(wrapper["receipt"]["status"], "found")
            self.assertEqual(wrapper["receipt"]["query"]["requested_block_hash"], digest)
            status, headers, raw = self.request(f"/api/receipts/{job['receipt_id']}/download")
            self.assertEqual(status, 200)
            self.assertIn("hemi-hash-", headers["Content-Disposition"])
            self.assertEqual(json.loads(raw)["query"]["requested_block_hash"], digest)
            refreshed = self.jobs.start("refresh", {"receipt_id": job["receipt_id"]})
            refreshed = self.wait_job(refreshed["id"])
            r = self.jobs.receipt(refreshed["receipt_id"])["receipt"]
            self.assertEqual(r["query"], wrapper["receipt"]["query"])
            self.assertEqual(r["status"], "found")

    def test_unresolved_hash_remains_a_downloadable_unknown_receipt(self):
        digest = REFERENCE["query"]["block_hash"]
        with patch("hemi_lookup.web.PublicSources", side_effect=lambda session: HashSources(mode="hash_unavailable")):
            job = self.jobs.start("lookup", {"block": digest})
            job = self.wait_job(job["id"])
        self.assertEqual(job["state"], "complete")
        wrapper = self.jobs.receipt(job["receipt_id"])
        self.assertIsNone(wrapper["receipt"]["query"]["block_number"])
        self.assertEqual(wrapper["receipt"]["status"], "incomplete")
        status, _, raw = self.request(f"/api/receipts/{job['receipt_id']}/download")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["query"]["requested_block_hash"], digest)

    def test_malformed_or_conflicting_inputs_create_no_job_or_ledger(self):
        payloads = ({"block": "0x" + "a" * 63}, {"block": True}, {"block": 5243125, "block_number": 5243125})
        for payload in payloads:
            self.assertEqual(self.request("/api/lookup", payload)[0], 400)
        self.assertEqual(self.jobs.jobs, {})
        self.assertFalse((self.jobs.state_dir / "session.json").exists())


if __name__ == "__main__":
    unittest.main()
