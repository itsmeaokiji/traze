"""Actual demo engine/transport tests. Controlled views are not live reorgs."""

import copy
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hemi_lookup import bitcoin as b
from hemi_lookup.engine import Engine, covering
from hemi_lookup.errors import DataError, BudgetError
from hemi_lookup.network import HTTP, HEMI_RPC
from hemi_lookup.proof import verify_receipt, verify_proof
from hemi_lookup.storage import Session, BlockCache

ROOT = Path(__file__).resolve().parent.parent
REFERENCE = json.loads((ROOT / "tests/fixtures/mainnet_reference.json").read_text())


class FakeSession:
    max_blocks = 20
    run = {"body_attempts": 0, "cache_reads": 0}

    def metrics(self):
        return {"response_bytes_this_run": 0, "response_bytes_session_with_prior_validation": 0,
                "bitcoin_body_attempts": self.run["body_attempts"], "cache_body_reads": self.run["cache_reads"], "http_requests": 0}


class FakeSources:
    def __init__(self, mode="baseline"):
        self.mode = mode
        self.headers = {}
        cp = REFERENCE["query"]["covering_checkpoint"]
        for header in (cp["header"], cp["previous_header"]):
            self.headers[int(header["number"], 16)] = header
        self.hashes = {item["height"]: item["hash"] for item in REFERENCE["coverage"]["blocks"]}

    def rpc(self, method, params):
        if method == "eth_chainId":
            if self.mode == "rpc_timeout":
                raise DataError("source_unavailable", "controlled RPC timeout")
            return hex(743111 if self.mode == "wrong_network" else 43111)
        if method == "web3_clientVersion":
            return "controlled-mainnet-fixture"
        number = int(params[0], 16)
        if method == "eth_getBlockByNumber":
            if self.mode == "hemi_header_timeout":
                raise DataError("source_unavailable", "controlled Hemi header timeout")
            value = copy.deepcopy(self.headers[number])
            if self.mode == "hemi_hash_changed":
                value["hash"] = "0x" + "11" * 32
            if self.mode == "hemi_root_changed":
                value["stateRoot"] = "0x" + "22" * 32
            return value
        if method == "eth_getTransactionByBlockNumberAndIndex":
            value = copy.deepcopy(REFERENCE["query"]["covering_checkpoint"]["attributes_transaction"])
            if self.mode == "unsupported_format":
                value["input"] = "0xdeadbeef"
            return value
        raise AssertionError("Unexpected fake RPC")

    def btc(self, path, as_json=True, secondary=False):
        if path.startswith("/block-height/"):
            height = int(path.rsplit("/", 1)[1])
            if self.mode == "secondary_timeout" and secondary:
                raise DataError("source_unavailable", "controlled secondary timeout")
            if self.mode == "btc_changed" or (self.mode == "provider_conflict" and secondary):
                return "33" * 32
            return self.hashes[height]
        tip = REFERENCE["bitcoin_tip_after"]
        if self.mode == "tip_timeout":
            raise DataError("source_unavailable", "controlled tip timeout")
        if path == "/blocks/tip/hash":
            return tip["hash"]
        if path == "/block/" + tip["hash"]:
            return {"id": tip["hash"], "height": tip["height"]}
        raise AssertionError("Unexpected fake Bitcoin metadata")

    def block_raw(self, *args):
        raise DataError("source_unavailable", "controlled body timeout")


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.network_guard = patch.object(socket, "create_connection", side_effect=AssertionError("Live networking forbidden in tests"))
        self.network_guard.start()
        self.addCleanup(self.network_guard.stop)

    def refresh(self, mode):
        return Engine(FakeSources(mode), FakeSession(), None).refresh(copy.deepcopy(REFERENCE))

    def test_positive_refresh(self):
        r = self.refresh("baseline")
        self.assertEqual(r["status"], "found")
        self.assertTrue(r["context"]["current_view_confirmed"])
        self.assertEqual(r["selection"]["current_proof_index"], 0)

    def test_timeouts_never_reuse_cached_confidence(self):
        for mode in ("rpc_timeout", "hemi_header_timeout", "secondary_timeout"):
            with self.subTest(mode=mode):
                r = self.refresh(mode)
                self.assertEqual(r["status"], "incomplete")
                self.assertIsNone(r["selection"]["current_proof_index"])
                self.assertFalse(r["context"]["current_view_confirmed"])
                self.assertEqual(r["proofs"][0]["raw_transaction_hex"], REFERENCE["proofs"][0]["raw_transaction_hex"])
                self.assertEqual(verify_receipt(r)["proofs_checked"], 1)

    def test_wrong_network_on_refresh(self):
        r = self.refresh("wrong_network")
        self.assertEqual(r["status"], "incomplete")
        self.assertFalse(r["context"]["hemi_network_same"])

    def test_wrong_network_initial_lookup(self):
        r = Engine(FakeSources("wrong_network"), FakeSession(), None).lookup(5243125)
        self.assertEqual(r["status"], "incomplete")
        self.assertEqual(r["proofs"], [])
        self.assertEqual(r["issues"][0]["code"], "wrong_network")

    def test_unsupported_l1_format_is_not_absence(self):
        r = Engine(FakeSources("unsupported_format"), FakeSession(), None).lookup(5243125)
        self.assertEqual(r["status"], "incomplete")
        self.assertIn("unsupported", r["issues"][0]["message"])

    def test_changed_or_conflicting_bitcoin_view(self):
        for mode in ("btc_changed", "provider_conflict", "secondary_timeout"):
            with self.subTest(mode=mode):
                r = self.refresh(mode)
                self.assertEqual(r["status"], "incomplete")
                self.assertIsNone(r["proofs"][0]["current_bitcoin_depth_in_provider_view"])
                self.assertEqual(verify_receipt(r)["proofs_checked"], 1)

    def test_changed_hemi_hash_or_root(self):
        for mode in ("hemi_hash_changed", "hemi_root_changed"):
            with self.subTest(mode=mode):
                r = self.refresh(mode)
                self.assertEqual(r["status"], "incomplete")
                self.assertFalse(r["context"]["hemi_headers"][0]["same"])
                self.assertEqual(verify_receipt(r)["proofs_checked"], 1)

    def test_tip_timeout_does_not_reuse_old_tip_or_depth(self):
        r = self.refresh("tip_timeout")
        self.assertNotIn("bitcoin_tip_after", r)
        self.assertIsNone(r["proofs"][0]["current_bitcoin_depth_in_provider_view"])

    def test_scoped_negative_and_gap_are_distinct(self):
        for gap in (False, True):
            receipt = copy.deepcopy(REFERENCE)
            receipt["proofs"] = []
            receipt["coverage"]["blocks"] = [item for item in receipt["coverage"]["blocks"] if item["height"] <= 965930]
            receipt["coverage"]["stop_reason"] = "explicit_range_end"
            if gap:
                receipt["coverage"]["gaps"].append({"height": 965929, "message": "controlled missing data"})
            engine = Engine(FakeSources(), FakeSession(), None)
            engine.receipt = receipt
            engine.requested = 5243125
            engine.recheck()
            r = engine.finish()
            self.assertEqual(r["status"], "incomplete" if gap else "not_found_within_coverage")
            self.assertFalse(r["selection"]["global_earliest_proven"])

    def test_unsupported_pop_payload_blocks_absence(self):
        engine = Engine(FakeSources(), FakeSession(), None)
        receipt = copy.deepcopy(REFERENCE)
        receipt["proofs"] = []
        receipt["coverage"]["stop_reason"] = "explicit_range_end"
        receipt["coverage"]["unsupported_payloads"] = 1
        engine.receipt, engine.requested = receipt, 5243125
        engine.recheck()
        self.assertEqual(engine.finish()["status"], "incomplete")

    def test_selection_uses_bitcoin_height_not_direct_preference(self):
        # Selection-only synthetic metadata; not a mined/live proof fixture.
        engine = Engine(FakeSources(), FakeSession(), None)
        receipt = copy.deepcopy(REFERENCE)
        receipt["proofs"] = [copy.deepcopy(receipt["proofs"][0]) for _ in range(2)]
        receipt["proofs"][1]["checkpoint"]["height"] = 5243150
        receipt["proofs"][1]["bitcoin_height"] = 965930
        receipt["context"]["current_view_confirmed"] = True
        engine.receipt = receipt
        self.assertEqual(engine.finish()["selection"]["best_observed_proof_index"], 1)

    def test_period_boundaries(self):
        self.assertEqual(covering(5243117), 5243125)
        self.assertEqual(covering(5243125), 5243125)
        self.assertEqual(covering(5243126), 5243150)
        with self.assertRaises(DataError):
            covering(0)


