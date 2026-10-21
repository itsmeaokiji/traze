"""Versioned history witnesses and explicitly conditional first-height assessment."""

from datetime import datetime
import hashlib
import re

from . import verify as interval
from .errors import VerificationError, require
from .secure_trie import account_membership, hex_bytes, rpc_quantity, storage_membership

SCHEMA = "traze-history-publication-evidence-v2"
RESULT_SCHEMA = "traze-history-verification-result-v2"
HISTORY_ADDRESS = "0x0000f90827f1c53a10cb7a02335b175320002935"
HISTORY_CODE_HASH = "0x6e49e66782037c0555897870e29fa5e552daf4719552131a0abce779daec0a5d"
HISTORY_WINDOW = 8191
POLICY_ID = "hemi-full-state-first-height-assumptions-v1"
PROFILE = {
    "id": "hemi-v1-rlp21-isthmus-history-first-height-v2",
    "interval_profile": interval.PROFILE["id"],
    "history_address": HISTORY_ADDRESS,
    "history_code_hash": HISTORY_CODE_HASH,
    "history_window": HISTORY_WINDOW,
    "history_trie": "secure-keccak-mpt-canonical-rlp",
    "lower_bound": "strict-anchor-fresh-parent-history",
    "model": "full-state-pre-output-zero-extended-labels-v1",
}
ASSUMPTIONS = (
    {"id": "selected_chain_validity",
     "description": "The selected Bitcoin branch is valid and the selected Hemi branch follows valid execution and rollup derivation under the declared rules."},
    {"id": "protected_history_rule",
     "description": "For every applicable Q at or after the covering keystone in the accepted rule scope, the deployed runtime persists, the correct parent update is active, and no later privileged write replaces it."},
    {"id": "publication_rule_applicability",
     "description": "The supported v1 publication predicate, period and same-branch descendant support apply throughout the claim scope; unreviewed upgrades are excluded."},
    {"id": "full_width_cycle_infeasibility",
     "description": "The reviewed full-width cross-chain commitment cycle is computationally infeasible. Its bounded classical random-oracle model does not supply measured historical work caps or a numerical confidence."},
)
_SLOT = re.compile(r"0x[0-9a-fA-F]{1,64}\Z")


def _observation(witness):
    source, stamp = witness["source"], witness["observed_at"]
    require(isinstance(source, str) and 0 < len(source) <= 2048,
            "history_observation", "Invalid recorded history source label")
    require(isinstance(stamp, str) and len(stamp) <= 128,
            "history_observation", "Invalid recorded history observation time")
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise VerificationError("history_observation", "Invalid recorded history observation time") from exc
    require(parsed.utcoffset() is not None, "history_observation", "History observation time needs a timezone")
    return {"source": source, "observed_at": stamp, "source_authenticity_verified": False,
            "fresh_canonical_membership_verified": False}


