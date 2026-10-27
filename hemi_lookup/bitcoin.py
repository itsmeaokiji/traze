"""Bitcoin transaction parsing and Hemi v1 keystone payload decoding.

This module checks serialization and commitments; it is not a consensus node.
"""
import hashlib
import re
import struct
from .errors import DataError
MAX_RAW_BLOCK = 4_000_000
POW_LIMIT = (1 << 224) - 1

def sha256d(data):
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()

def display_hash(data):
    return data[::-1].hex()

def hex32(value):
    if not isinstance(value, str):
        raise DataError('invalid_hash', 'Expected a 32-byte hexadecimal hash')
    value=value.removeprefix('0x')
    if not re.fullmatch(r'[0-9a-fA-F]{64}', value):
        raise DataError('invalid_hash', 'Expected a 32-byte hexadecimal hash')
    return bytes.fromhex(value)

class Reader:
    def __init__(self, data, check=None):
        self.data, self.pos = data, 0
        self.check, self.operations = check, 0
        if check:
            check()

    def take(self, size):
        self.operations += 1
        if self.check and self.operations % 256 == 0:
            self.check()
        if size < 0 or self.pos + size > len(self.data):
            raise DataError("truncated Bitcoin serialization")
        result = self.data[self.pos:self.pos + size]
        self.pos += size
        return result

    def compact(self):
        first = self.take(1)[0]
        if first < 253:
            return first
        sizes = {253: 2, 254: 4, 255: 8}
        number = int.from_bytes(self.take(sizes[first]), "little")
        if number < {253: 253, 254: 65_536, 255: 4_294_967_296}[first]:
            raise DataError("non-minimal CompactSize encoding")
        return number


def hemi_payload(script):
    """Mirror the reviewed first-OP_RETURN-data-push rule, not a byte grep."""
    if len(script) < 2 or script[0] != 0x6A:
        return None
    opcode, offset = script[1], 2
    if opcode <= 75:
        length = opcode
    elif opcode in (76, 77, 78):
        size = {76: 1, 77: 2, 78: 4}[opcode]
        if len(script) < offset + size:
            return None
        length = int.from_bytes(script[offset:offset + size], "little")
        offset += size
    else:
        return None
    if offset + length > len(script):
        return None
    pushed = script[offset:offset + length]
    if not pushed.startswith(b"HEMI"):
        return None
    if len(pushed) != 80 or pushed[4] != 1:
        return {"supported": False, "length": len(pushed), "prefix": pushed[:12].hex()}
    payload = pushed[4:]
    return {
        "supported": True, "payload": payload,
        "l2_height": int.from_bytes(payload[5:9], "big"),
        "keystone_id": display_hash(sha256d(payload)),
    }


def parse_transaction(reader):
    start = reader.pos
    version = reader.take(4)
    witness = False
    if reader.pos < len(reader.data) and reader.data[reader.pos] == 0:
        if reader.take(2) != b"\x00\x01":
            raise DataError("unsupported transaction witness flags")
        witness = True
    inputs_start = reader.pos
    inputs = reader.compact()
    if inputs == 0 or inputs > len(reader.data) // 41:
        raise DataError("invalid transaction input count")
    coinbase = False
    for i in range(inputs):
        outpoint = reader.take(36)
        script_length = reader.compact()
        reader.take(script_length)
        reader.take(4)
        if inputs == 1 and i == 0:
            coinbase = outpoint == b"\x00" * 32 + b"\xff" * 4
    inputs_raw = reader.data[inputs_start:reader.pos]
    outputs_start = reader.pos
    outputs = reader.compact()
    if outputs > len(reader.data) // 9:
        raise DataError("invalid transaction output count")
    candidates = []
    for output_index in range(outputs):
        reader.take(8)
        script = reader.take(reader.compact())
        candidate = hemi_payload(script)
        if candidate is not None and not coinbase:
            candidates.append({**candidate, "vout_index": output_index})
    outputs_raw = reader.data[outputs_start:reader.pos]
    nonempty_witness = False
    if witness:
        for _ in range(inputs):
            count = reader.compact()
            if count > len(reader.data):
                raise DataError("invalid witness item count")
            nonempty_witness |= count > 0
            for _ in range(count):
                reader.take(reader.compact())
        if not nonempty_witness:
            raise DataError("superfluous empty witness serialization")
    locktime = reader.take(4)
    raw = reader.data[start:reader.pos]
    stripped = version + inputs_raw + outputs_raw + locktime
    tx_hash = sha256d(stripped)
    return {
        "hash": tx_hash, "txid": display_hash(tx_hash), "wtxid": display_hash(sha256d(raw)),
        "raw": raw, "segwit": witness, "coinbase": coinbase, "candidates": candidates,
    }