class ProofTests(unittest.TestCase):
    def test_offline_verifier_ignores_current_success_flags(self):
        r = copy.deepcopy(REFERENCE)
        r["context"]["current_view_confirmed"] = False
        checked = verify_receipt(r)
        self.assertEqual(checked["proofs_checked"], 1)
        self.assertFalse(checked["current_chain_membership_checked"])

    def test_tampered_transaction_branch_and_hemi_root(self):
        for part in ("transaction", "branch", "hemi"):
            r = copy.deepcopy(REFERENCE)
            p = r["proofs"][0]
            if part == "transaction":
                p["raw_transaction_hex"] = "03" + p["raw_transaction_hex"][2:]
            elif part == "branch":
                p["merkle_siblings_display_hex"][0] = "11" * 32
            else:
                p["checkpoint"]["header"]["stateRoot"] = "0x" + "22" * 32
            with self.subTest(part=part), self.assertRaises(DataError):
                verify_receipt(r)

    def test_segwit_txid_not_wtxid(self):
        vin = bytes.fromhex("01" + "11" * 32 + "0000000000ffffffff")
        vout = bytes.fromhex("010100000000000000160014" + "22" * 20)
        stripped = bytes.fromhex("02000000") + vin + vout + b"\x00" * 4
        raw = bytes.fromhex("020000000001") + vin + vout + bytes.fromhex("0201010102") + b"\x00" * 4
        first = b.parse_transaction(b.Reader(raw))
        changed = b.parse_transaction(b.Reader(raw[:-5] + b"\x03" + raw[-4:]))
        self.assertEqual(first["hash"], b.sha256d(stripped))
        self.assertEqual(first["hash"], changed["hash"])
        self.assertNotEqual(first["wtxid"], changed["wtxid"])

    def test_unknown_pop_version(self):
        payload = bytes.fromhex(REFERENCE["proofs"][0]["checkpoint"]["payload_hex"])
        self.assertFalse(b.hemi_payload(b"\x6a\x4c\x50HEMI\x02" + payload[1:])["supported"])

    def test_requested_header_metadata_or_covering_tampered(self):
        for field in ("header", "covering"):
            r = copy.deepcopy(REFERENCE)
            if field == "header":
                r["query"]["header"]["stateRoot"] = "0x" + "33" * 32
            else:
                r["query"]["covering_keystone"] = 5243150
            with self.subTest(field=field), self.assertRaises(DataError):
                verify_receipt(r)

    def test_malformed_receipt_input(self):
        r = copy.deepcopy(REFERENCE)
        r["query"]["block_number"] = "not-an-integer"
        with self.assertRaises(DataError):
            verify_receipt(r)


