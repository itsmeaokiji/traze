"""Read-only WSGI application for a loopback production server behind HTTPS.

No dependency on the local browser bridge, Jobs, source clients or Session.
"""

import argparse
import json
import re
import threading
import time
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from .explorer import Explorer, ReadError, encode_response
from .operations import public_snapshot
from .assurance import POLICY_ID, SNAPSHOT_POLICY

ROOT = Path(__file__).resolve().parent.parent
CSP = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"


def origin_setting(value, local_review=False):
    parsed = urlsplit(value)
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("Invalid origin port")
    if (parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment or not re.fullmatch(r"[A-Za-z0-9.:-]+", parsed.netloc)
            or parsed.scheme != "https" and not (local_review and parsed.hostname in ("127.0.0.1", "localhost"))):
        raise ValueError("Use one exact HTTPS origin, or an explicit loopback HTTP review origin, without a trailing slash")
    return parsed.scheme + "://" + parsed.netloc.lower()


class PublicApplication:
    def __init__(self, state_dir, origin, *, static_dir=None, monitor_file=None, local_review=False, log=None,
                 assurance_policy=POLICY_ID, accepted_context_sha256=None):
        self.origin = origin_setting(origin, local_review)
        self.host = urlsplit(self.origin).netloc
        self.explorer = Explorer(Path(state_dir).resolve(), assurance_policy=assurance_policy, accepted_context_sha256=accepted_context_sha256)
        self.static = Path(static_dir or ROOT / "web").resolve(strict=True)
        self.monitor_file = Path(monitor_file) if monitor_file else None
        self.log = log or (lambda value: print(json.dumps(value, separators=(",", ":")), flush=True))
        self.gate = threading.Lock()
        self.tokens, self.last_token = 40., time.monotonic()

    def rate_admit(self):
        with self.gate:
            current = time.monotonic()
            self.tokens = min(40., self.tokens + max(0., current - self.last_token) * 20.)
            self.last_token = current
            if self.tokens < 1:
                raise ReadError("read_rate", "Read request rate is busy. Retry with backoff.", 429)
            self.tokens -= 1

    def status(self):
        try:
            if self.monitor_file is None or self.monitor_file.stat().st_size > 65536:
                return public_snapshot({})
            value = json.loads(self.monitor_file.read_text(encoding="utf-8"))
            return public_snapshot(value if isinstance(value, dict) else {})
        except (OSError, ValueError, TypeError, AttributeError):
            return public_snapshot({})

    def dispatch(self, path, params):
        packet = re.fullmatch(r'/api/v1/blocks/(0x[0-9a-f]{64})/first-publication/([0-9a-f]{64})/download', path)
        if packet:
            if params:
                raise ReadError('invalid_filter', 'Evidence downloads take no query parameters.')
            with self.explorer.admit():
                raw, _ = self.explorer.download(*packet.groups())
            return 200, raw, 'application/zip'
        if path == "/health/live":
            return 200, encode_response({"status": "alive"}), "application/json"
        if path == "/health/ready":
            with self.explorer.admit():
                self.explorer.read("index", {})
            return 200, encode_response({"status": "ready", "meaning": "Index readable; not a synchronization or freshness guarantee."}), "application/json"
        if path == "/api/v1/status":
            if params:
                raise ReadError("invalid_filter", "Status takes no query parameters.")
            return 200, encode_response(self.status()), "application/json"
        if path == "/api/state":
            if params:
                raise ReadError("invalid_filter", "State takes no query parameters.")
            snapshot = self.status()
            return 200, encode_response({"serving_mode": "indexed-only", "deployment": "hosted-candidate",
                "receipts": [], "active_job": None, "csrf_token": "", "collection": snapshot["collection"],
                "service_status": snapshot, "resources": {"available": False, "session_limit_bytes": 0},
                "limits": {"response_mb_per_lookup": 0, "bitcoin_body_attempts": 0, "ancestry_links": 256, "session_response_mb": 0}}), "application/json"
        if path == "/api/v1" or path.startswith("/api/v1/"):
            with self.explorer.admit():
                value = self.explorer.read(path[len("/api/v1"):].lstrip("/"), params)
                raw = encode_response(value)
            return 200, raw, "application/json"
        if path in ("/", "/browse", "/api-docs") or re.fullmatch(r"/records/(?:hemi/0x[0-9a-f]{64}|publications/[0-9a-f]{64})", path):
            target, mime = self.static / "index.html", "text/html; charset=utf-8"
        elif path == "/THIRD_PARTY_LICENSES.txt":
            target, mime = self.static / "THIRD_PARTY_LICENSES.txt", "text/plain; charset=utf-8"
        elif asset := re.fullmatch(r"/assets/([A-Za-z0-9][A-Za-z0-9_-]*\.(js|css|woff2))", path):
            target = self.static / "assets" / asset[1]
            mime = {"js": "text/javascript; charset=utf-8", "css": "text/css; charset=utf-8", "woff2": "font/woff2"}[asset[2]]
        elif path == "/favicon.ico":
            return 204, b"", "image/x-icon"
        else:
            raise ReadError("not_found", "This public route is unavailable.", 404)
        target = target.resolve()
        if not target.is_relative_to(self.static) or not target.is_file() or target.stat().st_size > 2_000_000:
            raise ReadError("not_found", "This website asset is unavailable.", 404)
        return 200, target.read_bytes(), mime

    def __call__(self, environ, start_response):
        started, status, category = time.monotonic(), 500, "rejected"
        extra = []
        try:
            method = environ.get("REQUEST_METHOD", "")
            if method not in ("GET", "HEAD"):
                extra.append(("Allow", "GET, HEAD"))
                raise ReadError("method_not_allowed", "Only indexed GET and HEAD reads are supported.", 405)
            if environ.get("HTTP_HOST", "").lower() != self.host:
                raise ReadError("invalid_host", "Use the configured website origin.", 403)
            if environ.get("HTTP_ORIGIN") not in (None, self.origin):
                raise ReadError("invalid_origin", "Use the configured website origin.", 403)
            if environ.get("CONTENT_LENGTH", "") not in ("", "0") or environ.get("HTTP_TRANSFER_ENCODING"):
                raise ReadError("request_body_disabled", "Read requests must not contain a body.", 400)
            path, query = environ.get("PATH_INFO", ""), environ.get("QUERY_STRING", "")
            if len(path.encode("utf-8")) + len(query.encode("utf-8")) + 1 > 2048:
                raise ReadError("input_too_large", "Request URL exceeds 2 KB.", 414)
            pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True, max_num_fields=8) if query else []
            if len({key for key, _ in pairs}) != len(pairs):
                raise ReadError("invalid_filter", "Do not repeat query parameters.")
            if path.startswith("/api/") or path.startswith("/health/"):
                self.rate_admit()
                category = "api" if path.startswith("/api/v1") else "health" if path.startswith("/health/") else "state"
            else:
                category = "page_or_asset"
            status, raw, mime = self.dispatch(path, dict(pairs))
        except ReadError as exc:
            status, mime = exc.status, "application/json"
            raw = encode_response({"error": {"code": exc.code, "message": str(exc)}})
        except (ValueError, UnicodeError):
            status, mime = 400, "application/json"
            raw = encode_response({"error": {"code": "invalid_filter", "message": "Invalid request encoding or query parameters."}})
        except Exception:
            status, mime = 503, "application/json"
            raw = encode_response({"error": {"code": "service_unavailable", "message": "The read service is unavailable. Retry later."}})
        cache = "public, max-age=31536000, immutable" if status == 200 and environ.get("PATH_INFO", "").startswith("/assets/") else "no-store"
        headers = [("Content-Type", mime), ("Content-Length", str(len(raw))), ("Cache-Control", cache),
                   ("Content-Security-Policy", CSP), ("X-Content-Type-Options", "nosniff"),
                   ("Referrer-Policy", "no-referrer"), ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"), *extra]
        if status == 200 and mime == 'application/zip':
            headers.append(('Content-Disposition', 'attachment; filename="traze-first-publication-evidence.zip"'))
        if status in (429, 503):
            headers.append(("Retry-After", "1"))
        self.log({"event": "public_read", "category": category, "status": status,
                  "response_bytes": len(raw), "processing_seconds": round(time.monotonic() - started, 6)})
        start_response(str(status) + " " + HTTPStatus(status).phrase, headers)
        return [b"" if environ.get("REQUEST_METHOD") == "HEAD" else raw]


def server_options(port):
    return {"host": "127.0.0.1", "port": port, "threads": 6, "connection_limit": 32,
            "backlog": 16, "channel_timeout": 15, "cleanup_interval": 5,
            "max_request_header_size": 8192, "max_request_body_size": 1024,
            "outbuf_overflow": 16_000_000, "outbuf_high_watermark": 16_000_000,
            "clear_untrusted_proxy_headers": True, "trusted_proxy": None,
            "channel_request_lookahead": 0, "ident": "Traze"}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Production indexed read adapter; requires an HTTPS reverse proxy")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--monitor-file", type=Path)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--local-review", action="store_true")
    parser.add_argument("--first-height-policy", choices=(POLICY_ID, SNAPSHOT_POLICY, 'evidence-only'), default=POLICY_ID)
    parser.add_argument('--accept-context', help='Operator-selected canonical snapshot context checksum')
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("Use a valid TCP port")
    app = PublicApplication(args.state_dir, args.origin, monitor_file=args.monitor_file, local_review=args.local_review,
                            assurance_policy=None if args.first_height_policy == 'evidence-only' else args.first_height_policy,
                            accepted_context_sha256=args.accept_context)
    try:
        from waitress import serve
    except ImportError:
        parser.error("Install the optional pinned deploy/requirements.txt dependency for this adapter")
    serve(app, **server_options(args.port))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
