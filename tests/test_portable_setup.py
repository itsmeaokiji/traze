"""Clean setup through the public CLI without a private catalogue or cache."""
import contextlib
import io
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import hemi_catalogue
from hemi_lookup.catalogue import Catalogue
from hemi_lookup.proof import verify_receipt

ROOT = Path(__file__).resolve().parent.parent
BODIES = ROOT / "tests/fixtures/bitcoin_blocks"


class PortableSetupTests(unittest.TestCase):
    def setUp(self):
        qa = (ROOT / "output/qa-temp-portable-setup").resolve()
        assert qa.is_relative_to(ROOT.resolve())
        qa.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="fresh_", dir=qa)
        self.directory = Path(self.temp.name).resolve()
        self.assertTrue(self.directory.is_relative_to(qa))
        self.addCleanup(self.temp.cleanup)

    def build(self, bodies):
        database = self.directory / "catalogue.sqlite3"
        output = io.StringIO()
        private = (ROOT / ".hemi_lookup_data").resolve()
        original_read = Path.read_bytes

        def public_read(path):
            if path.resolve().is_relative_to(private):
                raise AssertionError("Portable setup read private runtime state")
            return original_read(path)

        with patch.object(socket.socket, "connect", side_effect=AssertionError("No chain requests")), \
                patch.object(Path, "read_bytes", public_read), contextlib.redirect_stdout(output):
            status = hemi_catalogue.main(["--database", str(database), "build", "--body-dir", str(bodies)])
        self.assertEqual(status, 0)
        return database, json.loads(output.getvalue())

    def test_cli_builds_fresh_public_fixture_catalogue(self):
        database, report = self.build(BODIES)
        self.assertEqual(report["network_requests"], 0)
        self.assertEqual(report["summary"]["processed_bodies"], 17)
        self.assertEqual(report["summary"]["associated_publications"], 10)
        with Catalogue(database, readonly=True) as catalogue:
            requested = catalogue.resolve_number(5243119)
            packet = catalogue.query(requested)
        self.assertEqual(packet["status"], "found")
        self.assertEqual(verify_receipt(packet)["proofs_checked"], 4)
        self.assertEqual(min(len(p["ancestry"]) - 1 for p in packet["proofs"]), 6)

    def test_missing_fixture_directory_stays_explicitly_incomplete(self):
        database, report = self.build(self.directory / "missing-bodies")
        self.assertEqual(report["summary"]["processed_bodies"], 0)
        self.assertEqual(report["summary"]["failed_or_pending_bodies"], 17)
        with Catalogue(database, readonly=True) as catalogue:
            packet = catalogue.query("0x" + "44" * 32)
        self.assertEqual(packet["status"], "incomplete")
        self.assertEqual(packet["proofs"], [])


if __name__ == "__main__":
    unittest.main()
