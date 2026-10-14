"""Loopback-only browser bridge. No arbitrary endpoints, paths, or chain writes."""

import argparse
import copy
import hmac
import json
import re
import secrets
import sqlite3
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .cli import load_receipt, write_receipt
from .engine import Engine, covering
from .errors import DataError
from .network import PublicSources
from .proof import verify_receipt
from .storage import BlockCache, Session, SESSION_LIMIT, PRIOR_REPORTS, now
from .inputs import block_input
from .catalogue_lookup import recorded_lookup

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "web"
EXAMPLES = (
    ("example-ordinary", "Ordinary block", "mainnet_5243117.json"),
    ("example-checkpoint", "Checkpoint block", "mainnet_5243125.json"),
    ("example-no-match", "Scoped no-match", "scoped_no_match.json"),
)
SAFE_ID = re.compile(r"(?:example-[a-z-]+|[0-9a-f]{32})\Z")


class WebError(DataError):
    def __init__(self, code, message, status=400):
        super().__init__(code, message)
        self.status = status


class Jobs:
    """One live job at a time; immutable receipts persist across browser restarts."""

    def __init__(self, root=ROOT, state_dir=None):
        self.root = Path(root).resolve()
        self.state_dir = Path(state_dir or self.root / ".hemi_lookup_data").resolve()
        self.receipts = {}
        self.jobs = {}
        self.active = None
        self.guard = threading.Lock()
        for identity, label, filename in EXAMPLES:
            path = self.root / "demo_results" / filename
            if path.is_file():
                self.receipts[identity] = {"path": path, "label": label, "origin": "saved_example"}
        directory = self.state_dir / "receipts"
        if directory.is_dir():
            paths = sorted(directory.glob("web-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:20]
            for path in paths:
                identity = path.stem.removeprefix("web-")
                if re.fullmatch(r"[0-9a-f]{32}", identity) and path.resolve().is_relative_to(directory.resolve()):
                    self.receipts[identity] = {"path": path, "label": "Browser result", "origin": "browser_saved"}

    def receipt_path(self, identity):
        if not isinstance(identity, str) or not SAFE_ID.fullmatch(identity):
            raise WebError("unknown_receipt", "Choose a saved receipt from this session.", 404)
        with self.guard:
            item = self.receipts.get(identity)
        if item is None:
            raise WebError("unknown_receipt", "The saved receipt was not found.", 404)
        return item

    def receipt(self, identity):
        item = self.receipt_path(identity)
        receipt = load_receipt(item["path"])
        verified = verify_receipt(receipt)  # Recompute; never trust stored success flags.
        return {"id": identity, "label": item["label"], "origin": item["origin"],
                "filename": item["path"].name, "receipt": receipt, "verification": verified}

    def resources(self):
        path = self.state_dir / "session.json"
        try:
            if path.exists():
                state = json.loads(path.read_text(encoding="utf-8"))
                total = sum(state["prior_validation_bytes"].values()) + state["downloaded_bytes"]
            else:
                total = sum(json.loads((self.root / name).read_text(encoding="utf-8"))["http_response_bytes"]
                            for name in PRIOR_REPORTS if (self.root / name).exists())
            return {"total_response_bytes": total, "session_limit_bytes": SESSION_LIMIT,
                    "remaining_bytes": max(0, SESSION_LIMIT - total), "available": True}
        except (OSError, ValueError, KeyError, TypeError):
            return {"available": False, "session_limit_bytes": SESSION_LIMIT}

    def state(self):
        with self.guard:
            items = list(self.receipts.items())
            active = copy.deepcopy(self.jobs.get(self.active))
        summaries = []
        for identity, item in items:
            try:
                r = load_receipt(item["path"])
                summaries.append({"id": identity, "label": item["label"], "origin": item["origin"],
                                  "block_number": r["query"]["block_number"], "status": r["status"],
                                  "requested_block_hash": r["query"].get("requested_block_hash"),
                                  "completed_at": r.get("completed_at"), "mode": r.get("mode")})
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return {"receipts": summaries[-24:], "active_job": active, "resources": self.resources(),
                "limits": {"response_mb_per_lookup": 40, "bitcoin_body_attempts": 20,
                           "ancestry_links": 256, "session_response_mb": 100}}

    def job(self, identity):
        with self.guard:
            job = copy.deepcopy(self.jobs.get(identity))
        if job is None:
            raise WebError("unknown_job", "This browser job was not found.", 404)
        return job

    def start(self, operation, payload):
        if not isinstance(payload, dict):
            raise WebError("invalid_input", "Enter a Hemi block number or full block hash.")
        if operation == "lookup":
            force_discovery = payload.get("new_lookup", False)
            if not isinstance(force_discovery, bool):
                raise WebError("invalid_input", "New lookup must be a boolean choice.")
            if "block" in payload and "block_number" in payload:
                raise WebError("invalid_block", "Provide one Hemi block number or hash, not conflicting input fields.")
            if "block" in payload:
                target = block_input(payload["block"])
            else:
                target = payload.get("block_number")
                if isinstance(target, bool) or not isinstance(target, int):
                    raise WebError("invalid_block", "Use a positive whole Hemi block number or the block field for a full hash.")
                covering(target)
            number = target if isinstance(target, int) else None
            scope = payload.get("bitcoin_range")
            if scope is not None:
                if (not isinstance(scope, list) or len(scope) != 2
                        or any(isinstance(n, bool) or not isinstance(n, int) for n in scope)
                        or scope[0] < 0 or scope[1] < scope[0] or scope[1] - scope[0] + 1 > 200):
                    raise WebError("invalid_range", "Use a Bitcoin range of at most 200 heights, in ascending order.")
                scope = tuple(scope)
            value = (target, scope)
        elif operation == "refresh":
            force_discovery = False
            value = self.receipt(payload.get("receipt_id"))["receipt"]
            number = value["query"]["block_number"]
            target = value["query"].get("requested_block_hash", number)
            if "header" not in value["query"] or "covering_checkpoint" not in value["query"]:
                raise WebError("refresh_unavailable", "This incomplete receipt needs a new lookup.")
        else:
            raise WebError("unknown_operation", "Unknown operation.", 404)
        with self.guard:
            if self.active is not None or (self.state_dir / "session.lock").exists():
                raise WebError("busy", "A lookup is already active. Wait for it to finish; the shared ledger is preserved.", 409)
            # Bound in-memory jobs. Receipts remain on disk and downloadable.
            if len(self.jobs) >= 24:
                self.jobs.pop(next(iter(self.jobs)))
            identity = uuid.uuid4().hex
            self.jobs[identity] = {"id": identity, "operation": operation, "block_number": number,
                                   "block_input": target,
                                   "force_discovery": force_discovery,
                                   "state": "running", "started_at": now(), "events": [],
                                   "receipt_id": None, "error": None}
            self.active = identity
        threading.Thread(target=self.run, args=(identity, operation, value), daemon=True,
                         name="hemi-browser-lookup").start()
        return self.job(identity)

    def event(self, identity, message, session=None):
        with self.guard:
            job = self.jobs[identity]
            job["events"].append({"at": now(), "message": str(message)})
            job["events"] = job["events"][-80:]
            if session:
                job["resources"] = session.metrics()

    def run(self, identity, operation, value):
        try:
            receipt = None
            if operation == "lookup" and value[1] is None and not self.jobs[identity]["force_discovery"]:
                self.event(identity, "Checking recorded publication evidence…")
                try:
                    receipt = recorded_lookup(self.state_dir / "catalogue.sqlite3", value[0])
                except (DataError, OSError, ValueError, KeyError, TypeError, sqlite3.Error):
                    self.event(identity, "Recorded evidence is unavailable or inconsistent; starting normal discovery…")
            if receipt is None:
                receipt = self.public_operation(identity, operation, value)
            verify_receipt(receipt)
            path = self.state_dir / "receipts" / f"web-{identity}.json"
            write_receipt(path, receipt)
            archived = receipt.get("mode") == "offline_catalogue"
            with self.guard:
                self.receipts[identity] = {"path": path, "label": "Recorded evidence" if archived else "Live lookup" if operation == "lookup" else "Source refresh",
                                          "origin": "catalogue_lookup" if archived else "browser_lookup" if operation == "lookup" else "browser_refresh"}
                self.jobs[identity].update({"state": "complete", "receipt_id": identity, "completed_at": now(),
                                           "block_number": receipt["query"]["block_number"],
                                           "resources": receipt["resources"]})
        except Exception as exc:
            code = getattr(exc, "code", "local_error")
            message = str(exc) if isinstance(exc, (DataError, OSError, ValueError)) else "The local operation could not finish. See the terminal for details."
            print(f"Browser job {identity}: {type(exc).__name__}: {exc}", flush=True)
            with self.guard:
                self.jobs[identity].update({"state": "error", "error": {"code": code, "message": message}, "completed_at": now()})
        finally:
            with self.guard:
                if self.active == identity:
                    self.active = None

    def public_operation(self, identity, operation, value):
        self.event(identity, "Reading public sources…" if operation == "lookup" else "Refreshing source views; proof data is preserved…")
        with Session(self.state_dir, self.root) as session:
            engine = Engine(PublicSources(session), session, BlockCache(self.state_dir / "blocks"),
                            progress=lambda message: self.event(identity, message, session))
            return engine.lookup(*value) if operation == "lookup" else engine.refresh(value)


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port=8765, jobs=None):
        self.jobs = jobs or Jobs()
        self.token = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", port), Handler)
        self.allowed_hosts = {f"127.0.0.1:{self.server_port}", f"localhost:{self.server_port}"}


class Handler(BaseHTTPRequestHandler):
    server_version = "LocalPrototype/0.1"
    sys_version = ""

    def log_message(self, *args):
        pass

    def send(self, status, raw, content_type="application/json; charset=utf-8", filename=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def json(self, status, value):
        self.send(status, json.dumps(value).encode("utf-8"))

    def check_host(self):
        if self.headers.get("Host") not in self.server.allowed_hosts:
            raise WebError("invalid_host", "Open the prototype at its local address.", 403)

    def do_GET(self):
        try:
            self.check_host()
            path = urlsplit(self.path).path
            if path == "/":
                self.send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
                return
            asset = re.fullmatch(r"/assets/([A-Za-z0-9][A-Za-z0-9_-]*\.(js|css|woff2))", path)
            if asset:
                # Serve only UI JS/CSS/font assets beneath the fixed directory.
                # Resolving the final path also rejects symlinks outside that tree.
                target = (STATIC / "assets" / asset[1]).resolve()
                if not target.is_relative_to(STATIC.resolve() / "assets") or not target.is_file():
                    raise WebError("not_found", "This local asset was not found.", 404)
                mime = {"js": "text/javascript; charset=utf-8", "css": "text/css; charset=utf-8", "woff2": "font/woff2"}[asset[2]]
                self.send(200, target.read_bytes(), mime)
                return
            if path == "/favicon.ico":
                self.send(204, b"", "image/x-icon")
                return
            if path == "/api/state":
                self.json(200, {**self.server.jobs.state(), "csrf_token": self.server.token})
                return
            match = re.fullmatch(r"/api/receipts/([a-z0-9-]+)(/download)?", path)
            if match:
                identity, download = match.groups()
                wrapper = self.server.jobs.receipt(identity)
                if download:
                    query = wrapper["receipt"]["query"]
                    target = f"hash-{query['requested_block_hash'][2:]}" if query.get("input_type") == "hash" else f"block-{query['block_number']}"
                    self.send(200, json.dumps(wrapper["receipt"], indent=2).encode("utf-8"),
                              filename=f"hemi-{target}-{identity}.json")
                else:
                    self.json(200, wrapper)
                return
            match = re.fullmatch(r"/api/jobs/([0-9a-f]{32})", path)
            if match:
                self.json(200, self.server.jobs.job(match[1]))
                return
            raise WebError("not_found", "This local page was not found.", 404)
        except (DataError, OSError, ValueError, KeyError, TypeError) as exc:
            self.json(getattr(exc, "status", 422), {"error": {"code": getattr(exc, "code", "invalid_data"), "message": str(exc)}})

    def do_POST(self):
        try:
            self.check_host()
            origin = self.headers.get("Origin")
            token = self.headers.get("X-Local-Token", "")
            if origin != "http://" + self.headers.get("Host", "") or not hmac.compare_digest(token, self.server.token):
                raise WebError("invalid_origin", "Start the operation from the local prototype page.", 403)
            if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                raise WebError("invalid_input", "Expected a JSON operation.")
            length = self.headers.get("Content-Length", "")
            if not length.isdigit() or not 0 < int(length) <= 4096 or self.headers.get("Transfer-Encoding"):
                raise WebError("input_too_large", "Operation input must be at most 4 KB.", 413)
            self.connection.settimeout(5)
            payload = json.loads(self.rfile.read(int(length)))
            path = urlsplit(self.path).path
            if path not in ("/api/lookup", "/api/refresh"):
                raise WebError("not_found", "Unknown local operation.", 404)
            self.json(202, self.server.jobs.start(path.rsplit("/", 1)[1], payload))
        except (DataError, OSError, ValueError, KeyError, TypeError) as exc:
            self.json(getattr(exc, "status", 400), {"error": {"code": getattr(exc, "code", "invalid_data"), "message": str(exc)}})


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local browser prototype; public read-only lookup with the existing shared budget.")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("Use a local port between 1024 and 65535.")
    try:
        server = LocalServer(args.port)
        print(f"Local browser prototype: http://127.0.0.1:{server.server_port}", flush=True)
        print("Shared CLI ledger and cache; public-source reads begin only after Lookup or Refresh.", flush=True)
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 0
    except OSError as exc:
        print(f"Cannot start local prototype: {exc}", flush=True)
        return 2
    finally:
        if "server" in locals():
            server.server_close()
    return 0