class StorageTransportTests(unittest.TestCase):
    def safe_temp(self):
        temp = tempfile.TemporaryDirectory(prefix="hemi_lookup_test_")
        # Verify the resolved directory before the library's recursive cleanup.
        self.assertTrue(Path(temp.name).resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(temp.cleanup)
        return Path(temp.name)

    def test_session_persists_reservations_and_refuses_second_writer(self):
        root = self.safe_temp()
        with Session(root) as session:
            entry = session.reserve("https://rpc.hemi.network/rpc", 1024)
            with self.assertRaises(DataError):
                Session(root)
            reserved = session.total_bytes
        with Session(root) as resumed:
            self.assertEqual(resumed.total_bytes, reserved)
            self.assertEqual(resumed.state["runs"][0]["requests"][0]["status"], "reserved")

    def test_budget_stops_before_transport(self):
        root = self.safe_temp()
        with Session(root, max_bytes=2_000_000, max_blocks=1) as session:
            first = session.reserve("https://blockstream.info/api/block/x/raw", 100, True)
            session.charge(first, 100, "ok")
            with self.assertRaises(BudgetError):
                session.reserve("https://blockstream.info/api/block/x/raw", 100, True)
            with self.assertRaises(BudgetError):
                session.reserve("https://rpc.hemi.network/rpc", 2_000_000)

    def test_actual_http_timeout_accounting(self):
        root = self.safe_temp()
        with Session(root) as session:
            http = HTTP(session)
            class NeverConnect:
                def open(self, *args, **kwargs):
                    raise TimeoutError("controlled transport timeout")
            http.opener = NeverConnect()
            with self.assertRaises(DataError):
                http.read(HEMI_RPC, 1024, {"method": "eth_chainId"})
            self.assertEqual(session.run["requests"][0]["status"], "error")
            self.assertEqual(session.total_bytes, 0)

    def test_private_destination_and_writing_method_rejected(self):
        root = self.safe_temp()
        with Session(root) as session:
            http = HTTP(session)
            for url, body in (("http://127.0.0.1/", None), (HEMI_RPC, {"method": "eth_sendRawTransaction"})):
                with self.assertRaises(DataError):
                    http.read(url, rpc=body)
            self.assertEqual(session.run["requests"], [])

    def test_cache_identity_is_block_hash(self):
        cache = BlockCache(self.safe_temp())
        cache.put("11" * 32, b"fixture")
        self.assertEqual(cache.get("11" * 32), b"fixture")
        self.assertIsNone(cache.get("22" * 32))
        with self.assertRaises(DataError):
            cache.get("../escape")


if __name__ == "__main__":
    unittest.main()
