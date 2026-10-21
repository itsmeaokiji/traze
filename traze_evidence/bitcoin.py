"""Offline Bitcoin serialization and transaction-commitment checks.

These checks do not validate UTXOs, scripts, difficulty history or most-work
chain selection. Candidate enumeration is a conservative v1 output predicate.
"""

import hashlib

from .errors import require

MAX_BLOCK_BYTES = 4_000_000
POW_LIMIT = (1 << 224) - 1


def sha256d(data):
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


class Reader:
    def __init__(self, data):
        self.data, self.position = data, 0

    def take(self, size):
        require(size >= 0 and size <= len(self.data) - self.position,
                "bitcoin_truncated", "Truncated Bitcoin serialization")
        result = self.data[self.position:self.position + size]
        self.position += size
        return result

    def compact(self):
        first = self.take(1)[0]
        if first < 253:
            return first
        width = {253: 2, 254: 4, 255: 8}[first]
        value = int.from_bytes(self.take(width), "little")
        require(value >= {253: 253, 254: 65536, 255: 4294967296}[first],
                "bitcoin_compactsize", "Nonminimal CompactSize integer")
        return value


def publication_push(script):
    if len(script) < 2 or script[0] != 0x6A:
        return None
    opcode, offset = script[1], 2
    if opcode <= 75:
        length = opcode
    elif opcode in (76, 77, 78):
        width = {76: 1, 77: 2, 78: 4}[opcode]
        if len(script) < offset + width:
            return None
        length = int.from_bytes(script[offset:offset + width], "little")
        offset += width
    else:
        return None
    if offset + length > len(script):
        return None
    data = script[offset:offset + length]
    if not data.startswith(b"HEMI"):
        return None
    if len(data) != 80 or data[4] != 1:
        return {"supported": False, "length": len(data)}
    payload = data[4:]
    return {"supported": True, "payload": payload,
            "height": int.from_bytes(payload[5:9], "big")}


def parse_transaction(reader):
    start = reader.position
    version = reader.take(4)
    witness = reader.position < len(reader.data) and reader.data[reader.position] == 0
    if witness:
        require(reader.take(2) == b"\x00\x01", "bitcoin_witness", "Unsupported witness marker or flags")
    inputs_start = reader.position
    count = reader.compact()
    require(0 < count <= len(reader.data) // 41, "bitcoin_inputs", "Invalid Bitcoin input count")
    inputs = []
    for _ in range(count):
        outpoint = reader.take(36)
        script = reader.take(reader.compact())
        reader.take(4)
        inputs.append((outpoint, script))
    input_bytes = reader.data[inputs_start:reader.position]
    coinbase = count == 1 and inputs[0][0] == b"\0" * 32 + b"\xff" * 4
    outputs_start = reader.position
    count = reader.compact()
    require(0 < count <= len(reader.data) // 9, "bitcoin_outputs", "Invalid Bitcoin output count")
    candidates = []
    for index in range(count):
        value = int.from_bytes(reader.take(8), "little")
        require(value <= 21_000_000 * 100_000_000, "bitcoin_value", "Bitcoin output exceeds MAX_MONEY")
        script = reader.take(reader.compact())
        candidate = publication_push(script)
        if candidate is not None and not coinbase:
            candidates.append({**candidate, "vout": index})
    output_bytes = reader.data[outputs_start:reader.position]
    has_witness = False
    if witness:
        for _ in inputs:
            count = reader.compact()
            require(count <= len(reader.data), "bitcoin_witness", "Invalid witness item count")
            has_witness |= count > 0
            for _ in range(count):
                reader.take(reader.compact())
        require(has_witness, "bitcoin_witness", "Superfluous empty witness serialization")
    locktime = reader.take(4)
    stripped = version + input_bytes + output_bytes + locktime
    digest = sha256d(stripped)
    return {"hash": digest, "txid": digest[::-1].hex(), "coinbase": coinbase,
            "coinbase_script": inputs[0][1] if coinbase else None,
            "first_input_script": inputs[0][1], "candidates": candidates,
            "raw": reader.data[start:reader.position]}


def merkle_root(leaves):
    require(bool(leaves), "bitcoin_merkle", "Empty transaction Merkle tree")
    layer = list(leaves)
    while len(layer) > 1:
        require(not any(layer[i] == layer[i + 1] for i in range(0, len(layer) - 1, 2)),
                "bitcoin_merkle_mutated", "Mutated transaction Merkle tree")
        if len(layer) % 2:
            layer.append(layer[-1])
        layer = [sha256d(layer[i] + layer[i + 1]) for i in range(0, len(layer), 2)]
    return layer[0]


def header_target_valid(header):
    bits = int.from_bytes(header[72:76], "little")
    exponent, coefficient = bits >> 24, bits & 0x7FFFFF
    if bits & 0x800000 or not coefficient or exponent > 34:
        return False
    target = coefficient >> (8 * (3 - exponent)) if exponent <= 3 else coefficient << (8 * (exponent - 3))
    return 0 < target <= POW_LIMIT and int.from_bytes(sha256d(header), "little") <= target


def bip34_height(script):
    require(bool(script) and 2 <= len(script) <= 100, "bitcoin_coinbase", "Invalid coinbase script length")
    width = script[0]
    require(0 < width <= 5 and len(script) >= width + 1,
            "bitcoin_height", "Unsupported BIP34 height push")
    value = script[1:1 + width]
    require(not value[-1] & 128 and not (width > 1 and value[-1] == 0 and not value[-2] & 128),
            "bitcoin_height", "Nonminimal or negative BIP34 height")
    return int.from_bytes(value, "little")


def parse_block(raw, *, expected_hash, expected_height):
    require(81 <= len(raw) <= MAX_BLOCK_BYTES, "bitcoin_body_size", "Bitcoin body size outside supported bounds")
    reader = Reader(raw)
    header = reader.take(80)
    require(sha256d(header)[::-1].hex() == expected_hash, "bitcoin_header_hash", "Bitcoin header identity differs")
    require(header_target_valid(header), "bitcoin_pow", "Header fails its stated proof-of-work target")
    count = reader.compact()
    require(0 < count <= len(raw) // 10, "bitcoin_transactions", "Invalid block transaction count")
    transactions = []
    for index in range(count):
        transaction = parse_transaction(reader)
        require(transaction["coinbase"] == (index == 0), "bitcoin_coinbase", "Unexpected coinbase position")
        transactions.append(transaction)
    require(reader.position == len(raw), "bitcoin_trailing", "Trailing bytes after Bitcoin transactions")
    require(merkle_root([tx["hash"] for tx in transactions]) == header[36:68],
            "bitcoin_merkle", "Full transaction enumeration differs from the header root")
    require(bip34_height(transactions[0]["coinbase_script"]) == expected_height,
            "bitcoin_height", "BIP34 height differs from the supplied chain position")
    return {"header": header, "transactions": transactions}
