"""Opt-in, finite public-data collection with durable progress and proof reuse."""

import argparse
import copy
import json
import time
from pathlib import Path

from .bitcoin import MAX_RAW_BLOCK, display_hash, hex32, parse_block
from .catalogue import encode, timestamp
from .collection_store import CollectionStore, RunnerLock, collection_status
from .engine import Engine
from .errors import BudgetError, DataError, DeadlineError
from .network import PublicSources
from .proof import same_header
from .storage import BlockCache, SAFETY_MARGIN, Session, SESSION_LIMIT


class MetadataSources:
    """Reuse job-local RPC observations; new source checks always bypass this cache."""

    def __init__(self, sources, store, job, session):
        self.sources, self.store, self.job, self.session = sources, store, job, session

    @property
    def endpoint(self):
        return self.sources.provenance().get("hemi_rpc_used") or "configured public Hemi RPC"

    def rpc(self, method, params):
        self.session.check_deadline()
        cached = self.store.metadata(self.job, method, params)
        if cached is not None:
            return copy.deepcopy(cached)
        value = self.sources.rpc(method, params)
        if method == "eth_getBlockByNumber":
            number = int(params[0], 16)
            value = Engine(self.sources, self.session, None).checked_header(value, expected_number=number)
            self.store.observe("hemi", number, value["hash"], self.endpoint, header=value)
        self.store.cache_metadata(self.job, method, params, value, self.endpoint)
        return copy.deepcopy(value)


