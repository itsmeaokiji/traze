"""Main-search catalogue integration using retained data and controlled sources."""

import copy
import hashlib
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from hash_fixtures import HashSources, ORDINARY
from hemi_lookup.catalogue import Catalogue, build_catalogue
from hemi_lookup.catalogue_lookup import recorded_lookup, valid_cover
from hemi_lookup.errors import DeadlineError
from hemi_lookup.proof import verify_receipt
from hemi_lookup.web import Jobs, WebError

ROOT = Path(__file__).resolve().parent.parent
BODIES = ROOT / "tests/fixtures/bitcoin_blocks"
NUMBER = 5243119  # Not a preset reference-consumer example.


class MainCatalogueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qa = (ROOT / "output/qa-temp-main-catalogue").resolve()
        assert qa.is_relative_to(ROOT.resolve())
        qa.mkdir(parents=True, exist_ok=True)
        cls.corpus_temp = tempfile.TemporaryDirectory(prefix="corpus_", dir=qa)
        cls.addClassCleanup(cls.corpus_temp.cleanup)
        cls.corpus_database = Path(cls.corpus_temp.name) / "compiled.sqlite3"
        build_catalogue(ROOT, cls.corpus_database, block_directory=BODIES)

    def setUp(self):
        qa = (ROOT / "output/qa-temp-main-catalogue").resolve()
        assert qa.is_relative_to(ROOT.resolve())
        qa.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="main_", dir=qa)
        self.root = Path(self.temp.name).resolve()
        self.assertTrue(self.root.is_relative_to(qa))
        self.addCleanup(self.temp.cleanup)
        self.state = self.root / ".hemi_lookup_data"
        self.state.mkdir()
        self.database = self.state / "catalogue.sqlite3"
        shutil.copyfile(self.corpus_database, self.database)
        self.packet = recorded_lookup(self.database, NUMBER)
        self.requested = self.packet["query"]["block_hash"]
        self.fixture = copy.deepcopy(self.packet)
        self.fixture["bitcoin_tip_after"] = ORDINARY["bitcoin_tip_after"]
        blocks = self.state / "blocks"
        blocks.mkdir()
        block_hash = self.packet["proofs"][0]["bitcoin_block_hash"]
        shutil.copyfile(ROOT / "tests/fixtures/mainnet_965931.bin", blocks / (block_hash + ".bin"))
        self.jobs = Jobs(self.root)

    def wait(self, identity):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            result = self.jobs.job(identity)
            if result["state"] != "running" and self.jobs.active is None:
                self.assertEqual(result["state"], "complete", result.get("error"))
                return result
            time.sleep(.01)
        self.fail("Controlled main job did not finish")

    def lookup(self, payload):
        job = self.jobs.start("lookup", payload)
        finished = self.wait(job["id"])
        return self.jobs.receipt(finished["receipt_id"])

    def test_nonpreset_number_hits_without_session_or_body_reads(self):
        with patch("hemi_lookup.web.Session", side_effect=AssertionError("No live session on catalogue hit")), \
                patch("hemi_lookup.catalogue.parse_block", side_effect=AssertionError("No raw-body parse")):
            wrapper = self.lookup({"block": NUMBER})
        receipt = wrapper["receipt"]
        self.assertEqual(wrapper["origin"], "catalogue_lookup")
        self.assertEqual(receipt["query"]["input_type"], "number")
        self.assertNotIn("requested_block_hash", receipt["query"])
        self.assertEqual(receipt["query"]["block_number"], NUMBER)
        self.assertEqual(len(receipt["proofs"][0]["ancestry"])-1, 6)
        self.assertEqual(wrapper["verification"]["proofs_checked"], 4)
        self.assertFalse((self.state / "session.json").exists())

    def test_exact_hash_and_historical_times_preserved(self):
        wrapper = self.lookup({"block": self.requested})
        receipt = wrapper["receipt"]
        self.assertEqual(receipt["query"]["requested_block_hash"], self.requested)
        self.assertFalse(receipt["context"]["current_view_confirmed"])
        self.assertIsNone(receipt["context"]["checked_at"])
        self.assertEqual(receipt["catalogue"]["historical_source_times"], [ORDINARY["context"]["checked_at"]])
        self.assertEqual(receipt["query"]["covering_checkpoint"]["height"], 5243125)

    def test_written_result_survives_restart_without_index_mutation(self):
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        wrapper = self.lookup({"block": self.requested})
        reloaded = Jobs(self.root)
        reopened = reloaded.receipt(wrapper["id"])
        self.assertEqual(reopened["receipt"], wrapper["receipt"])
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).hexdigest(), before)
        self.assertEqual(reloaded.resources()["total_response_bytes"], 0)

    def test_refresh_success_checks_derived_target_and_keeps_original(self):
        wrapper = self.lookup({"block": self.requested})
        original = self.jobs.receipt_path(wrapper["id"])["path"].read_bytes()
        source = HashSources(self.fixture)
        with patch("hemi_lookup.web.PublicSources", return_value=source):
            job = self.jobs.start("refresh", {"receipt_id": wrapper["id"]})
            result = self.jobs.receipt(self.wait(job["id"])["receipt_id"])["receipt"]
        self.assertTrue(result["context"]["current_view_confirmed"])
        self.assertEqual(result["query"]["requested_block_hash"], self.requested)
        self.assertEqual(result["mode"], "live_metadata_refresh")
        self.assertEqual(result["coverage"], wrapper["receipt"]["coverage"])
        self.assertEqual(result["previous_observation"]["context"]["current_view_confirmed"], False)
        self.assertEqual(self.jobs.receipt_path(wrapper["id"])["path"].read_bytes(), original)
        self.assertEqual(verify_receipt(result)["proofs_checked"], 4)

    def test_refresh_conflict_retains_proofs_and_original_scope(self):
        wrapper = self.lookup({"block": NUMBER})
        with patch("hemi_lookup.web.PublicSources", return_value=HashSources(self.fixture, "canonical_changed")):
            job = self.jobs.start("refresh", {"receipt_id": wrapper["id"]})
            result = self.jobs.receipt(self.wait(job["id"])["receipt_id"])["receipt"]
        self.assertFalse(result["context"]["current_view_confirmed"])
        self.assertEqual(result["context"]["requested_block_identity"]["status"], "different_current_rpc_block")
        self.assertEqual(result["coverage"], wrapper["receipt"]["coverage"])
        self.assertEqual(verify_receipt(result)["proofs_checked"], 4)

    def test_refresh_timeout_retains_evidence(self):
        wrapper = self.lookup({"block": self.requested})
        source = HashSources(self.fixture)
        source.rpc = lambda *args: (_ for _ in ()).throw(DeadlineError("Controlled recheck timeout"))
        with patch("hemi_lookup.web.PublicSources", return_value=source):
            job = self.jobs.start("refresh", {"receipt_id": wrapper["id"]})
            result = self.jobs.receipt(self.wait(job["id"])["receipt_id"])["receipt"]
        self.assertFalse(result["context"]["current_view_confirmed"])
        self.assertEqual(result["coverage"], wrapper["receipt"]["coverage"])
        self.assertEqual(verify_receipt(result)["proofs_checked"], 4)

    def test_explicit_range_uses_actual_discovery_engine(self):
        source = HashSources(self.fixture)
        with patch("hemi_lookup.web.PublicSources", return_value=source):
            wrapper = self.lookup({"block": NUMBER, "bitcoin_range": [965931, 965931]})
        self.assertEqual(wrapper["receipt"]["mode"], "live_lookup")
        self.assertEqual(wrapper["receipt"]["coverage"]["fully_scanned_intervals"], [[965931,965931]])
        self.assertEqual(wrapper["verification"]["proofs_checked"], 4)

    def test_force_lookup_bypasses_catalogue(self):
        fallback = copy.deepcopy(self.packet)
        fallback["mode"] = "live_lookup"
        with patch.object(self.jobs, "public_operation", return_value=fallback) as operation, \
                patch("hemi_lookup.web.recorded_lookup", side_effect=AssertionError("Forced lookup must bypass index")):
            wrapper = self.lookup({"block": NUMBER, "new_lookup": True})
        operation.assert_called_once()
        self.assertEqual(wrapper["receipt"]["query"]["block_hash"], self.requested)
        self.assertEqual(wrapper["receipt"]["mode"], "live_lookup")

    def test_unknown_hash_reaches_normal_discovery(self):
        unknown = "0x" + "44" * 32
        with patch("hemi_lookup.web.PublicSources", return_value=HashSources(self.fixture, "hash_unavailable")):
            wrapper = self.lookup({"block": unknown})
        self.assertEqual(wrapper["receipt"]["query"]["requested_block_hash"], unknown)
        self.assertEqual(wrapper["receipt"]["status"], "incomplete")
        self.assertEqual(wrapper["origin"], "browser_lookup")

    def test_missing_or_corrupt_catalogue_falls_back(self):
        for mode in ("missing", "corrupt"):
            if self.database.exists():
                self.database.unlink()  # Owned test file inside the verified temporary directory.
            if mode == "corrupt":
                self.database.write_bytes(b"Controlled invalid SQLite file")
            fallback = copy.deepcopy(self.packet)
            fallback["mode"] = "live_lookup"
            with self.subTest(mode=mode), patch.object(self.jobs, "public_operation", return_value=fallback) as operation:
                wrapper = self.lookup({"block": NUMBER})
                operation.assert_called_once()
                self.assertEqual(wrapper["receipt"]["mode"], "live_lookup")

    def test_ambiguous_retained_number_does_not_select_a_hash(self):
        with Catalogue(self.database) as catalogue:
            header = copy.deepcopy(self.packet["query"]["header"])
            header["hash"] = "0x" + "55" * 32
            source_id = catalogue.db.execute("SELECT id FROM imports LIMIT 1").fetchone()[0]
            with catalogue.db:
                catalogue._header(header, source_id)
        with self.assertRaises(Exception):
            recorded_lookup(self.database, NUMBER)
        result = copy.deepcopy(self.packet)
        result["mode"] = "live_lookup"
        with patch.object(self.jobs, "public_operation", return_value=result) as fallback:
            self.lookup({"block": NUMBER})
        fallback.assert_called_once()

    def test_cover_metadata_is_reconstructed_not_trusted(self):
        checkpoint = copy.deepcopy(self.packet["query"]["covering_checkpoint"])
        self.assertTrue(valid_cover(checkpoint, checkpoint["header"], checkpoint["height"]))
        checkpoint["payload_hex"] = "00" * 76
        self.assertFalse(valid_cover(checkpoint, checkpoint["header"], checkpoint["height"]))

    def test_invalid_force_choice_starts_no_job(self):
        with self.assertRaises(WebError):
            self.jobs.start("lookup", {"block": NUMBER, "new_lookup": "yes"})
        self.assertEqual(self.jobs.jobs, {})


if __name__ == "__main__":
    unittest.main()
