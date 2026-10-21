"""Read-only offline command line interface."""

import argparse
import json
import sys

from .errors import VerificationError
from .api import verify_packet
from .history import POLICY_ID, RESULT_SCHEMA as HISTORY_RESULT_SCHEMA
from .snapshot import POLICY_ID as SNAPSHOT_POLICY, RESULT_SCHEMA as SNAPSHOT_RESULT_SCHEMA
from .verify import RESULT_SCHEMA


def main(argv=None):
    parser = argparse.ArgumentParser(description="Verify a portable Hemi publication evidence packet offline (v1, v2 or v3).")
    parser.add_argument("packet", help="Directory containing a versioned manifest.json and its evidence")
    parser.add_argument("--hemi-block", type=int, required=True, help="Expected requested Hemi block number")
    parser.add_argument("--hemi-hash", required=True, help="Expected full requested Hemi block hash")
    parser.add_argument("--accept-policy", choices=(POLICY_ID, SNAPSHOT_POLICY), default=None,
                        help="Explicitly accept the matching versioned external assumptions; not proof of those premises")
    parser.add_argument('--accept-context', default=None, help='Independently selected canonical context SHA-256 for v3; packet fields cannot accept it')
    args = parser.parse_args(argv)
    try:
        result = verify_packet(args.packet, expected_hemi_number=args.hemi_block,
                               expected_hemi_hash=args.hemi_hash, accepted_policy=args.accept_policy,
                               accepted_context_sha256=args.accept_context)
    except VerificationError as exc:
        result = {"schema": getattr(exc, "result_schema", RESULT_SCHEMA), "status": exc.status, "code": exc.code,
                  "message": str(exc), "network_requests": 0,
                  "assurance": {"first_in_checked_interval": False, "globally_first_proven": False}}
        if result["schema"] in (HISTORY_RESULT_SCHEMA, SNAPSHOT_RESULT_SCHEMA):
            result["assurance"]["first_height_under_accepted_assumptions"] = False
            result["first_height_assessment"] = {"status": "not_established", "bitcoin_height": None}
        exit_code = 3 if exc.status == "incomplete" else 2
    else:
        exit_code = 3 if result["status"] == "incomplete" else 2 if result["status"] == "invalid" else 0
    print(json.dumps(result, indent=2))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