def merkle_root(leaves, check=None):
    if not leaves:
        raise DataError("empty Bitcoin transaction merkle tree")
    layer = list(leaves)
    while len(layer) > 1:
        if check:
            check()
        if any(layer[i] == layer[i + 1] for i in range(0, len(layer) - 1, 2)):
            raise DataError("mutated transaction merkle tree")
        if len(layer) % 2:
            layer.append(layer[-1])
        next_layer = []
        for i in range(0, len(layer), 2):
            if check and i % 512 == 0:
                check()
            next_layer.append(sha256d(layer[i] + layer[i + 1]))
        layer = next_layer
    return layer[0]


def merkle_branch(leaves, position, check=None):
    layer, index, siblings = list(leaves), position, []
    while len(layer) > 1:
        if check:
            check()
        if len(layer) % 2:
            layer.append(layer[-1])
        siblings.append(layer[index ^ 1])
        next_layer = []
        for i in range(0, len(layer), 2):
            if check and i % 512 == 0:
                check()
            next_layer.append(sha256d(layer[i] + layer[i + 1]))
        layer = next_layer
        index //= 2
    return siblings


def verify_branch(leaf, position, siblings, expected_root):
    current = leaf
    for sibling in siblings:
        current = sha256d(sibling + current) if position & 1 else sha256d(current + sibling)
        position //= 2
    return position == 0 and current == expected_root


def header_pow(header):
    bits = struct.unpack_from("<I", header, 72)[0]
    exponent, mantissa = bits >> 24, bits & 0x7FFFFF
    if bits & 0x800000 or not mantissa or exponent > 34:
        return False
    target = mantissa >> (8 * (3 - exponent)) if exponent <= 3 else mantissa << (8 * (exponent - 3))
    return 0 < target <= POW_LIMIT and int.from_bytes(sha256d(header), "little") <= target


def parse_block(raw, expected_hash, check=None):
    if len(raw) > MAX_RAW_BLOCK:
        raise DataError("Bitcoin block exceeds the configured response-size limit")
    reader = Reader(raw, check)
    header = reader.take(80)
    if display_hash(sha256d(header)) != expected_hash:
        raise DataError("raw Bitcoin header does not match requested block hash")
    if not header_pow(header):
        raise DataError("header hash fails its stated proof-of-work target")
    count = reader.compact()
    if count < 1 or count > len(raw) // 10:
        raise DataError("invalid block transaction count")
    leaves, candidates = [], []
    for index in range(count):
        if check and index % 128 == 0:
            check()
        tx = parse_transaction(reader)
        if tx["coinbase"] != (index == 0):
            raise DataError("unexpected coinbase position")
        leaves.append(tx["hash"])
        for candidate in tx["candidates"]:
            candidates.append({**candidate, "tx_index": index, "tx": tx})
    if reader.pos != len(raw):
        raise DataError("trailing or incomplete block bytes")
    if merkle_root(leaves, check) != header[36:68]:
        raise DataError("full transaction enumeration does not match header merkle root")
    if check:
        check()
    return {"header": header, "leaves": leaves, "candidates": candidates, "tx_count": count}


def keystone_bytes(header, previous_hash, origin):
    height = int(header["number"], 16)
    if not 0 <= origin <= 0xFFFFFFFF or not 0 < height <= 0xFFFFFFFF:
        raise DataError("unsupported keystone integer range")
    return (
        struct.pack(">BII", 1, origin, height) + hex32(header["parentHash"])[:11]
        + hex32(previous_hash)[:12] + hex32(header["stateRoot"])
        + hex32(header["hash"])[:12]
    )


def origin_from_input(value):
    data = bytes.fromhex(value.removeprefix("0x"))
    formats = {"015d8eb9": 260, "440a5e20": 164, "098999be": 176}
    if formats.get(data[:4].hex()) != len(data):
        raise DataError("unsupported L1-attributes encoding; do not treat as absent publication")
    return int.from_bytes(data[28:36], "big")


