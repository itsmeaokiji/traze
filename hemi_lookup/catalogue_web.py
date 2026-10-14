"""Separate read-only reference consumer. Never calls the live lookup bridge."""

import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .catalogue import Catalogue
from .errors import DataError

EXAMPLE_NUMBERS = (5243118, 4028426)


def make_server(database, root, port=8768):
    root, database = Path(root).resolve(), Path(database).resolve()
    if not 0 <= port <= 65535:
        raise DataError("invalid_port", "Use a valid loopback port")
    with Catalogue(database, readonly=True) as catalogue:
        catalogue.summary()
    assets = {
        "/": (root / "catalogue_demo/index.html", "text/html; charset=utf-8"),
        "/demo.css": (root / "catalogue_demo/demo.css", "text/css; charset=utf-8"),
        "/demo.mjs": (root / "catalogue_demo/demo.mjs", "text/javascript; charset=utf-8"),
        "/assets/hemi.svg": (root / "frontend/src/assets/hemi-mark.svg", "image/svg+xml"),
        "/assets/bitcoin.svg": (root / "frontend/src/assets/bitcoin-mark.svg", "image/svg+xml"),
    }
    for family in ("geist-latin", "geist-mono-latin"):
        matches = list((root / "web/assets").glob(family + "-*.woff2"))
        if len(matches) == 1:
            assets["/assets/" + family + ".woff2"] = (matches[0], "font/woff2")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, data, mime="application/json; charset=utf-8"):
            if not isinstance(data, bytes):
                data = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if self.headers.get("Host") not in hosts:
                self.reply(403, {"error": "Loopback host required"})
                return
            if len(self.path) > 512:
                self.reply(414, {"error": "Request is too long"})
                return
            url = urlsplit(self.path)
            try:
                if url.path in assets and not url.query:
                    path, mime = assets[url.path]
                    self.reply(200, path.read_bytes(), mime)
                    return
                with Catalogue(database, readonly=True) as catalogue:
                    if url.path == "/api/catalogue" and not url.query:
                        examples = [{"number": number, "hash": catalogue.resolve_number(number)} for number in EXAMPLE_NUMBERS]
                        self.reply(200, {"summary": catalogue.summary(), "examples": examples})
                    elif url.path == "/api/evidence":
                        params = parse_qs(url.query, keep_blank_values=True, max_num_fields=2)
                        if set(params) != {"hash"} or len(params["hash"]) != 1:
                            raise DataError("invalid_request", "Use one exact Hemi hash")
                        self.reply(200, catalogue.query(params["hash"][0]))
                    else:
                        self.reply(404, {"error": "Reference resource not found"})
            except DataError as exc:
                self.reply(422, {"error": exc.code, "message": str(exc)})
            except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
                self.reply(500, {"error": "unavailable_catalogue", "message": "Local catalogue data could not be read or verified"})

        def do_POST(self):
            self.reply(405, {"error": "Reference consumer is read-only"})

        do_PUT = do_POST
        do_DELETE = do_POST

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve_catalogue(database, root, port=8768):
    server = make_server(database, root, port)
    print(json.dumps({"url": f"http://127.0.0.1:{server.server_port}",
                      "scope": "Separate retained-data reference consumer; no public-chain requests"}), flush=True)
    try:
        server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
