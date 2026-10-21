"""Read-only dispatch for independently versioned evidence packet formats."""

from pathlib import Path

from .errors import VerificationError, require
from . import verify as interval
from .history import POLICY_ID, RESULT_SCHEMA as HISTORY_RESULT_SCHEMA, SCHEMA as HISTORY_SCHEMA, verify_history_packet
from .snapshot import POLICY_ID as SNAPSHOT_POLICY, RESULT_SCHEMA as SNAPSHOT_RESULT_SCHEMA, SCHEMA as SNAPSHOT_SCHEMA, verify_snapshot_packet


def verify_packet(directory, *, expected_hemi_number, expected_hemi_hash, accepted_policy=None, accepted_context_sha256=None):
    """Verify evidence; only the caller may accept a known external-assumption policy.

    An untrusted packet cannot supply accepted_policy. V1 results keep their
    original contract and never acquire a first-height assumption policy.
    """
    expected_hemi_number = interval._integer(expected_hemi_number, "expected Hemi number", minimum=1)
    interval._hex(expected_hemi_hash, "expected Hemi hash", size=32)
    require(accepted_policy is None or (type(accepted_policy) is str and accepted_policy in (POLICY_ID, SNAPSHOT_POLICY)),
            "assurance_policy", "Unsupported caller assurance policy")
    if accepted_context_sha256 is not None:
        interval._hash(accepted_context_sha256, 'caller-selected context checksum')
    try:
        root = Path(directory).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise VerificationError("packet_path", "Cannot resolve the packet directory") from exc
    packet, digest = interval.load_manifest(root)
    require(isinstance(packet, dict), "packet_shape", "Manifest must be an object")
    if packet.get('schema') == SNAPSHOT_SCHEMA:
        try:
            return verify_snapshot_packet(root, packet, digest, expected_hemi_number=expected_hemi_number,
                expected_hemi_hash=expected_hemi_hash, accepted_policy=accepted_policy,
                accepted_context_sha256=accepted_context_sha256)
        except VerificationError as exc:
            exc.result_schema = SNAPSHOT_RESULT_SCHEMA
            raise
    require(accepted_context_sha256 is None and accepted_policy != SNAPSHOT_POLICY,
            'snapshot_packet_required', 'The snapshot policy/context requires a version-3 snapshot packet')
    if packet.get("schema") == interval.SCHEMA:
        require(accepted_policy is None, "policy_requires_history_packet",
                "The accepted history policy requires a version-2 history packet")
        return interval.verify_packet(root, expected_hemi_number=expected_hemi_number,
                                      expected_hemi_hash=expected_hemi_hash)
    require(packet.get("schema") == HISTORY_SCHEMA, "packet_schema", "Unsupported evidence schema")
    try:
        return verify_history_packet(root, packet, digest, expected_hemi_number=expected_hemi_number,
                                     expected_hemi_hash=expected_hemi_hash, accepted_policy=accepted_policy)
    except VerificationError as exc:
        exc.result_schema = HISTORY_RESULT_SCHEMA
        raise
