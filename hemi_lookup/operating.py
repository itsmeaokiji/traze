"""Explicit renewable service limits over the existing append-only run ledger."""

import copy
import hashlib
import json
import time
from pathlib import Path

from .errors import BudgetError, DataError
from .storage import HEADER_RESERVE, PRIOR_REPORTS, SAFETY_MARGIN


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def policy_settings(value):
    required = {"period_seconds", "response_bytes", "requests", "body_attempts", "cache_bytes", "state_bytes"}
    defaults = {"job_heights": 4, "poll_seconds": 60, "backoff_max_seconds": 600,
                "segment_seconds": 60, "active_seconds_per_update": 600}
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - defaults.keys():
        raise DataError("invalid_operating_policy", "Provide explicit period, response, request, body, cache and state limits; unknown settings are rejected")
    settings = {**defaults, **value}
    if any(isinstance(n, bool) or not isinstance(n, int) or n < 1 for n in settings.values()):
        raise DataError("invalid_operating_policy", "Operating settings must be positive whole numbers")
    if (not 60 <= settings["period_seconds"] <= 86400 or settings["job_heights"] > 200
            or not settings["poll_seconds"] <= settings["backoff_max_seconds"] <= 3600
            or not settings["segment_seconds"] <= settings["active_seconds_per_update"] <= 600
            or settings["response_bytes"] <= SAFETY_MARGIN + HEADER_RESERVE
            or settings["cache_bytes"] < 4_000_000):
        raise DataError("invalid_operating_policy", "Use supported finite work/polling bounds, response headroom and at least one maximum-size raw block of cache")
    return settings


def ledger_state(directory, prior_root=None):
    path = Path(directory) / "session.json"
    try:
        prior = {}
        if not path.exists() and prior_root:
            for name in PRIOR_REPORTS:
                report = Path(prior_root) / name
                if report.exists():
                    prior[name] = json.loads(report.read_text(encoding="utf-8"))["http_response_bytes"]
        state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
            "schema": 1, "prior_validation_bytes": prior, "downloaded_bytes": 0, "runs": []}
        if (state["schema"] != 1 or not isinstance(state["runs"], list)
                or not isinstance(state["prior_validation_bytes"], dict)
                or isinstance(state["downloaded_bytes"], bool) or not isinstance(state["downloaded_bytes"], int)
                or state["downloaded_bytes"] < 0):
            raise ValueError("Invalid ledger")
        return state
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise DataError("operating_ledger_changed", "Operating accounting could not be verified; preserve the ledger") from exc


def checkpoint(state):
    return {"prior_validation_bytes": copy.deepcopy(state["prior_validation_bytes"]),
            "runs": len(state["runs"]), "prefix": fingerprint(state["runs"]),
            "downloaded_bytes": state["downloaded_bytes"]}


def check_checkpoint(state, original):
    if (state["prior_validation_bytes"] != original["prior_validation_bytes"]
            or len(state["runs"]) < original["runs"]
            or fingerprint(state["runs"][:original["runs"]]) != original["prefix"]
            or state["downloaded_bytes"] < original["downloaded_bytes"]):
        raise DataError("operating_ledger_changed", "Existing accounting changed; service limits cannot be renewed by resetting the ledger")


class OperatingLimit(BudgetError):
    def __init__(self, message, code="operating_limit"):
        DataError.__init__(self, code, message)


class OperatingBudget:
    def __init__(self, service, period):
        self.identity = service["id"]
        self.ledger_directory = service.get('ledger_directory')
        self.policy = policy_settings(service["policy"])
        self.digest = fingerprint(self.policy)
        if self.digest != service["policy_digest"]:
            raise DataError("operating_policy_changed", "Saved operating policy changed; preserve the original policy and accounting")
        self.checkpoint = service["ledger_checkpoint"]
        self.period = period
        self.start = service["clock"]["armed_at"] + period * self.policy["period_seconds"]
        self.end = self.start + self.policy["period_seconds"]
        self.last_seen = service["clock"].get("max_seen_time", self.start)
        self.previous = {}

    def remaining_seconds(self):
        current = time.time()
        if current < self.last_seen - 1 or current < self.start:
            raise DataError("operating_clock_changed", "Clock moved backwards; operating work is suspended")
        self.last_seen = max(self.last_seen, current)
        if current >= self.end:
            raise OperatingLimit("Operating period ended; preserve progress for the next configured period", "operating_period_ended")
        return self.end - current

    def check_clock(self):
        self.remaining_seconds()

    def prepare(self, session):
        self.check_clock()
        if self.ledger_directory and str(session.directory) != self.ledger_directory:
            raise DataError('accounting_binding_changed', 'Operating Session uses a different lifetime ledger')
        check_checkpoint(session.state, self.checkpoint)
        expected = {"collection_service": self.identity, "operating_period": self.period,
                    "operating_policy": self.digest}
        if any(session.context.get(k) != v for k, v in expected.items()):
            raise DataError("operating_policy_changed", "Source session does not match the saved service policy")
        usage = {"response_bytes": 0, "requests": 0, "body_attempts": 0}
        for run in session.state["runs"]:
            context = run.get("context", {})
            if context.get("collection_service") != self.identity:
                continue
            period = context.get("operating_period")
            if (context.get("operating_policy") != self.digest or isinstance(period, bool)
                    or not isinstance(period, int) or not 0 <= period <= self.period):
                raise DataError("operating_ledger_changed", "Recorded operating periods or policy identity changed")
            if period == self.period:
                usage["response_bytes"] += run["received_bytes"]
                usage["requests"] += len(run["requests"])
                usage["body_attempts"] += run["body_attempts"]
        self.previous = usage
        # This is a ceiling for this operating session, not a reset of lifetime
        # totals or an amendment to a previous finite experiment's allowance.
        session.session_limit = session.total_bytes + max(0, self.policy["response_bytes"] - usage["response_bytes"])

    def before_request(self, session, amount, block):
        self.check_clock()
        usage = {"response_bytes": self.previous["response_bytes"] + session.run["received_bytes"],
                 "requests": self.previous["requests"] + len(session.run["requests"]),
                 "body_attempts": self.previous["body_attempts"] + session.run["body_attempts"]}
        if (usage["response_bytes"] + amount > self.policy["response_bytes"] - SAFETY_MARGIN
                or usage["requests"] + 1 > self.policy["requests"]
                or usage["body_attempts"] + int(block) > self.policy["body_attempts"]):
            raise OperatingLimit("Operating period allowance reached; no new source request admitted")
