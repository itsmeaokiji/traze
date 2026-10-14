"""Offline checks recompute evidence; receipt success flags are never trusted."""

from .bitcoin import Reader, parse_transaction, hex32, sha256d, display_hash, verify_branch, header_pow, keystone_bytes, origin_from_input
from .errors import DataError
from .inputs import HASH_INPUT, covering

SCHEMA = "hemi-publication-receipt-v1"


def same_header(a, b):
    return (all(hex32(a[key]) == hex32(b[key]) for key in ("hash", "parentHash", "stateRoot", "transactionsRoot"))
            and all(int(a[key], 16) == int(b[key], 16) for key in ("number", "timestamp")))


def verify_proof(record, check=None):
    if check:
        check()
    cp = record["checkpoint"]
    if cp["height"] < 25 or cp["height"] % 25 or int(cp["header"]["number"], 16) != cp["height"]:
        raise DataError("invalid_proof", "Invalid publishing checkpoint height")
    attrs = cp["attributes_transaction"]
    if (attrs["type"] != "0x7e" or attrs["blockHash"] != cp["header"]["hash"]
            or attrs["to"].lower() != "0x4200000000000000000000000000000000000015"
            or attrs["from"].lower() != "0xdeaddeaddeaddeaddeaddeaddeaddeaddead0001"):
        raise DataError("invalid_proof", "Inconsistent L1-attributes input")
    previous = cp["previous_header"]["hash"] if cp["previous_header"] else "00" * 32
    if int(attrs["blockNumber"], 16) != cp["height"]:
        raise DataError("invalid_proof", "L1 attributes name a different checkpoint height")
    if cp["height"] > 25 and (not cp["previous_header"] or int(cp["previous_header"]["number"], 16) != cp["height"] - 25):
        raise DataError("invalid_proof", "Previous checkpoint metadata has the wrong height")
    payload = keystone_bytes(cp["header"], previous, origin_from_input(attrs["input"]))
    if payload.hex() != cp["payload_hex"] or display_hash(sha256d(payload)) != cp["keystone_id"]:
        raise DataError("invalid_proof", "Reconstructed checkpoint bytes/identifier differ")
    reader = Reader(bytes.fromhex(record["raw_transaction_hex"]), check)
    tx = parse_transaction(reader)
    if reader.pos != len(reader.data) or tx["txid"] != record["txid"] or tx["coinbase"]:
        raise DataError("invalid_proof", "Raw transaction ID/serialization differs")
    if not any(item.get("supported") and item["vout_index"] == record["vout_index"] and item["payload"] == payload for item in tx["candidates"]):
        raise DataError("invalid_proof", "Actual OP_RETURN output does not contain the reconstructed payload")
    header = bytes.fromhex(record["bitcoin_header_hex"])
    if len(header) != 80 or display_hash(sha256d(header)) != record["bitcoin_block_hash"] or not header_pow(header):
        raise DataError("invalid_proof", "Bitcoin header hash/stated target check failed")
    branch = record["merkle_siblings_display_hex"]
    position = record["tx_index"]
    if not isinstance(position, int) or position < 0 or len(branch) > 32:
        raise DataError("invalid_proof", "Invalid inclusion position/branch size")
    siblings = [hex32(item)[::-1] for item in branch]
    if not verify_branch(tx["hash"], position, siblings, header[36:68]):
        raise DataError("invalid_proof", "Bitcoin Merkle inclusion failed")
    path = record["ancestry"]
    if not path or int(path[-1]["number"], 16) != cp["height"] or not same_header(path[-1], cp["header"]):
        raise DataError("invalid_proof", "Ancestry does not reach the publishing checkpoint")
    for parent, child in zip(path, path[1:]):
        if check:
            check()
        if int(child["number"], 16) != int(parent["number"], 16) + 1 or child["parentHash"] != parent["hash"]:
            raise DataError("invalid_proof", "Ancestry parent/height link differs")
    if check:
        check()
    return {"txid": record["txid"], "publishing_checkpoint": cp["height"],
            "bitcoin_height": record["bitcoin_height"], "payload_and_inclusion_checks": "passed",
            "ancestry_links_checked": len(path) - 1}


def verify_receipt(receipt, check=None):
    if check:
        check()
    if receipt.get("schema") != SCHEMA or receipt.get("query", {}).get("hemi_chain_id") != 43111:
        raise DataError("invalid_receipt", "Unsupported receipt schema/network")
    proofs = receipt.get("proofs", [])
    if len(proofs) > 128:
        raise DataError("invalid_receipt", "Receipt has too many proofs")
    results = []
    query = receipt["query"]
    number = query["block_number"]
    kind = query.get("input_type", "number")
    requested_hash = query.get("requested_block_hash")
    if kind not in ("number", "hash") or query.get("bitcoin_network") != "mainnet":
        raise DataError("invalid_receipt", "Unsupported receipt input/network")
    if kind == "hash":
        if not isinstance(requested_hash, str) or not HASH_INPUT.fullmatch(requested_hash):
            raise DataError("invalid_receipt", "Receipt does not preserve a full requested block hash")
        if hex32(query.get("block_hash", "")) != hex32(requested_hash):
            raise DataError("invalid_receipt", "Resolved block differs from the requested hash")
    elif requested_hash is not None:
        raise DataError("invalid_receipt", "Hash request cannot be relabeled as number input")
    if number is None:
        if kind != "hash" or proofs or query.get("header") or query.get("covering_checkpoint"):
            raise DataError("invalid_receipt", "Only an unresolved hash request may omit block height without proof data")
        expected_cover = None
    else:
        expected_cover = covering(number)
        if query.get("header") and (int(query["header"]["number"], 16) != number
                or hex32(query["header"]["hash"]) != hex32(query["block_hash"])):
            raise DataError("invalid_receipt", "Requested block header differs from preserved input identity")
    if proofs and receipt["query"].get("covering_keystone") != expected_cover:
        raise DataError("invalid_receipt", "Reported covering checkpoint differs from the reviewed period rule")
    for record in proofs:
        if check:
            check()
        if record["checkpoint"]["height"] < expected_cover:
            raise DataError("invalid_proof", "Publication does not cover the requested block")
        if int(record["ancestry"][0]["number"], 16) != number or hex32(record["ancestry"][0]["hash"]) != hex32(query["block_hash"]):
            raise DataError("invalid_proof", "Proof is attached to a different requested Hemi block")
        if not same_header(record["ancestry"][0], receipt["query"]["header"]):
            raise DataError("invalid_proof", "Requested header metadata differs from the ancestry input")
        results.append(verify_proof(record, check))
    return {"status": "verified_against_provided_headers" if results else "no_positive_proof_to_verify",
            "proofs_checked": len(results), "checks": results, "network_requests": 0,
            "current_chain_membership_checked": False,
            "limits": ["Hemi header RLP/execution and Bitcoin header-chain consensus are not independently verified",
                       "Bitcoin height is source-reported; offline inclusion verifies the supplied header, not its global chain position",
                       "Negative coverage is a scan record, not a cryptographic non-inclusion proof",
                       "Receipt current-confidence fields are historical observations, not fresh verification"]}
