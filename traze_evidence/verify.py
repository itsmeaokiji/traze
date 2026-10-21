"""Versioned full-header, anchor-reference and finite-coverage verification."""

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import stat

from .bitcoin import MAX_BLOCK_BYTES, parse_block, sha256d
from .crypto import keccak256, rlp_decode, rlp_uint, transaction_root
from .errors import VerificationError, require

SCHEMA = "traze-anchored-publication-evidence-v1"
RESULT_SCHEMA = "traze-anchored-verification-result-v1"
MAX_MANIFEST_BYTES = 2_000_000
MAX_HEADERS = 4096
MAX_BITCOIN_BLOCKS = 64
MAX_TOTAL_BODY_BYTES = 64_000_000
# Bitcoin Core v28.0, src/kernel/chainparams.cpp, mainnet BIP34Height.
MIN_BIP34_HEIGHT = 227931
PROFILE = {
    "id": "hemi-v1-rlp21-isthmus-anchored-interval-v1",
    "heminetwork_revision": "bf771ced8cfd2fb782c3b0d6d0bc8ea87b1e7cc1",
    "op_geth_reference": "539f4e33c02028c8a4207621cd04fa8102e372a0",
    "hemi_header_fields": 21,
    "keystone_period": 25,
    "payload_version": 1,
    "publication_set": "noncoinbase-v1-all-first-push-outputs",
    "inheritance": "same-branch-later-keystone-no-lag-limit",
    "l1_attributes": "098999be:176",
}
_HEX = re.compile(r"[0-9a-fA-F]*\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")


def _object(value, keys, label):
    require(isinstance(value, dict) and set(value) == set(keys),
            "packet_shape", f"Unsupported fields in {label}")
    return value


def _same_json(actual, expected):
    # Python otherwise considers True == 1 and 1.0 == 1.
    return json.dumps(actual, sort_keys=True, separators=(",", ":")) == json.dumps(
        expected, sort_keys=True, separators=(",", ":"))


def _integer(value, label, *, minimum=0, maximum=(1 << 32) - 1):
    require(type(value) is int and minimum <= value <= maximum,
            "packet_integer", f"Invalid {label}")
    return value


def _hex(value, label, *, size=None, maximum=MAX_MANIFEST_BYTES):
    require(isinstance(value, str), "packet_hex", f"Invalid {label}")
    text = value[2:] if value.startswith("0x") else value
    require(len(text) % 2 == 0 and len(text) <= maximum * 2 and _HEX.fullmatch(text) is not None,
            "packet_hex", f"Invalid {label}")
    raw = bytes.fromhex(text)
    require(size is None or len(raw) == size, "packet_hex", f"Wrong byte width for {label}")
    return raw


def _hash(value, label, *, hemi=False):
    require(isinstance(value, str), "packet_hash", f"Invalid {label}")
    if hemi:
        require(value.startswith("0x"), "packet_hash", f"Expected 0x-prefixed {label}")
        value = value[2:]
    require(_HASH.fullmatch(value) is not None, "packet_hash", f"Invalid canonical {label}")
    return value


def _read_file(root, relative, limit, missing_code):
    path = root / relative
    try:
        require(path.resolve().is_relative_to(root), "packet_path", "Evidence path escapes the packet directory")
        part = root
        for component in Path(relative).parts:
            part = part / component
            require(not part.is_symlink() and not (hasattr(part, "is_junction") and part.is_junction()),
                    "packet_path", "Symlink/junction evidence paths are unsupported")
        require(stat.S_ISREG(path.stat().st_mode), "packet_path", "Evidence must be a regular file")
        with path.open("rb") as handle:
            raw = handle.read(limit + 1)
    except FileNotFoundError as exc:
        raise VerificationError(missing_code, f"Required evidence is missing: {relative}", incomplete=True) from exc
    except OSError as exc:
        raise VerificationError("packet_unreadable", f"Cannot read evidence: {relative}", incomplete=True) from exc
    except (RuntimeError, ValueError) as exc:
        if isinstance(exc, VerificationError):
            raise
        raise VerificationError("packet_path", "Invalid or cyclic evidence path") from exc
    require(len(raw) <= limit, "packet_size", f"Evidence exceeds the byte limit: {relative}")
    return raw


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "packet_duplicate_key", "Duplicate JSON object key")
        result[key] = value
    return result


