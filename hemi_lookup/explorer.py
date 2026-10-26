"""Versioned, source-free reads over one retained SQLite snapshot per request.

No transport, accounting session, cache-body reader or receipt writer is used.
Record URLs identify records; responses describe the index view read at that time.
"""

import base64
import json
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .bitcoin import display_hash, sha256d
from .catalogue import Catalogue, digest, timestamp
from .catalogue_lookup import recorded_lookup
from .errors import DataError
from .inputs import block_input
from .proof import verify_receipt
from .assurance import EvidenceStore, POLICY_ID

HEX_ID = re.compile(r"[0-9a-f]{64}\Z")
API_VERSION = "v1"
MAX_READERS = 5
MAX_RESPONSE_BYTES = 8_000_000
MAX_INPUT_BYTES = 32_000_000
MAX_SQL_STEPS = 2_000_000
READ_SECONDS = 5
PAGE_SIZE = 20
MAX_PAGE_SIZE = 50


class ReadError(DataError):
    def __init__(self, code, message, status=400):
        super().__init__(code, message)
        self.status = status


class ReadBudget:
    """Bound SQL work and decoded input, and check time during proof parsing."""

    def __init__(self):
        self.deadline = time.monotonic() + READ_SECONDS
        self.bytes = 0
        self.steps = 0
        self.error = None

    def __call__(self, amount=0):
        self.bytes += amount
        if (time.monotonic() >= self.deadline or self.bytes > MAX_INPUT_BYTES
                or self.steps > MAX_SQL_STEPS):
            self.error = ReadError("read_capacity", "This indexed view exceeds the read budget. Narrow the request or retry later.", 503)
            raise self.error

    def progress(self):
        self.steps += 1000
        try:
            self()
            return 0
        except ReadError:
            return 1

    def trace(self, statement):
        # Reserve one progress quantum for each statement, including those too
        # short to invoke SQLite's progress callback. Actual callbacks add work.
        self.steps += 1000


def integer(value, name, *, zero=False):
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,10}", value):
        raise ReadError("invalid_filter", f"Use a whole number for {name}.")
    number = int(value)
    if not (0 if zero else 1) <= number <= 0xffffffff:
        raise ReadError("invalid_filter", f"{name} is outside the supported range.")
    return number


def identifier(value):
    if not isinstance(value, str) or not HEX_ID.fullmatch(value):
        raise ReadError("invalid_record", "Use a complete lowercase publication identifier.")
    return value


def encode_response(value):
    # iterencode avoids allocating an unlimited serialized response first.
    raw = bytearray()
    for part in json.JSONEncoder(ensure_ascii=True, separators=(",", ":")).iterencode(value):
        chunk = part.encode("utf-8")
        if len(raw) + len(chunk) > MAX_RESPONSE_BYTES:
            raise ReadError("response_capacity", "This indexed response exceeds the response limit. Narrow the request.", 503)
        raw.extend(chunk)
    return bytes(raw)


