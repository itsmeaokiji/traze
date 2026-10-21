"""Bounded canonical RLP, legacy Keccak-256 and transaction-trie reconstruction.

Keccak uses the legacy 0x01 suffix; hashlib.sha3_256 is not interchangeable.
The trie is rebuilt from every ordered raw transaction, rather than accepting
unchecked sibling references in a supplied inclusion proof.
"""

from .errors import require

_MASK = (1 << 64) - 1
_ROUND_CONSTANTS = (
    0x1, 0x8082, 0x800000000000808A, 0x8000000080008000,
    0x808B, 0x80000001, 0x8000000080008081, 0x8000000000008009,
    0x8A, 0x88, 0x80008009, 0x8000000A, 0x8000808B,
    0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x800A,
    0x800000008000000A, 0x8000000080008081, 0x8000000000008080,
    0x80000001, 0x8000000080008008,
)
_ROTATIONS = ((0, 36, 3, 41, 18), (1, 44, 10, 45, 2),
              (62, 6, 43, 15, 61), (28, 55, 25, 21, 56),
              (27, 20, 39, 8, 14))


def keccak256(data):
    padded = bytearray(data)
    padded.append(1)
    padded.extend(b"\0" * (-len(padded) % 136))
    padded[-1] |= 128
    state = [0] * 25
    for offset in range(0, len(padded), 136):
        for index in range(17):
            state[index] ^= int.from_bytes(padded[offset + 8 * index:offset + 8 * index + 8], "little")
        for constant in _ROUND_CONSTANTS:
            columns = [state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20]
                       for x in range(5)]
            for x in range(5):
                neighbor = columns[(x + 1) % 5]
                delta = columns[(x - 1) % 5] ^ ((neighbor << 1 | neighbor >> 63) & _MASK)
                for y in range(5):
                    state[x + 5 * y] ^= delta
            rotated = [0] * 25
            for x in range(5):
                for y in range(5):
                    lane, shift = state[x + 5 * y], _ROTATIONS[x][y]
                    rotated[y + 5 * ((2 * x + 3 * y) % 5)] = (
                        lane << shift | lane >> ((64 - shift) % 64)) & _MASK
            for x in range(5):
                for y in range(5):
                    state[x + 5 * y] = rotated[x + 5 * y] ^ (
                        ~rotated[(x + 1) % 5 + 5 * y] & rotated[(x + 2) % 5 + 5 * y])
            state[0] ^= constant
    return b"".join(lane.to_bytes(8, "little") for lane in state)[:32]


def integer_bytes(number):
    require(type(number) is int and number >= 0, "rlp_integer", "RLP integer must be nonnegative")
    return number.to_bytes((number.bit_length() + 7) // 8, "big")


def rlp_encode(value):
    if type(value) is int:
        value = integer_bytes(value)
    listing = isinstance(value, (list, tuple))
    payload = b"".join(rlp_encode(item) for item in value) if listing else bytes(value)
    if not listing and len(payload) == 1 and payload[0] < 128:
        return payload
    base = 192 if listing else 128
    if len(payload) <= 55:
        return bytes([base + len(payload)]) + payload
    length = integer_bytes(len(payload))
    return bytes([base + 55 + len(length)]) + length + payload


def rlp_decode(raw, *, max_depth=32):
    def read(position, limit, depth):
        require(depth <= max_depth, "rlp_depth", "RLP nesting exceeds the supported limit")
        require(position < limit, "rlp_truncated", "Truncated RLP item")
        tag = raw[position]
        if tag < 128:
            return raw[position:position + 1], position + 1
        listing = tag >= 192
        base = 192 if listing else 128
        length, start = tag - base, position + 1
        if length > 55:
            width = length - 55
            require(start + width <= limit and raw[start] != 0,
                    "rlp_length", "Invalid RLP length encoding")
            length = int.from_bytes(raw[start:start + width], "big")
            start += width
            require(length > 55, "rlp_noncanonical", "Nonminimal RLP long form")
        end = start + length
        require(end <= limit, "rlp_truncated", "RLP item crosses its containing boundary")
        if not listing:
            require(not (length == 1 and raw[start] < 128),
                    "rlp_noncanonical", "Nonminimal RLP byte string")
            return raw[start:end], end
        items = []
        while start < end:
            value, start = read(start, end, depth + 1)
            items.append(value)
        return items, end

    value, end = read(0, len(raw), 0)
    require(end == len(raw), "rlp_trailing", "Trailing RLP bytes")
    return value


def rlp_uint(value, *, bits=256):
    require(isinstance(value, bytes) and len(value) <= (bits + 7) // 8
            and (not value or value[0] != 0), "rlp_integer", "Invalid canonical RLP integer")
    number = int.from_bytes(value, "big")
    require(number < 1 << bits, "rlp_integer", "RLP integer exceeds its field width")
    return number


def transaction_root(transactions):
    """Build the canonical ordered MPT over raw values keyed by RLP(index)."""
    def nibbles(data):
        return tuple(part for byte in data for part in (byte >> 4, byte & 15))

    def compact(path, leaf):
        values = [2 * int(leaf) + len(path) % 2] + ([] if len(path) % 2 else [0]) + list(path)
        return bytes(values[index] * 16 + values[index + 1] for index in range(0, len(values), 2))

    def child(entries):
        node = build(entries)
        encoded = rlp_encode(node)
        return node if len(encoded) < 32 else keccak256(encoded)

    def build(entries):
        if len(entries) == 1:
            path, value = entries[0]
            return [compact(path, True), value]
        common = 0
        while all(len(path) > common for path, _ in entries) and len({path[common] for path, _ in entries}) == 1:
            common += 1
        if common:
            return [compact(entries[0][0][:common], False),
                    child([(path[common:], value) for path, value in entries])]
        node = [b""] * 17
        for slot in range(16):
            group = [(path[1:], value) for path, value in entries if path and path[0] == slot]
            if group:
                node[slot] = child(group)
        terminal = [value for path, value in entries if not path]
        require(len(terminal) <= 1, "trie_key", "Duplicate transaction-trie key")
        if terminal:
            node[16] = terminal[0]
        return node

    if not transactions:
        return keccak256(rlp_encode(b""))
    entries = [(nibbles(rlp_encode(index)), value) for index, value in enumerate(transactions)]
    return keccak256(rlp_encode(build(entries)))