class Collector:
    def __init__(self, root, state=None, *, sources_factory=PublicSources, event=None):
        self.root = Path(root).resolve()
        self.state = Path(state or self.root / ".hemi_lookup_data").resolve()
        self.sources_factory = sources_factory
        self.event = event or (lambda value: None)

    def _body(self, store, job, height, sources, session, cache):
        started = time.monotonic()
        key = str(height)
        item = job["items"].get(key, {})
        if item.get("state") == "processed":
            return
        block_hash = hex32(sources.btc("/block-height/" + str(height), False)).hex()
        metadata = sources.btc("/block/" + block_hash)
        if metadata.get("id") != block_hash or metadata.get("height") != height:
            raise DataError("source_conflict", "Bitcoin height/hash metadata disagree; collection remains incomplete")
        source = sources.last_btc_api or "configured public Bitcoin API"
        import_id = store.nominate(job, height, block_hash, source)
        item.update(hash=block_hash, import_id=import_id, state="pending")
        job["items"][key] = item
        store.save(job)
        row = store.db.execute("SELECT status,data FROM blocks WHERE hash=?", (block_hash,)).fetchone()
        if row[0] != "complete":
            raw = cache.get(block_hash)
            if raw is None:
                raw, source = sources.block_raw(block_hash, MAX_RAW_BLOCK)
                item["body_source"] = source
                store.save(job)
            else:
                session.run["cache_reads"] += 1
                source = item.get("body_source", "verified_local_collection_cache")
            parsed = parse_block(raw, block_hash, session.check_deadline)
            previous = job["items"].get(str(height - 1))
            if previous and previous.get("state") == "processed" and display_hash(parsed["header"][4:36]) != previous["hash"]:
                store.observe("bitcoin", height, block_hash, source, status="source_conflict")
                raise DataError("source_conflict", "Collected Bitcoin headers do not link across adjacent heights")
            cache.put(block_hash, raw)
            store.catalogue._store_body(block_hash, raw, parsed)
        store.body_source(import_id, item.get("body_source", "reused_compiled_evidence"))
        store.observe("bitcoin", height, block_hash, source)
        item.update(state="processed", processed_at=timestamp(), processing_seconds=item.get("processing_seconds", 0) + time.monotonic() - started)
        store.save(job)
        self.event({"stage": "block_processed", "height": height, "hash": block_hash})

    def _associations(self, store, job, low, high, sources, session):
        adapted = MetadataSources(sources, store, job, session)
        engine = Engine(adapted, session, None)
        items = [job["items"][str(h)] for h in range(low, high + 1) if job["items"].get(str(h), {}).get("state") == "processed"]
        failed = set()
        for item in items:
            rows = store.db.execute("SELECT * FROM publications WHERE block_hash=? AND supported=1 ORDER BY keystone_height,txid,vout", (item["hash"],)).fetchall()
            for publication in rows:
                started = time.monotonic()
                session.check_deadline()
                identity, height = publication["id"], publication["keystone_height"]
                if job["associations"].get(identity, {}).get("state") == "complete":
                    continue
                if height in failed:
                    continue
                try:
                    cp = engine.checkpoint(height)
                    if cp["payload_hex"] != publication["payload"]:
                        raise DataError("payload_mismatch", "Publication bytes do not match the reconstructed keystone in the observed Hemi view")
                    # Commitment inclusion is useful even if the following ancestry
                    # acquisition is interrupted. It is not a complete target path.
                    store.template(item["import_id"], cp, [cp["header"]], adapted.endpoint,
                                   store.metadata_observations(job, cp, [cp["header"]]))
                    job["associations"][identity] = {"state": "commitment_checked", "keystone": height}
                    store.save(job)
                    floor = max(1, height - 25)
                    path = [cp["header"]]
                    try:
                        for number in range(height - 1, floor - 1, -1):
                            parent = engine.header(number)
                            if path[0]["parentHash"] != parent["hash"]:
                                raise DataError("source_conflict", "Stored ancestry does not follow the observed parent links")
                            path.insert(0, parent)
                    finally:
                        store.template(item["import_id"], cp, path, adapted.endpoint,
                                       store.metadata_observations(job, cp, path))
                    job["associations"][identity] = {"state": "complete", "keystone": height,
                                                      "ancestry_range": [floor, height], "observed_at": timestamp(),
                                                      "processing_seconds": time.monotonic() - started}
                    store.save(job)
                    self.event({"stage": "association_checked", "keystone": height, "publication_id": identity})
                except BudgetError:
                    raise
                except (DataError, KeyError, TypeError, ValueError) as exc:
                    failed.add(height)
                    job["associations"][identity] = {"state": "unresolved", "keystone": height,
                        "processing_seconds": time.monotonic() - started,
                        "issue": {"code": getattr(exc, "code", "invalid_metadata"), "message": str(exc)}}
                    store.save(job)

    def _recheck(self, store, job, low, high, sources, session):
        changed = False
        for height in range(low, high + 1):
            item = job["items"].get(str(height))
            if not item or item.get("state") != "processed":
                continue
            observed = hex32(sources.btc("/block-height/" + str(height), False)).hex()
            differs = observed != item["hash"]
            store.observe("bitcoin", height, observed, sources.last_btc_api, status="changed" if differs else "observed")
            changed |= differs
        heights = sorted({a["keystone"] for identity, a in job["associations"].items()
                          if store.db.execute("SELECT 1 FROM associations WHERE publication_id=?", (identity,)).fetchone()})
        for height in heights:
            session.check_deadline()
            old = store.metadata(job, "eth_getBlockByNumber", [hex(height), False])
            try:
                fresh = Engine(sources, session, None).checked_header(sources.rpc("eth_getBlockByNumber", [hex(height), False]), expected_number=height)
                differs = not old or not same_header(old, fresh)
                store.observe("hemi", height, fresh["hash"], sources.provenance().get("hemi_rpc_used"),
                              status="changed" if differs else "observed", header=fresh)
                changed |= differs
            except BudgetError:
                raise
            except (DataError, KeyError, ValueError, TypeError):
                store.observe("hemi", height, None, "configured public Hemi RPC", status="unavailable")
                changed = True
        if changed:
            job["issue"] = {"code": "source_view_changed", "message": "A later source check differs or is unavailable; retained evidence remains historical"}
        return changed

    def run(self, identity, *, max_updates=None, recheck=False):
        with RunnerLock(self.state), CollectionStore(self.state) as store:
            job = store.job(identity)
            store.recover_session(job)
            limits = job["limits"]
            cache = BlockCache(self.state / "collection_blocks")
            start, end = job["range"]
            count = 0
            for index, low in enumerate(range(start, end + 1, job["batch_size"])):
                high = min(end, low + job["batch_size"] - 1)
                if len(job["updates"]) <= index:
                    job["updates"].append({"range": [low, high], "state": "queued"})
                update = job["updates"][index]
                if update["state"] == "complete" and not recheck:
                    continue
                if max_updates is not None and count >= max_updates:
                    break
                count += 1
                while True:
                    usage = store.usage(job, index)
                    remaining = {"bytes": limits["response_bytes"] - usage["response_bytes"],
                                 "attempts": limits["body_attempts"] - usage["body_attempts"],
                                 "requests": limits["requests"] - usage["requests"],
                                 "seconds": limits["active_seconds_per_update"] - usage["active_seconds"]}
                    if remaining["bytes"] <= 0 or remaining["requests"] <= 0 or remaining["seconds"] <= 0:
                        job.update(state="limited", issue={"code": "collection_limit", "message": "Collection allowance exhausted; progress and evidence are retained"})
                        store.save(job)
                        return job
                    job["state"], update["state"] = "running", "running"
                    job["issue"] = None
                    store.save(job)
                    try:
                        with Session(self.state, self.root, max_bytes=remaining["bytes"] + SAFETY_MARGIN,
                                     max_blocks=max(0, remaining["attempts"]), max_requests=remaining["requests"],
                                     max_seconds=min(limits["segment_seconds"], remaining["seconds"]),
                                     session_limit=limits["cumulative_response_bytes"],
                                     context={"collection_job": job["id"], "update": index}) as session:
                            sources = self.sources_factory(session)
                            if int(sources.rpc("eth_chainId", []), 16) != 43111:
                                raise DataError("wrong_network", "Collector requires Hemi mainnet")
                            if not recheck:
                                tip_hash = hex32(sources.btc("/blocks/tip/hash", False)).hex()
                                tip = sources.btc("/block/" + tip_hash)
                                if tip.get("id") != tip_hash or isinstance(tip.get("height"), bool) or not isinstance(tip.get("height"), int) or tip["height"] < 0:
                                    raise DataError("invalid_response", "Invalid observed Bitcoin tip")
                                update["tip_observation"] = {"height": tip["height"], "hash": tip_hash, "observed_at": timestamp()}
                                if tip["height"] < low:
                                    job["state"], update["state"] = "waiting", "waiting"
                                    store.save(job)
                                    return job
                                available = min(high, tip["height"])
                                for height in range(low, available + 1):
                                    self._body(store, job, height, sources, session, cache)
                                self._associations(store, job, low, available, sources, session)
                                if available < high:
                                    job["state"], update["state"] = "waiting", "waiting"
                                    store.save(job)
                                    return job
                            changed = self._recheck(store, job, low, high, sources, session)
                        ids = {row[0] for h in range(low, high + 1) for row in store.db.execute(
                            "SELECT id FROM publications WHERE block_hash=? AND supported=1",
                            (job["items"].get(str(h), {}).get("hash", ""),))}
                        incomplete = any(job["associations"].get(identity, {}).get("state") != "complete" for identity in ids)
                        update["state"] = "source_conflict" if changed else "incomplete_associations" if incomplete else "complete"
                        if not changed:
                            job["issue"] = {"code": "unresolved_associations", "message": "Some publications still need Hemi metadata or ancestry"} if incomplete else None
                        update["completed_at"] = timestamp()
                        store.save(job)
                        break
                    except DeadlineError:
                        job["issue"] = {"code": "segment_interrupted", "message": "Processing segment expired; remaining job allowance is preserved"}
                        store.save(job)
                        continue
                    except (DataError, OSError, KeyError, TypeError, ValueError) as exc:
                        message = str(exc) if isinstance(exc, DataError) else "Local collection work could not finish. Progress is retained; review the operator log and resume the same job."
                        job.update(state="limited" if isinstance(exc, BudgetError) else "incomplete",
                                   issue={"code": getattr(exc, "code", "collection_error"), "message": message},
                                   diagnostic={"type": type(exc).__name__, "message": str(exc)})
                        update["state"] = job["state"]
                        store.save(job)
                        return job
            processed = sum(item.get("state") == "processed" for item in job["items"].values())
            pending = sum(a.get("state") != "complete" for a in job["associations"].values())
            task_count = sum(store.db.execute("SELECT COUNT(*) FROM publications WHERE block_hash=? AND supported=1", (i["hash"],)).fetchone()[0]
                             for i in job["items"].values() if i.get("state") == "processed")
            associated = sum(a.get("state") == "complete" for a in job["associations"].values())
            conflict = any(u["state"] == "source_conflict" for u in job["updates"])
            job["state"] = "source_conflict" if conflict else "complete" if processed == end - start + 1 and not pending and associated == task_count else "incomplete"
            job["usage"] = store.usage(job)
            store.save(job)
            return job


