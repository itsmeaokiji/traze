"""Secure-trie, history-scope and caller-policy assurance boundaries."""

from copy import deepcopy
import unittest

from traze_evidence import verify_packet
from traze_evidence.crypto import keccak256, rlp_encode
from traze_evidence.errors import VerificationError
from traze_evidence.history import HISTORY_ADDRESS, HISTORY_CODE_HASH, _state_witness, check_history
from traze_evidence.secure_trie import (
    EMPTY_TRIE_ROOT, account_membership, rpc_quantity, storage_membership, trie_value,
)


def compact(path, leaf=True):
    items = [2 * int(leaf) + len(path) % 2] + ([] if len(path) % 2 else [0]) + list(path)
    return bytes(items[i] * 16 + items[i + 1] for i in range(0, len(items), 2))


def nibble(raw):
    return tuple(n for byte in raw for n in (byte >> 4, byte & 15))


def single(key, value):
    raw = rlp_encode([compact(nibble(key)), value])
    return keccak256(raw), ["0x" + raw.hex()]


def history_fixture(value=None, code_hash=HISTORY_CODE_HASH):
    number, anchor_number = 100, 50
    parent_hash = value if value is not None else b"\0\0" + b"\x32" * 30
    slot = (number - 1) % 8191
    storage_root, storage_proof = single(keccak256(slot.to_bytes(32, "big")),
                                         rlp_encode(int.from_bytes(parent_hash, "big")))
    address = bytes.fromhex(HISTORY_ADDRESS[2:])
    state_root, account_proof = single(keccak256(address),
        rlp_encode([1, 0, storage_root, bytes.fromhex(code_hash[2:])]))
    header = {"number": number, "hash": b"\x44" * 32,
              "fields": [parent_hash, b"", b"", state_root]}
    parent = {"number": number - 1, "hash": parent_hash}
    anchor = {"number": anchor_number, "hash": b"\x55" * 32}
    witness = {"number": number, "hash": "0x" + header["hash"].hex(),
               "source": "synthetic offline fixture", "observed_at": "2026-10-19T00:00:00+00:00",
               "proof": {"address": HISTORY_ADDRESS, "accountProof": account_proof,
                         "nonce": "0x1", "balance": "0x0", "codeHash": code_hash,
                         "storageHash": "0x" + storage_root.hex(),
                         "storageProof": [{"key": hex(slot), "value": hex(int.from_bytes(parent_hash, "big")),
                                           "proof": storage_proof}]}}
    return witness, header, parent, anchor


