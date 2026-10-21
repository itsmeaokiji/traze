"""Offline checks for versioned Hemi anchor and Bitcoin interval evidence.

This package performs no source acquisition and does not select canonical chains.
"""

__version__ = "3.0.0"


def verify_packet(directory, *, expected_hemi_number, expected_hemi_hash, accepted_policy=None, accepted_context_sha256=None):
    """Verify versioned evidence; external policy/context acceptance is caller-owned."""
    from .api import verify_packet as verify

    return verify(directory, expected_hemi_number=expected_hemi_number,
                  expected_hemi_hash=expected_hemi_hash, accepted_policy=accepted_policy,
                  accepted_context_sha256=accepted_context_sha256)
