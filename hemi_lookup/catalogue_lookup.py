"""Adapt recorded catalogue evidence for the main browser workflow.

Number requests resolve only an unambiguous retained view, explicitly labelled.
Neither number nor hash retrieval confirms current chain membership. Discovery
remains the fallback; this module has no transport or accounting session.
"""

import copy
from itertools import chain
from pathlib import Path

from .bitcoin import display_hash, hex32, keystone_bytes, origin_from_input, sha256d
from .catalogue import Catalogue
from .errors import DataError
from .inputs import block_input
from .proof import same_header, verify_receipt


def valid_cover(checkpoint, expected_header, expected_height):
    """Check retained covering metadata before enabling the existing recheck."""
    if checkpoint["height"] != expected_height or not same_header(checkpoint["header"], expected_header):
        return False
    attrs, previous = checkpoint["attributes_transaction"], checkpoint["previous_header"]
    if (attrs["type"] != "0x7e" or hex32(attrs["blockHash"]) != hex32(expected_header["hash"])
            or int(attrs["blockNumber"], 16) != expected_height
            or attrs["from"].lower() != "0xdeaddeaddeaddeaddeaddeaddeaddeaddead0001"
            or attrs["to"].lower() != "0x4200000000000000000000000000000000000015"):
        return False
    if expected_height > 25 and (not previous or int(previous["number"], 16) != expected_height - 25):
        return False
    payload = keystone_bytes(expected_header, previous["hash"] if previous else "00" * 32, origin_from_input(attrs["input"]))
    return payload.hex() == checkpoint["payload_hex"] and display_hash(sha256d(payload)) == checkpoint["keystone_id"]


def covering_metadata(catalogue, packet):
    expected_height = packet["query"]["covering_keystone"]
    cover_headers = [header for proof in packet["proofs"] for header in proof["ancestry"]
                     if int(header["number"], 16) == expected_height]
    if not cover_headers or any(not same_header(cover_headers[0], header) for header in cover_headers):
        return None
    candidates = [proof["checkpoint"] for proof in packet["proofs"]]
    candidates.extend(source["observation"]["query"].get("covering_checkpoint")
                      for source in packet["coverage"]["historical_observations"])
    retained = (catalogue.read_json(row[0])["checkpoint"] for row in catalogue.db.execute("SELECT data FROM templates"))
    for candidate in chain(candidates, retained):
        if catalogue.check:
            catalogue.check()
        if not candidate:
            continue
        try:
            if valid_cover(candidate, cover_headers[0], expected_height):
                return copy.deepcopy(candidate)
        except (DataError, KeyError, TypeError, ValueError):
            continue
    return None


def recorded_lookup(database, requested, *, check=None):
    if not Path(database).is_file():
        return None
    requested = block_input(requested)
    with Catalogue(database, readonly=True, check=check) as catalogue:
        catalogue.db.execute("BEGIN")
        # Misses stay cheap even when the surrounding corpus grows. This is a
        # retained-header availability check, never a publication absence claim.
        if isinstance(requested, int):
            if not catalogue.db.execute("SELECT 1 FROM headers WHERE height=? LIMIT 1", (requested,)).fetchone():
                return None
        elif not catalogue.db.execute("SELECT 1 FROM headers WHERE hash=? LIMIT 1", (requested[2:],)).fetchone():
            return None
        resolved = catalogue.resolve_number(requested) if isinstance(requested, int) else requested
        packet = catalogue.query(resolved)
        if check and any(issue.get("code") == "conflicting_retained_headers" for issue in packet["issues"]):
            raise DataError("conflicting_retained_headers", "The exact hash has conflicting retained metadata; no current-view resolution is asserted")
        # Do not let absence/ambiguity in a small corpus replace generic discovery.
        if not packet["proofs"] or any(proof["bitcoin_height"] is None for proof in packet["proofs"]):
            return None
        checkpoint = covering_metadata(catalogue, packet)
    if isinstance(requested, int):
        packet["query"]["input_type"] = "number"
        packet["query"].pop("requested_block_hash", None)
        packet["query"]["number_resolution"] = "retained_view"
    if checkpoint:
        packet["query"]["covering_checkpoint"] = checkpoint
    else:
        packet["issues"].append({"code": "catalogue_recheck_unavailable",
                                 "message": "Recorded covering metadata is insufficient for source recheck; run a new lookup"})
    times = {source["observation"].get("context", {}).get("checked_at")
                    for source in packet["coverage"]["historical_observations"]
                    if isinstance(source["observation"].get("context", {}).get("checked_at"), str)}
    times.update(item["observed_at"] for source in packet["coverage"]["historical_observations"]
                 for item in source["observation"].get("metadata_observations", []) if isinstance(item.get("observed_at"), str))
    times = sorted(times)
    # Display bounds stay compact as indexed metadata grows. Individual RPC
    # observation times remain in the portable packet's coverage records.
    display_times = [times[0], times[-1]] if len(times) > 2 else times
    packet["catalogue"] = {"historical_source_times": display_times,
                           "historical_source_time_count": len(times),
                           "source_observation_range": {"first": times[0], "last": times[-1]} if times else None,
                           "number_resolved_from_retained_view": isinstance(requested, int)}
    recorded_sources = [source["observation"].get("sources", {}) for source in packet["coverage"]["historical_observations"]]
    hemi_urls = {source.get("hemi_rpc_used") or source.get("hemi_rpc") for source in recorded_sources}
    if len(hemi_urls) == 1:
        packet["sources"]["hemi_rpc"] = next(iter(hemi_urls))
    packet["sources"]["bitcoin_apis"] = sorted({url for source in recorded_sources for url in source.get("bitcoin_apis", [])})
    packet["resources"].update(response_bytes_this_run=0, bitcoin_body_attempts=0, cache_body_reads=0, http_requests=0)
    packet["offline_recomputation"] = verify_receipt(packet, check)
    return packet