def load_manifest(root):
    raw = _read_file(root, "manifest.json", MAX_MANIFEST_BYTES, "manifest_missing")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON number")))
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, VerificationError):
            raise
        raise VerificationError("packet_json", "Invalid or excessively nested JSON manifest") from exc
    return value, hashlib.sha256(raw).hexdigest()


def decode_header(item):
    _object(item, ("number", "hash", "raw"), "Hemi header")
    number = _integer(item["number"], "Hemi header number", minimum=1)
    wanted = _hash(item["hash"], "Hemi header hash", hemi=True)
    raw = _hex(item["raw"], "Hemi header", maximum=2048)
    require(keccak256(raw).hex() == wanted, "hemi_header_hash", "Full Hemi header hash differs")
    fields = rlp_decode(raw)
    require(isinstance(fields, list) and len(fields) == 21 and all(isinstance(f, bytes) for f in fields),
            "hemi_header_format", "This profile requires exactly 21 scalar RLP header fields")
    widths = {0: 32, 1: 32, 2: 20, 3: 32, 4: 32, 5: 32, 6: 256,
              13: 32, 14: 8, 16: 32, 19: 32, 20: 32}
    require(all(len(fields[index]) == width for index, width in widths.items())
            and len(fields[12]) <= 32, "hemi_header_width", "Invalid Hemi header field width")
    for index in (7, 15):
        rlp_uint(fields[index])
    for index in (8, 9, 10, 11, 17, 18):
        rlp_uint(fields[index], bits=64)
    require(rlp_uint(fields[8], bits=64) == number, "hemi_header_number", "Hemi height differs from full header")
    return {"number": number, "hash": bytes.fromhex(wanted), "fields": fields}


def _typed(raw, kind, count):
    require(bool(raw) and raw[0] == kind, "hemi_transaction_type", "Unexpected authenticated transaction type")
    fields = rlp_decode(raw[1:])
    require(isinstance(fields, list) and len(fields) == count and all(isinstance(f, bytes) for f in fields),
            "hemi_transaction_format", "Invalid typed transaction fields")
    return fields


def decode_anchor(raw):
    fields = _typed(raw, 0x7C, 3)
    require(fields[0] == b"\x88" * 20, "anchor_recipient", "Unexpected Bitcoin Attributes recipient")
    rlp_uint(fields[1], bits=64)
    data = fields[2]
    require(len(data) >= 100 and data[:4].hex() == "c94f1cca",
            "anchor_calldata", "Unsupported Bitcoin Attributes selector")
    word = lambda offset: int.from_bytes(data[offset:offset + 32], "big")
    require(word(36) == 64, "anchor_calldata", "Invalid Bitcoin header-array offset")
    count = word(68)
    require(count <= 30 and len(data) == 100 + count * 160,
            "anchor_calldata", "Invalid Bitcoin header-array length")
    for index in range(count):
        require(word(100 + index * 32) == 32 * count + 128 * index,
                "anchor_calldata", "Invalid Bitcoin header element offset")
        start = 100 + 32 * count + 128 * index
        require(word(start) == 80 and not any(data[start + 112:start + 128]),
                "anchor_calldata", "Invalid Bitcoin header element length or padding")
    return data[4:36]


def publishing_payload(header, previous, raw_attributes):
    fields = _typed(raw_attributes, 0x7E, 8)
    require(len(fields[0]) == 32 and fields[1].hex() == "deaddeaddeaddeaddeaddeaddeaddeaddead0001"
            and fields[2].hex() == "4200000000000000000000000000000000000015",
            "l1_attributes_identity", "Invalid authenticated L1-attributes identity")
    for index in (3, 4):
        rlp_uint(fields[index])
    rlp_uint(fields[5], bits=64)
    require(rlp_uint(fields[6], bits=8) in (0, 1), "l1_attributes_flag", "Invalid system-transaction flag")
    data = fields[7]
    require(len(data) == 176 and data[:4].hex() == "098999be",
            "l1_attributes_format", "Unsupported L1-attributes version", incomplete=True)
    origin = int.from_bytes(data[28:36], "big")
    require(origin <= 0xFFFFFFFF, "payload_integer", "L1 origin does not fit the reviewed v1 codec", incomplete=True)
    return (b"\x01" + origin.to_bytes(4, "big") + header["number"].to_bytes(4, "big")
            + header["fields"][0][:11] + previous[:12] + header["fields"][3] + header["hash"][:12])


