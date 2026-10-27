"""Offline publication catalogue. No transport, Session, or chain client is used.

Extraction, supplied Hemi associations, and historical provider observations are
separate records. SQLite commits each body atomically; imports can resume without
reparsing completed bodies. Queries use compiled records, never the raw cache.
"""

import copy
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .bitcoin import MAX_RAW_BLOCK, display_hash, hex32, merkle_branch, parse_block
from .errors import DataError
from .inputs import block_input, covering
from .proof import SCHEMA, same_header, verify_proof, verify_receipt

CATALOGUE_SCHEMA = "traze-offline-catalogue-v1"
DEFAULT_RECEIPTS = (
    "demo_results/mainnet_5243117.json",
    "demo_results/mainnet_4028425_automatic.json",
)


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def height(header):
    return int(header["number"], 16)


def intervals(rows):
    """Do not join different branches or the two disjoint nominated periods."""
    result, previous = [], None
    for row in sorted(rows, key=lambda item: (item["height"], item["hash"])):
        if (previous and row["height"] == previous["height"] + 1
                and row["previous_hash"] == previous["hash"]):
            result[-1][1] = row["height"]
        else:
            result.append([row["height"], row["height"]])
        previous = row
    return result


class Catalogue:
    def __init__(self, path, *, readonly=False, check=None):
        self.path = Path(path).resolve()
        self.check = check
        if readonly:
            self.db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=.25 if check else 5)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        if check:
            self.db.set_progress_handler(check.progress, 1000)
            self.db.set_trace_callback(check.trace)
            if hasattr(self.db, "setlimit"):
                self.db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 8_000_000)
        self.db.execute("PRAGMA foreign_keys=ON")
        if not readonly:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS imports (
                    id TEXT PRIMARY KEY, label TEXT NOT NULL, observation TEXT NOT NULL,
                    imported_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS blocks (
                    hash TEXT PRIMARY KEY, status TEXT NOT NULL, data TEXT, issue TEXT);
                CREATE TABLE IF NOT EXISTS nominations (
                    import_id TEXT REFERENCES imports(id), hash TEXT REFERENCES blocks(hash),
                    height INTEGER NOT NULL, observation TEXT NOT NULL,
                    PRIMARY KEY(import_id, hash, height));
                CREATE TABLE IF NOT EXISTS headers (
                    hash TEXT NOT NULL, variant TEXT NOT NULL, height INTEGER NOT NULL,
                    data TEXT NOT NULL, PRIMARY KEY(hash, variant));
                CREATE TABLE IF NOT EXISTS header_origins (
                    hash TEXT NOT NULL, variant TEXT NOT NULL,
                    import_id TEXT REFERENCES imports(id), PRIMARY KEY(hash, variant, import_id),
                    FOREIGN KEY(hash, variant) REFERENCES headers(hash, variant));
                CREATE TABLE IF NOT EXISTS templates (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS template_origins (
                    template_id TEXT REFERENCES templates(id), import_id TEXT REFERENCES imports(id),
                    PRIMARY KEY(template_id, import_id));
                CREATE TABLE IF NOT EXISTS publications (
                    id TEXT PRIMARY KEY, block_hash TEXT REFERENCES blocks(hash), txid TEXT NOT NULL,
                    vout INTEGER NOT NULL, supported INTEGER NOT NULL, payload TEXT,
                    keystone_height INTEGER, data TEXT NOT NULL,
                    UNIQUE(block_hash, txid, vout));
                CREATE TABLE IF NOT EXISTS associations (
                    publication_id TEXT REFERENCES publications(id),
                    template_id TEXT REFERENCES templates(id), PRIMARY KEY(publication_id, template_id));
                CREATE TABLE IF NOT EXISTS attempts (
                    id INTEGER PRIMARY KEY, block_hash TEXT REFERENCES blocks(hash),
                    processed_at TEXT NOT NULL, status TEXT NOT NULL, issue TEXT);
                CREATE INDEX IF NOT EXISTS publication_payload ON publications(payload);
                CREATE INDEX IF NOT EXISTS header_height ON headers(height);
            """)
            with self.db:
                self.db.execute("INSERT OR IGNORE INTO meta VALUES ('schema', ?)", (CATALOGUE_SCHEMA,))
        try:
            row = self.db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
        except sqlite3.Error:
            # A failed constructor never enters the context manager. Release the
            # read-only handle so main discovery fallback does not lock the file.
            self.db.close()
            raise
        if not row or row[0] != CATALOGUE_SCHEMA:
            self.close()
            raise DataError("unsupported_catalogue", "Unsupported catalogue schema")

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def read_json(self, raw):
        if self.check:
            self.check(len(raw.encode("utf-8")))
        return json.loads(raw)

    def _header(self, header, import_id):
        normalized, variant = hex32(header["hash"]).hex(), digest(header)
        self.db.execute("INSERT OR IGNORE INTO headers VALUES (?, ?, ?, ?)",
                        (normalized, variant, height(header), encode(header)))
        self.db.execute("INSERT OR IGNORE INTO header_origins VALUES (?, ?, ?)",
                        (normalized, variant, import_id))

    def import_receipt(self, receipt_path, block_directory, *, body_reader=None):
        """Recompute checks on supplied evidence; never trust its success flags.

        A crash interrupts only the current body transaction. Retrying a missing
        body repairs its status; attempts and original nominations are retained.
        """
        path = Path(receipt_path)
        raw_receipt = path.read_bytes()
        if len(raw_receipt) > 16_000_000:
            raise DataError("receipt_too_large", "Retained receipt exceeds the offline import bound")
        receipt = json.loads(raw_receipt)
        verify_receipt(receipt)
        import_id = hashlib.sha256(raw_receipt).hexdigest()
        observation = {key: copy.deepcopy(receipt.get(key)) for key in
                       ("query", "sources", "context", "started_at", "completed_at",
                        "bitcoin_tip_before", "bitcoin_tip_after")}
        observation["qualification"] = "Historical observations retain their original target and scope; not new target confirmation"
        nominations = receipt["coverage"]["blocks"]
        if len(nominations) > 100:
            raise DataError("corpus_too_large", "This offline import supports at most 100 nominated bodies per receipt")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO imports VALUES (?, ?, ?, ?)",
                            (import_id, path.name, encode(observation), timestamp()))
            if receipt["query"].get("header"):
                self._header(receipt["query"]["header"], import_id)
            for proof in receipt["proofs"]:
                template = {"checkpoint": proof["checkpoint"], "ancestry": proof["ancestry"]}
                template_id = digest(template)
                self.db.execute("INSERT OR IGNORE INTO templates VALUES (?, ?, ?)",
                                (template_id, proof["checkpoint"]["payload_hex"], encode(template)))
                self.db.execute("INSERT OR IGNORE INTO template_origins VALUES (?, ?)",
                                (template_id, import_id))
                for header in proof["ancestry"] + [proof["checkpoint"]["previous_header"]]:
                    if header:
                        self._header(header, import_id)
            for block in nominations:
                block_hash = hex32(block["hash"]).hex()
                if isinstance(block["height"], bool) or not isinstance(block["height"], int) or block["height"] < 0:
                    raise DataError("invalid_nomination", "Bitcoin height must be a source-reported nonnegative integer")
                self.db.execute("INSERT OR IGNORE INTO blocks(hash,status) VALUES (?, 'pending')", (block_hash,))
                self.db.execute("INSERT OR IGNORE INTO nominations VALUES (?, ?, ?, ?)",
                                (import_id, block_hash, block["height"], encode(block)))
        metrics = {"body_reads": 0, "body_bytes_read": 0, "body_parses": 0, "reused_bodies": 0, "failed_bodies": 0}
        directory = Path(block_directory).resolve()
        for block in nominations:
            block_hash = hex32(block["hash"]).hex()
            if self.db.execute("SELECT status FROM blocks WHERE hash=?", (block_hash,)).fetchone()[0] == "complete":
                metrics["reused_bodies"] += 1
                continue
            try:
                metrics["body_reads"] += 1
                if body_reader:
                    raw = body_reader(block_hash)
                else:
                    body_path = directory / (block_hash + ".bin")
                    if not body_path.resolve().is_relative_to(directory):
                        raise DataError("unsafe_body_path", "Retained body path escapes the corpus directory")
                    if body_path.stat().st_size > MAX_RAW_BLOCK:
                        raise DataError("body_too_large", "Retained body exceeds the parser bound")
                    raw = body_path.read_bytes()
                metrics["body_bytes_read"] += len(raw)
                metrics["body_parses"] += 1
                parsed = parse_block(raw, block_hash)
                self._store_body(block_hash, raw, parsed)
            except (OSError, DataError, ValueError) as exc:
                metrics["failed_bodies"] += 1
                issue = {"code": getattr(exc, "code", "unavailable_body"),
                         "message": str(exc) if isinstance(exc, DataError) else "Retained body is unavailable or malformed"}
                with self.db:
                    self.db.execute("UPDATE blocks SET status='failed', issue=? WHERE hash=?", (encode(issue), block_hash))
                    self.db.execute("INSERT INTO attempts(block_hash,processed_at,status,issue) VALUES (?,?,'failed',?)",
                                    (block_hash, timestamp(), encode(issue)))
        self._associate()
        return {"receipt_sha256": import_id, **metrics, "network_requests": 0}

    def _store_body(self, block_hash, raw, parsed):
        info = {"hash": block_hash, "previous_hash": display_hash(parsed["header"][4:36]),
                "header_hex": parsed["header"].hex(), "transaction_count": parsed["tx_count"],
                "raw_bytes": len(raw), "raw_sha256": hashlib.sha256(raw).hexdigest(),
                "full_transaction_root_checked": True, "hemi_candidates": len(parsed["candidates"])}
        with self.db:
            for candidate in parsed["candidates"]:
                tx = candidate["tx"]
                identity = digest([block_hash, tx["txid"], candidate["vout_index"]])
                record = {"bitcoin_block_hash": block_hash, "bitcoin_header_hex": parsed["header"].hex(),
                          "txid": tx["txid"], "raw_transaction_hex": tx["raw"].hex(),
                          "tx_index": candidate["tx_index"], "vout_index": candidate["vout_index"],
                          "merkle_siblings_display_hex": [display_hash(sibling) for sibling in
                              merkle_branch(parsed["leaves"], candidate["tx_index"])]}
                if not candidate["supported"]:
                    record["unsupported_format"] = {key: candidate[key] for key in ("length", "prefix")}
                self.db.execute("INSERT OR IGNORE INTO publications VALUES (?,?,?,?,?,?,?,?)",
                                (identity, block_hash, tx["txid"], candidate["vout_index"], int(candidate["supported"]),
                                 candidate["payload"].hex() if candidate["supported"] else None,
                                 candidate.get("l2_height"), encode(record)))
            self.db.execute("UPDATE blocks SET status='complete',data=?,issue=NULL WHERE hash=?", (encode(info), block_hash))
            self.db.execute("INSERT INTO attempts(block_hash,processed_at,status) VALUES (?,?,'complete')", (block_hash, timestamp()))

    def _associate(self):
        """Match reconstructed template bytes, not only abbreviated identifiers."""
        with self.db:
            for row in self.db.execute("""SELECT p.id, p.data, t.id AS template_id, t.data AS template
                    FROM publications p JOIN templates t ON t.payload=p.payload WHERE p.supported=1""").fetchall():
                record, template = json.loads(row["data"]), json.loads(row["template"])
                record.update(template)
                record["bitcoin_height"] = None  # Inclusion checks do not prove a global chain height.
                verify_proof(record)
                self.db.execute("INSERT OR IGNORE INTO associations VALUES (?,?)", (row["id"], row["template_id"]))

    def summary(self):
        def count(table, clause=""):
            return self.db.execute("SELECT COUNT(*) FROM " + table + clause).fetchone()[0]
        return {"schema": CATALOGUE_SCHEMA, "imports": count("imports"), "nominated_bodies": count("blocks"),
                "processed_bodies": count("blocks", " WHERE status='complete'"),
                "failed_or_pending_bodies": count("blocks", " WHERE status NOT IN ('complete','proof_only')"),
                "proof_only_blocks": count("blocks", " WHERE status='proof_only'"),
                "supported_publications": count("publications", " WHERE supported=1"),
                "unsupported_publications": count("publications", " WHERE supported=0"),
                "associated_publications": self.db.execute("SELECT COUNT(DISTINCT publication_id) FROM associations").fetchone()[0],
                "unassociated_publications": self.db.execute("SELECT COUNT(*) FROM publications p WHERE supported=1 AND NOT EXISTS (SELECT 1 FROM associations a WHERE a.publication_id=p.id)").fetchone()[0],
                "retained_header_variants": count("headers"), "network_requests": 0,
                "source_observations_current": False}

    def resolve_number(self, number):
        covering(number)
        hashes = self.db.execute("SELECT DISTINCT hash FROM headers WHERE height=? LIMIT 2", (number,)).fetchall()
        if len(hashes) != 1:
            raise DataError("unresolved_retained_height", "Retained height is unavailable or has conflicting hashes; use an exact retained hash")
        return "0x" + hashes[0][0]

    def _scope(self, import_ids):
        scopes, blocks, gaps, observations = [], {}, [], []
        for import_id in import_ids:
            if self.check:
                self.check()
            source = self.db.execute("SELECT * FROM imports WHERE id=?", (import_id,)).fetchone()
            origin_key = "source_record_id" if source["label"] == "Collected Bitcoin block" else "receipt_sha256"
            observations.append({origin_key: import_id, "label": source["label"],
                                 "observation": self.read_json(source["observation"])})
            complete = []
            for row in self.db.execute("SELECT n.height,n.hash,b.status,b.data,b.issue FROM nominations n JOIN blocks b ON b.hash=n.hash WHERE n.import_id=? ORDER BY n.height,n.hash", (import_id,)):
                if row["status"] != "complete":
                    gaps.append({"height": row["height"], "hash": row["hash"], origin_key: import_id,
                                 "reason": row["status"], "issue": self.read_json(row["issue"]) if row["issue"] else None})
                    continue
                data = self.read_json(row["data"])
                entry = {**data, "height": row["height"], "source": "compiled_retained_body", "cache_hit": True}
                complete.append(entry)
                blocks[(row["height"], row["hash"])] = entry
            scopes.append({origin_key: import_id, "label": source["label"],
                           "fully_scanned_intervals": intervals(complete), "height_scope": "source_reported"})
        return {"blocks": sorted(blocks.values(), key=lambda item: (item["height"], item["hash"])),
                "scopes": scopes, "gaps": gaps,
                "fully_scanned_intervals": [span for scope in scopes for span in scope["fully_scanned_intervals"]],
                "height_scope": "source_reported; header-chain position not independently verified",
                "stop_reason": "retained_corpus_processed", "extraction_scope": "reviewed HEMI first-data-push parser",
                "historical_observations": observations}

    def query(self, requested_hash):
        # A read transaction keeps a packet consistent while a collector commits
        # new bodies or metadata. The constructor itself remains network-free.
        if not self.db.in_transaction:
            self.db.execute("BEGIN")
        requested_hash = block_input(requested_hash)
        if not isinstance(requested_hash, str):
            raise DataError("exact_hash_required", "Catalogue query requires an exact Hemi block hash")
        key = hex32(requested_hash).hex()
        headers = self.db.execute("SELECT * FROM headers WHERE hash=? LIMIT 2", (key,)).fetchall()
        import_ids = [row[0] for row in self.db.execute("SELECT DISTINCT import_id FROM header_origins WHERE hash=? ORDER BY import_id", (key,))]
        maintained = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='collection_jobs'").fetchone() is not None
        if not import_ids or maintained:
            import_ids = [row[0] for row in self.db.execute("SELECT id FROM imports ORDER BY id")]
        coverage = self._scope(import_ids)
        if maintained:
            coverage["fully_scanned_intervals"] = intervals(coverage["blocks"])
        packet = {"schema": SCHEMA, "mode": "offline_catalogue", "started_at": timestamp(),
                  "query": {"input_type": "hash", "requested_block_hash": requested_hash,
                            "block_hash": requested_hash, "block_number": None,
                            "hemi_chain_id": 43111, "bitcoin_network": "mainnet"},
                  "proofs": [], "issues": [], "coverage": coverage,
                  "sources": {"kind": "retained_receipt_observations"},
                  "context": {"current_view_confirmed": False, "checked_at": None,
                              "requested_block_identity": {"status": "not_rechecked", "scope": "derived_target"}},
                  "selection": {"best_observed_proof_index": None, "current_proof_index": None,
                                "label": "Earliest observed within compiled scope; source-reported Bitcoin heights"},
                  "resources": {"network_requests": 0, "bitcoin_body_reads_this_query": 0, "raw_block_parses_this_query": 0},
                  "assurance": {"provided_data_only": True, "new_source_checks": False}}
        if len(headers) != 1:
            packet["status"] = "incomplete"
            packet["issues"] = [{"code": "conflicting_retained_headers" if headers else "outside_retained_headers",
                                 "message": "Requested hash has conflicting retained metadata" if headers else "Requested hash is outside retained Hemi metadata"}]
            packet["completed_at"] = timestamp()
            packet["offline_recomputation"] = verify_receipt(packet, self.check)
            return packet
        header = self.read_json(headers[0]["data"])
        number, expected_cover = height(header), covering(height(header))
        packet["query"].update(block_number=number, header=header, covering_keystone=expected_cover)
        placeholders = ",".join("?" for _ in import_ids)
        candidates = self.db.execute("SELECT DISTINCT p.* FROM publications p LEFT JOIN nominations n ON n.hash=p.block_hash "
            "LEFT JOIN associations a ON a.publication_id=p.id LEFT JOIN template_origins o ON o.template_id=a.template_id "
            "WHERE n.import_id IN (" + placeholders + ") OR o.import_id IN (" + placeholders + ") ORDER BY p.block_hash,p.txid,p.vout", import_ids + import_ids)
        unresolved, unsupported = [], []
        supported_count = 0
        # Reuse parent reads only inside this query's SQLite snapshot. Each
        # candidate still checks its own path and recomputes its proof below.
        parent_headers = {}
        for candidate in candidates:
            if self.check:
                self.check(len(candidate["data"]))
            if not candidate["supported"]:
                unsupported.append(candidate["id"])
                continue
            supported_count += 1
            if candidate["keystone_height"] < expected_cover:
                continue
            matches = self.db.execute("SELECT t.data,t.id FROM associations a JOIN templates t ON t.id=a.template_id WHERE a.publication_id=? ORDER BY t.id", (candidate["id"],))
            accepted = False
            for match in matches:
                template = self.read_json(match["data"])
                indexes = [i for i, item in enumerate(template["ancestry"]) if hex32(item["hash"]).hex() == key and same_header(item, header)]
                path = template["ancestry"][indexes[0]:] if indexes else self._linked_path(header, template["checkpoint"]["header"], parent_headers) if maintained else None
                if not path:
                    continue
                record = self.read_json(candidate["data"])
                reported = sorted({row[0] for row in self.db.execute("SELECT height FROM nominations WHERE hash=?", (candidate["block_hash"],))})
                record.update(checkpoint=template["checkpoint"], ancestry=path,
                              bitcoin_height=reported[0] if len(reported) == 1 else None,
                              reported_bitcoin_heights=reported, publication_id=candidate["id"],
                              current_bitcoin_depth_in_provider_view=None,
                              relationship="direct_to_covering_checkpoint" if template["checkpoint"]["height"] == expected_cover else "later_checkpoint_support")
                record["verification"] = {"against_provided_headers": True, "checks": verify_proof(record, self.check)}
                if not any(p["publication_id"] == candidate["id"] and p["checkpoint"] == record["checkpoint"] and p["ancestry"] == path for p in packet["proofs"]):
                    packet["proofs"].append(record)
                accepted = True
            if not accepted:
                unresolved.append({"publication_id": candidate["id"], "bitcoin_block_hash": candidate["block_hash"],
                                   "txid": candidate["txid"], "publishing_keystone": candidate["keystone_height"],
                                   "reason": "No complete supplied ancestry/keystone metadata for this exact target"})
        packet["proofs"].sort(key=lambda p: (p["bitcoin_height"] is None, p["bitcoin_height"] or 0,
                                                p["checkpoint"]["height"], p["txid"], p["vout_index"]))
        coverage.update(unresolved_candidates=unresolved, unsupported_payloads=len(unsupported),
                        unsupported_publication_ids=unsupported, supported_candidates_in_scope=supported_count,
                        target_association_complete=bool(coverage["blocks"]) and not unresolved and not unsupported and not coverage["gaps"]
                            and not any(p['bitcoin_height'] is None for p in packet['proofs']))
        if not coverage["blocks"]:
            packet["issues"].append({"code": "no_processed_scope", "message": "No complete Bitcoin bodies are available in this target's retained scope"})
        packet["status"] = "found" if packet["proofs"] else "incomplete" if not coverage["target_association_complete"] else "no_match"
        if packet["proofs"] and all(p["bitcoin_height"] is not None for p in packet["proofs"]):
            packet["selection"]["best_observed_proof_index"] = 0
        if any(p["bitcoin_height"] is None for p in packet["proofs"]):
            packet["issues"].append({"code": "historical_height_conflict", "message": "Historical sources report different heights for an inclusion; no earliest-height selection is made"})
        if len(packet["proofs"]) > 128:
            raise DataError("packet_capacity", "Compiled matches exceed this receipt verifier's packet capacity; no truncated success is returned")
        if maintained:
            self._collection_views(packet)
        packet["completed_at"] = timestamp()
        packet["offline_recomputation"] = verify_receipt(packet, self.check)
        return packet

    def _linked_path(self, requested, publishing, parent_headers=None):
        """Reuse an unambiguous stored parent graph, bounded to 256 links."""
        distance = height(publishing) - height(requested)
        if not 0 <= distance <= 256:
            return None
        if parent_headers is None:
            parent_headers = {}
        path = [publishing]
        while height(path[-1]) > height(requested):
            if self.check:
                self.check()
            parent_hash = hex32(path[-1]["parentHash"]).hex()
            if parent_hash not in parent_headers:
                rows = self.db.execute("SELECT data FROM headers WHERE hash=? LIMIT 2", (parent_hash,)).fetchall()
                parent_headers[parent_hash] = self.read_json(rows[0][0]) if len(rows) == 1 else None
            parent = parent_headers[parent_hash]
            if parent is None:
                return None
            if height(parent) + 1 != height(path[-1]):
                return None
            path.append(parent)
        if not same_header(path[-1], requested):
            return None
        return list(reversed(path))

    def _collection_views(self, packet):
        """Attach last observed source differences without discarding proofs."""
        checks = []
        requested = packet["query"]["header"]
        targets = [("hemi", height(requested), hex32(requested["hash"]).hex())]
        targets.extend(("bitcoin", proof["bitcoin_height"], proof["bitcoin_block_hash"]) for proof in packet["proofs"])
        targets.extend(("hemi", proof["checkpoint"]["height"], hex32(proof["checkpoint"]["header"]["hash"]).hex()) for proof in packet["proofs"])
        for chain, number, block_hash in sorted(set(targets), key=lambda item: (item[0], item[1] or -1, item[2])):
            if number is None:
                continue
            row = self.db.execute("SELECT data FROM collection_views WHERE chain=? AND height=? ORDER BY sequence DESC LIMIT 1", (chain, number)).fetchone()
            if not row:
                continue
            observation = self.read_json(row[0])
            changed = observation.get("hash") != block_hash or observation.get("status") != "observed"
            checks.append({**observation, "record_hash": block_hash, "matches_record": not changed})
            if changed:
                packet["issues"].append({"code": "collection_source_view_changed", "message": f"Last observed {chain} source view differs or is unavailable for height {number}; retained proof is historical"})
        packet["context"]["collection_source_views"] = checks
        packet["context"]["current_view_confirmed"] = False


def build_catalogue(root, database=None, *, block_directory=None):
    root = Path(root).resolve()
    database = database or root / ".hemi_lookup_data/catalogue.sqlite3"
    block_directory = Path(block_directory) if block_directory is not None else root / ".hemi_lookup_data/blocks"
    with Catalogue(database) as catalogue:
        runs = [catalogue.import_receipt(root / name, block_directory) for name in DEFAULT_RECEIPTS]
        return {"imports": runs, "summary": catalogue.summary(), "network_requests": 0}