def _state_witness(witness, header, parent, anchor):
    interval._object(witness, ("number", "hash", "proof", "source", "observed_at"), "history witness")
    number = interval._integer(witness["number"], "history state number", minimum=1)
    claimed_hash = interval._hash(witness["hash"], "history state hash", hemi=True)
    require(number == header["number"] and bytes.fromhex(claimed_hash) == header["hash"],
            "history_state_identity", "History proof is attached to a different authenticated Hemi state")
    observation = _observation(witness)
    proof = interval._object(witness["proof"],
        ("address", "accountProof", "balance", "codeHash", "nonce", "storageHash", "storageProof"), "history account proof")
    address = hex_bytes(proof["address"], size=20)
    require(address == bytes.fromhex(HISTORY_ADDRESS[2:]),
            "history_address", "Unexpected history account address")
    account, account_trace = account_membership(header["fields"][3], address, proof["accountProof"])
    require(account is not None, "history_account_absent", "History account is authentically absent", incomplete=True)
    require(rpc_quantity(proof["nonce"], bits=64) == account["nonce"]
            and rpc_quantity(proof["balance"]) == account["balance"],
            "history_account_metadata", "RPC account quantities disagree with authenticated account bytes")
    require(hex_bytes(proof["storageHash"], size=32) == account["storage_root"]
            and hex_bytes(proof["codeHash"], size=32) == account["code_hash"],
            "history_account_metadata", "RPC account hashes disagree with authenticated account bytes")
    require(account["code_hash"] == bytes.fromhex(HISTORY_CODE_HASH[2:]),
            "history_runtime_unsupported", "Authenticated history runtime is outside the supported profile", incomplete=True)

    parent_slot = (number - 1) % HISTORY_WINDOW
    expected = {parent_slot: {"header": parent, "roles": ["immediate_parent"]}}
    # The anchor's own slot is optional corroboration, never a permanent record.
    if number - HISTORY_WINDOW <= anchor["number"] < number:
        slot = anchor["number"] % HISTORY_WINDOW
        if slot in expected:
            require(expected[slot]["header"]["number"] == anchor["number"],
                    "history_slot", "Ambiguous in-window history slot")
            expected[slot]["roles"].append("anchor")
        else:
            expected[slot] = {"header": anchor, "roles": ["anchor"]}
    rows = proof["storageProof"]
    require(isinstance(rows, list) and len(rows) <= 2,
            "history_storage_shape", "Invalid or oversized history storage proof list")
    seen, records = set(), []
    for row in rows:
        interval._object(row, ("key", "value", "proof"), "history storage proof")
        key = row["key"]
        require(isinstance(key, str) and _SLOT.fullmatch(key) is not None,
                "history_slot", "Invalid uint256 storage slot key")
        slot = int(key, 16)
        require(slot in expected and slot not in seen,
                "history_slot", "Unexpected, stale or duplicate history storage slot")
        seen.add(slot)
        value, trace = storage_membership(account["storage_root"], slot, row["proof"])
        require(value is not None, "history_record_absent", "Required history record is authentically absent", incomplete=True)
        require(value == expected[slot]["header"]["hash"],
                "history_record_hash", "Authenticated history value differs from the complete expected Hemi hash")
        require(rpc_quantity(row["value"]) == int.from_bytes(value, "big"),
                "history_storage_metadata", "RPC value disagrees with authenticated storage bytes")
        records.append({"slot": slot, "referenced_hemi_number": expected[slot]["header"]["number"],
                        "roles": expected[slot]["roles"], "full_hash": "0x" + value.hex(), **trace})
    require(parent_slot in seen, "history_parent_missing", "The immediate-parent storage witness is required", incomplete=True)
    return {"number": number, "hash": "0x" + header["hash"].hex(),
            "state_root": "0x" + header["fields"][3].hex(), "address": HISTORY_ADDRESS,
            "code_hash": "0x" + account["code_hash"].hex(),
            "storage_root": "0x" + account["storage_root"].hex(),
            "nonce": account["nonce"], "balance": account["balance"],
            "account_membership": account_trace, "records": records, "observation": observation}


def check_history(witnesses, headers, *, cover, publisher, anchor):
    """Check witnesses against already authenticated headers and route identities."""
    require(anchor < cover, "history_anchor_order",
            "The parent-based starting bound requires an anchor strictly before the covering keystone", incomplete=True)
    require(isinstance(witnesses, list) and len(witnesses) <= 2,
            "history_witness_shape", "Invalid or oversized history witness list")
    needed, seen, checked = {cover, publisher}, set(), []
    for witness in witnesses:
        require(isinstance(witness, dict), "history_witness_shape", "Invalid history witness object")
        number = interval._integer(witness.get("number"), "history state number", minimum=1)
        require(number in needed and number not in seen,
                "history_state_identity", "Unexpected or duplicate history state witness")
        seen.add(number)
        require(number in headers and number - 1 in headers and anchor in headers,
                "history_ancestry_missing", "History parent/anchor header is missing", incomplete=True)
        checked.append(_state_witness(witness, headers[number], headers[number - 1], headers[anchor]))
    missing = sorted(needed - seen)
    return {"status": "incomplete" if missing else "verified", "states": checked,
            "missing_hemi_states": missing, "required_hemi_states": sorted(needed),
            "all_applicable_states_empirically_checked": False,
            "general_rule_basis": "conditional_protocol_invariant_under_declared_execution_and_derivation_profile"}