def _source_observations(observations, block_hashes):
    require(isinstance(observations, list) and len(observations) <= 128,
            "observation_shape", "Invalid source-observation list")
    identities = set()
    for observation in observations:
        _object(observation, ("source", "height", "hash", "observed_at"), "source observation")
        source = observation["source"]
        require(isinstance(source, str) and 0 < len(source) <= 2048,
                "observation_shape", "Invalid recorded source label")
        height = _integer(observation["height"], "observation height", minimum=1)
        observed_hash = _hash(observation["hash"], "observed Bitcoin hash")
        require((source, height) not in identities, "observation_duplicate", "Duplicate source/height observation")
        identities.add((source, height))
        require(height in block_hashes and observed_hash == block_hashes[height],
                "source_view_conflict", "Recorded source view conflicts with supplied Bitcoin headers", incomplete=True)
        try:
            require(isinstance(observation["observed_at"], str), "observation_time", "Invalid observation time")
            stamp = datetime.fromisoformat(observation["observed_at"].replace("Z", "+00:00"))
            require(stamp.utcoffset() is not None, "observation_time", "Observation time must include a timezone")
        except ValueError as exc:
            if isinstance(exc, VerificationError):
                raise
            raise VerificationError("observation_time", "Invalid observation time") from exc
    return {"records_checked": len(observations),
            "recorded_agreement": "consistent" if observations else "not_supplied",
            "source_authenticity_verified": False, "backend_independence_verified": False,
            "fresh_membership_checked": False}


def check_earlier_candidate(candidate, cover):
    """Conservatively reject an unresolved candidate after body authentication."""
    require(candidate["supported"], "unsupported_earlier_publication",
            "An earlier HEMI candidate uses an unsupported payload format", incomplete=True)
    require(candidate["height"] < cover or candidate["height"] % 25 != 0,
            "unresolved_earlier_candidate",
            "An earlier candidate could support the query and requires additional association evidence", incomplete=True)


