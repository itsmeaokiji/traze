"""Actual deadline controls using loopback HTTP and saved proof/body data.

Loopback replies are controlled test data, not live Hemi/Bitcoin observations.
All ledger/cache writes use isolated, verified temporary test directories.
"""

import copy
import json
import multiprocessing
import socket
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from hemi_lookup.bitcoin import parse_block
from hemi_lookup.engine import Engine
from hemi_lookup.errors import DataError, DeadlineError
from hemi_lookup.network import HTTP, HEMI_RPC, PublicSources
from hemi_lookup.proof import verify_receipt
from hemi_lookup.storage import Session, BlockCache
from hemi_lookup.transport import ProcessHTTP, CLEANUP_TOLERANCE_SECONDS
from hemi_lookup.web import Jobs
from test_demo import FakeSession, FakeSources, REFERENCE

ROOT = Path(__file__).resolve().parent.parent


class ControlledServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        self.release = threading.Event()
        self.entered = threading.Event()
        self.paths = []
        super().__init__(("127.0.0.1", 0), ControlledHandler)


class ControlledHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.do_GET()

    def do_GET(self):
        self.server.paths.append(self.path)
        try:
            if self.path == "/fast":
                self.send_response(200)
                self.send_header("Content-Length", "64")
                self.end_headers()
                self.wfile.write(b"x" * 64)
                return
            self.server.entered.set()
            if self.path == "/headers":
                self.server.release.wait(5)
            self.send_response(500 if self.path == "/error" else 200)
            self.send_header("Content-Length", "65536")
            self.end_headers()
            self.wfile.write(b"x" * 2048)
            self.wfile.flush()
            self.server.release.wait(5)
            self.wfile.write(b"x" * (65536 - 2048))
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


class LoopbackTransport:
    """Map a validated facade request to a controlled local reply for tests."""

    def __init__(self, base):
        self.base, self.route = base, "/fast"
        self.worker = ProcessHTTP()
        self.requests = []

    def read(self, url, body, headers, ceiling, deadline, on_count, check=None):
        self.requests.append(url)
        return self.worker.read(self.base + self.route, body, headers, ceiling, deadline, on_count, check=check)

    def close(self):
        self.worker.close()