def verify_history_packet(root, packet, manifest_digest, *, expected_hemi_number, expected_hemi_hash, accepted_policy):
    interval._object(packet, ("schema", "profile", "query", "interval_manifest_sha256", "history_witnesses"), "history manifest")
    query = interval._object(packet["query"], ("number", "hash"), "history query")
    number = interval._integer(query["number"], "history query number", minimum=1)
    wanted_hash = interval._hash(query["hash"], "history query hash", hemi=True)
    require(number == expected_hemi_number and bytes.fromhex(wanted_hash) == interval._hex(expected_hemi_hash, "expected Hemi hash", size=32),
            "query_mismatch", "History packet does not belong to the caller's expected Hemi block")
    expected_digest = interval._hash(packet["interval_manifest_sha256"], "interval manifest checksum")
    # Check the child path from the outer root before resolving it independently.
    raw = interval._read_file(root, "interval/manifest.json", interval.MAX_MANIFEST_BYTES, "interval_manifest_missing")
    require(hashlib.sha256(raw).hexdigest() == expected_digest,
            "interval_manifest_checksum", "Nested interval manifest checksum differs")
    base_packet, digest = interval.load_manifest(root / "interval")
    require(digest == expected_digest, "interval_manifest_checksum", "Nested interval manifest changed while being read")
    base = interval.verify_packet(root / "interval", expected_hemi_number=expected_hemi_number,
                                  expected_hemi_hash=expected_hemi_hash)
    require(base["manifest_sha256"] == digest, "interval_manifest_checksum", "Validated interval manifest differs from the bound manifest")
    result = {"schema": RESULT_SCHEMA, "verifier_version": "2.0.0", "status": "verified_history_evidence",
              "manifest_sha256": manifest_digest, "profile": PROFILE["id"], "query": dict(query),
              "interval_evidence": base, "history_evidence": {"status": "not_checked"},
              "first_height_assessment": {"status": "not_established", "bitcoin_height": None},
              "assurance": {"first_in_checked_interval": True, "first_height_under_accepted_assumptions": False,
                            "globally_first_proven": False, "pre_anchor_absence": "not_established",
                            "current_canonical_membership_verified": False,
                            "full_consensus_or_execution_verified": False,
                            "independent_finality_recalculated": False},
              "policy": {"id": POLICY_ID, "accepted_by_caller": accepted_policy == POLICY_ID,
                         "accepted_from_packet": False, "assumptions": [dict(item) for item in ASSUMPTIONS],
                         "useful_historical_work_envelope_measured": False,
                         "numerical_confidence": None, "claimed_system_security_bits": None},
              "scope": {"meaning": "first_applicable_bitcoin_block_height",
                        "applicable_keystones": "all_same_branch_Q_at_or_after_cover_under_the_supported_profile",
                        "restricted_to_found_publisher": False, "future_unreviewed_upgrades_covered": False,
                        "recorded_source_context_only": True, "fresh_source_check_performed": False},
              "issues": [], "network_requests": 0}
    try:
        require(interval._same_json(packet["profile"], PROFILE), "history_profile_unsupported",
                "Unsupported history/model profile; interval evidence remains separate", incomplete=True)
        cover = base["covering_keystone"]
        publisher = base["publication"]["keystone_number"]
        anchor = base["anchor_reference"]["hemi_number"]
        required = {anchor, cover, cover - 1, publisher, publisher - 1}
        headers = {h["number"]: interval.decode_header(h) for h in base_packet["hemi_headers"] if h["number"] in required}
        checked = check_history(packet["history_witnesses"], headers, cover=cover, publisher=publisher, anchor=anchor)
        result["history_evidence"] = checked
        if checked["missing_hemi_states"]:
            result["status"] = "incomplete"
            result["issues"].append({"code": "history_witness_missing", "status": "incomplete",
                                     "message": "Required covering/publishing history witnesses are missing"})
            return result
    except VerificationError as exc:
        result["status"] = exc.status
        result["history_evidence"] = {"status": exc.status, "code": exc.code, "message": str(exc)}
        result["issues"].append({"code": exc.code, "status": exc.status, "message": str(exc)})
        return result
    if accepted_policy != POLICY_ID:
        result["first_height_assessment"]["status"] = "policy_not_accepted"
        result["issues"].append({"code": "policy_not_accepted", "status": "not_accepted",
                                 "message": "History/interval evidence passed; the external assumptions were not accepted by the caller"})
        return result
    result["status"] = "verified_conditional_first_height"
    result["first_height_assessment"] = {
        "status": "conditional", "bitcoin_height": base["publication"]["bitcoin_height"],
        "covering_keystone": base["covering_keystone"], "policy_id": POLICY_ID,
        "label": "First applicable publication under supported rules",
        "qualification": "Complete interval checks plus a computational starting bound in the recorded chain view, under the caller-accepted execution, derivation, history and security assumptions"}
    result["assurance"].update(first_height_under_accepted_assumptions=True,
                               pre_anchor_absence="computational_inference_under_caller_accepted_assumptions")
    return result