def verify_packet(directory, *, expected_hemi_number, expected_hemi_hash):
    """Recompute supplied evidence. No network, cache, ledger or receipt flags are used.

    The caller supplies the query identity separately to prevent attaching a
    valid packet for one block to a different requested block.
    """
    expected_hemi_number = _integer(expected_hemi_number, "expected Hemi number", minimum=1)
    expected_hash = _hex(expected_hemi_hash, "expected Hemi hash", size=32)
    try:
        root = Path(directory).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise VerificationError("packet_path", "Cannot resolve the packet directory") from exc
    packet, packet_digest = load_manifest(root)
    _object(packet, ("schema", "profile", "networks", "query", "anchor", "publication",
                     "hemi_headers", "hemi_bodies", "bitcoin_blocks", "observations"), "manifest")
    require(packet["schema"] == SCHEMA, "packet_schema", "Unsupported evidence schema")
    require(_same_json(packet["profile"], PROFILE), "packet_profile", "Unsupported or altered rule profile")
    require(_same_json(packet["networks"], {"hemi_chain_id": 43111, "bitcoin": "mainnet"}),
            "packet_network", "Unsupported evidence networks")
    query = _object(packet["query"], ("number", "hash"), "query")
    query_number = _integer(query["number"], "query number", minimum=1)
    query_hash = _hash(query["hash"], "query hash", hemi=True)
    require(query_number == expected_hemi_number and bytes.fromhex(query_hash) == expected_hash,
            "query_mismatch", "Packet does not belong to the caller's expected Hemi block")
    cover = ((query_number + 24) // 25) * 25
    anchor = _object(packet["anchor"], ("hemi_number", "transaction_index", "bitcoin_height"), "anchor")
    anchor_number = _integer(anchor["hemi_number"], "anchor Hemi number", minimum=1)
    anchor_index = _integer(anchor["transaction_index"], "anchor transaction index", maximum=4095)
    low = _integer(anchor["bitcoin_height"], "anchor Bitcoin height", minimum=MIN_BIP34_HEIGHT)
    selected = _object(packet["publication"], ("keystone_number", "bitcoin_height", "txid", "tx_index", "vout"), "publication")
    publisher = _integer(selected["keystone_number"], "publishing keystone", minimum=25)
    high = _integer(selected["bitcoin_height"], "publication Bitcoin height", minimum=low)
    selected_txid = _hash(selected["txid"], "publication txid")
    selected_index = _integer(selected["tx_index"], "publication transaction index", maximum=400000)
    selected_output = _integer(selected["vout"], "publication output index", maximum=400000)
    require(anchor_number <= cover <= publisher and publisher % 25 == 0,
            "hemi_route", "Invalid anchor, covering or publishing relationship")
    start = min(anchor_number, query_number, publisher - 25 if publisher > 25 else query_number)
    require(publisher - start + 1 <= MAX_HEADERS and high - low + 1 <= MAX_BITCOIN_BLOCKS,
            "packet_scope", "Packet range exceeds the supported verification envelope")
    raw_headers = packet["hemi_headers"]
    require(isinstance(raw_headers, list) and len(raw_headers) == publisher - start + 1,
            "hemi_ancestry_missing", "Complete Hemi ancestry is required", incomplete=True)
    headers = {}
    previous = None
    for offset, item in enumerate(raw_headers):
        header = decode_header(item)
        require(header["number"] == start + offset, "hemi_ancestry_order", "Hemi headers are not contiguous and ordered")
        if previous is not None:
            require(header["fields"][0] == previous["hash"], "hemi_parent", "Full-hash Hemi parent link differs")
        headers[header["number"]] = header
        previous = header
    require(headers[query_number]["hash"] == expected_hash,
            "query_header", "Full target header differs from the expected hash")
    bodies = packet["hemi_bodies"]
    needed_bodies = {anchor_number, publisher}
    require(isinstance(bodies, list) and len(bodies) == len(needed_bodies),
            "hemi_body_missing", "Anchor and publishing full transaction bodies are required", incomplete=True)
    transactions = {}
    for body in bodies:
        _object(body, ("number", "transactions"), "Hemi body")
        number = _integer(body["number"], "Hemi body number", minimum=1)
        require(number in needed_bodies and number not in transactions, "hemi_body_identity", "Unexpected or duplicate Hemi body")
        values = body["transactions"]
        require(isinstance(values, list) and 0 < len(values) <= 4096, "hemi_body_format", "Invalid Hemi transaction body")
        raws = [_hex(value, "raw Hemi transaction") for value in values]
        require(all(raws), "hemi_body_format", "Empty raw Hemi transaction")
        require(transaction_root(raws) == headers[number]["fields"][4],
                "hemi_transaction_root", "Reconstructed full Hemi transaction root differs")
        transactions[number] = raws
    require(anchor_index < len(transactions[anchor_number]), "anchor_index", "Anchor transaction index is outside its body")
    tip = decode_anchor(transactions[anchor_number][anchor_index])
    previous_keystone = headers[publisher - 25]["hash"] if publisher > 25 else bytes(32)
    payload = publishing_payload(headers[publisher], previous_keystone, transactions[publisher][0])
    block_rows = packet["bitcoin_blocks"]
    require(isinstance(block_rows, list) and len(block_rows) == high - low + 1,
            "bitcoin_coverage_missing", "Every Bitcoin body in the declared interval is required", incomplete=True)
    total_size = 0
    for offset, row in enumerate(block_rows):
        _object(row, ("height", "hash", "sha256", "size", "transactions"), "Bitcoin body manifest")
        require(_integer(row["height"], "Bitcoin height", minimum=1) == low + offset,
                "bitcoin_coverage_order", "Bitcoin bodies are not contiguous and ordered")
        _hash(row["hash"], "Bitcoin block hash")
        _hash(row["sha256"], "Bitcoin body checksum")
        total_size += _integer(row["size"], "Bitcoin body size", minimum=81, maximum=MAX_BLOCK_BYTES)
        _integer(row["transactions"], "Bitcoin transaction count", minimum=1, maximum=400000)
    require(total_size <= MAX_TOTAL_BODY_BYTES, "packet_body_budget", "Aggregate Bitcoin bodies exceed the verification envelope")
    total_transactions, excluded_candidates, same_height_candidates = 0, 0, 0
    prior_header, positive = None, None
    block_hashes = {}
    for row in block_rows:
        relative = "bitcoin/" + row["hash"] + ".bin"
        raw = _read_file(root, relative, MAX_BLOCK_BYTES, "bitcoin_body_missing")
        require(len(raw) == row["size"] and hashlib.sha256(raw).hexdigest() == row["sha256"],
                "bitcoin_body_checksum", "Bitcoin body length or checksum differs")
        parsed = parse_block(raw, expected_hash=row["hash"], expected_height=row["height"])
        header, txs = parsed["header"], parsed["transactions"]
        require(len(txs) == row["transactions"], "bitcoin_transaction_count", "Recomputed transaction count differs")
        if prior_header is None:
            require(sha256d(header) == tip, "anchor_bitcoin_identity", "Authenticated Bitcoin reference differs from interval start")
        else:
            require(header[4:36] == sha256d(prior_header), "bitcoin_parent", "Bitcoin full-hash ancestry differs")
        prior_header = header
        block_hashes[row["height"]] = row["hash"]
        total_transactions += len(txs)
        for index, tx in enumerate(txs):
            for candidate in tx["candidates"]:
                if row["height"] < high:
                    check_earlier_candidate(candidate, cover)
                    excluded_candidates += 1
                else:
                    same_height_candidates += 1
                    if (index == selected_index and tx["txid"] == selected_txid
                            and candidate["vout"] == selected_output and candidate["supported"]
                            and candidate["payload"] == payload):
                        positive = {"bitcoin_height": high, "bitcoin_hash": row["hash"],
                                    "keystone_number": publisher, "txid": tx["txid"],
                                    "tx_index": index, "vout": selected_output}
    require(positive is not None, "publication_missing", "Selected Bitcoin output does not contain the authenticated keystone payload")
    observations = _source_observations(packet["observations"], block_hashes)
    return {
        "schema": RESULT_SCHEMA, "verifier_version": "1.0.0", "status": "verified_interval_evidence",
        "manifest_sha256": packet_digest, "profile": PROFILE["id"], "query": dict(query),
        "covering_keystone": cover, "publication": positive,
        "anchor_reference": {"hemi_number": anchor_number, "bitcoin_height": low,
                             "bitcoin_hash": block_hashes[low], "transaction_index": anchor_index,
                             "literal_reference_verified": True},
        "verified_interval": {"first_height": low, "last_height": high,
                              "earlier_absence_range": [low, high - 1] if low < high else None,
                              "earlier_absence_checked": True},
        "checks": {"full_hemi_header_hashes": len(headers), "hemi_parent_links": len(headers) - 1,
                   "full_hemi_transaction_roots": len(transactions),
                   "hemi_transactions": sum(map(len, transactions.values())),
                   "bitcoin_bodies": len(block_rows), "bitcoin_body_bytes": total_size,
                   "bitcoin_transactions": total_transactions,
                   "earlier_candidates_excluded": excluded_candidates,
                   "selected_height_candidates": same_height_candidates,
                   "payload_bytes": len(payload), "selected_inclusion": "full_body_merkle_reconstruction"},
        "source_observations": observations,
        "assurance": {"first_in_checked_interval": True, "globally_first_proven": False,
                      "pre_anchor_absence": "requires_separate_computational_starting_bound",
                      "starting_bound_model": "bounded-query-random-oracle-model-not-established-by-this-checker",
                      "historical_rule_applicability": "declared_profile_assumption",
                      "current_canonical_membership_verified": False,
                      "full_consensus_or_execution_verified": False,
                      "independent_finality_recalculated": False,
                      "complete_cofirst_transaction_set_verified": False,
                      "miner_reward_eligibility_verified": False},
        "network_requests": 0,
    }
