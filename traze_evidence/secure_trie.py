"""Bounded canonical account/storage secure-MPT membership verification.

Roots and keys come from the caller's authenticated context, never from RPC
claims. This checks membership, not execution or selection of canonical chains.
"""

import re

from .crypto import keccak256, rlp_decode, rlp_encode, rlp_uint
from .errors import require

MAX_PROOF_NODES = 128
MAX_NODE_BYTES = 32_768
MAX_PROOF_BYTES = 200_000
EMPTY_TRIE_ROOT = keccak256(rlp_encode(b""))
_HEX_BYTES = re.compile(r"0x(?:[0-9a-fA-F]{2})*\Z")
_QUANTITY = re.compile(r"0x(?:0|[1-9a-fA-F][0-9a-fA-F]*)\Z")


def hex_bytes(value, *, size=None, limit=MAX_NODE_BYTES):
    require(isinstance(value, str) and len(value) <= 2 + 2 * limit
            and _HEX_BYTES.fullmatch(value) is not None,
            "history_hex", "Invalid or oversized 0x-prefixed byte string")
    raw = bytes.fromhex(value[2:])
    require(size is None or len(raw) == size, "history_width", "Wrong history byte-string width")
    return raw


def rpc_quantity(value, *, bits=256):
    require(isinstance(value, str) and len(value) <= 2 + (bits + 3) // 4
            and _QUANTITY.fullmatch(value) is not None,
            "history_quantity", "Invalid canonical RPC quantity")
    result = int(value, 16)
    require(result < 1 << bits, "history_quantity", "RPC quantity exceeds its field width")
    return result


def _nibbles(raw):
    return tuple(nibble for byte in raw for nibble in (byte >> 4, byte & 15))


def _compact(raw):
    require(isinstance(raw, bytes) and raw, "trie_path", "Empty compact trie path")
    values = _nibbles(raw)
    flag = values[0]
    require(flag < 4 and (flag & 1 or values[1] == 0),
            "trie_path", "Invalid or noncanonical compact trie path")
    path = values[1:] if flag & 1 else values[2:]
    leaf = bool(flag & 2)
    require(leaf or path, "trie_path", "An extension path cannot be empty")
    require(len(path) <= 64, "trie_path", "Trie path exceeds the secure-key width")
    return path, leaf


def _child(reference, *, empty=False):
    if isinstance(reference, bytes):
        valid = len(reference) == 32 or (empty and not reference)
    else:
        valid = isinstance(reference, list) and len(reference) in (2, 17) and len(rlp_encode(reference)) < 32
    require(valid, "trie_reference", "Invalid hashed or inline child reference")


