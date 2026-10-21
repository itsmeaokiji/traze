"""Offline canonical-encoding and conservative assurance boundary checks."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from traze_evidence.bitcoin import Reader, merkle_root, publication_push, parse_transaction, sha256d
from traze_evidence.crypto import keccak256, rlp_decode, rlp_encode, rlp_uint, transaction_root
from traze_evidence.errors import VerificationError
from traze_evidence.verify import PROFILE, _read_file, _same_json, check_earlier_candidate, load_manifest, verify_packet


class CanonicalEncodingTests(unittest.TestCase):
    def test_legacy_keccak_known_answers(self):
        self.assertEqual(keccak256(b"").hex(), "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470")
        self.assertEqual(keccak256(b"abc").hex(), "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45")
        self.assertNotEqual(keccak256(b"abc"), hashlib.sha3_256(b"abc").digest())

    def test_rlp_canonical_vectors(self):
        vectors = [(b"", "80"), (0, "80"), (15, "0f"), (1024, "820400"),
                   (b"dog", "83646f67"), ([b"cat", b"dog"], "c88363617483646f67")]
        for value, encoded in vectors:
            with self.subTest(encoded=encoded):
                self.assertEqual(rlp_encode(value).hex(), encoded)
                self.assertEqual(rlp_encode(rlp_decode(bytes.fromhex(encoded))).hex(), encoded)

    def test_rlp_noncanonical_and_boundary_inputs_rejected(self):
        for encoded in ("8100", "b80100", "b9003800", "c28100", "c1820000", "8000", ""):
            with self.subTest(encoded=encoded), self.assertRaises(VerificationError):
                rlp_decode(bytes.fromhex(encoded))

    def test_rlp_integer_width_and_leading_zero_rejected(self):
        for value in (b"\0", b"\0\1", b"\1" * 9):
            with self.subTest(value=value), self.assertRaises(VerificationError):
                rlp_uint(value, bits=64)

    def test_rlp_depth_is_bounded(self):
        value = b""
        for _ in range(34):
            value = [value]
        with self.assertRaises(VerificationError) as caught:
            rlp_decode(rlp_encode(value))
        self.assertEqual(caught.exception.code, "rlp_depth")

    def test_empty_trie_known_answer(self):
        self.assertEqual(transaction_root([]).hex(),
                         "56e81f171bcc55a6ff8345e692c0f86e5b48e01b996cadc001622fb5e363b421")

    def test_transaction_trie_binds_raw_order_and_values(self):
        root = transaction_root([b"first", b"second"])
        self.assertNotEqual(root, transaction_root([b"second", b"first"]))
        self.assertNotEqual(root, transaction_root([b"first"]))
        self.assertNotEqual(root, transaction_root([b"first", b"changed"]))


class BitcoinBoundaryTests(unittest.TestCase):
    def test_compactsize_nonminimal_rejected(self):
        for raw in (b"\xfd\xfc\x00", b"\xfe\xff\xff\x00\x00", b"\xff" + bytes(8)):
            with self.subTest(raw=raw), self.assertRaises(VerificationError):
                Reader(raw).compact()

    def test_merkle_mutated_real_sibling_pair_rejected(self):
        leaf = sha256d(b"same transaction")
        with self.assertRaises(VerificationError) as caught:
            merkle_root([leaf, leaf])
        self.assertEqual(caught.exception.code, "bitcoin_merkle_mutated")
        self.assertEqual(len(merkle_root([sha256d(b"a"), sha256d(b"b"), leaf])), 32)

    def test_all_reviewed_pushdata_widths(self):
        data = b"HEMI" + b"\x01" + bytes(75)
        for push in (b"\x4c\x50", b"\x4d\x50\x00", b"\x4e\x50\x00\x00\x00"):
            with self.subTest(push=push):
                self.assertEqual(publication_push(b"\x6a" + push + data)["payload"], data[4:])
        self.assertIsNone(publication_push(b"\x6a\x01\x00\x4c\x50" + data))
        self.assertIsNone(publication_push(b"\x6a\x4c\x51" + data))

    def test_unsupported_hemi_push_is_preserved_as_unresolved(self):
        candidate = publication_push(b"\x6a\x4c\x50HEMI\x02" + bytes(75))
        self.assertFalse(candidate["supported"])

    def test_witness_stripping_keeps_outputs_and_txid(self):
        inputs = b"\x01" + b"\x11" * 32 + bytes(4) + b"\x00" + b"\xff" * 4
        script = b"\x6a\x4c\x50HEMI\x01" + bytes(75)
        outputs = b"\x01" + bytes(8) + bytes([len(script)]) + script
        legacy = b"\x02\x00\x00\x00" + inputs + outputs + bytes(4)
        witness = legacy[:4] + b"\x00\x01" + inputs + outputs + b"\x01\x01\x01" + bytes(4)
        first, second = parse_transaction(Reader(legacy)), parse_transaction(Reader(witness))
        self.assertEqual(first["hash"], second["hash"])
        self.assertEqual(first["candidates"], second["candidates"])
        self.assertEqual(second["candidates"][0]["payload"], b"\x01" + bytes(75))


class AssuranceBoundaryTests(unittest.TestCase):
    def test_unresolved_earlier_applicable_height_is_incomplete(self):
        for height in (125, 150, 100000):
            with self.subTest(height=height), self.assertRaises(VerificationError) as caught:
                check_earlier_candidate({"supported": True, "height": height}, 125)
            self.assertEqual(caught.exception.status, "incomplete")
            self.assertEqual(caught.exception.code, "unresolved_earlier_candidate")

    def test_old_or_nonkeystone_height_can_be_excluded(self):
        for height in (100, 124, 126):
            check_earlier_candidate({"supported": True, "height": height}, 125)

    def test_unknown_earlier_format_is_incomplete(self):
        with self.assertRaises(VerificationError) as caught:
            check_earlier_candidate({"supported": False}, 125)
        self.assertEqual(caught.exception.status, "incomplete")

    def test_profile_equality_preserves_json_types(self):
        for value in (True, 1.0):
            altered = dict(PROFILE, payload_version=value)
            self.assertFalse(_same_json(altered, PROFILE))

    def test_expected_query_cannot_use_boolean_number(self):
        with self.assertRaises(VerificationError) as caught:
            verify_packet("unused", expected_hemi_number=True, expected_hemi_hash="00" * 32)
        self.assertEqual(caught.exception.code, "packet_integer")

    def test_missing_manifest_is_incomplete(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(VerificationError) as caught:
                load_manifest(Path(temporary).resolve())
        self.assertEqual(caught.exception.status, "incomplete")

    def test_duplicate_json_keys_are_invalid(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "manifest.json").write_text('{"schema":1,"schema":2}', encoding="utf-8")
            with self.assertRaises(VerificationError) as caught:
                load_manifest(root.resolve())
        self.assertEqual(caught.exception.code, "packet_duplicate_key")

    def test_nonfinite_json_is_invalid(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "manifest.json").write_text('{"number":NaN}', encoding="utf-8")
            with self.assertRaises(VerificationError) as caught:
                load_manifest(root.resolve())
        self.assertEqual(caught.exception.code, "packet_json")


class FilesystemBoundaryTests(unittest.TestCase):
    def test_cyclic_path_is_a_structured_failure(self):
        root = Path.cwd().resolve()
        with patch.object(Path, "resolve", side_effect=RuntimeError("Symlink loop")):
            with self.assertRaises(VerificationError) as caught:
                _read_file(root, "manifest.json", 100, "manifest_missing")
        self.assertEqual(caught.exception.code, "packet_path")

    def test_unreadable_path_is_incomplete(self):
        root = Path.cwd().resolve()
        with patch.object(Path, "resolve", side_effect=PermissionError("Denied")):
            with self.assertRaises(VerificationError) as caught:
                _read_file(root, "manifest.json", 100, "manifest_missing")
        self.assertEqual(caught.exception.code, "packet_unreadable")
        self.assertEqual(caught.exception.status, "incomplete")


if __name__ == "__main__":
    unittest.main()
