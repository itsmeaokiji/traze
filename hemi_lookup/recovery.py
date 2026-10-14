"""One-way Hemi recovery with replayed critical observations and sticky conflicts."""

import copy

from .bitcoin import hex32
from .errors import DataError, BudgetError
from .storage import now


def fingerprint(method, value):
    try:
        if method == "eth_chainId": return int(value, 16)
        if method in ("eth_getBlockByNumber", "eth_getBlockByHash"):
            return {**{k: hex32(value[k]).hex() for k in ("hash", "parentHash", "stateRoot", "transactionsRoot")},
                    "number": int(value["number"], 16), "timestamp": int(value["timestamp"], 16)}
        if method == "eth_getTransactionByBlockNumberAndIndex":
            return {**{k: hex32(value[k]).hex() for k in ("hash", "blockHash")},
                    **{k: value[k].lower() for k in ("from", "to", "input")},
                    "type": int(value["type"], 16), "blockNumber": int(value["blockNumber"], 16)}
        return None  # Client versions may differ; they are not chain identity.
    except (DataError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise DataError("invalid_response", "Hemi RPC lacks valid critical metadata fields") from exc


class HemiRecovery:
    def __init__(self, endpoints, fetch, check):
        self.endpoints, self.fetch, self.check = endpoints, fetch, check
        self.active = endpoints[0]
        self.journal, self.events = {}, []
        self.failure = None
        self.switched = False
        self.attempted = False

    def event(self, kind, **fields):
        self.events.append({"at": now(), "kind": kind, **fields})

    def block(self, code, message, **context):
        self.failure = {"code": code, "message": message, **context}
        self.event("hemi_source_rejected", **self.failure)
        raise DataError(code, message)

    def observe(self, method, params, value):
        fp = fingerprint(method, value)
        if method == "eth_chainId" and fp != 43111:
            self.block("wrong_network", "Hemi source does not report mainnet 43111", source=self.active)
        if fp is not None:
            key = (method, tuple(params))
            existing = self.journal.get(key)
            if existing and existing["fingerprint"] != fp:
                # Return this changed observation once so Engine can identify
                # an exact requested-block mismatch; later reads stay blocked.
                self.failure = {"code": "source_conflict", "message": "Hemi critical metadata changed during this operation", "source": self.active}
                self.event("hemi_observation_changed", method=method, params=params, source=self.active)
            else:
                item = self.journal.setdefault(key, {"method": method, "params": list(params), "fingerprint": fp,
                    "first_source": self.active, "checked_sources": []})
                if self.active not in item["checked_sources"]: item["checked_sources"].append(self.active)
                item["last_source"] = self.active
        return value

    def switch(self, method, params, cause):
        self.attempted = True
        alternate = self.endpoints[1]
        self.event("hemi_primary_unavailable", source=self.active, method=method, reason=str(cause))
        self.check()
        chain = self.fetch(alternate, "eth_chainId", [])
        if fingerprint("eth_chainId", chain) != 43111:
            self.block("wrong_network", "Alternate Hemi source is not mainnet 43111", source=alternate)
        replayed = {}
        for key, item in self.journal.items():
            self.check()
            value = chain if item["method"] == "eth_chainId" else self.fetch(alternate, item["method"], item["params"])
            if fingerprint(item["method"], value) != item["fingerprint"]:
                self.block("source_conflict", "Alternate Hemi source disagrees with previously observed critical data",
                           source=alternate, method=item["method"], params=item["params"])
            replayed[key] = value
        self.check()
        old, self.active, self.switched = self.active, alternate, True
        for item in self.journal.values():
            if alternate not in item["checked_sources"]: item["checked_sources"].append(alternate)
            item["last_source"] = alternate
        self.event("hemi_fallback_activated", previous_source=old, source=alternate,
                   revalidated_observations=len(replayed), chain_id_checked=43111)
        key = (method, tuple(params))
        value = chain if method == "eth_chainId" else replayed[key] if key in replayed else self.fetch(alternate, method, params)
        return self.observe(method, params, value)

    def rpc(self, method, params):
        self.check()
        if self.failure: raise DataError(self.failure["code"], self.failure["message"])
        if method == "web3_clientVersion":
            return self.fetch(self.active, method, params)
        try:
            value = self.fetch(self.active, method, params)
            return self.observe(method, params, value)
        except BudgetError:
            raise  # Includes the overall deadline; never start recovery after it.
        except DataError as exc:
            if self.failure:
                raise
            if self.attempted:
                self.failure = {"code": exc.code, "message": str(exc), "source": self.active}
                self.event("hemi_alternate_unavailable", **self.failure)
                raise
            if exc.code not in ("source_unavailable", "invalid_response"): raise
            try:
                return self.switch(method, params, exc)
            except BudgetError:
                raise
            except DataError as failed:
                if not self.failure:
                    self.failure = {"code": failed.code, "message": str(failed), "source": self.endpoints[1]}
                    self.event("hemi_alternate_unavailable", **self.failure)
                raise

    def snapshot(self):
        return {"hemi_rpc_used": self.active, "hemi_fallback_used": self.switched,
                "hemi_events": copy.deepcopy(self.events), "hemi_failure": copy.deepcopy(self.failure),
                "hemi_observations": [{k: copy.deepcopy(v) for k, v in item.items() if k != "fingerprint"} for item in self.journal.values()],
                "independent_hemi_consensus_verified": False, "provider_backend_independence_established": False}