def trie_value(root, secure_key, proof):
    """Return (authenticated value or None, traversal facts).

    Supports canonical geth-style proof arrays: hashed path nodes are supplied;
    short inline children are embedded in their parent. Array order is immaterial.
    Redundant, duplicate and unused nodes are rejected. Authenticated absence is
    distinct from a missing witness. The secure key is exactly 32 bytes.
    """
    require(isinstance(root, bytes) and len(root) == 32,
            "trie_root", "Trusted trie root must have 32 bytes")
    require(isinstance(secure_key, bytes) and len(secure_key) == 32,
            "trie_key", "Secure trie key must have 32 bytes")
    require(isinstance(proof, list) and len(proof) <= MAX_PROOF_NODES,
            "trie_proof_bound", "Invalid or oversized trie proof node list")
    nodes, total = {}, 0
    for item in proof:
        raw = hex_bytes(item)
        total += len(raw)
        require(raw and total <= MAX_PROOF_BYTES,
                "trie_proof_bound", "Trie proof exceeds its byte bound")
        digest = keccak256(raw)
        require(digest not in nodes, "trie_duplicate_node", "Duplicate trie proof node")
        decoded = rlp_decode(raw)
        require(isinstance(decoded, list) and len(decoded) in (2, 17),
                "trie_node", "Invalid trie node shape")
        nodes[digest] = (decoded, raw)
    if root == EMPTY_TRIE_ROOT:
        require(not nodes, "trie_unused_nodes", "An empty trie cannot use supplied proof nodes")
        return None, {"proof_nodes": 0, "visited_nodes": 0, "membership": "absent"}
    require(nodes, "trie_proof_missing", "Required trie proof is missing", incomplete=True)
    require(root in nodes, "trie_root_mismatch", "Supplied proof does not open the trusted root")

    reference, remaining, used, visited = root, _nibbles(secure_key), set(), 0
    from_extension = False

    def finish(value):
        require(used == set(nodes), "trie_unused_nodes", "Unreferenced trie proof nodes")
        return value, {"proof_nodes": len(nodes), "visited_nodes": visited,
                       "membership": "included" if value is not None else "absent"}

    while visited < MAX_PROOF_NODES:
        visited += 1
        if isinstance(reference, bytes):
            _child(reference)
            if reference not in nodes:
                require(not (set(nodes) - used), "trie_reference_mismatch",
                        "Supplied nodes do not match a required child commitment")
                require(False, "trie_node_missing", "A required child proof node is missing", incomplete=True)
            require(reference not in used, "trie_cycle", "Repeated or cyclic trie reference")
            used.add(reference)
            node, raw = nodes[reference]
            require(visited == 1 or len(raw) >= 32,
                    "trie_reference", "A short non-root child must be embedded inline")
        else:
            _child(reference)
            node = reference
        require(isinstance(node, list) and len(node) in (2, 17), "trie_node", "Invalid traversed trie node")
        require(not from_extension or len(node) == 17,
                "trie_extension", "A canonical extension must lead to a branch")
        from_extension = False
        if len(node) == 17:
            for child in node[:16]:
                _child(child, empty=True)
            require(isinstance(node[16], bytes), "trie_value", "Invalid branch terminal value")
            occupied = sum(child != b"" for child in node[:16]) + bool(node[16])
            require(occupied >= 2, "trie_branch", "Noncanonical single-entry branch")
            if not remaining:
                return finish(node[16] or None)
            reference, remaining = node[remaining[0]], remaining[1:]
            if reference == b"":
                return finish(None)
        else:
            path, leaf = _compact(node[0])
            if leaf:
                require(isinstance(node[1], bytes) and node[1], "trie_value", "Invalid leaf value")
            else:
                _child(node[1])
            if remaining[:len(path)] != path:
                return finish(None)
            remaining = remaining[len(path):]
            if leaf:
                return finish(node[1] if not remaining else None)
            reference, from_extension = node[1], True
    require(False, "trie_proof_bound", "Trie traversal exceeds its node bound")


def account_membership(state_root, address, proof):
    """Authenticate and canonically decode an account, or return None for absence."""
    require(isinstance(address, bytes) and len(address) == 20,
            "history_address", "Account address must have 20 bytes")
    value, trace = trie_value(state_root, keccak256(address), proof)
    if value is None:
        return None, trace
    fields = rlp_decode(value)
    require(isinstance(fields, list) and len(fields) == 4,
            "history_account", "Invalid authenticated account tuple")
    nonce, balance = rlp_uint(fields[0], bits=64), rlp_uint(fields[1])
    storage_root, code_hash = fields[2:]
    require(isinstance(storage_root, bytes) and len(storage_root) == 32
            and isinstance(code_hash, bytes) and len(code_hash) == 32,
            "history_account", "Invalid authenticated account hash fields")
    return {"nonce": nonce, "balance": balance, "storage_root": storage_root, "code_hash": code_hash}, trace


def storage_membership(storage_root, slot, proof):
    """Authenticate a uint256 slot and restore its complete 32-byte value."""
    require(type(slot) is int and 0 <= slot < 1 << 256,
            "history_slot", "Storage slot must be a uint256 integer")
    key = keccak256(slot.to_bytes(32, "big"))
    value, trace = trie_value(storage_root, key, proof)
    if value is None:
        return None, trace
    scalar = rlp_decode(value)
    number = rlp_uint(scalar)
    return number.to_bytes(32, "big"), {**trace, "secure_key": "0x" + key.hex(),
                                      "canonical_integer_bytes": len(scalar)}