class DeadlineTests(unittest.TestCase):
    measurements = []
    evidence = None

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="hemi_deadline_test_")
        self.directory = Path(temp.name).resolve()
        self.assertTrue(self.directory.is_relative_to(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(temp.cleanup)
        # Protect parent-process tests from accidental external connections.
        original = socket.create_connection

        def loopback_only(address, *args, **kwargs):
            if address[0] not in ("127.0.0.1", "localhost", "::1"):
                raise AssertionError("External networking forbidden in deadline tests")
            return original(address, *args, **kwargs)

        self.guard = patch.object(socket, "create_connection", side_effect=loopback_only)
        self.guard.start()
        self.addCleanup(self.guard.stop)

    def server(self):
        server = ControlledServer()
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        thread.start()

        def stop():
            server.release.set()
            server.shutdown()
            server.server_close()
            thread.join(2)

        self.addCleanup(stop)
        return server, f"http://127.0.0.1:{server.server_port}"

    def connect(self, session, base):
        http = HTTP(session)
        http.transport = LoopbackTransport(base)
        # Establish the actual spawned HTTP worker before shortening the test
        # deadline. Production startup still consumes the original deadline.
        self.assertEqual(http.read(HEMI_RPC, 1024, {"method": "eth_chainId"}), b"x" * 64)
        return http

    def test_fast_reads_reuse_worker_and_close_with_session(self):
        _, base = self.server()
        with Session(self.directory) as session:
            http = self.connect(session, base)
            pid = http.transport.worker.process.pid
            http.read(HEMI_RPC, 1024, {"method": "eth_chainId"})
            self.assertEqual(http.transport.worker.process.pid, pid)
            self.assertEqual(session.metrics()["conservative_requests"], 0)
            self.assertTrue(all(e["received_bytes"] < e["reserved_bytes"] for e in session.run["requests"]))
        self.assertFalse(any(p.pid == pid for p in multiprocessing.active_children()))
        self.assertFalse(session.lock_path.exists())

    def interrupted(self, route):
        server, base = self.server()
        with Session(self.directory) as session:
            http = self.connect(session, base)
            pid = http.transport.worker.process.pid
            http.transport.route = route
            session.deadline = time.monotonic() + .5
            started = time.monotonic()
            with self.assertRaises(DeadlineError):
                http.read(HEMI_RPC, 131072, {"method": "eth_chainId"})
            elapsed = time.monotonic() - started
            self.assertTrue(server.entered.is_set(), "Request must reach the controlled server")
            self.assertLessEqual(elapsed, .5 + CLEANUP_TOLERANCE_SECONDS)
            self.assertFalse(any(p.pid == pid for p in multiprocessing.active_children()))
            entry = session.run["requests"][-1]
            self.assertEqual(entry["status"], "deadline_interrupted")
            self.assertEqual(entry["accounting"], "conservative_reservation")
            self.assertEqual(entry["received_bytes"], entry["reserved_bytes"])
            total = session.total_bytes
            with self.assertRaises(DeadlineError):
                http.read(HEMI_RPC, 1024, {"method": "eth_chainId"})
            self.assertEqual(len(session.run["requests"]), 2)
            self.assertEqual(len(http.transport.requests), 2)
            self.measurements.append({"case": route, "effective_deadline_seconds": .5,
                "elapsed_seconds": round(elapsed, 4), "cleanup_tolerance_seconds": CLEANUP_TOLERANCE_SECONDS,
                "accounted_bytes": entry["received_bytes"], "received_bytes_lower_bound": entry["received_bytes_lower_bound"],
                "worker_stopped": True})
        self.assertFalse(session.lock_path.exists())
        with Session(self.directory) as resumed:
            self.assertEqual(resumed.total_bytes, total)
        return entry

    def test_blocked_header_request_is_terminated(self):
        self.interrupted("/headers")

    def test_active_body_read_stops_and_preserves_accounting(self):
        entry = self.interrupted("/body")
        self.assertGreaterEqual(entry["received_bytes_lower_bound"], 2048)

    def test_blocked_http_error_body_is_also_terminated(self):
        self.interrupted("/error")

    def test_partial_download_cannot_enter_cache_or_trigger_fallback(self):
        server, base = self.server()
        with Session(self.directory) as session:
            sources = PublicSources(session)
            sources.http = self.connect(session, base)
            sources.http.transport.route = "/body"
            session.deadline = time.monotonic() + .5

            class SlowBodySources(FakeSources):
                def block_raw(self, block_hash, max_bytes):
                    return sources.block_raw(block_hash, max_bytes)

            cache = BlockCache(self.directory / "blocks")
            result = Engine(SlowBodySources(), session, cache).lookup(5243125, (965931, 965931))
            self.assertEqual(result["status"], "incomplete")
            self.assertEqual(result["coverage"]["stop_reason"], "time_budget_exhausted")
            self.assertEqual(result["coverage"]["blocks"], [])
            self.assertEqual(list(cache.directory.glob("*.bin")), [])
            self.assertEqual(len(session.run["requests"]), 2)  # Warmup + one body; no fallback.
            self.assertEqual(result["proofs"], [])
            self.assertTrue(server.entered.is_set())

    def test_startup_consumes_deadline_and_expired_call_never_starts_worker(self):
        worker = ProcessHTTP()
        self.addCleanup(worker.close)
        with self.assertRaises(DeadlineError):
            worker.read("http://127.0.0.1:1/", None, {}, 1024, time.monotonic() - 1, lambda n: None)
        self.assertIsNone(worker.process)

    def test_delayed_worker_startup_cannot_start_a_late_request(self):
        server, base = self.server()
        worker = ProcessHTTP()
        self.addCleanup(worker.close)
        original = worker._start

        def delayed_start():
            time.sleep(.05)
            original()

        started = time.monotonic()
        with patch.object(worker, "_start", side_effect=delayed_start), self.assertRaises(DeadlineError):
            worker.read(base + "/fast", None, {}, 1024, started + .02, lambda n: None)
        self.assertLessEqual(time.monotonic() - started, .02 + CLEANUP_TOLERANCE_SECONDS)
        self.assertEqual(server.paths, [])
        self.assertIsNone(worker.process)

    def test_unexpected_worker_exit_keeps_reservation_and_releases_session(self):
        server, base = self.server()
        with Session(self.directory) as session:
            http = self.connect(session, base)
            http.transport.route = "/body"
            errors = []

            def read():
                try:
                    http.read(HEMI_RPC, 131072, {"method": "eth_chainId"})
                except Exception as exc:
                    errors.append(exc)

            thread = threading.Thread(target=read, daemon=True)
            thread.start()
            self.assertTrue(server.entered.wait(2))
            http.transport.worker.process.terminate()
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], DataError)
            entry = session.run["requests"][-1]
            self.assertEqual(entry["accounting"], "conservative_reservation")
            self.assertEqual(entry["received_bytes"], entry["reserved_bytes"])
            total = session.total_bytes
        self.assertFalse(session.lock_path.exists())
        with Session(self.directory) as resumed:
            self.assertEqual(resumed.total_bytes, total)

    def test_deadline_during_cached_body_parsing_is_incomplete_without_download(self):
        block_hash = REFERENCE["proofs"][0]["bitcoin_block_hash"]
        raw = (ROOT / "tests/fixtures/mainnet_965931.bin").read_bytes()

        with Session(self.directory) as session:
            class Cache:
                started = None

                def get(self, identity):
                    if identity != block_hash:
                        raise AssertionError("Unexpected cached block")
                    self.started = time.monotonic()
                    session.deadline = self.started + .01
                    return raw

                def put(self, *args):
                    raise AssertionError("Expired cache parsing must not write a body")

            cache = Cache()
            result = Engine(FakeSources(), session, cache).lookup(5243125, (965931, 965931))
            elapsed = time.monotonic() - cache.started
            self.assertEqual(result["status"], "incomplete")
            self.assertEqual(result["coverage"]["stop_reason"], "time_budget_exhausted")
            self.assertEqual(result["coverage"]["blocks"], [])
            self.assertEqual(session.run["cache_reads"], 0)
            self.assertEqual(session.run["requests"], [])
            self.assertLessEqual(elapsed, .01 + CLEANUP_TOLERANCE_SECONDS)
            self.measurements.append({"case": "cached_body_parse", "effective_deadline_seconds": .01,
                                      "elapsed_seconds": round(elapsed, 4), "new_public_requests": 0})

    def test_lookup_timeout_after_verified_match_keeps_actual_proof(self):
        block_hash = REFERENCE["proofs"][0]["bitcoin_block_hash"]
        raw = (ROOT / "tests/fixtures/mainnet_965931.bin").read_bytes()
        with Session(self.directory) as session:
            class Expiring(FakeSources):
                network_checks = 0

                def rpc(self, method, params):
                    value = super().rpc(method, params)
                    if method == "eth_chainId":
                        self.network_checks += 1
                        if self.network_checks == 2:
                            session.deadline = time.monotonic() - 1
                    return value

            class Cache:
                def get(self, identity):
                    if identity != block_hash:
                        raise AssertionError("Unexpected block")
                    return raw

                def put(self, *args):
                    raise AssertionError("Only retained bodies are used")

            result = Engine(Expiring(), session, Cache()).lookup(5243125, (965931, 965931))
            self.assertEqual(result["status"], "incomplete")
            self.assertEqual(len(result["proofs"]), 1)
            self.assertEqual(result["proofs"][0]["raw_transaction_hex"], REFERENCE["proofs"][0]["raw_transaction_hex"])
            self.assertEqual(verify_receipt(result)["proofs_checked"], 1)
            self.assertFalse(result["context"]["current_view_confirmed"])
            self.assertIsNone(result["selection"]["current_proof_index"])
            self.assertEqual(session.run["requests"], [])
            result["mode"] = "controlled_lookup_deadline"
            result["test_context"] = {"inputs": "saved mainnet body and controlled source replies",
                                      "live_chain_observation": False, "new_public_requests": 0}
            type(self).evidence = result

    def test_time_limit_cannot_be_disabled_or_expanded_by_configuration(self):
        for value in (0, -1, 601, float("inf"), float("nan"), True):
            with self.subTest(value=value), self.assertRaises(DataError):
                Session(self.directory, max_seconds=value)
        self.assertFalse((self.directory / "session.lock").exists())

    def test_refresh_timeout_preserves_proof_and_original_coverage_without_stale_confidence(self):
        with Session(self.directory) as session:
            class Expiring(FakeSources):
                def rpc(self, method, params):
                    value = super().rpc(method, params)
                    if method == "eth_chainId":
                        session.deadline = time.monotonic() - 1
                    return value

            result = Engine(Expiring(), session, None).refresh(copy.deepcopy(REFERENCE))
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["coverage"], REFERENCE["coverage"])
        self.assertFalse(result["context"]["current_view_confirmed"])
        self.assertIsNone(result["selection"]["current_proof_index"])
        self.assertIsNone(result["proofs"][0]["current_bitcoin_depth_in_provider_view"])
        self.assertNotIn("bitcoin_tip_after", result)
        self.assertEqual(result["proofs"][0]["raw_transaction_hex"], REFERENCE["proofs"][0]["raw_transaction_hex"])
        self.assertEqual(verify_receipt(result)["proofs_checked"], 1)

    def test_timeout_at_finish_cannot_leave_found_or_scoped_absence(self):
        for positive in (True, False):
            with self.subTest(positive=positive), Session(self.directory) as session:
                result = copy.deepcopy(REFERENCE)
                if not positive:
                    result["proofs"] = []
                    result["coverage"]["stop_reason"] = "explicit_range_end"
                result["context"]["current_view_confirmed"] = True
                engine = Engine(FakeSources(), session, None)
                engine.receipt = result
                session.deadline = time.monotonic() - 1
                done = engine.finish()
                self.assertEqual(done["status"], "incomplete")
                self.assertFalse(done["context"]["current_view_confirmed"])
                self.assertIsNone(done["selection"]["current_proof_index"])

    def test_browser_job_receipt_and_cli_summary_keep_timeout_qualified(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from hemi_lookup.cli import readable

        samples = self.directory / "demo_results"
        samples.mkdir()
        original = samples / "mainnet_5243117.json"
        original.write_text(json.dumps(REFERENCE), encoding="utf-8")
        jobs = Jobs(self.directory, mode="discovery")

        class Expiring(FakeSources):
            def __init__(self, session):
                super().__init__()
                self.session = session

            def rpc(self, method, params):
                value = super().rpc(method, params)
                self.session.deadline = time.monotonic() - 1
                return value

        with patch("hemi_lookup.web.PublicSources", side_effect=Expiring):
            job = jobs.start("refresh", {"receipt_id": "example-ordinary"})
            deadline = time.monotonic() + 3
            while jobs.active is not None and time.monotonic() < deadline:
                time.sleep(.01)
        final = jobs.job(job["id"])
        self.assertEqual(final["state"], "complete")
        receipt = jobs.receipt(final["receipt_id"])["receipt"]
        self.assertEqual(receipt["status"], "incomplete")
        self.assertEqual(receipt["termination"]["reason"], "time_budget_exhausted")
        self.assertEqual(verify_receipt(receipt)["proofs_checked"], 1)
        out = StringIO()
        with redirect_stdout(out):
            readable(receipt, original)
        self.assertIn("Result: incomplete", out.getvalue())
        self.assertIn("Historical/unconfirmed", out.getvalue())
        self.assertIn("time_budget_exhausted", out.getvalue())
        self.assertFalse((self.directory / ".hemi_lookup_data/session.lock").exists())


if __name__ == "__main__":
    unittest.main()
