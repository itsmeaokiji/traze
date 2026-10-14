"""Public-source-shaped controls backed only by the included corpus."""

import copy
import json
from pathlib import Path

from hemi_lookup.catalogue import DEFAULT_RECEIPTS
from hemi_lookup.errors import DataError
from hash_fixtures import HashSources

ROOT = Path(__file__).resolve().parent.parent


class CollectionSources:
    def __init__(self, session, *, outage=False, changed=False, tip=965931):
        self.session, self.outage, self.changed, self.tip = session, outage, changed, tip
        self.last_btc_api = "https://blockstream.info/api"
        self.headers, self.attributes, self.hashes, self.raw_calls = {}, {}, {}, []
        for name in DEFAULT_RECEIPTS:
            receipt = json.loads((ROOT / name).read_text())
            source = HashSources(receipt)
            self.headers.update(source.headers)
            self.attributes.update(source.attributes)
            self.hashes.update(source.hashes)

    def charge(self, value, *, block=False, error=False):
        raw = value if isinstance(value, bytes) else json.dumps(value).encode()
        reservation = self.session.reserve("https://blockstream.info/api/controlled", 4_000_000 if block else 65536, block)
        self.session.charge(reservation, len(raw) + 128, "error" if error else "ok")
        if error:
            raise DataError("source_unavailable", "Controlled metadata unavailable")
        return copy.deepcopy(value)

    def rpc(self, method, params):
        if method == "eth_chainId":
            return self.charge(hex(43111))
        number = int(params[0], 16)
        values = self.headers if method == "eth_getBlockByNumber" else self.attributes
        if self.outage or number not in values:
            return self.charge({}, error=True)
        value = copy.deepcopy(values[number])
        if self.changed and method == "eth_getBlockByNumber" and number == 5243125:
            value["hash"] = "0x" + "22" * 32
        return self.charge(value)

    def btc(self, path, as_json=True):
        if path == "/blocks/tip/hash":
            return self.charge(self.hashes[self.tip])
        if path.startswith("/block-height/"):
            return self.charge(self.hashes[int(path.rsplit("/", 1)[1])])
        if path.startswith("/block/"):
            block_hash = path.rsplit("/", 1)[1]
            height = next(h for h, value in self.hashes.items() if value == block_hash)
            return self.charge({"id": block_hash, "height": height})
        raise AssertionError(path)

    def block_raw(self, block_hash, maximum):
        self.raw_calls.append(block_hash)
        raw = (ROOT / "tests/fixtures/bitcoin_blocks" / (block_hash + ".bin")).read_bytes()
        return self.charge(raw, block=True), self.last_btc_api

    def provenance(self):
        return {"hemi_rpc_used": "https://rpc.hemi.network/rpc"}
