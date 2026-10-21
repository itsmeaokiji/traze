"""Opt-in continuous collection through bounded work and explicit period limits."""

import argparse
import copy
import json
import time
import uuid
from pathlib import Path

from .bitcoin import hex32
from .catalogue import timestamp
from .collection_control import CONTROL_POLL_SECONDS, RunnerRecord, request_stop, stop_request
from .collection_store import CollectionStore, RunnerLock
from .collector import Collector
from .errors import CollectionStopped, DataError
from .operating import OperatingBudget, OperatingLimit, check_checkpoint, checkpoint, fingerprint, ledger_state, policy_settings
from .network import PublicSources


class AnchoredSources:
    def __init__(self, source, anchor, store):
        self.source, self.anchor, self.store = source, anchor, store
        self.checked = False

    def __getattr__(self, name):
        return getattr(self.source, name)

    def btc(self, path, *args, **kwargs):
        if self.anchor and not self.checked:
            height = self.anchor["height"]
            observed = hex32(self.source.btc("/block-height/" + str(height), False)).hex()
            differs = observed != self.anchor["hash"]
            self.store.observe("bitcoin", height, observed, self.source.last_btc_api,
                               status="changed" if differs else "observed")
            if differs:
                raise DataError("source_conflict", "The last contiguous service block changed in the source view; retained evidence remains historical")
            self.checked = True
        return self.source.btc(path, *args, **kwargs)


