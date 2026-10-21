"""Public WSGI boundary and read-only monitoring; no chain or package requests."""

import io
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from hemi_lookup.catalogue import build_catalogue, timestamp
from hemi_lookup.operations import inspect_state, public_snapshot, MONITOR_SCHEMA
from hemi_lookup.public import PublicApplication, origin_setting, server_options
from hemi_lookup.collection_store import CollectionStore

ROOT = Path(__file__).resolve().parent.parent


class HostingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base = ROOT / "output/qa-hosting"
        base.mkdir(parents=True, exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(dir=base)
        cls.addClassCleanup(cls.temp.cleanup)
        cls.fixture = Path(cls.temp.name) / "fixture.sqlite3"
        build_catalogue(ROOT, cls.fixture, block_directory=ROOT / "tests/fixtures/bitcoin_blocks")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "output/qa-hosting")
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        shutil.copyfile(self.fixture, self.state / "catalogue.sqlite3")
        self.logs = []
        self.app = PublicApplication(self.state, "https://traze.example.com", log=self.logs.append)

    def request(self, path, *, method="GET", host="traze.example.com", origin=None, **changes):
        name, _, query = path.partition("?")
        env = {"REQUEST_METHOD": method, "PATH_INFO": name, "QUERY_STRING": query,
               "HTTP_HOST": host, "wsgi.input": io.BytesIO(), **changes}
        if origin:
            env["HTTP_ORIGIN"] = origin
        found = {}
        def start(status, headers):
            found.update(status=int(status.split()[0]), headers=dict(headers))
        raw = b"".join(self.app(env, start))
        value = json.loads(raw) if raw and found["headers"]["Content-Type"] == "application/json" else raw
        return found["status"], found["headers"], value

    def test_origin_and_loopback_server_configuration(self):
        for value in ("http://traze.example.com", "https://traze.example.com/", "https://user@traze.example.com", "https://traze.example.com?x=1"):
            with self.assertRaises(ValueError):
                origin_setting(value)
        self.assertEqual(origin_setting("http://127.0.0.1:8785", True), "http://127.0.0.1:8785")
        options = server_options(8000)
        self.assertEqual(options["host"], "127.0.0.1")
        self.assertEqual(options["threads"], 6)
        self.assertEqual(options["connection_limit"], 32)
        self.assertIsNone(options["trusted_proxy"])
        self.assertTrue(options["clear_untrusted_proxy_headers"])

    def test_exact_host_origin_and_forwarded_spoof(self):
        self.assertEqual(self.request("/api/v1", origin="https://traze.example.com")[0], 200)
        self.assertEqual(self.request("/api/v1", host="evil.example", HTTP_X_FORWARDED_HOST="traze.example.com")[0], 403)
        self.assertEqual(self.request("/api/v1", origin="https://evil.example")[0], 403)
        self.assertNotIn("Access-Control-Allow-Origin", self.request("/api/v1")[1])

    def test_private_source_and_arbitrary_file_routes_absent(self):
        for path in ("/api/jobs/" + "a" * 32, "/api/receipts/example-ordinary", "/api/receipts/example-ordinary/download",
                     "/api/lookup", "/api/refresh", "/.env", "/.git/config", "/session.json", "/assets/../session.json"):
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 404)

    def test_method_and_body_rejected_with_security_headers(self):
        status, headers, body = self.request("/api/v1", method="POST")
        self.assertEqual(status, 405)
        self.assertEqual(headers["Allow"], "GET, HEAD")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(self.request("/api/v1", CONTENT_LENGTH="1")[0], 400)
        self.assertEqual(self.request("/api/v1", HTTP_TRANSFER_ENCODING="chunked")[0], 400)
        head = self.request("/health/live", method="HEAD")
        self.assertEqual(head[0], 200)
        self.assertEqual(head[2], b"")
        self.assertGreater(int(head[1]["Content-Length"]), 0)

    def test_query_and_url_limits_fail_without_source_fallback(self):
        for path, expected in (("/api/v1/index?new_lookup=1", 400), ("/api/v1/publications?limit=1&limit=2", 400),
                               ("/api/v1/index?x", 400), ("/api/v1/index?" + "a" * 2050, 414),
                               ("/api/v1/publications?limit=51", 400)):
            self.assertEqual(self.request(path)[0], expected)

    def test_real_proofs_and_number_hash_link_preserved(self):
        with patch("hemi_lookup.network.PublicSources", side_effect=AssertionError("No source")), patch("hemi_lookup.storage.Session", side_effect=AssertionError("No session")):
            first = self.request("/api/v1/blocks/5243119")
            self.assertEqual(first[0], 200)
            exact = self.request(first[2]["record_url"].replace("/records/hemi/", "/api/v1/blocks/"))
            self.assertEqual(first[2]["record_url"], exact[2]["record_url"])
            self.assertEqual(exact[2]["verification"]["proofs_checked"], 4)
            self.assertFalse(exact[2]["verification"]["current_chain_membership_checked"])
            self.assertEqual(self.request(first[2]["record_url"])[0], 200)
        self.assertFalse((self.state / "session.json").exists())
        self.assertFalse((self.state / "receipts").exists())
        self.assertFalse(any("5243119" in json.dumps(line) for line in self.logs))

    def test_readiness_is_not_liveness_or_freshness(self):
        self.assertEqual(self.request("/health/ready")[0], 200)
        self.assertEqual(self.request("/api/v1/status")[2]["monitoring"], "unavailable")
        (self.state / "service.sqlite3").write_bytes(b"corrupt maintained view")
        self.assertEqual(self.request("/health/ready")[0], 503)
        self.assertEqual(self.request("/health/live")[0], 200)
        self.assertEqual(self.request("/api/v1/blocks/5243119")[0], 503)

    def test_reader_admission_returns_retry_without_waiting(self):
        slots = [self.app.explorer.admit() for _ in range(5)]
        for slot in slots:
            slot.__enter__()
        try:
            result = self.request("/api/v1/index")
            self.assertEqual(result[0], 429)
            self.assertEqual(result[1]["Retry-After"], "1")
        finally:
            for slot in slots:
                slot.__exit__(None, None, None)

    def test_global_rate_is_bounded_independent_of_spoofed_ip(self):
        with patch("hemi_lookup.public.time.monotonic", return_value=10):
            for _ in range(40):
                self.assertEqual(self.request("/health/live", HTTP_X_FORWARDED_FOR="203.0.113.1")[0], 200)
            self.assertEqual(self.request("/health/live", HTTP_X_FORWARDED_FOR="203.0.113.2")[0], 429)

    def test_public_state_has_no_saved_files_tokens_or_ledger(self):
        value = self.request("/api/state")[2]
        self.assertEqual(value["deployment"], "hosted-candidate")
        self.assertEqual(value["receipts"], [])
        self.assertEqual(value["csrf_token"], "")
        self.assertIsNone(value["active_job"])
        self.assertFalse(value["resources"]["available"])

    def test_only_built_assets_are_cacheable(self):
        asset = next((ROOT / "web/assets").glob("*.js"))
        self.assertIn("immutable", self.request("/assets/" + asset.name)[1]["Cache-Control"])
        self.assertEqual(self.request("/")[1]["Cache-Control"], "no-store")
        self.assertEqual(self.request("/api/v1")[1]["Cache-Control"], "no-store")

    def test_monitoring_missing_future_or_stale_snapshot_remains_unavailable(self):
        for record in ({}, {"schema": MONITOR_SCHEMA, "generated_at": "2000-01-01T00:00:00Z"},
                       {"schema": MONITOR_SCHEMA, "generated_at": "2099-01-01T00:00:00Z"}):
            self.assertEqual(public_snapshot(record)["monitoring"], "unavailable")

    def test_monitoring_recent_snapshot_can_have_old_source_data_and_no_private_fields(self):
        value = {"schema": MONITOR_SCHEMA, "generated_at": timestamp(), "observation_max_age_seconds": 60,
                 "observations": {"bitcoin": {"observed_at": "2020-01-01T00:00:00Z"}},
                 "collection": {"state": "stopped", "id": "secret", "issue": {"message": "private"}, "processed_blocks": 17}}
        self.app.monitor_file = self.state / "monitor.json"
        self.app.monitor_file.write_text(json.dumps(value))
        result = self.request("/api/v1/status")[2]
        self.assertEqual(result["monitoring"], "available")
        self.assertEqual(result["observations"]["bitcoin"]["recency"], "stale")
        self.assertEqual(result["collection"], {"state": "stopped", "processed_blocks": 17})
        self.assertNotIn("secret", json.dumps(result))

    def test_operator_monitoring_has_separate_disk_index_observation_and_restore_checks(self):
        before = (self.state / "catalogue.sqlite3").read_bytes()
        result = inspect_state(self.state, min_free_bytes=0)
        self.assertTrue(result["checks"]["index_readable"])
        self.assertTrue(result["checks"]["disk_space"])
        self.assertFalse(result["checks"]["observation_recency"])
        self.assertFalse(result["healthy"])
        (self.state / "restore-guard.json").write_text("{}")
        result = inspect_state(self.state, min_free_bytes=10**30)
        self.assertFalse(result["checks"]["disk_space"])
        self.assertFalse(result["checks"]["restore_review_clear"])
        self.assertEqual(before, (self.state / "catalogue.sqlite3").read_bytes())

    def test_new_monitor_report_can_report_recent_sources_and_valid_accounting(self):
        shutil.copyfile(self.fixture, self.state / "service.sqlite3")
        (self.state / "session.json").write_text(json.dumps({"schema": 1, "prior_validation_bytes": {},
            "downloaded_bytes": 23, "runs": []}))
        with CollectionStore(self.state) as store:
            for chain in ("bitcoin", "hemi"):
                store.observe(chain, 1, "a" * 64, "controlled-fixture")
        result = inspect_state(self.state, min_free_bytes=0)
        self.assertTrue(result["checks"]["accounting_readable"])
        self.assertTrue(result["checks"]["observation_recency"])
        self.assertEqual(result["public"]["monitoring"], "available")
        self.assertEqual(result["accounting"]["counted_response_bytes"], 23)


if __name__ == "__main__":
    unittest.main()
