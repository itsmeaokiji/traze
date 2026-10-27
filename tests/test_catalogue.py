"""Real retained-body controls for the offline catalogue and reference boundary."""

import copy
import hashlib
import http.client
import json
import shutil
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from hemi_lookup.catalogue import Catalogue, DEFAULT_RECEIPTS, build_catalogue
from hemi_lookup.catalogue_web import make_server
from hemi_lookup.collection_store import CollectionStore
from hemi_lookup.errors import DataError
from hemi_lookup.explorer import ReadBudget, ReadError
from hemi_lookup.proof import verify_receipt

ROOT = Path(__file__).resolve().parent.parent
BODIES = ROOT / "tests/fixtures/bitcoin_blocks"
SEPTEMBER = ROOT / DEFAULT_RECEIPTS[0]
MARCH = ROOT / DEFAULT_RECEIPTS[1]
TARGETS = (
    (5243118, "0xe6c881d834d01c8e220ecf38e0638dba756e5f0dd9b492fd6871fee3e73d2302", 4, 7),
    (4028426, "0xeafe9c8e646d13a7f81bff56ff4e9d1c0ff2862a5acf05984f0bcc453657a79f", 6, 24),
)


class CatalogueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qa_root = (ROOT / "output/qa-temp-catalogue").resolve()
        assert qa_root.is_relative_to(ROOT.resolve())
        qa_root.mkdir(parents=True, exist_ok=True)
        cls.temporary = tempfile.TemporaryDirectory(prefix="traze_catalogue_test_", dir=qa_root)
        cls.directory = Path(cls.temporary.name).resolve()
        assert cls.directory.is_relative_to(qa_root)
        cls.database = cls.directory / "compiled.sqlite3"
        with patch.object(socket.socket, "connect", side_effect=AssertionError("No network allowed during compilation")):
            cls.cold = build_catalogue(ROOT, cls.database, block_directory=BODIES)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def fresh(self):
        directory = Path(tempfile.mkdtemp(dir=self.directory))
        return directory / "catalogue.sqlite3"

    def copied(self):
        database = self.fresh()
        shutil.copyfile(self.database, database)
        return database

    def fixture(self, receipt, parent):
        path = parent / "controlled-receipt.json"
        path.write_text(json.dumps(receipt), encoding="utf-8")
        return path

    def test_full_corpus_and_reconciliation(self):
        self.assertEqual(sum(run["body_reads"] for run in self.cold["imports"]), 17)
        self.assertEqual(sum(run["body_bytes_read"] for run in self.cold["imports"]), 25_845_550)
        self.assertEqual(self.cold["summary"]["processed_bodies"], 17)
        self.assertEqual(self.cold["summary"]["supported_publications"], 22)
        self.assertEqual(self.cold["summary"]["associated_publications"], 10)
        self.assertEqual(self.cold["summary"]["unassociated_publications"], 12)
        with Catalogue(self.database, readonly=True) as catalogue:
            actual = {(row[0], row[1], row[2]) for row in catalogue.db.execute("SELECT block_hash,txid,keystone_height FROM publications")}
            observed = set()
            for path in (SEPTEMBER, MARCH):
                receipt = json.loads(path.read_text(encoding="utf-8"))
                hashes = {block["height"]: block["hash"] for block in receipt["coverage"]["blocks"]}
                for item in receipt["candidate_observations"]:
                    observed.add((hashes[item["bitcoin_height"]], item["txid"], item["publishing_height"]))
            self.assertEqual(actual, observed)  # Reconciliation, not the proof of complete enumeration.
            for row in catalogue.db.execute("SELECT data FROM blocks"):
                self.assertTrue(json.loads(row[0])["full_transaction_root_checked"])

    def test_additional_targets_and_correct_slices(self):
        with Catalogue(self.database, readonly=True) as catalogue:
            for number, requested, proof_count, shortest in TARGETS:
                with self.subTest(number=number):
                    packet = catalogue.query(requested)
                    self.assertEqual(packet["query"]["block_number"], number)
                    self.assertEqual(packet["status"], "found")
                    self.assertEqual(len(packet["proofs"]), proof_count)
                    self.assertEqual(min(len(p["ancestry"]) - 1 for p in packet["proofs"]), shortest)
                    self.assertEqual(verify_receipt(packet)["proofs_checked"], proof_count)
                    for proof in packet["proofs"]:
                        self.assertEqual(proof["ancestry"][0]["hash"], requested)
                        self.assertEqual(proof["ancestry"][-1], proof["checkpoint"]["header"])

    def test_query_has_no_network_or_raw_body_reads(self):
        with Catalogue(self.database, readonly=True) as catalogue, \
                patch.object(socket.socket, "connect", side_effect=AssertionError("No network")), \
                patch("hemi_lookup.catalogue.parse_block", side_effect=AssertionError("No body parse")), \
                patch.object(Path, "read_bytes", side_effect=AssertionError("No raw cache or receipt reads")):
            for _, requested, _, _ in TARGETS:
                packet = catalogue.query(requested)
                self.assertEqual(packet["resources"]["bitcoin_body_reads_this_query"], 0)
                self.assertEqual(packet["resources"]["raw_block_parses_this_query"], 0)

    def test_restart_and_idempotent_import(self):
        database = self.copied()
        with Catalogue(database) as catalogue:
            before = catalogue.summary()
            attempts = catalogue.db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
            for path in (SEPTEMBER, MARCH):
                result = catalogue.import_receipt(path, BODIES, body_reader=lambda _: self.fail("Repeated import read a body"))
                self.assertEqual(result["body_reads"], 0)
            self.assertEqual(catalogue.summary(), before)
            self.assertEqual(catalogue.db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], attempts)
        with Catalogue(database, readonly=True) as reopened:
            self.assertEqual(len(reopened.query(TARGETS[1][1])["proofs"]), 6)

    def test_interrupted_import_resumes_committed_bodies(self):
        database, reads = self.fresh(), []
        def interrupt(block_hash):
            reads.append(block_hash)
            if len(reads) == 4:
                raise KeyboardInterrupt("Controlled interruption")
            return (BODIES / (block_hash + ".bin")).read_bytes()
        with Catalogue(database) as catalogue:
            with self.assertRaises(KeyboardInterrupt):
                catalogue.import_receipt(SEPTEMBER, BODIES, body_reader=interrupt)
            self.assertEqual(catalogue.summary()["processed_bodies"], 3)
        with Catalogue(database) as reopened:
            repaired = reopened.import_receipt(SEPTEMBER, BODIES)
            self.assertEqual(repaired["reused_bodies"], 3)
            self.assertEqual(repaired["body_reads"], 4)
            self.assertEqual(reopened.query(TARGETS[0][1])["status"], "found")

    def test_missing_body_visible_and_repairable(self):
        database = self.fresh()
        first_hash = json.loads(SEPTEMBER.read_text())["coverage"]["blocks"][0]["hash"]
        def missing(block_hash):
            if block_hash == first_hash:
                raise FileNotFoundError("Controlled missing body")
            return (BODIES / (block_hash + ".bin")).read_bytes()
        with Catalogue(database) as catalogue:
            result = catalogue.import_receipt(SEPTEMBER, BODIES, body_reader=missing)
            self.assertEqual(result["failed_bodies"], 1)
            packet = catalogue.query(TARGETS[0][1])
            self.assertEqual(packet["status"], "found")
            self.assertEqual(len(packet["coverage"]["gaps"]), 1)
            self.assertFalse(packet["coverage"]["target_association_complete"])
            repaired = catalogue.import_receipt(SEPTEMBER, BODIES)
            self.assertEqual(repaired["body_reads"], 1)
            self.assertEqual(len(catalogue.query(TARGETS[0][1])["coverage"]["gaps"]), 0)
            self.assertEqual(catalogue.db.execute("SELECT COUNT(*) FROM attempts WHERE block_hash=?", (first_hash,)).fetchone()[0], 2)

    def test_corrupt_publication_body_cannot_be_complete(self):
        database = self.fresh()
        bad_hash = json.loads(SEPTEMBER.read_text())["proofs"][0]["bitcoin_block_hash"]
        def corrupted(block_hash):
            raw = bytearray((BODIES / (block_hash + ".bin")).read_bytes())
            if block_hash == bad_hash:
                raw[-1] ^= 1
            return bytes(raw)
        with Catalogue(database) as catalogue:
            result = catalogue.import_receipt(SEPTEMBER, BODIES, body_reader=corrupted)
            self.assertEqual(result["failed_bodies"], 1)
            packet = catalogue.query(TARGETS[0][1])
            self.assertEqual(packet["status"], "incomplete")
            self.assertEqual(packet["proofs"], [])
            self.assertNotIn(bad_hash, [row["hash"] for row in packet["coverage"]["blocks"]])

    def test_missing_association_metadata_is_incomplete(self):
        database = self.fresh()
        receipt = json.loads(SEPTEMBER.read_text())
        receipt["proofs"] = []
        path = self.fixture(receipt, database.parent)
        with Catalogue(database) as catalogue:
            catalogue.import_receipt(path, BODIES)
            packet = catalogue.query(receipt["query"]["block_hash"])
            self.assertEqual(packet["status"], "incomplete")
            self.assertGreater(len(packet["coverage"]["unresolved_candidates"]), 0)
            self.assertEqual(catalogue.summary()["processed_bodies"], 7)

    def test_outside_metadata_is_not_absence(self):
        with Catalogue(self.database, readonly=True) as catalogue:
            packet = catalogue.query("0x" + "44" * 32)
            self.assertEqual(packet["status"], "incomplete")
            self.assertEqual(packet["issues"][0]["code"], "outside_retained_headers")
            self.assertEqual(packet["offline_recomputation"]["proofs_checked"], 0)

    def test_empty_processed_scope_can_return_scoped_no_match(self):
        database = self.fresh()
        receipt = json.loads(SEPTEMBER.read_text())
        receipt["proofs"] = []
        receipt["coverage"]["blocks"] = receipt["coverage"]["blocks"][:1]
        path = self.fixture(receipt, database.parent)
        with Catalogue(database) as catalogue:
            catalogue.import_receipt(path, BODIES)
            packet = catalogue.query(receipt["query"]["block_hash"])
            self.assertEqual(packet["status"], "no_match")
            self.assertEqual(packet["coverage"]["fully_scanned_intervals"], [[965925, 965925]])

    def test_zero_processed_bodies_is_incomplete(self):
        database = self.fresh()
        receipt = json.loads(SEPTEMBER.read_text())
        receipt["proofs"] = []
        receipt["coverage"]["blocks"] = []
        path = self.fixture(receipt, database.parent)
        with Catalogue(database) as catalogue:
            catalogue.import_receipt(path, BODIES)
            packet = catalogue.query(receipt["query"]["block_hash"])
            self.assertEqual(packet["status"], "incomplete")
            self.assertEqual(packet["issues"][0]["code"], "no_processed_scope")

    def test_new_target_does_not_inherit_confirmation(self):
        original = json.loads(SEPTEMBER.read_text())
        with Catalogue(self.database, readonly=True) as catalogue:
            packet = catalogue.query(TARGETS[0][1])
        self.assertFalse(packet["context"]["current_view_confirmed"])
        self.assertIsNone(packet["context"]["checked_at"])
        self.assertEqual(packet["context"]["requested_block_identity"]["status"], "not_rechecked")
        observation = packet["coverage"]["historical_observations"][0]["observation"]
        self.assertEqual(observation["context"]["checked_at"], original["context"]["checked_at"])
        self.assertEqual(observation["query"]["block_number"], 5243117)
        self.assertNotEqual(packet["query"]["block_hash"], observation["query"]["block_hash"])
        self.assertTrue(all(p["current_bitcoin_depth_in_provider_view"] is None for p in packet["proofs"]))

    def test_controlled_unsupported_record_prevents_completeness_claim(self):
        # Read-side control, not a newly observed unsupported mainnet format.
        database = self.copied()
        with Catalogue(database) as catalogue:
            row = catalogue.db.execute("SELECT p.id FROM publications p JOIN associations a ON a.publication_id=p.id JOIN nominations n ON n.hash=p.block_hash WHERE n.height=965931 LIMIT 1").fetchone()
            with catalogue.db:
                catalogue.db.execute("UPDATE publications SET supported=0 WHERE id=?", (row[0],))
            packet = catalogue.query(TARGETS[0][1])
            self.assertEqual(packet["status"], "found")
            self.assertEqual(packet["coverage"]["unsupported_payloads"], 1)
            self.assertFalse(packet["coverage"]["target_association_complete"])

    def test_disjoint_periods_are_separate_and_hash_qualified(self):
        with Catalogue(self.database, readonly=True) as catalogue:
            packet = catalogue.query("0x" + "44" * 32)
        self.assertEqual(sorted(packet["coverage"]["fully_scanned_intervals"]), [[941741,941750],[965925,965931]])
        self.assertEqual(len(packet["coverage"]["blocks"]), 17)
        self.assertTrue(all(len(row["hash"]) == 64 for row in packet["coverage"]["blocks"]))

    def test_historical_height_conflict_keeps_both_observations(self):
        database = self.copied()
        receipt = json.loads(SEPTEMBER.read_text())
        receipt["coverage"]["blocks"][-1]["height"] += 1
        path = self.fixture(receipt, database.parent)
        with Catalogue(database) as catalogue:
            catalogue.import_receipt(path, BODIES, body_reader=lambda _: self.fail("Already compiled body reread"))
            packet = catalogue.query(TARGETS[0][1])
            self.assertEqual(len(packet["proofs"]), 4)
            self.assertEqual(len(packet["coverage"]["historical_observations"]), 2)
            self.assertTrue(all(p["reported_bitcoin_heights"] == [965931, 965932] for p in packet["proofs"]))
            self.assertIsNone(packet["selection"]["best_observed_proof_index"])
            self.assertEqual(packet["issues"][0]["code"], "historical_height_conflict")

    def test_conflicting_target_metadata_is_explicit(self):
        database = self.copied()
        with Catalogue(database) as catalogue:
            row = catalogue.db.execute("SELECT id FROM imports LIMIT 1").fetchone()
            header = copy.deepcopy(catalogue.query(TARGETS[0][1])["query"]["header"])
            header["stateRoot"] = "0x" + "55" * 32
            with catalogue.db:
                catalogue._header(header, row[0])
            packet = catalogue.query(TARGETS[0][1])
            self.assertEqual(packet["status"], "incomplete")
            self.assertEqual(packet["issues"][0]["code"], "conflicting_retained_headers")

    def test_tampered_compiled_transaction_rejected(self):
        database = self.copied()
        with Catalogue(database) as catalogue:
            row = catalogue.db.execute("SELECT p.id,p.data FROM publications p JOIN associations a ON a.publication_id=p.id LIMIT 1").fetchone()
            data = json.loads(row["data"])
            data["raw_transaction_hex"] = data["raw_transaction_hex"][:-2] + "ff"
            with catalogue.db:
                catalogue.db.execute("UPDATE publications SET data=? WHERE id=?", (json.dumps(data),row["id"]))
            with self.assertRaises(DataError):
                for _, requested, _, _ in TARGETS:
                    catalogue.query(requested)

    def test_tampered_derived_ancestry_and_target_rejected(self):
        with Catalogue(self.database, readonly=True) as catalogue:
            original = catalogue.query(TARGETS[0][1])
        for kind in ("old_start", "parent_link", "requested_hash", "merkle"):
            packet = copy.deepcopy(original)
            if kind == "old_start":
                packet["proofs"][0]["ancestry"] = json.loads(SEPTEMBER.read_text())["proofs"][0]["ancestry"]
            elif kind == "parent_link":
                packet["proofs"][0]["ancestry"][1]["parentHash"] = "0x" + "44" * 32
            elif kind == "requested_hash":
                packet["query"]["requested_block_hash"] = "0x" + "44" * 32
            else:
                packet["proofs"][0]["merkle_siblings_display_hex"][0] = "44" * 32
            with self.subTest(kind=kind), self.assertRaises(DataError):
                verify_receipt(packet)

    def test_original_keystone_boundary_and_hash_normalization(self):
        original = json.loads(SEPTEMBER.read_text())
        requested = original["proofs"][0]["checkpoint"]["header"]["hash"]
        with Catalogue(self.database, readonly=True) as catalogue:
            packet = catalogue.query('0x' + requested[2:].upper())
        self.assertEqual(packet["query"]["block_number"], 5243125)
        self.assertEqual(len(packet["proofs"][0]["ancestry"]), 1)
        self.assertEqual(verify_receipt(packet)["proofs_checked"], 4)

    def linked_fixture(self):
        # Controlled storage layout over real retained proofs: force the
        # maintained graph path instead of a pre-sliced template ancestry.
        database = self.fresh().parent / 'service.sqlite3'
        shutil.copyfile(self.database, database)
        with CollectionStore(database.parent) as store:
            for identity, raw in store.db.execute('SELECT id,data FROM templates').fetchall():
                template = json.loads(raw)
                template['ancestry'] = [template['checkpoint']['header']]
                store.db.execute('UPDATE templates SET data=? WHERE id=?', (json.dumps(template), identity))
            store.db.commit()
        return database

    def test_overlapping_graph_proofs_share_parent_reads_per_request(self):
        database = self.linked_fixture()
        with Catalogue(database, readonly=True) as catalogue:
            statements = []
            catalogue.db.set_trace_callback(statements.append)
            for _ in range(2):
                statements.clear()
                packet = catalogue.query(TARGETS[0][1])
                self.assertEqual(verify_receipt(packet)['proofs_checked'], 4)
                reads = [s for s in statements if s.startswith('SELECT data FROM headers WHERE hash=')]
                self.assertTrue(reads)
                self.assertEqual(len(reads), len(set(reads)))
                self.assertEqual(len(reads), 82)

    def test_parent_ambiguity_and_repair_are_seen_on_subsequent_requests(self):
        database = self.linked_fixture()
        parent = json.loads(SEPTEMBER.read_text())['proofs'][0]['ancestry'][-2]
        altered = copy.deepcopy(parent)
        altered['stateRoot'] = '0x' + '44' * 32
        with Catalogue(database) as catalogue:
            self.assertEqual(len(catalogue.query(TARGETS[0][1])['proofs']), 4)
            catalogue.db.commit()
            with catalogue.db:
                catalogue.db.execute('INSERT INTO headers VALUES (?,?,?,?)',
                    (parent['hash'][2:], 'controlled-parent-conflict', int(parent['number'],16), json.dumps(altered)))
            rejected = catalogue.query(TARGETS[0][1])
            self.assertFalse(rejected['proofs'])
            self.assertTrue(rejected['coverage']['unresolved_candidates'])
            catalogue.db.commit()
            with catalogue.db:
                catalogue.db.execute("DELETE FROM headers WHERE variant='controlled-parent-conflict'")
            self.assertEqual(verify_receipt(catalogue.query(TARGETS[0][1]))['proofs_checked'], 4)

    def test_missing_or_wrong_height_parent_never_becomes_a_cached_positive(self):
        parent = json.loads(SEPTEMBER.read_text())['proofs'][0]['ancestry'][-2]
        for kind in ('missing', 'height'):
            with self.subTest(kind=kind), Catalogue(self.linked_fixture()) as catalogue:
                with catalogue.db:
                    if kind == 'missing':
                        catalogue.db.execute('DELETE FROM header_origins WHERE hash=?', (parent['hash'][2:],))
                        catalogue.db.execute('DELETE FROM headers WHERE hash=?', (parent['hash'][2:],))
                    else:
                        altered = copy.deepcopy(parent)
                        altered['number'] = hex(int(parent['number'],16) - 1)
                        catalogue.db.execute('UPDATE headers SET data=? WHERE hash=?',
                            (json.dumps(altered), parent['hash'][2:]))
                packet = catalogue.query(TARGETS[0][1])
                self.assertFalse(packet['proofs'])
                self.assertTrue(packet['coverage']['unresolved_candidates'])

    def test_reused_parent_graph_still_checks_target_metadata_and_distance(self):
        reference = json.loads(SEPTEMBER.read_text())
        requested = reference['proofs'][0]['ancestry'][1]
        publishing = reference['proofs'][-1]['checkpoint']['header']
        with Catalogue(self.database, readonly=True) as catalogue:
            parents = {}
            path = catalogue._linked_path(requested, publishing, parents)
            self.assertTrue(path)
            altered = copy.deepcopy(requested)
            altered['stateRoot'] = '0x' + '44' * 32
            self.assertIsNone(catalogue._linked_path(altered, publishing, parents))
            beyond = copy.deepcopy(publishing)
            beyond['number'] = hex(int(requested['number'],16) + 257)
            self.assertIsNone(catalogue._linked_path(requested, beyond, parents))

    def test_deadline_applies_even_when_every_parent_is_already_read(self):
        reference = json.loads(SEPTEMBER.read_text())
        requested = reference['proofs'][0]['ancestry'][1]
        publishing = reference['proofs'][-1]['checkpoint']['header']
        with Catalogue(self.database, readonly=True) as catalogue:
            parents = {}
            self.assertTrue(catalogue._linked_path(requested, publishing, parents))
            catalogue.check = ReadBudget()
            catalogue.check.deadline = 0
            with self.assertRaises(ReadError):
                catalogue._linked_path(requested, publishing, parents)

    def test_reference_server_read_only_boundary(self):
        server = make_server(self.database, ROOT, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        def request(path, method="GET", host=None):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            connection.request(method, path, headers={"Host": host or f"127.0.0.1:{server.server_port}"})
            response = connection.getresponse()
            body, headers, status = response.read(), dict(response.getheaders()), response.status
            connection.close()
            return status, body, headers
        try:
            status, body, headers = request("/api/catalogue")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["examples"][0]["number"], 5243118)
            self.assertIn("connect-src 'self'", headers["Content-Security-Policy"])
            self.assertEqual(request("/api/evidence?hash=" + TARGETS[0][1])[0], 200)
            self.assertEqual(request("/api/evidence?hash=5243118")[0], 422)
            self.assertEqual(request("/api/evidence?hash=" + TARGETS[0][1] + "&hash=" + TARGETS[1][1])[0], 422)
            self.assertEqual(request("/api/catalogue", host="example.com")[0], 403)
            self.assertEqual(request("/api/evidence", method="POST")[0], 405)
            self.assertEqual(request("/../.hemi_lookup_data/session.json")[0], 404)
            self.assertEqual(request("/")[0], 200)
            self.assertEqual(request("/assets/hemi.svg")[0], 200)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()
