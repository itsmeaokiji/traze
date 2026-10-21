"""Browser bridge tests: actual HTTP routes and engine with controlled public views."""

import copy
import json
import re
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from hemi_lookup.engine import Engine
from hemi_lookup.proof import verify_receipt
from hemi_lookup.web import Jobs, LocalServer, WebError
from test_demo import FakeSources, REFERENCE


class WebTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="hemi_browser_test_")
        self.root = Path(temp.name).resolve()
        self.assertTrue(self.root.is_relative_to(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(temp.cleanup)
        samples = self.root / "demo_results"
        samples.mkdir()
        self.path = samples / "mainnet_5243117.json"
        self.path.write_text(json.dumps(REFERENCE), encoding="utf-8")
        self.jobs = Jobs(self.root, mode="discovery")
        self.server = LocalServer(0, self.jobs)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, path, payload=None, extra=None):
        headers = {}
        if payload is not None:
            headers = {"Content-Type": "application/json", "Origin": self.base, "X-Local-Token": self.server.token}
        headers.update(extra or {})
        request = Request(self.base + path, data=None if payload is None else json.dumps(payload).encode(), headers=headers)
        try:
            with urlopen(request, timeout=2) as response:
                return response.status, response.headers, response.read()
        except HTTPError as response:
            return response.code, response.headers, response.read()

    def wait_job(self, identity):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            job = self.jobs.job(identity)
            if job["state"] != "running" and self.jobs.active is None:
                return job
            time.sleep(.01)
        self.fail("Controlled local job did not finish")

    def test_saved_proofs_are_recomputed_and_download_preserves_evidence(self):
        before = self.path.read_bytes()
        status, _, raw = self.request("/api/receipts/example-ordinary")
        self.assertEqual(status, 200)
        wrapper = json.loads(raw)
        self.assertEqual(wrapper["verification"]["proofs_checked"], 1)
        self.assertFalse(wrapper["verification"]["current_chain_membership_checked"])
        status, headers, raw = self.request("/api/receipts/example-ordinary/download")
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertEqual(json.loads(raw), REFERENCE)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse((self.root / ".hemi_lookup_data/session.json").exists())

    def test_default_indexed_mode_missing_index_never_starts_source_work(self):
        self.jobs = self.server.jobs = Jobs(self.root)
        self.assertEqual(self.jobs.state()['serving_mode'], 'indexed-only')
        with patch('hemi_lookup.web.PublicSources', side_effect=AssertionError('No source construction')), \
             patch('hemi_lookup.web.Session', side_effect=AssertionError('No source session')):
            status, _, raw = self.request('/api/v1/blocks/12345')
            self.assertEqual(status, 503)
        self.assertEqual(json.loads(raw)['error']['code'], 'index_unavailable')
        self.assertEqual(self.jobs.jobs, {})
        self.assertFalse((self.root / '.hemi_lookup_data/session.json').exists())

    def test_indexed_mode_rejects_forged_source_actions_without_new_jobs(self):
        self.jobs = self.server.jobs = Jobs(self.root)
        with patch('hemi_lookup.web.PublicSources', side_effect=AssertionError('No source construction')):
            for path, payload in [('/api/refresh', {'receipt_id': 'example-ordinary'}),
                                  ('/api/lookup', {'block': 5243119, 'new_lookup': True}),
                                  ('/api/lookup', {'block': 5243119, 'bitcoin_range': [1, 2]})]:
                status, _, raw = self.request(path, payload)
                self.assertEqual(status, 403)
                self.assertEqual(json.loads(raw)['error']['code'], 'source_operations_disabled')
        self.assertEqual(self.jobs.jobs, {})
        self.assertFalse((self.root / '.hemi_lookup_data/session.json').exists())

    def test_indexed_hit_number_hash_inspection_and_export_preserve_accounting(self):
        from collection_fixtures import CollectionSources
        from hemi_lookup.collection_store import CollectionStore
        from hemi_lookup.collector import Collector
        state = self.root / '.hemi_lookup_data'
        with CollectionStore(state) as store:
            identity = store.create(965931, 965931)['id']
        Collector(self.root, sources_factory=CollectionSources).run(identity)
        ledger = (state / 'session.json').read_bytes()
        self.jobs = self.server.jobs = Jobs(self.root)
        number = 5243119
        with patch('hemi_lookup.web.Session', side_effect=AssertionError('No acquisition')):
            for target in [number, '0x9520c8410a38e2feaeb1878dc39c0fceb57d75cb4873dc89b4b1370a777293f9']:
                status, _, raw = self.request('/api/v1/blocks/' + str(target))
                self.assertEqual(status, 200)
                self.assertGreater(json.loads(raw)['verification']['proofs_checked'], 0)
                self.assertGreater(verify_receipt(json.loads(raw)['receipt'])['proofs_checked'], 0)
        self.assertEqual((state / 'session.json').read_bytes(), ledger)
        self.assertFalse((state / 'receipts').exists())

    def test_tampered_saved_receipt_has_no_success_response(self):
        receipt = copy.deepcopy(REFERENCE)
        receipt["proofs"][0]["checkpoint"]["payload_hex"] = "00" * 76
        self.path.write_text(json.dumps(receipt), encoding="utf-8")
        status, _, raw = self.request("/api/receipts/example-ordinary")
        self.assertEqual(status, 422)
        self.assertEqual(json.loads(raw)["error"]["code"], "invalid_proof")

    def test_local_only_routes_and_security_headers(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        status, headers, raw = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("Publication evidence", raw.decode())
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        for path in ("/api/receipts/../../session.json", "/.hemi_lookup_data/session.json", "/README.md"):
            self.assertEqual(self.request(path)[0], 404)
        self.assertEqual(self.request("/api/state", extra={"Host": "evil.example"})[0], 403)

    def test_cross_origin_or_missing_token_cannot_start_public_reads(self):
        for headers in ({"Origin": "https://evil.example"}, {"X-Local-Token": ""}):
            self.assertEqual(self.request("/api/lookup", {"block_number": 5243125}, headers)[0], 403)
        self.assertEqual(self.jobs.jobs, {})
        self.assertFalse((self.root / ".hemi_lookup_data/session.json").exists())

    def test_compiled_frontend_assets_load_with_same_security_headers(self):
        status, _, raw = self.request("/")
        self.assertEqual(status, 200)
        html = raw.decode()
        self.assertIn('id="root"', html)
        paths = re.findall(r'(?:src|href)="(/assets/[^" ]+)"', html)
        self.assertEqual(len(paths), 3)
        self.assertIn('/assets/theme-init.js', paths)
        self.assertTrue(any(path.endswith('.js') for path in paths))
        self.assertTrue(any(path.endswith('.css') for path in paths))
        for path in paths:
            status, headers, content = self.request(path)
            self.assertEqual(status, 200)
            self.assertTrue(content)
            self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
            self.assertIn("script-src 'self'", headers['Content-Security-Policy'])
            self.assertIn('javascript' if path.endswith('.js') else 'text/css', headers['Content-Type'])
        self.assertFalse((self.root / '.hemi_lookup_data/session.json').exists())

    def test_local_font_assets_load_with_correct_type_without_relaxing_csp(self):
        _, _, raw = self.request('/')
        styles = re.findall(r'href="(/assets/[^" ]+\.css)"', raw.decode())
        self.assertEqual(len(styles), 1)
        _, _, css = self.request(styles[0])
        fonts = re.findall(r'url\((/assets/[^) ]+\.woff2)\)', css.decode())
        self.assertEqual(len(fonts), 2)
        for path in fonts:
            status, headers, content = self.request(path)
            self.assertEqual(status, 200)
            self.assertEqual(headers['Content-Type'], 'font/woff2')
            self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
            self.assertTrue(content.startswith(b'wOF2'))
            self.assertIn("default-src 'self'", headers['Content-Security-Policy'])
            self.assertNotIn('unsafe-inline', headers['Content-Security-Policy'])
        self.assertFalse((self.root / '.hemi_lookup_data/session.json').exists())

    def test_asset_routes_do_not_expose_source_maps_or_private_paths(self):
        for path in ('/assets/../README.md', '/assets/%2e%2e/session.json',
                     '/assets/nested/index.js', '/assets/package.json', '/assets/index.js.map',
                     '/src/App.tsx', '/frontend/package.json', '/app.js', '/style.css'):
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 404)
        self.assertFalse((self.root / '.hemi_lookup_data/session.json').exists())

    def test_asset_path_resolving_outside_fixed_directory_is_rejected(self):
        static = self.root / 'compiled'
        assets = static / 'assets'
        assets.mkdir(parents=True)
        secret = self.root / 'outside.js'
        secret.write_text('private test data', encoding='utf-8')
        alias = assets / 'outside.js'
        original_resolve = Path.resolve

        def resolve_asset(path, *args, **kwargs):
            # Model an escaping symlink without requiring Windows link privileges.
            return original_resolve(secret) if path == alias else original_resolve(path, *args, **kwargs)

        with patch('hemi_lookup.web.STATIC', static), patch.object(Path, 'resolve', autospec=True, side_effect=resolve_asset):
            self.assertEqual(self.request('/assets/outside.js')[0], 404)

    def test_invalid_inputs_are_rejected_before_job_or_ledger_creation(self):
        inputs = [
            {"block_number": 0}, {"block_number": True}, {"block_number": "5243125"},
            {"block_number": 0xffffffff}, {"block_number": 5243125, "bitcoin_range": [965931, 965925]},
            {"block_number": 5243125, "bitcoin_range": [1, 201]},
        ]
        for payload in inputs:
            with self.subTest(payload=payload):
                self.assertEqual(self.request("/api/lookup", payload)[0], 400)
        self.assertEqual(self.jobs.jobs, {})
        self.assertFalse((self.root / ".hemi_lookup_data/session.json").exists())

    def test_async_refresh_reuses_actual_engine_and_writes_new_snapshot(self):
        before = self.path.read_bytes()
        with patch("hemi_lookup.web.PublicSources", side_effect=lambda session: FakeSources()):
            status, _, raw = self.request("/api/refresh", {"receipt_id": "example-ordinary"})
            self.assertEqual(status, 202)
            job = self.wait_job(json.loads(raw)["id"])
        self.assertEqual(job["state"], "complete")
        receipt = self.jobs.receipt(job["receipt_id"])["receipt"]
        self.assertEqual(receipt["status"], "found")
        self.assertEqual(receipt["mode"], "live_metadata_refresh")
        self.assertEqual(receipt["proofs"][0]["raw_transaction_hex"], REFERENCE["proofs"][0]["raw_transaction_hex"])
        self.assertEqual(verify_receipt(receipt)["proofs_checked"], 1)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.jobs.resources()["total_response_bytes"], 0)
        self.assertFalse((self.root / ".hemi_lookup_data/session.lock").exists())
        reloaded = Jobs(self.root)
        self.assertIn(job["receipt_id"], [r["id"] for r in reloaded.state()["receipts"]])

    def test_failed_source_view_remains_incomplete_in_browser_receipt(self):
        with patch("hemi_lookup.web.PublicSources", side_effect=lambda session: FakeSources("provider_conflict")):
            job = self.jobs.start("refresh", {"receipt_id": "example-ordinary"})
            result = self.wait_job(job["id"])
        receipt = self.jobs.receipt(result["receipt_id"])["receipt"]
        self.assertEqual(receipt["status"], "incomplete")
        self.assertIsNone(receipt["selection"]["current_proof_index"])
        self.assertFalse(receipt["context"]["current_view_confirmed"])
        self.assertEqual(verify_receipt(receipt)["proofs_checked"], 1)

    def test_second_lookup_is_rejected_while_worker_holds_shared_session(self):
        entered, gate = threading.Event(), threading.Event()
        original = Engine.refresh

        def paused(engine, receipt):
            entered.set()
            if not gate.wait(2):
                raise AssertionError("Controlled gate timeout")
            return original(engine, receipt)

        with patch("hemi_lookup.web.PublicSources", side_effect=lambda session: FakeSources()), patch.object(Engine, "refresh", autospec=True, side_effect=paused):
            first = self.jobs.start("refresh", {"receipt_id": "example-ordinary"})
            try:
                self.assertTrue(entered.wait(1))
                with self.assertRaises(WebError) as rejection:
                    self.jobs.start("lookup", {"block_number": 5243125})
                self.assertEqual(rejection.exception.status, 409)
            finally:
                gate.set()
                self.wait_job(first["id"])

    def test_existing_cli_lock_prevents_browser_job_without_resetting_state(self):
        state = self.root / ".hemi_lookup_data"
        state.mkdir()
        lock = state / "session.lock"
        lock.write_text("controlled-external-cli-writer")
        with self.assertRaises(WebError) as rejection:
            self.jobs.start("lookup", {"block_number": 5243125})
        self.assertEqual(rejection.exception.code, "busy")
        self.assertEqual(lock.read_text(), "controlled-external-cli-writer")


if __name__ == "__main__":
    unittest.main()
