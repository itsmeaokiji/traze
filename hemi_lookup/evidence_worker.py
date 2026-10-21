"""Bounded-process entry point for source-free packet verification."""

import json
from pathlib import Path
import sys

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from traze_evidence import verify_packet
    from traze_evidence.errors import VerificationError
    try:
        result = verify_packet(sys.argv[1], expected_hemi_number=int(sys.argv[2]),
                               expected_hemi_hash=sys.argv[3],
                               accepted_policy=None if sys.argv[4] == '-' else sys.argv[4],
                               accepted_context_sha256=None if len(sys.argv) < 6 or sys.argv[5] == '-' else sys.argv[5])
    except VerificationError as exc:
        result = {'status': exc.status, 'code': exc.code, 'message': str(exc),
                  'assurance': {'globally_first_proven': False,
                                'first_height_under_accepted_assumptions': False}, 'network_requests': 0}
    except Exception:
        result = {'status': 'incomplete', 'code': 'verification_unavailable',
                  'message': 'The evidence checker could not finish.', 'network_requests': 0}
    print(json.dumps(result, separators=(',', ':')))