def main(argv=None):
    parser = argparse.ArgumentParser(description="Traze finite publication collector (collection is opt-in)")
    parser.add_argument("--state-dir", type=Path, default=Path(__file__).resolve().parent.parent / ".hemi_lookup_data")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="Persist a finite job without contacting sources")
    create.add_argument("--start", type=int, required=True)
    create.add_argument("--end", type=int, required=True)
    create.add_argument("--batch-size", type=int, default=4)
    create.add_argument("--max-bytes", type=int, default=40_000_000)
    create.add_argument("--max-attempts", type=int, default=20)
    create.add_argument("--max-requests", type=int, default=600)
    create.add_argument("--max-seconds", type=int, default=600)
    create.add_argument("--cumulative-limit", type=int, default=SESSION_LIMIT)
    create.add_argument("--segment-seconds", type=int, default=60)
    run = sub.add_parser("run", help="Run/resume a saved job within its remaining allowance")
    run.add_argument("job")
    run.add_argument("--max-updates", type=int)
    run.add_argument("--recheck", action="store_true")
    run.add_argument("--follow", action="store_true", help="Retry waiting-for-tip jobs inside their finite range and remaining allowance")
    sub.add_parser("status", help="Read collection status without contacting sources")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent.parent
    if args.command == "create":
        with CollectionStore(args.state_dir) as store:
            result = store.create(args.start, args.end, batch_size=args.batch_size, max_bytes=args.max_bytes,
                                  max_attempts=args.max_attempts, max_requests=args.max_requests, max_seconds=args.max_seconds,
                                  cumulative_limit=args.cumulative_limit, segment_seconds=args.segment_seconds)
    elif args.command == "status":
        result = collection_status(args.state_dir)
    else:
        collector = Collector(root, args.state_dir, event=lambda value: print(encode(value), flush=True))
        while True:
            result = collector.run(args.job, max_updates=args.max_updates, recheck=args.recheck)
            if not args.follow or result["state"] != "waiting":
                break
            time.sleep(15)
    print(json.dumps(result, indent=2))
    return 0 if not result or result["state"] in ("complete", "queued", "waiting") else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DataError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"error": getattr(exc, "code", "collection_error"), "message": str(exc)}))
        raise SystemExit(2)
