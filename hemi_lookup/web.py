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
from urllib.parse import parse_qsl, urlsplit

from .cli import load_receipt, write_receipt
from .engine import Engine, covering
from .errors import DataError
from .network import PublicSources
from .proof import verify_receipt
from .storage import BlockCache, Session, SESSION_LIMIT, PRIOR_REPORTS, now
from .inputs import block_input
from .catalogue_lookup import recorded_lookup
from .accounting import accounting_directory, accounting_state
from .collection_store import collection_status
from .explorer import Explorer, ReadError, ReadBudget, encode_response
from .assurance import EvidenceStore, POLICY_ID, SNAPSHOT_POLICY
from .evidence_builder import EvidenceBuilder
from .finality import SOURCE_POLICY as FINALITY_SOURCE_POLICY

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "web"
EXAMPLES = (
    ("example-ordinary", "Ordinary block", "mainnet_5243117.json"),
    ("example-checkpoint", "Keystone block", "mainnet_5243125.json"),
    ("example-no-match", "Scoped no-match", "scoped_no_match.json"),
)
SAFE_ID = re.compile(r"(?:example-[a-z-]+|[0-9a-f]{32})\Z")


class WebError(DataError):
    def __init__(self, code, message, status=400):
        super().__init__(code, message)
        self.status = status