class CollectionService(Collector):
    def __init__(self, root, state=None, *, sources_factory=PublicSources, event=None):
        super().__init__(root, state, sources_factory=sources_factory, event=event)
        self.base_sources_factory = sources_factory
        self.service_store = None
        self.active_child = None
        self.sources_factory = self._sources

    def _sources(self, session):
        return AnchoredSources(self.base_sources_factory(session), copy.deepcopy(self.previous_block), self.service_store)

    def _check_control(self, force=False):
        previous_poll = self.last_control_poll
        super()._check_control(force=force)
        if (self.active_child and (force or self.last_control_poll != previous_poll)
                and stop_request(self.state, self.active_child)):
            request_stop(self.state, self.control_job, "An operator stopped the active child job")
            raise CollectionStopped("Active child was stopped")

    def create(self, start, policy):
        if (self.state / "restore-guard.json").exists():
            raise DataError("restored_state_requires_review", "Reconcile restored accounting and controls before configuring acquisition")
        if isinstance(start, bool) or not isinstance(start, int) or start < 0:
            raise DataError("invalid_collection", "Use a nonnegative initial Bitcoin height")
        policy = policy_settings(policy)
        with RunnerLock(self.state), CollectionStore(self.state) as store:
            if (self.state / "session.lock").exists():
                raise DataError("session_locked", "Wait for the existing source operation before binding a policy")
            original = ledger_state(self.state, self.root)
            service = {"id": uuid.uuid4().hex, "created_at": timestamp(), "state": "queued",
                       "start_height": start, "next_height": start, "anchor": None,
                       "policy": policy, "policy_digest": fingerprint(policy),
                       "ledger_checkpoint": checkpoint(original), "clock": {},
                       "current_job": None, "last_job": None, "latest_tip": None,
                       "next_attempt_at": None, "consecutive_failures": 0, "issue": None}
            store.save_service(service)
            return service

    def _period(self, service):
        # No unused-period credits accumulate while the process is offline.
        current = time.time()
        clock = service["clock"]
        if not clock:
            clock.update(armed_at=current, period=0, max_seen_time=current)
        if current < clock["max_seen_time"] - 1:
            raise DataError("operating_clock_changed", "Clock moved backwards; no operating allowance is renewed")
        period = int((current - clock["armed_at"]) // service["policy"]["period_seconds"])
        if period < clock["period"]:
            raise DataError("operating_clock_changed", "A recorded operating period cannot be reopened")
        clock.update(period=period, max_seen_time=max(current, clock["max_seen_time"]))
        return period

    def _storage(self):
        names = ("service.sqlite3", "service.sqlite3-wal", "service.sqlite3-shm", "session.json")
        return sum((self.state / n).stat().st_size for n in names if (self.state / n).is_file())

    def _advance(self, service, job):
        next_height = service["next_height"]
        low, high = job["range"]
        while low <= next_height <= high:
            item = job["items"].get(str(next_height), {})
            update = next((u for u in job["updates"] if u["range"][0] <= next_height <= u["range"][1]), {})
            if (item.get("state") != "processed" or update.get("state") != "complete"
                    or not item.get("evidence_ready_at") or item.get("unresolved_publications")):
                break
            service["anchor"] = {"height": next_height, "hash": item["hash"]}
            service["last_ready_at"] = item["evidence_ready_at"]
            next_height += 1
        service["next_height"] = next_height

    def _acknowledge(self, store, identity):
        service = store.service(identity)
        if service["state"] == "stopped":
            return service
        request = stop_request(self.state, identity)
        if not request:
            raise DataError("collection_control_unavailable", "Service stop request is unavailable")
        if service["current_job"]:
            job = store.job(service["current_job"])
            if job["state"] != "complete":
                request_stop(self.state, job["id"], request["reason"])
                store.acknowledge_stop(job["id"])
        service.update(state="stopped", stop={**request, "acknowledged_at": timestamp()},
                       issue={"code": "collection_stopped", "message": request["reason"]}, next_attempt_at=None)
        store.save_service(service)
        return service

    def stop(self, identity, reason="Stopped by operator"):
        service = CollectionStore.read_service(self.state, identity)
        if service["state"] == "stopped":
            return service
        request = request_stop(self.state, identity, reason)
        try:
            with RunnerLock(self.state), CollectionStore(self.state) as store:
                return self._acknowledge(store, identity)
        except DataError as exc:
            if exc.code != "collector_busy":
                raise
            return {"id": identity, "state": "stop_requested", "stop": request}

    def _cycle(self, store, service):
        period = self._period(service)
        budget = OperatingBudget(service, period)
        check_checkpoint(ledger_state(self.state, self.root), service["ledger_checkpoint"])
        job = store.job(service["current_job"]) if service["current_job"] else None
        if job:
            if job["state"] == "stopped" or stop_request(self.state, job["id"]):
                request_stop(self.state, service["id"], "An operator stopped the active child job")
                raise CollectionStopped("Active child was stopped")
            # A crash may cross a period boundary. Recover only this child's
            # owned stale session before retiring its immutable finite scope.
            store.recover_session(job)
            self._advance(service, job)
            if job["state"] == "complete" or job["operating_period"] != period:
                if job["state"] != "complete":
                    job.update(state="period_closed", issue={"code": "operating_period_ended",
                               "message": "This period ended; unfinished work remains recorded for the service cursor"})
                    job.setdefault("clock", {}).setdefault("ended_at", timestamp())
                    store.save(job)
                service.update(last_job=job["id"], current_job=None)
                job = None
        measured = self._storage()
        service["managed_state_bytes"] = measured
        if measured > budget.policy["state_bytes"]:
            service.update(state="storage_limited", issue={"code": "operating_storage_limit",
                           "message": "Managed evidence/WAL/accounting exceeds the configured admission threshold; no evidence was deleted"}, next_attempt_at=None)
            store.save_service(service)
            return service
        if job is None:
            low = service["next_height"]
            job = store.create(low, low + budget.policy["job_heights"] - 1, batch_size=1,
                               max_bytes=budget.policy["response_bytes"], max_requests=budget.policy["requests"],
                               max_attempts=budget.policy["body_attempts"],
                               max_seconds=budget.policy["active_seconds_per_update"],
                               segment_seconds=budget.policy["segment_seconds"],
                               wall_seconds=max(1, int(budget.remaining_seconds())),
                               poll_seconds=budget.policy["poll_seconds"],
                               backoff_max_seconds=budget.policy["backoff_max_seconds"], service=service)
        self.previous_block = copy.deepcopy(service["anchor"])
        self.operating_budget = budget
        self.cache_directory = self.state / "service-blocks" / service["id"]
        self.cache_bytes = budget.policy["cache_bytes"]
        self.active_child = job["id"]
        # Recheck storage and operating state between bounded updates, even
        # when a child covers several heights.
        job = self._run(store, job["id"], max_updates=1)
        self._check_control(force=True)
        self.active_child = None
        self._advance(service, job)
        service["clock"]["max_seen_time"] = max(service["clock"]["max_seen_time"], budget.last_seen, time.time())
        if job["tip_observations"]:
            service["latest_tip"] = job["tip_observations"][-1]
        issue = job.get("issue") or {}
        service["issue"] = issue or None
        if job["state"] == "complete":
            service.update(state="running", last_job=job["id"], current_job=None,
                           next_attempt_at=time.time(), consecutive_failures=0)
        elif (job["state"] == "incomplete" and not issue
              and any(u["state"] == "complete" for u in job["updates"])
              and all(u["state"] in ("complete", "queued") for u in job["updates"])):
            service.update(state="running", next_attempt_at=time.time(), consecutive_failures=0)
        elif job["state"] == "waiting":
            service.update(state="waiting", consecutive_failures=0,
                           next_attempt_at=min(time.time() + budget.policy["poll_seconds"], budget.end))
        elif issue.get("code") in ("operating_limit", "operating_period_ended", "collection_window_expired", "budget_limit", "collection_limit"):
            service.update(state="limited", next_attempt_at=budget.end)
        else:
            codes = [a.get("issue", {}).get("code") for a in job["associations"].values() if a.get("state") != "complete"]
            transient = issue.get("code") == "source_unavailable" or (
                issue.get("code") == "unresolved_associations" and codes
                and all(code in ("source_unavailable", "invalid_response") for code in codes))
            if transient:
                failures = service["consecutive_failures"] + 1
                wait = min(budget.policy["backoff_max_seconds"], budget.policy["poll_seconds"] * 2 ** min(failures, 10))
                service.update(state="incomplete", consecutive_failures=failures,
                               next_attempt_at=min(time.time() + wait, budget.end))
            else:
                service.update(state="blocked", next_attempt_at=None)
        service["managed_state_bytes"] = self._storage()
        if service["managed_state_bytes"] > budget.policy["state_bytes"]:
            service.update(state="storage_limited", issue={"code": "operating_storage_limit",
                           "message": "Managed storage crossed the admission threshold; completed evidence is retained"}, next_attempt_at=None)
        store.save_service(service)
        self.event({"stage": "service_update", "service": service["id"], "state": service["state"],
                    "next_height": service["next_height"], "period": period})
        return service

    def run(self, identity, *, max_cycles=None, sleep=time.sleep):
        if (self.state / "restore-guard.json").exists():
            raise DataError("restored_state_requires_review", "Reconcile restored accounting and controls before resuming acquisition")
        if max_cycles is not None and (isinstance(max_cycles, bool) or not isinstance(max_cycles, int) or max_cycles < 1):
            raise DataError("invalid_collection", "The service cycle limit must be a positive integer")
        with RunnerLock(self.state), CollectionStore(self.state) as store:
            service = store.service(identity)
            if service["state"] in ("stopped", "blocked", "storage_limited"):
                return service
            if stop_request(self.state, identity):
                return self._acknowledge(store, identity)
            self.service_store = store
            self.control_job = identity
            self.last_control_poll = float("-inf")
            self.runner = RunnerRecord(self.state, identity, kind="service")
            try:
                cycles = 0
                while max_cycles is None or cycles < max_cycles:
                    self._check_control(force=True)
                    service = store.service(identity)
                    if service["clock"] and time.time() < service["clock"]["max_seen_time"] - 1:
                        raise DataError("operating_clock_changed", "Clock moved backwards before a saved wait; work is suspended")
                    if service["next_attempt_at"]:
                        wait = max(0, service["next_attempt_at"] - time.time())
                        if sleep is time.sleep:
                            until = time.monotonic() + wait
                            while time.monotonic() < until:
                                self._check_control(force=True)
                                sleep(min(CONTROL_POLL_SECONDS, max(0, until - time.monotonic())))
                        elif wait:
                            sleep(wait)
                        self._check_control(force=True)
                    try:
                        service = self._cycle(store, service)
                    except OperatingLimit as exc:
                        # A period can end between the clock check and child
                        # admission. Preserve the cursor and wait for renewal.
                        service.update(state="limited", issue={"code": exc.code, "message": str(exc)},
                                       next_attempt_at=service["clock"]["armed_at"] +
                                       (service["clock"]["period"] + 1) * service["policy"]["period_seconds"])
                        store.save_service(service)
                    cycles += 1
                    if service["state"] in ("blocked", "storage_limited", "stopped"):
                        return service
                return service
            except KeyboardInterrupt:
                request_stop(self.state, identity, "Operator interrupted continuous collection")
                return self._acknowledge(store, identity)
            except CollectionStopped:
                return self._acknowledge(store, identity)
            except (DataError, OSError, ValueError, KeyError, TypeError) as exc:
                service = store.service(identity)
                service.update(state="blocked", issue={"code": getattr(exc, "code", "service_error"),
                               "message": str(exc)}, next_attempt_at=None)
                store.save_service(service)
                return service
            finally:
                self.runner.close()
                self.runner = None
                self.control_job = None
                self.service_store = None
                self.active_child = None
                self.operating_budget = None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Opt-in ongoing collector; creating a policy does not start acquisition")
    parser.add_argument("--state-dir", type=Path, default=Path(__file__).resolve().parent.parent / ".hemi_lookup_data")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="Save an explicit renewable policy without source calls")
    create.add_argument("--start", type=int, required=True)
    create.add_argument("--policy", type=Path, required=True)
    run = sub.add_parser("run", help="Explicitly start/recover a configured service")
    run.add_argument("service")
    run.add_argument("--max-cycles", type=int)
    stop = sub.add_parser("stop", help="Persist a terminal operator stop")
    stop.add_argument("service")
    stop.add_argument("--reason", default="Stopped by operator")
    status = sub.add_parser("status", help="Read a saved service without source calls")
    status.add_argument("service")
    args = parser.parse_args(argv)
    service = CollectionService(Path(__file__).resolve().parent.parent, args.state_dir,
                                event=lambda value: print(json.dumps(value), flush=True))
    try:
        if args.command == "create":
            result = service.create(args.start, json.loads(args.policy.read_text(encoding="utf-8")))
        elif args.command == "run":
            result = service.run(args.service, max_cycles=args.max_cycles)
        elif args.command == "stop":
            result = service.stop(args.service, args.reason)
        else:
            result = CollectionStore.read_service(args.state_dir, args.service)
        print(json.dumps(result, indent=2))
        return 2 if result["state"] in ("blocked", "storage_limited") else 0
    except (DataError, OSError, ValueError) as exc:
        print(f"Collection service could not proceed: {exc}", flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
