"""Opt-in, finite public-data collection with durable progress and proof reuse."""

import argparse
import copy
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from .bitcoin import MAX_RAW_BLOCK, display_hash, hex32, parse_block
from .catalogue import encode, timestamp
from .collection_store import CollectionStore, RunnerLock, collection_status, epoch
from .collection_control import CONTROL_POLL_SECONDS, RunnerRecord, request_stop, stop_request
from .engine import Engine
from .errors import BudgetError, CollectionStopped, DataError, DeadlineError
from .network import PublicSources
from .proof import same_header
from .storage import BlockCache, SAFETY_MARGIN, Session, SESSION_LIMIT
from .accounting import accounting_state


class MetadataSources:
    """Reuse retained job observations, including bounded successor carryover.

    Original source times survive reuse. New source checks bypass this cache.
    """

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
    def __init__(self, root, state=None, *, sources_factory=PublicSources, event=None,
                 operating_budget=None, cache_directory=None, cache_bytes=40_000_000, previous_block=None,
                 raise_source_conflicts=False):
        self.root = Path(root).resolve()
        self.state = Path(state or self.root / ".hemi_lookup_data").resolve()
        self.sources_factory = sources_factory
        self.event = event or (lambda value: None)
        self.wall_deadlines = {}
        self.runner = None
        self.control_job = None
        self.last_control_poll = float("-inf")
        self.operating_budget = operating_budget
        self.cache_directory = Path(cache_directory) if cache_directory else self.state / "collection_blocks"
        self.cache_bytes = cache_bytes
        self.previous_block = previous_block
        self.raise_source_conflicts = raise_source_conflicts

    def _check_control(self, force=False):
        if self.runner is None:
            return
        current = time.monotonic()
        if force or current - self.last_control_poll >= CONTROL_POLL_SECONDS:
            try:
                request = stop_request(self.state, self.control_job)
            except DataError as exc:
                raise CollectionStopped(str(exc)) from exc
            if request:
                raise CollectionStopped("Operator requested collection stop")
            self.last_control_poll = current
            self.runner.touch()

    def _operate(self, identity, operation):
        if (self.state / 'restore-guard.json').exists():
            raise DataError('restored_state_requires_review', 'Reconcile restored index state before source acquisition')
        with RunnerLock(self.state), CollectionStore(self.state) as store:
            job = store.job(identity)
            if job.get("operating_service"):
                raise DataError("service_owned_job", "Run the owning collection service so its saved operating policy remains enforced")
            if job["state"] == "stopped":
                return job
            if stop_request(self.state, identity):
                return store.acknowledge_stop(identity)
            self.control_job = identity
            self.last_control_poll = float("-inf")
            self.runner = RunnerRecord(self.state, identity)
            try:
                self._check_control(force=True)
                result = operation(store)
                # A request racing the final commit still gets acknowledged.
                self._check_control(force=True)
                return result
            except KeyboardInterrupt:
                request_stop(self.state, identity, "Operator interrupted collection")
                return store.acknowledge_stop(identity)
            except CollectionStopped:
                # Session/transport contexts have closed before acknowledgement.
                try:
                    return store.acknowledge_stop(identity)
                except DataError as exc:
                    if exc.code != "collection_control_unavailable":
                        raise
                    job = store.job(identity)
                    job.update(state="incomplete", issue={"code": exc.code, "message": str(exc)})
                    job["usage"] = store.usage(job)
                    store.save(job)
                    return job
            finally:
                self.runner.close()
                self.runner = None
                self.control_job = None

    def stop(self, identity, reason="Stopped by operator"):
        job = CollectionStore.read_job(self.state, identity)
        if job["state"] in ("complete", "stopped") or job.get("clock", {}).get("ended_at"):
            return job
        request = request_stop(self.state, identity, reason)
        try:
            with RunnerLock(self.state), CollectionStore(self.state) as store:
                return store.acknowledge_stop(identity)
        except DataError as exc:
            if exc.code != "collector_busy":
                raise
            return {"id": identity, "state": "stop_requested", "stop": request,
                    "message": "Stop requested; waiting for runner acknowledgement"}

    def _wall_remaining(self, store, job):
        remaining = store.wall_remaining(job)
        if job["id"] not in self.wall_deadlines:
            self.wall_deadlines[job["id"]] = time.monotonic() + remaining
        return max(0, min(remaining, self.wall_deadlines[job["id"]] - time.monotonic()))

    def _window_expired(self, store, job):
        job.update(state="limited", issue={"code": "collection_window_expired",
                   "message": "Collection window ended; completed evidence and pending work are retained"})
        job.setdefault("clock", {})["ended_at"] = timestamp()
        job["usage"] = store.usage(job)
        store.save(job)
        self.event({"stage": "collection_window_expired", "job": job["id"]})
        return job

    def _ready(self, store, job, low, high, changed):
        for height in range(low, high + 1):
            item = job["items"].get(str(height), {})
            if item.get("state") != "processed":
                continue
            identities = [row[0] for row in store.db.execute(
                "SELECT id FROM publications WHERE block_hash=? AND supported=1", (item["hash"],))]
            item["supported_publications"] = len(identities)
            item["unresolved_publications"] = sum(job["associations"].get(identity, {}).get("state") != "complete" for identity in identities)
            if not changed and not item["unresolved_publications"]:
                first_ready = "evidence_ready_at" not in item
                item.setdefault("evidence_ready_at", timestamp())
                if item.get("first_observed_at"):
                    item["ready_latency_seconds"] = max(0, epoch(item["evidence_ready_at"]) - epoch(item["first_observed_at"]))
                if first_ready:
                    self.event({"stage": "block_evidence_ready", "height": height,
                                "supported_publications": len(identities),
                                "ready_latency_seconds": item.get("ready_latency_seconds")})
        store.save(job)

    def _body(self, store, job, height, sources, session, cache):
        started = time.monotonic()
        key = str(height)
        item = job["items"].get(key, {})
        if item.get("state") == "processed":
            return
        block_hash = hex32(sources.btc("/block-height/" + str(height), False)).hex()
        inherited_hash = job.get("work_reuse", {}).get("bitcoin_hashes", {}).get(key)
        if inherited_hash and inherited_hash != block_hash:
            store.observe("bitcoin", height, block_hash, sources.last_btc_api, status="changed")
            raise DataError("source_conflict", "The unfinished Bitcoin height changed before reuse; retained work remains historical")
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
            if previous is None and self.previous_block and self.previous_block["height"] == height - 1:
                previous = {**self.previous_block, "state": "processed"}
            if previous and previous.get("state") == "processed" and display_hash(parsed["header"][4:36]) != previous["hash"]:
                store.observe("bitcoin", height, block_hash, source, status="source_conflict")
                raise DataError("source_conflict", "Collected Bitcoin headers do not link across adjacent heights")
            cache.put(block_hash, raw)
            store.catalogue._store_body(block_hash, raw, parsed)
        elif self.previous_block and self.previous_block["height"] == height - 1:
            compiled = json.loads(row[1])
            if compiled["previous_hash"] != self.previous_block["hash"]:
                store.observe("bitcoin", height, block_hash, source, status="source_conflict")
                raise DataError("source_conflict", "Reused Bitcoin evidence does not link to the preceding service block")
        store.body_source(import_id, item.get("body_source", "reused_compiled_evidence"))
        store.observe("bitcoin", height, block_hash, source)
        item.update(state="processed", processed_at=timestamp(), processing_seconds=item.get("processing_seconds", 0) + time.monotonic() - started)
        if item.get("first_observed_at"):
            item["body_latency_seconds"] = max(0, epoch(item["processed_at"]) - epoch(item["first_observed_at"]))
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
                    if self.raise_source_conflicts and getattr(exc, 'code', '') in (
                            'source_conflict', 'inconsistent_data', 'backfill_scope_changed'):
                        raise

    def _recheck(self, store, job, low, high, sources, session):
        # Inherited association markers are not current-view confirmation.
        # Both chains are checked afresh before _ready can release the cursor.
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
        return self._operate(identity, lambda store: self._run(store, identity, max_updates=max_updates, recheck=recheck))

    def _run(self, store, identity, *, max_updates=None, recheck=False):
        if max_updates is not None and (isinstance(max_updates, bool) or not isinstance(max_updates, int) or max_updates < 1):
            raise DataError("invalid_collection", "The update limit must be a positive whole number")
        job = store.job(identity)
        if job.get("operating_service") and (
                self.operating_budget is None or self.control_job != job["operating_service"]
                or self.operating_budget.identity != job["operating_service"]
                or self.operating_budget.period != job["operating_period"]):
            raise DataError("service_owned_job", "The owning service and operating period must match before source work")
        self._check_control(force=True)
        store.recover_session(job)
        limits = job["limits"]
        cache = BlockCache(self.cache_directory, max_bytes=self.cache_bytes)
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
            store.start_clock(job)
            while True:
                self._check_control(force=True)
                wall_remaining = self._wall_remaining(store, job)
                if wall_remaining <= 0:
                    return self._window_expired(store, job)
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
                    period_remaining = self.operating_budget.remaining_seconds() if self.operating_budget else float("inf")
                    context = {"collection_job": job["id"], "update": index}
                    if self.operating_budget:
                        context.update(collection_service=self.operating_budget.identity,
                                       operating_period=self.operating_budget.period,
                                       operating_policy=self.operating_budget.digest)
                    ledger_directory = store.ledger_directory
                    accounting_state(self.state, db=store.db, prior_root=self.root)
                    if ledger_directory != self.state:
                        context['collection_index'] = str(self.state)
                        if not (ledger_directory / 'session.json').is_file():
                            raise DataError('missing_accounting', 'The bound lifetime ledger must not be recreated')
                    with Session(ledger_directory, self.root, max_bytes=remaining["bytes"] + SAFETY_MARGIN,
                                 max_blocks=max(0, remaining["attempts"]), max_requests=remaining["requests"],
                                 max_seconds=min(limits["segment_seconds"], remaining["seconds"], wall_remaining, period_remaining),
                                 session_limit=limits["cumulative_response_bytes"],
                                 context=context, interrupt=self._check_control,
                                 operating_budget=self.operating_budget) as session:
                        sources = self.sources_factory(session)
                        if int(sources.rpc("eth_chainId", []), 16) != 43111:
                            raise DataError("wrong_network", "Collector requires Hemi mainnet")
                        if not recheck:
                            tip_hash = hex32(sources.btc("/blocks/tip/hash", False)).hex()
                            tip = sources.btc("/block/" + tip_hash)
                            if tip.get("id") != tip_hash or isinstance(tip.get("height"), bool) or not isinstance(tip.get("height"), int) or tip["height"] < 0:
                                raise DataError("invalid_response", "Invalid observed Bitcoin tip")
                            update["tip_observation"] = store.tip(job, tip["height"], tip_hash, sources.last_btc_api)
                            self.event({"stage": "tip_observed", **update["tip_observation"]})
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
                        self._ready(store, job, low, high, changed)
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
        self._check_control(force=True)
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

    def follow(self, identity, *, max_updates=None, recheck=False, sleep=time.sleep):
        """Follow a fixed range inside one persisted window and owned runner lock."""
        return self._operate(identity, lambda store: self._follow(store, identity, max_updates, recheck, sleep))

    def _follow(self, store, identity, max_updates, recheck, sleep):
        job = store.job(identity)
        if not job["limits"].get("wall_seconds"):
            raise DataError("invalid_collection", "Following requires a saved finite wall-time allowance; preserve earlier jobs and accounting")
        while True:
            self._check_control(force=True)
            job = store.job(identity)
            next_attempt = job.get("clock", {}).get("next_attempt_at")
            if next_attempt:
                remaining = self._wall_remaining(store, job)
                if remaining <= 0:
                    return self._window_expired(store, job)
                wait = min(remaining, max(0, epoch(next_attempt) - time.time()))
                if wait:
                    if sleep is time.sleep:
                        until = time.monotonic() + wait
                        while time.monotonic() < until:
                            self._check_control(force=True)
                            sleep(min(CONTROL_POLL_SECONDS, max(0, until - time.monotonic())))
                    else:
                        # Deterministic test clocks retain one whole wait.
                        self._check_control(force=True)
                        sleep(wait)
                self._check_control(force=True)
                job["clock"].pop("next_attempt_at", None)
                store.save(job)
            job = self._run(store, identity, max_updates=max_updates, recheck=recheck)
            issue = job.get("issue") or {}
            retryable = job["state"] == "incomplete" and issue.get("code") == "source_unavailable"
            if job["state"] == "incomplete" and issue.get("code") == "unresolved_associations":
                issues = [a.get("issue", {}).get("code") for a in job["associations"].values() if a.get("state") != "complete"]
                retryable = bool(issues) and all(code in ("source_unavailable", "invalid_response") for code in issues)
            if job["state"] != "waiting" and not retryable:
                return job
            remaining = self._wall_remaining(store, job)
            if remaining <= 0:
                return self._window_expired(store, job)
            clock = job.setdefault("clock", {})
            failures = clock.get("consecutive_failures", 0) + 1 if retryable else 0
            clock["consecutive_failures"] = failures
            poll = job["limits"]["poll_seconds"]
            seconds = min(remaining, min(job["limits"]["backoff_max_seconds"], poll * 2 ** min(failures, 10)))
            clock["next_attempt_at"] = datetime.fromtimestamp(time.time() + seconds, timezone.utc).isoformat()
            store.save(job)
            self.event({"stage": "collection_wait", "state": job["state"], "seconds": seconds,
                        "retry": retryable, "range": job["range"]})


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
    create.add_argument("--wall-seconds", type=int, default=7200)
    create.add_argument("--poll-seconds", type=int, default=60)
    create.add_argument("--backoff-max-seconds", type=int, default=600)
    run = sub.add_parser("run", help="Run/resume a saved job within its remaining allowance")
    run.add_argument("job")
    run.add_argument("--max-updates", type=int)
    run.add_argument("--recheck", action="store_true")
    run.add_argument("--follow", action="store_true", help="Retry waiting-for-tip jobs inside their finite range and remaining allowance")
    stop = sub.add_parser("stop", help="Request a durable terminal stop without contacting sources")
    stop.add_argument("job")
    stop.add_argument("--reason", default="Stopped by operator", help="Saved stop reason, up to 500 characters")
    sub.add_parser("status", help="Read collection status without contacting sources")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent.parent
    if args.command == "create":
        with CollectionStore(args.state_dir) as store:
            result = store.create(args.start, args.end, batch_size=args.batch_size, max_bytes=args.max_bytes,
                                  max_attempts=args.max_attempts, max_requests=args.max_requests, max_seconds=args.max_seconds,
                                  cumulative_limit=args.cumulative_limit, segment_seconds=args.segment_seconds,
                                  wall_seconds=args.wall_seconds, poll_seconds=args.poll_seconds,
                                  backoff_max_seconds=args.backoff_max_seconds)
    elif args.command == "status":
        result = collection_status(args.state_dir)
    elif args.command == "stop":
        result = Collector(root, args.state_dir).stop(args.job, args.reason)
    else:
        collector = Collector(root, args.state_dir, event=lambda value: print(encode(value), flush=True))
        operation = collector.follow if args.follow else collector.run
        result = operation(args.job, max_updates=args.max_updates, recheck=args.recheck)
    print(json.dumps(result, indent=2))
    return 0 if not result or result["state"] in ("complete", "queued", "waiting", "stopped", "stop_requested") else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DataError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"error": getattr(exc, "code", "collection_error"), "message": str(exc)}))
        raise SystemExit(2)