class Jobs:
    """One live job at a time; immutable receipts persist across browser restarts."""

    def __init__(self, root=ROOT, state_dir=None, *, mode="indexed-only", assurance_policy=POLICY_ID, accepted_context_sha256=None,
                 finality_source_policy=None, accepted_finality_source_context_sha256=None):
        if mode not in ("indexed-only", "discovery"):
            raise ValueError("Use indexed-only or discovery serving mode")
        self.mode = mode
        self.root = Path(root).resolve()
        self.state_dir = Path(state_dir or self.root / ".hemi_lookup_data").resolve()
        self.evidence = EvidenceStore(self.state_dir, policy=assurance_policy, accepted_context_sha256=accepted_context_sha256,
            finality_source_policy=finality_source_policy,
            accepted_finality_source_context_sha256=accepted_finality_source_context_sha256)
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

    def receipt(self, identity, *, include_finality=True):
        item = self.receipt_path(identity)
        receipt = load_receipt(item["path"])
        verified = verify_receipt(receipt)  # Recompute; never trust stored success flags.
        return self.evidence.attach({"id": identity, "label": item["label"], "origin": item["origin"],
                "filename": item["path"].name, "receipt": receipt, "verification": verified}, check=ReadBudget(), include_finality=include_finality)

    def resources(self):
        try:
            state = accounting_state(self.state_dir, prior_root=self.root)
            total = sum(state['prior_validation_bytes'].values()) + state['downloaded_bytes']
            return {"total_response_bytes": total, "session_limit_bytes": SESSION_LIMIT,
                    "remaining_bytes": max(0, SESSION_LIMIT - total), "available": True}
        except (DataError, OSError, ValueError, KeyError, TypeError):
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
        try:
            collection = collection_status(self.state_dir)
        except (DataError, OSError, ValueError, sqlite3.Error):
            collection = {"state": "unavailable"}
        return {"receipts": summaries[-24:], "active_job": active, "resources": self.resources(), "collection": collection,
                "serving_mode": self.mode,
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
        if self.mode == "indexed-only" and (operation == "refresh" or payload.get("new_lookup")
                                           or payload.get("bitcoin_range") is not None):
            raise WebError("source_operations_disabled", "This website serves indexed evidence. Live discovery and source rechecks are disabled.", 403)
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
            recorded_allowed = operation == "lookup" and scope is None and not force_discovery
            if self.active is not None or (self.mode == "discovery" and (accounting_directory(self.state_dir) / "session.lock").exists()
                    and (not recorded_allowed or self.recorded(target) is None)):
                raise WebError("busy", "A public-source operation is active. Indexed searches and saved results remain available.", 409)
            # Bound in-memory jobs. Receipts remain on disk and downloadable.
            if len(self.jobs) >= 24:
                self.jobs.pop(next(iter(self.jobs)))
            identity = uuid.uuid4().hex
            self.jobs[identity] = {"id": identity, "operation": operation, "block_number": number,
                                   "block_input": target,
                                   "force_discovery": force_discovery,
                                   "phase": "source_refresh" if operation == "refresh" else "public_discovery" if force_discovery or scope is not None else "recorded_lookup",
                                   "state": "running", "started_at": now(), "events": [],
                                   "receipt_id": None, "error": None}
            self.active = identity
        threading.Thread(target=self.run, args=(identity, operation, value), daemon=True,
                         name="hemi-browser-lookup").start()
        return self.job(identity)

    def event(self, identity, message, session=None):
        with self.guard:
            job = self.jobs[identity]
            if message.startswith(("Preparing first-publication evidence", "Checking first-publication evidence")):
                job["phase"] = "first_publication_check"
            job["events"].append({"at": now(), "message": str(message)})
            job["events"] = job["events"][-80:]
            if session:
                job["resources"] = session.metrics()

    def recorded(self, target):
        for database in ("service.sqlite3", "catalogue.sqlite3"):
            try:
                receipt = recorded_lookup(self.state_dir / database, target)
            except (DataError, OSError, ValueError, KeyError, TypeError, sqlite3.Error):
                receipt = None
            if receipt is not None:
                return receipt
        # Reuse a completed browser result even when collection has not imported
        # it into SQLite. Number resolution must remain unambiguous and old
        # observation times must never become a new live-source confirmation.
        matches = []
        for item in list(self.receipts.values()):
            try:
                receipt = load_receipt(item['path'])
                query = receipt['query']
                if not receipt.get('proofs') or (query.get('block_number') != target if isinstance(target, int)
                                               else query.get('block_hash') != target):
                    continue
                if not self.evidence.has_packet(receipt):
                    continue
                verify_receipt(receipt)
                matches.append(receipt)
            except (DataError, OSError, ValueError, KeyError, TypeError):
                continue
        if matches and len({r['query']['block_hash'] for r in matches}) == 1:
            receipt = copy.deepcopy(matches[0])
            receipt['mode'] = 'offline_catalogue'
            query = receipt['query']
            query['input_type'] = 'number' if isinstance(target, int) else 'hash'
            query.pop('requested_block_hash', None)
            if isinstance(target, str):
                query['requested_block_hash'] = target
            checked_at = receipt.get('context', {}).get('checked_at')
            receipt['catalogue'] = {'historical_source_times': [checked_at] if checked_at else [],
                                    'number_resolved_from_retained_view': isinstance(target, int)}
            receipt['context'] = {**receipt.get('context', {}), 'current_view_confirmed': False, 'requested_block_identity': {
                'status': 'not_rechecked', 'current_hash': None}}
            receipt['resources'] = {**receipt.get('resources', {}), 'response_bytes_this_run': 0,
                                    'bitcoin_body_attempts': 0, 'cache_body_reads': 0, 'http_requests': 0}
            return receipt
        return None

    def run(self, identity, operation, value):
        try:
            receipt = None
            if operation == "lookup" and value[1] is None and not self.jobs[identity]["force_discovery"]:
                self.event(identity, "Checking recorded publication evidence…")
                try:
                    receipt = self.recorded(value[0])
                except (DataError, OSError, ValueError, KeyError, TypeError, sqlite3.Error):
                    self.event(identity, "Recorded evidence is unavailable or inconsistent; starting normal discovery…")
            if receipt is None:
                if self.mode == "indexed-only":
                    raise WebError("indexed_evidence_unavailable", "No usable indexed evidence is available for this input. This does not establish publication absence.", 404)
                with self.guard:
                    self.jobs[identity]["phase"] = "source_refresh" if operation == "refresh" else "public_discovery"
                receipt = self.public_operation(identity, operation, value)
            elif self.mode == "discovery" and operation == "lookup":
                assessment = self.evidence.assess(receipt, check=ReadBudget())
                if assessment['status'] not in ('verified_conditional_first_height', 'verified_history_evidence', 'verified_snapshot_evidence', 'invalid', 'source_conflict'):
                    receipt = self.public_operation(identity, operation, value, retained=receipt)
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

    def public_operation(self, identity, operation, value, retained=None):
        if self.mode != "discovery":
            raise WebError("source_operations_disabled", "Live source operations are disabled in indexed-only mode.", 403)
        ledger_directory = accounting_directory(self.state_dir)
        if (ledger_directory / "session.lock").exists():
            raise WebError("busy", "Public-source collection is active and this input has no usable indexed evidence. Open a saved result or retry after collection finishes.", 409)
        self.event(identity, "Reading public sources…" if operation == "lookup" else "Refreshing source views; proof data is preserved…")
        if ledger_directory != self.state_dir and not (ledger_directory / 'session.json').is_file():
            raise DataError('missing_accounting', 'The bound lifetime ledger must not be recreated')
        with Session(ledger_directory, self.root) as session:
            engine = Engine(PublicSources(session), session, BlockCache(self.state_dir / "blocks"),
                            progress=lambda message: self.event(identity, message, session))
            receipt = engine.refresh(retained) if retained is not None else engine.lookup(*value) if operation == "lookup" else engine.refresh(value)
            if retained is not None:
                receipt['mode'] = 'live_evidence_extension'
            if operation == "lookup" and receipt.get('proofs'):
                existing = self.evidence.assess(receipt, check=session.check_deadline)
                if existing['status'] not in ('verified_conditional_first_height', 'verified_history_evidence', 'verified_snapshot_evidence'):
                    receipt['first_publication_attempt'] = EvidenceBuilder(engine, self.evidence).prepare(receipt)
                return engine.finish()
            return receipt


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16

    def __init__(self, port=8765, jobs=None):
        self.jobs = jobs or Jobs()
        self.explorer = Explorer(self.jobs.state_dir, assurance_policy=self.jobs.evidence.policy,
            accepted_context_sha256=self.jobs.evidence.accepted_context_sha256,
            finality_source_policy=self.jobs.evidence.finality.source_policy,
            accepted_finality_source_context_sha256=self.jobs.evidence.finality.accepted_source_context_sha256)
        self.connections = threading.BoundedSemaphore(16)
        self.token = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", port), Handler)
        self.allowed_hosts = {f"127.0.0.1:{self.server_port}", f"localhost:{self.server_port}"}

    def process_request(self, request, client_address):
        if not self.connections.acquire(blocking=False):
            try:
                request.settimeout(.1)
                request.sendall(b"HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\nRetry-After: 1\r\nConnection: close\r\n\r\n")
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.connections.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.connections.release()


class Handler(BaseHTTPRequestHandler):
    server_version = "LocalPrototype/0.1"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *args):
        pass

    def send(self, status, raw, content_type="application/json; charset=utf-8", filename=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        if status in (429, 503):
            self.send_header("Retry-After", "1")
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
            if len(self.path) > 2048:
                raise WebError("input_too_large", "Request URL exceeds 2 KB.", 414)
            parsed = urlsplit(self.path)
            path = parsed.path
            indexed_download = re.fullmatch(r"/api/v1/blocks/(0x[0-9a-f]{64})/first-publication/([0-9a-f]{64})/download", path)
            if indexed_download:
                if parsed.query:
                    raise ReadError('invalid_filter', 'Evidence downloads take no query parameters.')
                with self.server.explorer.admit():
                    raw, assessment = self.server.explorer.download(*indexed_download.groups())
                self.send(200, raw, 'application/zip', filename=f"traze-first-publication-{assessment['query']['number']}-{indexed_download[2][:12]}.zip")
                return
            if path == "/api/v1" or path.startswith("/api/v1/"):
                try:
                    pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=8) if parsed.query else []
                except ValueError:
                    raise ReadError("invalid_filter", "Use at most eight query parameters with explicit values.") from None
                if len({key for key, _ in pairs}) != len(pairs):
                    raise ReadError("invalid_filter", "Do not repeat query parameters.")
                route = path[len("/api/v1"):].lstrip("/")
                with self.server.explorer.admit():
                    value = self.server.explorer.read(route, dict(pairs))
                    self.send(200, encode_response(value))
                return
            if path in ("/", "/browse", "/api-docs") or re.fullmatch(r"/records/(?:hemi/0x[0-9a-f]{64}|publications/[0-9a-f]{64})", path):
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
            packet_download = re.fullmatch(r"/api/receipts/([a-z0-9-]+)/first-publication/([0-9a-f]{64})/download", path)
            if packet_download:
                identity, digest = packet_download.groups()
                receipt = load_receipt(self.server.jobs.receipt_path(identity)['path'])
                raw, assessment = self.server.jobs.evidence.export(receipt, digest, check=ReadBudget())
                self.send(200, raw, 'application/zip', filename=f"traze-first-publication-{assessment['query']['number']}-{digest[:12]}.zip")
                return
            match = re.fullmatch(r"/api/receipts/([a-z0-9-]+)(/download)?", path)
            if match:
                identity, download = match.groups()
                wrapper = self.server.jobs.receipt(identity, include_finality=not download)
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
            if self.server.jobs.mode == "indexed-only":
                if path == "/api/refresh" or isinstance(payload, dict) and (payload.get("new_lookup") or payload.get("bitcoin_range") is not None):
                    raise WebError("source_operations_disabled", "This website serves indexed evidence. Live discovery and source rechecks are disabled.", 403)
                raise WebError("read_api_required", "Use the versioned read API for indexed searches. No browser job or receipt file was created.", 410)
            self.json(202, self.server.jobs.start(path.rsplit("/", 1)[1], payload))
        except (DataError, OSError, ValueError, KeyError, TypeError) as exc:
            self.json(getattr(exc, "status", 400), {"error": {"code": getattr(exc, "code", "invalid_data"), "message": str(exc)}})


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local browser prototype; public read-only lookup with the existing shared budget.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--mode", choices=("indexed-only", "discovery"), default="indexed-only",
                        help="Serve recorded evidence only (default), or explicitly allow bounded developer source operations")
    parser.add_argument("--state-dir", type=Path, help="Server-side evidence and accounting directory")
    parser.add_argument("--first-height-policy", choices=(POLICY_ID, SNAPSHOT_POLICY, 'evidence-only'), default=POLICY_ID,
                        help="Operator-selected conditional policy, or evidence-only without accepting external assumptions")
    parser.add_argument('--accept-context', help='Operator-selected canonical snapshot context checksum; only with the snapshot policy')
    parser.add_argument('--finality-source-policy', choices=('evidence-only', FINALITY_SOURCE_POLICY), default='evidence-only',
                        help='Separate recorded-source assumptions for conditional finality calculations')
    parser.add_argument('--accept-finality-source-context', help='Operator-selected exact finality source-context checksum')
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("Use a local port between 1024 and 65535.")
    try:
        server = LocalServer(args.port, Jobs(state_dir=args.state_dir, mode=args.mode,
                            assurance_policy=None if args.first_height_policy == 'evidence-only' else args.first_height_policy,
                            accepted_context_sha256=args.accept_context,
                            finality_source_policy=None if args.finality_source_policy == 'evidence-only' else args.finality_source_policy,
                            accepted_finality_source_context_sha256=args.accept_finality_source_context))
        print(f"Local browser prototype: http://127.0.0.1:{server.server_port}", flush=True)
        print("Indexed-only mode: visitor operations never acquire public-source data." if args.mode == "indexed-only"
              else "Discovery mode: explicit developer lookups/rechecks use the shared source allowance.", flush=True)
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
