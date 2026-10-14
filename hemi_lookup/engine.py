"""Generic bounded discovery with source-qualified verification and coverage."""

import copy
import struct
import time

from .bitcoin import parse_block, sha256d, display_hash, hex32, keystone_bytes, origin_from_input, merkle_branch, MAX_RAW_BLOCK
from .errors import DataError, BudgetError, DeadlineError
from .network import BTC_APIS, HEMI_RPC
from .proof import SCHEMA, verify_proof, same_header
from .storage import now
from .inputs import covering, block_input

HASH_KEYS = ("hash", "parentHash", "stateRoot", "transactionsRoot")


def intervals(blocks):
    groups = []
    for height in sorted(set(block["height"] for block in blocks)):
        if groups and height == groups[-1][1] + 1:
            groups[-1][1] = height
        else:
            groups.append([height, height])
    return groups


class Engine:
    def __init__(self, sources, session, cache, max_ancestry=256, no_cache=False, progress=None):
        self.sources, self.session, self.cache = sources, session, cache
        self.max_ancestry, self.no_cache = max_ancestry, no_cache
        self.progress = progress or (lambda message: None)
        self.headers, self.checkpoints = {}, {}
        self.requested = None
        self.receipt = None
        self.start = time.monotonic()
        self.timed_out = False

    def check_deadline(self):
        check = getattr(self.session, "check_deadline", None)
        if check:
            check()

    def expire(self, exc):
        r = self.receipt
        self.timed_out = True
        if not any(i["code"] == "time_budget_exhausted" for i in r["issues"]):
            self.issue("time_budget_exhausted", exc)
        r["termination"] = {"reason": "time_budget_exhausted", "mode": r["mode"]}
        if r["mode"] != "live_metadata_refresh":
            r["coverage"]["stop_reason"] = "time_budget_exhausted"
        context = r.setdefault("context", {"checked_at": None})
        context.update({"current_view_confirmed": False, "deadline_expired": True})
        identity = context.get("requested_block_identity")
        if identity:
            identity.update({"status": "unavailable", "reason": "Overall lookup deadline expired"})
        r.pop("bitcoin_tip_after", None)
        for proof in r["proofs"]:
            proof["current_bitcoin_depth_in_provider_view"] = None
        r["selection"]["current_proof_index"] = None
        r["selection"]["earliest_within_scanned_prefix"] = False

    def issue(self, code, message, **context):
        self.receipt["issues"].append({"code": code, "message": str(message), **context})

    def header(self, number, fresh=False):
        self.check_deadline()
        if not fresh and number in self.headers:
            return self.headers[number]
        data = self.sources.rpc("eth_getBlockByNumber", [hex(number), False])
        self.check_deadline()
        result = self.checked_header(data, expected_number=number)
        if not fresh:
            self.headers[number] = result
        return result

    def checked_header(self, data, expected_number=None, expected_hash=None):
        if not isinstance(data, dict):
            raise DataError("invalid_response", "Hemi block response is not an object")
        result = {key: data[key] for key in ("number", "hash", "parentHash", "stateRoot", "transactionsRoot", "timestamp")}
        number = int(result["number"], 16)
        if number < 0 or (expected_number is not None and number != expected_number):
            raise DataError("inconsistent_data", "Hemi response has an unexpected height")
        if int(result["timestamp"], 16) < 0:
            raise DataError("inconsistent_data", "Hemi response has an invalid timestamp")
        for key in HASH_KEYS:
            result[key] = "0x" + hex32(result[key]).hex()
        if expected_hash is not None and hex32(result["hash"]) != hex32(expected_hash):
            raise DataError("inconsistent_data", "Hemi hash lookup returned a different block")
        return result

    def identity_observation(self, expected, actual=None, error=None):
        identity = {"checked_at": now(), "requested_hash": expected["hash"],
                    "current_hash": actual["hash"] if actual else None,
                    "status": "unavailable", "scope": "current public Hemi RPC view"}
        if error:
            identity["reason"] = str(error)
        elif actual and hex32(actual["hash"]) != hex32(expected["hash"]):
            identity["status"] = "different_current_rpc_block"
        elif actual and same_header(expected, actual):
            identity["status"] = "matches_current_rpc_view"
        elif actual:
            identity["reason"] = "Matching hash has inconsistent critical header fields"
        return identity

    def resolve_requested(self, value):
        r = self.receipt
        if isinstance(value, int):
            return value, self.header(value)
        context = r.setdefault("context", {"current_view_confirmed": False})
        context["checked_at"] = now()
        context["requested_block_identity"] = {"checked_at": now(), "requested_hash": value,
            "current_hash": None, "status": "unavailable", "scope": "current public Hemi RPC view"}
        try:
            try:
                data = self.sources.rpc("eth_getBlockByHash", [value, False])
            except BudgetError:
                raise
            except DataError as exc:
                if exc.code in ("source_conflict", "wrong_network", "inconsistent_data"):
                    raise
                context["requested_block_identity"]["reason"] = str(exc)
                raise DataError("requested_block_unavailable", "The Hemi RPC could not return the requested block; hash preserved") from exc
            self.check_deadline()
            if data is None:
                raise DataError("requested_block_unavailable", "The Hemi RPC did not return the requested block; hash preserved")
            requested = self.checked_header(data, expected_hash=value)
            number = int(requested["number"], 16)
            covering(number)
            self.requested = number
            r["query"].update({"block_number": number, "block_hash": value, "header": requested})
            current = self.header(number, fresh=True)
            identity = self.identity_observation(requested, current)
            context["requested_block_identity"] = identity
            context["checked_at"] = identity["checked_at"]
            if identity["status"] != "matches_current_rpc_view":
                code = "requested_block_not_current" if identity["status"] == "different_current_rpc_block" else "inconsistent_data"
                raise DataError(code, "Requested block does not match the current Hemi RPC view; target preserved")
            self.headers[number] = requested
            return number, requested
        except DeadlineError:
            raise
        except (DataError, ValueError, KeyError, TypeError) as exc:
            context["checked_at"] = now()
            context["requested_block_identity"]["checked_at"] = context["checked_at"]
            context["requested_block_identity"].setdefault("reason", str(exc))
            raise

    def checkpoint(self, number):
        self.check_deadline()
        if number in self.checkpoints:
            return self.checkpoints[number]
        if number < 25 or number % 25:
            raise DataError("unsupported_input", "Outside the reviewed 25-block checkpoint configuration")
        if len(self.checkpoints) >= 60:
            raise BudgetError("Checkpoint reconstruction limit reached")
        header = self.header(number)
        previous = self.header(number - 25) if number > 25 else None
        attrs = self.sources.rpc("eth_getTransactionByBlockNumberAndIndex", [hex(number), "0x0"])
        self.check_deadline()
        if not isinstance(attrs, dict):
            raise DataError("invalid_response", "L1-attributes response is not an object")
        if (attrs.get("type") != "0x7e" or attrs.get("blockHash") != header["hash"]
                or int(attrs["blockNumber"], 16) != number
                or attrs.get("to", "").lower() != "0x4200000000000000000000000000000000000015"
                or attrs.get("from", "").lower() != "0xdeaddeaddeaddeaddeaddeaddeaddeaddead0001"):
            raise DataError("inconsistent_data", "L1 attributes do not belong to the observed Hemi checkpoint")
        origin = origin_from_input(attrs["input"])
        payload = keystone_bytes(header, previous["hash"] if previous else "00" * 32, origin)
        result = {"height": number, "header": header, "previous_header": previous,
                  "attributes_transaction": {key: attrs[key] for key in ("hash", "type", "to", "from", "input", "blockHash", "blockNumber")},
                  "ethereum_origin_height": origin, "payload_hex": payload.hex(), "keystone_id": display_hash(sha256d(payload))}
        self.checkpoints[number] = result
        return result

    def ancestry(self, number):
        self.check_deadline()
        distance = number - self.requested
        if distance < 0:
            raise DataError("not_applicable", "Earlier checkpoint cannot cover the requested block")
        if distance > self.max_ancestry:
            raise BudgetError("Ancestry-check budget reached; this is not a protocol inheritance limit")
        path = [self.header(height) for height in range(self.requested, number + 1)]
        for parent, child in zip(path, path[1:]):
            self.check_deadline()
            if child["parentHash"] != parent["hash"]:
                raise DataError("inconsistent_data", "Requested block is not linked to publishing checkpoint in the RPC view")
        return path

    def new_receipt(self, number, mode="live_lookup"):
        by_hash = isinstance(number, str)
        return {"schema": SCHEMA, "mode": mode, "started_at": now(),
            "query": {"block_number": None if by_hash else number, "input_type": "hash" if by_hash else "number",
                      **({"requested_block_hash": number, "block_hash": number} if by_hash else {}),
                      "hemi_chain_id": 43111, "bitcoin_network": "mainnet"},
            "rules": {"codec": "HEMI + 76-byte abbreviated keystone v1", "checkpoint_period": 25,
                      "heminetwork_revision": "bf771ced8cfd2fb782c3b0d6d0bc8ea87b1e7cc1",
                      "construction_reference": "539f4e33c02028c8a4207621cd04fa8102e372a0"},
            "sources": {"hemi_rpc": HEMI_RPC, "bitcoin_apis": list(BTC_APIS)},
            "coverage": {"blocks": [], "fully_scanned_intervals": [], "gaps": [], "unresolved_candidates": [],
                         "unsupported_payloads": 0, "extra_verified_proofs": [], "stop_reason": None},
            "candidate_observations": [], "proofs": [], "issues": [], "status": "incomplete",
            "selection": {"current_proof_index": None, "best_observed_proof_index": None,
                          "global_earliest_proven": False, "earliest_within_scanned_prefix": False},
            "assurance": {"bitcoin_full_spv_chain_validated": False, "expected_difficulty_transitions_validated": False,
                          "bitcoin_script_utxo_validation": False, "independent_hemi_execution": False,
                          "hemi_header_rlp_hashes_recomputed": False, "miner_reward_eligibility": False,
                          "finality_counter_equivalence": False,
                          "bitcoin_depth_formula": "source_tip_height - publication_height + 1",
                          "canonical_membership": "fresh public-provider hash-by-height / Hemi RPC view, not independent consensus",
                          "negative_coverage": "complete local body parsing/root-check record, not an exported non-inclusion proof"}}

    def lookup(self, number, explicit_range=None):
        number = block_input(number)
        self.requested = number if isinstance(number, int) else None
        self.receipt = self.new_receipt(number)
        r = self.receipt
        if isinstance(number, str):
            r["context"] = {"current_view_confirmed": False, "requested_block_identity": {
                "checked_at": now(), "requested_hash": number, "current_hash": None,
                "status": "unavailable", "scope": "current public Hemi RPC view",
                "reason": "Requested hash has not yet been resolved on Hemi mainnet"}}
        try:
            self.check_deadline()
            if int(self.sources.rpc("eth_chainId", []), 16) != 43111:
                raise DataError("wrong_network", "Hemi endpoint is not mainnet chain 43111")
            try:
                r["sources"]["hemi_client"] = self.sources.rpc("web3_clientVersion", [])
            except DeadlineError:
                raise
            except DataError as exc:
                self.issue(exc.code, exc)
            number, requested = self.resolve_requested(number)
            cover = covering(number)
            cp = self.checkpoint(cover)
            r["query"].update({"block_hash": requested["hash"], "header": requested,
                                "covering_keystone": cover, "covering_checkpoint": cp})
            self.ancestry(cover)
            tip_hash = self.sources.btc("/blocks/tip/hash", False)
            tip = self.sources.btc("/block/" + tip_hash)
            r["bitcoin_tip_before"] = {"height": tip["height"], "hash": tip_hash}
            stamp = int(cp["header"]["timestamp"], 16)
            if explicit_range:
                low, high = explicit_range
                if low < 0 or high < low or high > int(tip["height"]) or high - low + 1 > 200:
                    raise DataError("invalid_range", "Use a declared Bitcoin range of at most 200 existing heights; download caps still apply")
                r["coverage"]["plan"] = {"start": low, "initial_end": high, "mode": "explicit_range", "automatic_extension": False}
            else:
                hints = [self.sources.timestamp(stamp - 900), self.sources.timestamp(stamp), self.sources.timestamp(stamp + 7200)]
                low = max(0, min(item["lookup"]["height"] for item in hints) - 1)
                high = min(max(item["lookup"]["height"] for item in hints) + 1, int(tip["height"]))
                r["coverage"]["plan"] = {"start": low, "initial_end": high, "mode": "timestamp_guided",
                    "timestamp_hints": hints, "timestamp_start": stamp - 900, "timestamp_end": stamp + 7200,
                    "automatic_extension": True, "not_a_protocol_lower_bound": True}
            r["coverage"]["plan"]["maximum_body_attempts"] = self.session.max_blocks
            self.progress(f"Search Bitcoin {low}-{high}; requested Hemi {number}, covering checkpoint {cover}")
            height = low
            while height <= int(tip["height"]):
                self.check_deadline()
                if explicit_range and height > high:
                    r["coverage"]["stop_reason"] = "explicit_range_end"
                    break
                if not explicit_range and height > high:
                    if self.session.run["body_attempts"] >= self.session.max_blocks:
                        r["coverage"]["stop_reason"] = "body_attempt_budget"
                        break
                    high = min(high + 4, int(tip["height"]))
                    r["coverage"].setdefault("extensions", []).append({"direction": "forward", "new_end": high})
                    self.progress(f"No match in previous window; extending through Bitcoin {high}")
                try:
                    self.scan_height(height, cover)
                    if r["proofs"]:
                        r["coverage"]["stop_reason"] = "first_matching_height_fully_processed"
                        break
                except DeadlineError:
                    raise
                except BudgetError as exc:
                    self.issue(exc.code, exc, bitcoin_height=height)
                    r["coverage"]["stop_reason"] = "budget_limit"
                    break
                except (DataError, ValueError, KeyError) as exc:
                    r["coverage"]["gaps"].append({"height": height, "message": str(exc)})
                    self.progress(f"Gap at Bitcoin {height}: {exc}")
                height += 1
                if height > int(tip["height"]):
                    r["coverage"]["stop_reason"] = "observed_tip_reached"
            r["coverage"]["fully_scanned_intervals"] = intervals(r["coverage"]["blocks"])
            if r["proofs"] and not explicit_range and not any(p["relationship"] == "direct_to_covering_checkpoint" for p in r["proofs"]):
                # A small earlier search can correct a later-only observation.
                # It does not establish a consensus lower bound/global absence.
                for earlier in range(low - 1, max(-1, low - 3), -1):
                    try:
                        self.scan_height(earlier, cover)
                        r["coverage"].setdefault("extensions", []).append({"direction": "backward", "height": earlier})
                        if any(p["relationship"] == "direct_to_covering_checkpoint" for p in r["proofs"]):
                            break
                    except DeadlineError:
                        raise
                    except BudgetError as exc:
                        self.issue(exc.code, exc)
                        break
                    except DataError as exc:
                        r["coverage"]["gaps"].append({"height": earlier, "message": str(exc)})
            self.recheck()
        except DeadlineError as exc:
            self.expire(exc)
        except (DataError, ValueError, KeyError, TypeError, TimeoutError) as exc:
            self.issue(getattr(exc, "code", "source_unavailable"), exc)
            r["coverage"]["stop_reason"] = "lookup_incomplete"
            r.setdefault("context", {}).update({"current_view_confirmed": False, "error": str(exc)})
        return self.finish()

    def scan_height(self, height, cover):
        self.check_deadline()
        r = self.receipt
        block_hash = self.sources.btc("/block-height/" + str(height), False)
        hex32(block_hash)
        raw, origin, cached = None, None, False
        if not self.no_cache:
            try:
                raw = self.cache.get(block_hash)
                self.check_deadline()
            except DeadlineError:
                raise
            except DataError as exc:
                self.issue(exc.code, exc, bitcoin_height=height)
        if raw is not None:
            try:
                parsed = parse_block(raw, block_hash, self.check_deadline)
                cached, origin = True, "verified_local_body_cache"
                self.session.run["cache_reads"] += 1
            except DeadlineError:
                raise
            except DataError:
                raw = None
        if raw is None:
            self.check_deadline()
            raw, origin = self.sources.block_raw(block_hash, MAX_RAW_BLOCK)
            parsed = parse_block(raw, block_hash, self.check_deadline)
            self.check_deadline()
            self.cache.put(block_hash, raw)
        self.check_deadline()
        head = parsed["header"]
        previous = next((b for b in r["coverage"]["blocks"] if b["height"] == height - 1), None)
        following = next((b for b in r["coverage"]["blocks"] if b["height"] == height + 1), None)
        if previous and previous["height"] == height - 1 and display_hash(head[4:36]) != previous["hash"]:
            raise DataError("inconsistent_data", "Bitcoin headers disagree across adjacent scanned heights")
        if following and following["previous_hash"] != block_hash:
            raise DataError("inconsistent_data", "Backward extension does not link to the retained Bitcoin interval")
        meta = {"height": height, "hash": block_hash, "previous_hash": display_hash(head[4:36]),
                "header_hex": head.hex(), "timestamp": struct.unpack_from("<I", head, 68)[0],
                "transaction_count": parsed["tx_count"], "raw_bytes": len(raw), "cache_hit": cached,
                "source": origin, "full_transaction_root_checked": True, "hemi_candidates": len(parsed["candidates"])}
        r["coverage"]["blocks"].append(meta)
        for item in parsed["candidates"]:
            self.check_deadline()
            if not item["supported"]:
                r["coverage"]["unsupported_payloads"] += 1
                continue
            number = item["l2_height"]
            observation = {"bitcoin_height": height, "txid": item["tx"]["txid"], "publishing_height": number}
            if len(r["candidate_observations"]) < 256:
                r["candidate_observations"].append(observation)
            if number < cover or number % 25:
                continue
            if len(r["proofs"]) >= 16:
                if not any(i["code"] == "proof_retention_limit" for i in r["issues"]):
                    self.issue("proof_retention_limit", "16-proof retention cap reached; additional candidates are unresolved")
                    r["coverage"]["unresolved_candidates"].append({"bitcoin_height": height, "message": "Additional proof records not retained"})
                continue
            try:
                cp = self.checkpoint(number)
                if cp["payload_hex"] != item["payload"].hex():
                    observation["association"] = "does_not_match_current_hemi_rpc"
                    continue
                path = self.ancestry(number)
                tx = item["tx"]
                record = {"bitcoin_height": height, "bitcoin_block_hash": block_hash, "bitcoin_header_hex": head.hex(),
                    "txid": tx["txid"], "raw_transaction_hex": tx["raw"].hex(), "tx_index": item["tx_index"], "vout_index": item["vout_index"],
                    "merkle_siblings_display_hex": [display_hash(s) for s in merkle_branch(parsed["leaves"], item["tx_index"], self.check_deadline)],
                    "checkpoint": copy.deepcopy(cp), "ancestry": copy.deepcopy(path),
                    "relationship": "direct_to_covering_checkpoint" if number == cover else "later_checkpoint_support",
                    "verification": {"against_provided_headers": True}}
                verify_proof(record, self.check_deadline)
                r["proofs"].append(record)
                observation["association"] = "verified"
            except DeadlineError:
                raise
            except (DataError, KeyError, ValueError) as exc:
                r["coverage"]["unresolved_candidates"].append({**observation, "message": str(exc)})
        self.progress(f"Bitcoin {height}: {parsed['tx_count']} txs, {len(parsed['candidates'])} HEMI candidates, {len(r['proofs'])} matches" + (" (cache)" if cached else ""))

    def recheck(self):
        r = self.receipt
        r.pop("bitcoin_tip_after", None)
        context = {"checked_at": now(), "hemi_network_same": None, "hemi_headers": [], "bitcoin_blocks": [],
                   "bitcoin_secondary": [], "current_view_confirmed": False}
        r["context"] = context  # Fresh object: no previous successful flags reused.
        self.check_deadline()
        try:
            context["hemi_chain_id"] = int(self.sources.rpc("eth_chainId", []), 16)
            context["hemi_network_same"] = context["hemi_chain_id"] == 43111
        except DeadlineError:
            raise
        except (DataError, ValueError, TimeoutError) as exc:
            self.issue("network_recheck_unavailable", exc)
        wanted = {self.requested: r["query"]["header"]}
        wanted[r["query"]["covering_keystone"]] = r["query"]["covering_checkpoint"]["header"]
        for record in r["proofs"]:
            wanted[record["checkpoint"]["height"]] = record["checkpoint"]["header"]
            record["current_bitcoin_depth_in_provider_view"] = None
        for number, expected in wanted.items():
            self.check_deadline()
            try:
                current = self.header(number, True)
                same = same_header(current, expected)
                context["hemi_headers"].append({"height": number, "same": same})
                if number == self.requested:
                    context["requested_block_identity"] = self.identity_observation(expected, current)
            except DeadlineError:
                raise
            except (DataError, ValueError, KeyError, TimeoutError) as exc:
                context["hemi_headers"].append({"height": number, "same": None, "error": str(exc)})
                if number == self.requested:
                    context["requested_block_identity"] = self.identity_observation(expected, error=exc)
        hashes = {block["height"]: block["hash"] for block in r["coverage"]["blocks"]}
        for proof in r["proofs"]:
            hashes[proof["bitcoin_height"]] = proof["bitcoin_block_hash"]
        for height, expected in hashes.items():
            self.check_deadline()
            try:
                actual = self.sources.btc("/block-height/" + str(height), False)
                context["bitcoin_blocks"].append({"height": height, "same": actual == expected,
                                                  "source_api": getattr(self.sources, "last_btc_api", None)})
            except DeadlineError:
                raise
            except (DataError, TimeoutError) as exc:
                context["bitcoin_blocks"].append({"height": height, "same": None, "error": str(exc)})
        for height in sorted(set(proof["bitcoin_height"] for proof in r["proofs"])):
            self.check_deadline()
            try:
                actual = self.sources.btc("/block-height/" + str(height), False, secondary=True)
                context["bitcoin_secondary"].append({"height": height, "same": actual == hashes[height],
                                                     "source_api": getattr(self.sources, "last_btc_api", None)})
            except DeadlineError:
                raise
            except (DataError, TimeoutError) as exc:
                context["bitcoin_secondary"].append({"height": height, "same": None, "error": str(exc)})
        try:
            self.check_deadline()
            tip_hash = self.sources.btc("/blocks/tip/hash", False)
            tip = self.sources.btc("/block/" + tip_hash)
            r["bitcoin_tip_after"] = {"height": tip["height"], "hash": tip_hash}
            for proof in r["proofs"]:
                primary = next((item["same"] for item in context["bitcoin_blocks"] if item["height"] == proof["bitcoin_height"]), None)
                secondary = next((item["same"] for item in context["bitcoin_secondary"] if item["height"] == proof["bitcoin_height"]), None)
                if (primary is True and secondary is True and tip["height"] >= proof["bitcoin_height"]
                        and context.get("requested_block_identity", {}).get("status") == "matches_current_rpc_view"
                        and context["hemi_network_same"] is True):
                    proof["current_bitcoin_depth_in_provider_view"] = tip["height"] - proof["bitcoin_height"] + 1
        except DeadlineError:
            raise
        except (DataError, TimeoutError, KeyError) as exc:
            self.issue("tip_recheck_unavailable", exc)
        self.check_deadline()
        if context["hemi_network_same"] is not True and context.get("requested_block_identity"):
            context["requested_block_identity"].update({"status": "unavailable", "reason": "Hemi mainnet network identity is unconfirmed"})
        context["current_view_confirmed"] = (context["hemi_network_same"] is True
            and bool(context["hemi_headers"]) and all(item["same"] is True for item in context["hemi_headers"])
            and bool(context["bitcoin_blocks"]) and all(item["same"] is True for item in context["bitcoin_blocks"])
            and all(item["same"] is True for item in context["bitcoin_secondary"]))
        context["distinct_bitcoin_api_crosscheck"] = all(
            len({item.get("source_api") for group in (context["bitcoin_blocks"], context["bitcoin_secondary"])
                 for item in group if item["height"] == height and item.get("source_api")}) >= 2
            for height in {p["bitcoin_height"] for p in r["proofs"]}) if r["proofs"] else False

    def finish(self):
        r = self.receipt
        try:
            self.check_deadline()
        except DeadlineError as exc:
            self.expire(exc)
        provenance = getattr(self.sources, "provenance", None)
        if provenance:
            snapshot = provenance()
            r["source_recovery"] = snapshot
            r["sources"]["hemi_rpc_used"] = snapshot["hemi_rpc_used"]
            failure = snapshot.get("hemi_failure") or snapshot.get("bitcoin_failure")
            if failure:
                r.setdefault("context", {})["current_view_confirmed"] = False
                identity = r["context"].get("requested_block_identity")
                if snapshot.get("hemi_failure") and identity and identity["status"] == "matches_current_rpc_view":
                    identity.update({"status": "unavailable", "reason": failure["message"]})
                for proof in r["proofs"]: proof["current_bitcoin_depth_in_provider_view"] = None
                if not any(i["code"] == failure["code"] for i in r["issues"]):
                    self.issue(failure["code"], failure["message"])
        r["coverage"]["fully_scanned_intervals"] = intervals(r["coverage"]["blocks"])
        stable = r.get("context", {}).get("current_view_confirmed") is True
        clean = not r["coverage"]["gaps"] and not r["coverage"]["unresolved_candidates"] and not r["coverage"]["unsupported_payloads"]
        if r["proofs"]:
            best = min(range(len(r["proofs"])), key=lambda index: (r["proofs"][index]["bitcoin_height"], r["proofs"][index]["checkpoint"]["height"]))
            r["selection"]["best_observed_proof_index"] = best
            r["selection"]["current_proof_index"] = best if stable else None
            r["selection"]["earliest_within_scanned_prefix"] = stable and clean
            r["status"] = "found" if stable else "incomplete"
        else:
            stop = r["coverage"]["stop_reason"]
            complete_stop = stop in ("explicit_range_end", "observed_tip_reached")
            r["status"] = "not_found_within_coverage" if stable and clean and complete_stop else "incomplete"
        r["resources"] = self.session.metrics()
        r["resources"]["elapsed_seconds"] = round(time.monotonic() - self.start, 3)
        r["completed_at"] = now()
        return r

    def refresh(self, receipt):
        from .proof import verify_receipt
        verify_receipt(receipt, self.check_deadline)
        if "header" not in receipt["query"] or "covering_checkpoint" not in receipt["query"]:
            raise DataError("refresh_unavailable", "This unresolved receipt needs a new lookup")
        self.receipt = copy.deepcopy(receipt)
        self.requested = self.receipt["query"]["block_number"]
        self.receipt["mode"] = "live_metadata_refresh"
        self.receipt["previous_observation"] = {"completed_at": receipt.get("completed_at"), "context": receipt.get("context"),
                                                "bitcoin_tip_after": receipt.get("bitcoin_tip_after"),
                                                "source_recovery": receipt.get("source_recovery")}
        self.receipt.pop("source_recovery", None)
        self.receipt["sources"].pop("hemi_rpc_used", None)
        self.receipt["started_at"] = now()
        self.receipt["issues"] = []
        self.receipt.pop("termination", None)
        self.receipt.pop("bitcoin_tip_after", None)
        self.receipt["context"] = {"checked_at": None, "current_view_confirmed": False}
        self.receipt["selection"]["current_proof_index"] = None
        for proof in self.receipt["proofs"]:
            proof["current_bitcoin_depth_in_provider_view"] = None
        try:
            self.check_deadline()
            self.recheck()
        except DeadlineError as exc:
            self.expire(exc)
        return self.finish()