class SecureTrieTests(unittest.TestCase):
    def test_full_secure_leaf_and_authenticated_different_key_absence(self):
        key = b"\x12" * 32
        root, nodes = single(key, b"complete value")
        self.assertEqual(trie_value(root, key, nodes)[0], b"complete value")
        self.assertIsNone(trie_value(root, bytes(32), nodes)[0])

    def test_empty_trie_absence_is_not_a_missing_witness(self):
        self.assertIsNone(trie_value(EMPTY_TRIE_ROOT, bytes(32), [])[0])
        root, _ = single(bytes(32), b"value")
        with self.assertRaises(VerificationError) as caught:
            trie_value(root, bytes(32), [])
        self.assertEqual(caught.exception.status, "incomplete")

    def test_wrong_root_duplicate_unused_and_oversized_nodes_fail(self):
        key = b"\x12" * 32
        root, nodes = single(key, b"value")
        _, extra = single(b"\x33" * 32, b"other")
        for wanted, proof in ((bytes(32), nodes), (root, nodes + nodes),
                               (root, nodes + extra), (root, ["0x" + "00" * 32769])):
            with self.subTest(proof_length=len(proof)), self.assertRaises(VerificationError):
                trie_value(wanted, key, proof)

    def test_noncanonical_compact_padding_fails(self):
        raw = rlp_encode([b"\x21" + bytes(32), b"value"])
        with self.assertRaises(VerificationError) as caught:
            trie_value(keccak256(raw), bytes(32), ["0x" + raw.hex()])
        self.assertEqual(caught.exception.code, "trie_path")

    def test_inline_branch_and_leaf_children(self):
        key = b"\x11" * 32
        branch = [b""] * 17
        branch[1], branch[2] = [compact(()), b"a"], [compact(()), b"b"]
        raw = rlp_encode([compact(nibble(key)[:63], False), branch])
        value, facts = trie_value(keccak256(raw), key, ["0x" + raw.hex()])
        self.assertEqual(value, b"a")
        self.assertEqual(facts["proof_nodes"], 1)
        self.assertEqual(facts["visited_nodes"], 3)

    def test_hashed_children_order_independence_and_missing_child(self):
        key = b"\x11" * 32
        leaves = [rlp_encode([compact(()), bytes([n]) * 40]) for n in (1, 2)]
        branch = [b""] * 17
        branch[1], branch[2] = map(keccak256, leaves)
        branch_raw = rlp_encode(branch)
        root_raw = rlp_encode([compact(nibble(key)[:63], False), keccak256(branch_raw)])
        root = keccak256(root_raw)
        proof = ["0x" + raw.hex() for raw in (leaves[0], root_raw, branch_raw)]
        self.assertEqual(trie_value(root, key, proof)[0], b"\x01" * 40)
        with self.assertRaises(VerificationError) as caught:
            trie_value(root, key, proof[1:])
        self.assertEqual(caught.exception.status, "incomplete")

    def test_noncanonical_extension_to_leaf_fails(self):
        key = b"\x11" * 32
        child = [compact(nibble(key)[1:]), b"value"]
        child_raw = rlp_encode(child)
        raw = rlp_encode([compact(nibble(key)[:1], False), keccak256(child_raw)])
        with self.assertRaises(VerificationError) as caught:
            trie_value(keccak256(raw), key, ["0x" + n.hex() for n in (raw, child_raw)])
        self.assertEqual(caught.exception.code, "trie_extension")

    def test_noncanonical_single_entry_branch_fails(self):
        branch = [b""] * 17
        branch[0] = keccak256(rlp_encode([compact((0,) * 63), b"v"]))
        raw = rlp_encode(branch)
        with self.assertRaises(VerificationError) as caught:
            trie_value(keccak256(raw), bytes(32), ["0x" + raw.hex()])
        self.assertEqual(caught.exception.code, "trie_branch")

    def test_noncanonical_storage_integer_and_oversized_scalar_fail(self):
        slot = 12
        for scalar in (b"\0", b"\0\1", b"\x01" * 33):
            root, proof = single(keccak256(slot.to_bytes(32, "big")), rlp_encode(scalar))
            with self.subTest(width=len(scalar)), self.assertRaises(VerificationError):
                storage_membership(root, slot, proof)

    def test_account_tuple_and_integer_widths_are_strict(self):
        address = bytes(20)
        for fields in ([b""], [b"\0", b"", bytes(32), bytes(32)],
                       [b"\x01" * 9, b"", bytes(32), bytes(32)], [b"", b"", b"short", bytes(32)]):
            root, proof = single(keccak256(address), rlp_encode(fields))
            with self.subTest(fields=len(fields)), self.assertRaises(VerificationError):
                account_membership(root, address, proof)

    def test_rpc_quantity_does_not_accept_boolean_nonminimal_or_overflow(self):
        for value in (True, 1, "0x", "0x00", "0x01", "0x" + "1" * 65):
            with self.subTest(value=value), self.assertRaises(VerificationError):
                rpc_quantity(value)


