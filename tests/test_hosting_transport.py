"""Optional actual Waitress transport checks using loopback and retained data."""

import importlib.util
import json
import shutil
import socket
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from hemi_lookup.catalogue import build_catalogue
from hemi_lookup.public import PublicApplication, server_options

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(importlib.util.find_spec("waitress"), "Optional pinned production runtime is not installed")
class WaitressTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base = ROOT / "output/qa-hosting-transport"
        base.mkdir(parents=True, exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(dir=base)
        cls.addClassCleanup(cls.temp.cleanup)
        cls.state = Path(cls.temp.name)
        build_catalogue(ROOT, cls.state / "catalogue.sqlite3", block_directory=ROOT / "tests/fixtures/bitcoin_blocks")

    def setUp(self):
        from waitress.server import create_server
        self.logs = []
        self.app = PublicApplication(self.state, "https://traze.example.com", log=self.logs.append)
        self.server = create_server(self.app, **{**server_options(0), "asyncore_loop_timeout": .02})
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:" + str(self.server.effective_port)
        self.addCleanup(self.stop)

    def stop(self):
        from waitress import wasyncore
        self.server.task_dispatcher.shutdown()
        wasyncore.close_all(map=self.server._map)
        self.thread.join(2)
        self.assertFalse(self.thread.is_alive())

    def request(self, path, headers=None):
        request = Request(self.base + path, headers={"Host": "traze.example.com", **(headers or {})})
        try:
            response = urlopen(request, timeout=8)
        except HTTPError as exc:
            response = exc
        with response:
            raw = response.read()
            return response.status, dict(response.headers), json.loads(raw) if raw and response.headers.get_content_type() == "application/json" else raw

    def test_actual_transport_parallel_proofs_and_private_routes(self):
        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(lambda _: self.request("/api/v1/blocks/5243119"), range(5)))
        self.assertEqual([item[0] for item in results], [200] * 5)
        self.assertEqual([item[2]["verification"]["proofs_checked"] for item in results], [4] * 5)
        self.assertEqual(self.request("/api/receipts/example-ordinary")[0], 404)
        self.assertEqual(self.request("/health/ready")[0], 200)

    def test_transport_header_and_body_caps_precede_application(self):
        port = int(self.server.effective_port)
        for raw, expected in ((b"GET /health/live HTTP/1.1\r\nHost: traze.example.com\r\nX-Large: " + b"a" * 9000 + b"\r\n\r\n", b"431"),
                              (b"POST /api/lookup HTTP/1.1\r\nHost: traze.example.com\r\nContent-Length: 2048\r\n\r\n", b"413")):
            with socket.create_connection(("127.0.0.1", port), timeout=3) as sock:
                sock.sendall(raw)
                response = sock.recv(4096)
            self.assertIn(expected, response.split(b"\r\n")[0])
        self.assertEqual(self.logs, [])


if __name__ == "__main__":
    unittest.main()
