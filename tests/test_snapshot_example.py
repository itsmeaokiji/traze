"""The optional public mainnet snapshot example must verify from its own bytes."""
from pathlib import Path
import unittest

from traze_evidence import verify_packet
from traze_evidence.snapshot import POLICY_ID


class PublicSnapshotExampleTests(unittest.TestCase):
    def test_recorded_snapshot_example_matches_independently_declared_identities(self):
        root = Path(__file__).resolve().parent.parent
        result = verify_packet(root / 'examples/snapshot-first-height/packet',
            expected_hemi_number=5200117,
            expected_hemi_hash='0x2e9a22f7e0f7fa385b80c767ba5ab0e1939b140ea6a00780440933baeb6b48bd',
            accepted_policy=POLICY_ID,
            accepted_context_sha256='840184d1dab42688a714b71deba8938cb97958d48191bda32619586df4b03b34')
        self.assertEqual(result['status'], 'verified_conditional_first_height')
        self.assertEqual(result['manifest_sha256'], 'ee5852fa2eb7dfa74b45a6a35fad69e113ceb51cf0822744e4b4296e8616b8c7')
        self.assertEqual(result['first_height_assessment']['bitcoin_height'], 965045)
        self.assertEqual(result['context_evidence']['snapshot']['number'], 5558131)
        self.assertEqual(result['scope']['first_hemi_keystone'], 5200125)
        self.assertEqual(result['scope']['last_hemi_block'], 5558131)
        self.assertFalse(result['assurance']['globally_first_proven'])
        self.assertFalse(result['context_evidence']['historical_execution_certified'])
        self.assertFalse(result['context_evidence']['current_bitcoin_membership_verified'])
        self.assertEqual(result['network_requests'], 0)


if __name__ == '__main__':
    unittest.main()