class HistoryBoundaryTests(unittest.TestCase):
    def test_parent_only_witness_restores_leading_zeros(self):
        witness, header, parent, anchor = history_fixture()
        checked = _state_witness(witness, header, parent, anchor)
        self.assertEqual(checked["records"][0]["full_hash"], "0x" + parent["hash"].hex())
        self.assertEqual(checked["records"][0]["canonical_integer_bytes"], 30)

    def test_stale_anchor_slot_is_not_required(self):
        witness, header, parent, _ = history_fixture()
        header["number"] = witness["number"] = 10000
        parent["number"] = 9999
        slot = 9999 % 8191
        value = parent["hash"]
        storage_root, storage_proof = single(keccak256(slot.to_bytes(32, "big")), rlp_encode(int.from_bytes(value, "big")))
        state_root, account_proof = single(keccak256(bytes.fromhex(HISTORY_ADDRESS[2:])),
            rlp_encode([1, 0, storage_root, bytes.fromhex(HISTORY_CODE_HASH[2:])]))
        header["fields"][3] = state_root
        witness["proof"].update(accountProof=account_proof, storageHash="0x" + storage_root.hex(),
            storageProof=[{"key": hex(slot), "value": hex(int.from_bytes(value, "big")), "proof": storage_proof}])
        checked = _state_witness(witness, header, parent, {"number": 50, "hash": b"\x55" * 32})
        self.assertEqual(checked["records"][0]["roles"], ["immediate_parent"])

    def test_full_parent_mismatch_cannot_pass_with_correct_rpc_metadata(self):
        witness, header, parent, anchor = history_fixture()
        changed = dict(parent, hash=b"\x66" * 32)
        with self.assertRaises(VerificationError) as caught:
            _state_witness(witness, header, changed, anchor)
        self.assertEqual(caught.exception.code, "history_record_hash")

    def test_rpc_metadata_cannot_override_account_or_storage_membership(self):
        original, header, parent, anchor = history_fixture()
        for mutation in ("nonce", "balance", "codeHash", "storageHash", "value"):
            witness = deepcopy(original)
            if mutation == "value":
                witness["proof"]["storageProof"][0]["value"] = "0x1"
            else:
                witness["proof"][mutation] = "0x2" if mutation in ("nonce", "balance") else "0x" + "00" * 32
            with self.subTest(field=mutation), self.assertRaises(VerificationError) as caught:
                _state_witness(witness, header, parent, anchor)
            self.assertEqual(caught.exception.status, "invalid")

    def test_authentic_but_unknown_runtime_is_unsupported(self):
        witness, header, parent, anchor = history_fixture(code_hash="0x" + "77" * 32)
        with self.assertRaises(VerificationError) as caught:
            _state_witness(witness, header, parent, anchor)
        self.assertEqual(caught.exception.code, "history_runtime_unsupported")
        self.assertEqual(caught.exception.status, "incomplete")

    def test_missing_parent_is_incomplete(self):
        witness, header, parent, anchor = history_fixture()
        witness["proof"]["storageProof"] = []
        with self.assertRaises(VerificationError) as caught:
            _state_witness(witness, header, parent, anchor)
        self.assertEqual(caught.exception.code, "history_parent_missing")

    def test_anchor_equal_cover_cannot_use_direct_publication_parent_path(self):
        with self.assertRaises(VerificationError) as caught:
            check_history([], {}, cover=100, publisher=100, anchor=100)
        self.assertEqual(caught.exception.code, "history_anchor_order")

    def test_missing_state_reports_exact_needed_states(self):
        result = check_history([], {}, cover=100, publisher=200, anchor=50)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["missing_hemi_states"], [100, 200])

    def test_same_cover_and_publisher_need_one_state_not_two(self):
        witness, header, parent, anchor = history_fixture()
        headers = {100: header, 99: parent, 50: anchor}
        result = check_history([witness], headers, cover=100, publisher=100, anchor=50)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["required_hemi_states"], [100])

    def test_packet_supplied_policy_cannot_be_used_as_a_caller_policy(self):
        for policy in (True, {}, "unreviewed-policy"):
            with self.subTest(policy=policy), self.assertRaises(VerificationError) as caught:
                verify_packet("unused", expected_hemi_number=1, expected_hemi_hash="00" * 32,
                              accepted_policy=policy)
            self.assertEqual(caught.exception.code, "assurance_policy")


if __name__ == "__main__":
    unittest.main()