class Explorer:
    def __init__(self, state_dir, *, assurance_policy=POLICY_ID, accepted_context_sha256=None,
                 finality_source_policy=None, accepted_finality_source_context_sha256=None):
        self.state_dir = Path(state_dir)
        self.evidence = EvidenceStore(self.state_dir, policy=assurance_policy, accepted_context_sha256=accepted_context_sha256,
            finality_source_policy=finality_source_policy,
            accepted_finality_source_context_sha256=accepted_finality_source_context_sha256)
        self.slots = threading.BoundedSemaphore(MAX_READERS)

    @contextmanager
    def admit(self):
        if not self.slots.acquire(blocking=False):
            raise ReadError("read_busy", "Indexed read capacity is busy. Retry shortly.", 429)
        try:
            yield
        finally:
            self.slots.release()

    def database(self):
        # Once present, the maintained index is authoritative for this request.
        # A corrupt maintained index must not silently switch to an older corpus.
        for name, kind in (("service.sqlite3", "maintained"), ("catalogue.sqlite3", "catalogue")):
            path = self.state_dir / name
            if path.is_file():
                return path, kind
        raise ReadError("index_unavailable", "The publication index is unavailable. This does not establish publication absence.", 503)

    def download(self, target, digest):
        budget = ReadBudget()
        path, _ = self.database()
        receipt = recorded_lookup(path, block_input(target), check=budget)
        if receipt is None:
            raise ReadError('indexed_evidence_unavailable', 'No indexed evidence is available for this exact block.', 404)
        verify_receipt(receipt, budget)
        try:
            return self.evidence.export(receipt, digest, check=budget)
        except DataError as exc:
            raise ReadError(exc.code, str(exc), 429 if exc.code == 'evidence_export_busy' else 409) from exc

    @contextmanager
    def snapshot(self, path, budget):
        with Catalogue(path, readonly=True, check=budget) as catalogue:
            catalogue.db.execute("BEGIN")
            yield catalogue

    def read(self, route, params):
        budget = ReadBudget()
        try:
            result = self._read(route, params, budget)
            budget()
            return result
        except sqlite3.Error as exc:
            if budget.error:
                raise budget.error from exc
            raise ReadError("index_unavailable", "The index could not be read consistently. Retry later; no source search was started.", 503) from exc
        except ReadError:
            raise
        except DataError as exc:
            code = "retained_view_conflict" if exc.code in ("unresolved_retained_height", "conflicting_retained_headers") else exc.code
            if code == "unsupported_input":
                code = "invalid_block"
            status = 409 if code == "retained_view_conflict" else 400 if code in ("invalid_block", "invalid_input") else 422
            raise ReadError(code, str(exc), status) from exc
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ReadError("index_unavailable", "Stored index data could not be read. No source search was started.", 503) from exc

    def _read(self, route, params, budget):
        if route == "":
            self.parameters(params, set())
            return {"api_version": API_VERSION, "mode": "indexed-only", "documentation": "/api-docs",
                    "endpoints": ["/api/v1/index", "/api/v1/publications", "/api/v1/keystones",
                                  "/api/v1/blocks/{number-or-hash}", "/api/v1/publications/{id}"],
                    "limits": {"concurrent_reads": MAX_READERS, "maximum_page_size": MAX_PAGE_SIZE,
                               "response_bytes": MAX_RESPONSE_BYTES, "decoded_input_bytes": MAX_INPUT_BYTES,
                               "sql_work_units": MAX_SQL_STEPS, "read_seconds": READ_SECONDS},
                    "source_operations": False}
        if route not in ("index", "publications", "keystones") and not re.fullmatch(r"(?:blocks/[^/]+|publications/[0-9a-f]{64})", route):
            raise ReadError("not_found", "This API route was not found.", 404)
        allowed = {"limit", "cursor", "keystone_height", "bitcoin_height", "state"} if route in ("publications", "keystones") else {"publication"} if route.startswith("blocks/") else set()
        self.parameters(params, allowed)
        path, kind = self.database()
        if route.startswith("blocks/"):
            target = block_input(route[7:])
            focus = identifier(params["publication"]) if "publication" in params else None
            receipt = recorded_lookup(path, target, check=budget)
            if receipt is None:
                raise ReadError("indexed_evidence_unavailable", "No usable indexed evidence is available for this input. This does not establish publication absence.", 404)
            exact_hash = receipt["query"]["block_hash"].lower()
            record_url = "/records/hemi/" + exact_hash
            if focus:
                record_url += "?publication=" + focus
            verification = verify_receipt(receipt, budget)
            return self.evidence.attach({"api_version": API_VERSION, "index_kind": kind,
                    "id": "indexed-" + digest(receipt)[:32], "label": "Recorded evidence", "origin": "indexed_read",
                    "filename": "traze-hemi-" + exact_hash[2:] + ".json",
                    "record_url": record_url, "selected_publication": focus,
                    "selected_publication_available": focus is None or any(p["publication_id"] == focus for p in receipt["proofs"]),
                    "receipt": receipt, "verification": verification}, check=budget)
        with self.snapshot(path, budget) as catalogue:
            if route == "index":
                return self.index(catalogue, kind)
            if route in ("publications", "keystones"):
                return self.browse(catalogue, kind, route, params, budget)
            if route.startswith("publications/"):
                return self.publication(catalogue, kind, identifier(route[13:]), budget)
        raise ReadError("not_found", "This API record was not found.", 404)

    @staticmethod
    def parameters(params, allowed):
        if set(params) - allowed:
            raise ReadError("invalid_filter", "Unknown query parameter. Source operations are not supported by the read API.")

    @staticmethod
    def index(catalogue, kind):
        from .collection_progress import public_collection_status
        counts = catalogue.summary()
        bounds = catalogue.db.execute("SELECT MIN(n.height),MAX(n.height) FROM nominations n JOIN blocks b ON b.hash=n.hash WHERE b.status='complete'").fetchone()
        return {"api_version": API_VERSION, "index_kind": kind, "retrieved_at": timestamp(), "counts": counts,
                "bitcoin_height_bounds": list(bounds) if bounds[0] is not None else None,
                "coverage_note": "Bounds are not contiguous coverage. Records retain source-reported heights; gaps and unassociated candidates may remain.",
                "source_observations_current": False, "history_complete": False,
                "collection": public_collection_status(catalogue.path.parent, catalogue.db) if kind == 'maintained' else None,
                "last_import_stored_at": catalogue.db.execute("SELECT MAX(imported_at) FROM imports").fetchone()[0]}

    @staticmethod
    def filters(params):
        filters = {}
        for key in ("keystone_height", "bitcoin_height"):
            if key in params:
                filters[key] = integer(params[key], key, zero=key == "bitcoin_height")
        state = params.get("state", "all")
        if state not in ("all", "associated", "pending", "unsupported"):
            raise ReadError("invalid_filter", "Choose all, associated, pending or unsupported records.")
        filters["state"] = state
        return filters

    def browse(self, catalogue, kind, route, params, budget):
        limit = integer(params.get("limit", str(PAGE_SIZE)), "limit")
        if limit > MAX_PAGE_SIZE:
            raise ReadError("invalid_filter", f"Page size must be at most {MAX_PAGE_SIZE}.")
        filters = self.filters(params)
        signature = digest([route, filters, kind])
        after = self.cursor(params.get("cursor"), signature, route)
        where, values = ["b.status IN ('complete','proof_only')"], []
        if "keystone_height" in filters:
            where.append("p.keystone_height=?")
            values.append(filters["keystone_height"])
        if "bitcoin_height" in filters:
            where.append("EXISTS(SELECT 1 FROM nominations fn WHERE fn.hash=p.block_hash AND fn.height=?)")
            values.append(filters["bitcoin_height"])
        association = "EXISTS(SELECT 1 FROM associations a WHERE a.publication_id=p.id)"
        if filters["state"] == "associated":
            where.extend(("p.supported=1", association))
        elif filters["state"] == "pending":
            where.extend(("p.supported=1", "NOT " + association))
        elif filters["state"] == "unsupported":
            where.append("p.supported=0")
        common = " FROM publications p JOIN blocks b ON b.hash=p.block_hash LEFT JOIN nominations n ON n.hash=p.block_hash WHERE " + " AND ".join(where)
        if route == "publications":
            sql = ("WITH page AS (SELECT p.id,p.block_hash,p.txid,p.vout,p.supported,p.payload,p.keystone_height,b.status AS block_status,"
                   "COALESCE(MAX(n.height),-1) AS sort_height,MIN(n.height) AS min_height,COUNT(DISTINCT n.height) AS height_count,"
                   + association + " AS associated" + common + " GROUP BY p.id) SELECT * FROM page")
            if after:
                sql += " WHERE (sort_height<? OR (sort_height=? AND id>?))"
                values.extend((after[0], after[0], after[1]))
            sql += " ORDER BY sort_height DESC,id LIMIT ?"
            values.append(limit + 1)
            rows = list(catalogue.db.execute(sql, values))
            items = [self.publication_summary(row) for row in rows[:limit]]
            position = [rows[limit - 1]["sort_height"], rows[limit - 1]["id"]] if len(rows) > limit else None
        else:
            where.append("p.supported=1")
            common = " FROM publications p JOIN blocks b ON b.hash=p.block_hash LEFT JOIN nominations n ON n.hash=p.block_hash WHERE " + " AND ".join(where)
            sql = ("WITH page AS (SELECT p.keystone_height,p.payload,COUNT(DISTINCT p.id) AS publication_count,"
                   "COUNT(DISTINCT CASE WHEN " + association + " THEN p.id END) AS associated_count"
                   + common + " GROUP BY p.keystone_height,p.payload) SELECT * FROM page")
            if after:
                sql += " WHERE (keystone_height<? OR (keystone_height=? AND payload>?))"
                values.extend((after[0], after[0], after[1]))
            sql += " ORDER BY keystone_height DESC,payload LIMIT ?"
            values.append(limit + 1)
            rows = list(catalogue.db.execute(sql, values))
            items = [{"height": row["keystone_height"], "keystone_id": display_hash(sha256d(bytes.fromhex(row["payload"]))),
                      "publication_count": row["publication_count"], "associated_count": row["associated_count"],
                      "publications_url": "/browse?keystone_height=" + str(row["keystone_height"])} for row in rows[:limit]]
            position = [rows[limit - 1]["keystone_height"], rows[limit - 1]["payload"]] if len(rows) > limit else None
        budget()
        cursor = self.make_cursor(signature, position) if position else None
        return {"api_version": API_VERSION, "index_kind": kind, "retrieved_at": timestamp(),
                "items": items, "page": {"limit": limit, "next_cursor": cursor}, "filters": filters,
                "source_observations_current": False,
                "qualification": "Indexed summaries, not freshly verified proofs or current chain confirmation. New records may appear before this page; restart browsing to include them."}

    @staticmethod
    def make_cursor(signature, position):
        raw = json.dumps({"v": 1, "scope": signature, "after": position}, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @staticmethod
    def cursor(value, signature, route):
        if value is None:
            return None
        try:
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,512}", value):
                raise ValueError()
            item = json.loads(base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True))
            after = item["after"]
            if (item["v"] != 1 or item["scope"] != signature or not isinstance(after, list) or len(after) != 2
                    or type(after[0]) is not int or not -1 <= after[0] <= 0xffffffff):
                raise ValueError()
            pattern = HEX_ID if route == "publications" else re.compile(r"[0-9a-f]{152}\Z")
            if not isinstance(after[1], str) or not pattern.fullmatch(after[1]):
                raise ValueError()
            return after
        except (ValueError, KeyError, TypeError):
            raise ReadError("invalid_cursor", "This page cursor is invalid for the selected filters. Restart browsing.") from None

    @staticmethod
    def publication_summary(row):
        associated = bool(row["associated"]) and bool(row["supported"])
        return {"id": row["id"], "record_url": "/records/publications/" + row["id"],
                "bitcoin_block_hash": row["block_hash"], "bitcoin_height": row["min_height"] if row["height_count"] == 1 else None,
                "height_conflict": row["height_count"] > 1, "txid": row["txid"], "vout_index": row["vout"],
                "keystone_height": row["keystone_height"],
                "keystone_id": display_hash(sha256d(bytes.fromhex(row["payload"]))) if row["supported"] else None,
                "state": "unsupported" if not row["supported"] else "associated" if associated else "pending",
                "association_available": associated,
                "evidence_scope": 'full_block_scan' if row['block_status'] == 'complete' else 'publication_proof'}

    def publication(self, catalogue, kind, identity, budget):
        row = catalogue.db.execute("""SELECT p.*,b.status AS block_status,MIN(n.height) AS min_height,COUNT(DISTINCT n.height) AS height_count,
            EXISTS(SELECT 1 FROM associations a WHERE a.publication_id=p.id) AS associated
            FROM publications p JOIN blocks b ON b.hash=p.block_hash LEFT JOIN nominations n ON n.hash=p.block_hash
            WHERE p.id=? AND b.status IN ('complete','proof_only') GROUP BY p.id""", (identity,)).fetchone()
        if row is None:
            raise ReadError("record_not_found", "This publication is not in the selected index. This does not establish publication absence.", 404)
        budget(len(row["data"]))
        data = json.loads(row["data"])
        hemi = {}
        for match in catalogue.db.execute("SELECT t.data FROM associations a JOIN templates t ON t.id=a.template_id WHERE a.publication_id=? ORDER BY t.id", (identity,)) if row['height_count'] == 1 else []:
            budget(len(match[0]))
            checkpoint = json.loads(match[0])["checkpoint"]
            block_hash = checkpoint["header"]["hash"].lower()
            hemi[block_hash] = {"height": checkpoint["height"], "hash": block_hash,
                                "record_url": "/records/hemi/" + block_hash + "?publication=" + identity}
            if len(hemi) > 20:
                break
        heights = [item[0] for item in catalogue.db.execute("SELECT DISTINCT height FROM nominations WHERE hash=? ORDER BY height LIMIT 51", (row["block_hash"],))]
        return {"api_version": API_VERSION, "index_kind": kind, "retrieved_at": timestamp(),
                "publication": self.publication_summary(row), "data": data,
                "reported_bitcoin_heights": heights[:50], "reported_heights_truncated": len(heights) > 50,
                "hemi_records": list(hemi.values())[:20], "hemi_records_truncated": len(hemi) > 20,
                "source_observations_current": False,
                "qualification": "Retained publication data. Open Hemi evidence to recompute payload, inclusion and ancestry checks. Association availability is not current chain confirmation."}
