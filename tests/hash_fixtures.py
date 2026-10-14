"""Retained mainnet data with explicitly controlled RPC views; no public reads."""

import copy
import json
from pathlib import Path

from hemi_lookup.errors import DataError

ROOT = Path(__file__).resolve().parent.parent
REFERENCE = json.loads((ROOT / "demo_results/mainnet_5243125.json").read_text())
ORDINARY = json.loads((ROOT / "demo_results/mainnet_5243117.json").read_text())
MARCH = json.loads((ROOT / "demo_results/milestone2_automatic_20261004T170034660491Z.json").read_text())
BODY = (ROOT / "tests/fixtures/mainnet_965931.bin").read_bytes()


class ControlledSession:
    max_blocks = 0

    def __init__(self):
        self.run = {"body_attempts": 0, "cache_reads": 0}

    def metrics(self):
        return {"run_id": "controlled-offline", "response_bytes_this_run": 0,
                "response_bytes_session_with_prior_validation": 0,
                "bitcoin_body_attempts": 0, "cache_body_reads": self.run["cache_reads"], "http_requests": 0}


class FixtureCache:
    def get(self, block_hash):
        return BODY if block_hash == REFERENCE["proofs"][0]["bitcoin_block_hash"] else None

    def put(self, *args):
        raise AssertionError("Controlled cache must not receive a new body")


class HashSources:
    def __init__(self, receipt=REFERENCE, mode="baseline"):
        self.receipt, self.mode = receipt, mode
        self.target = receipt["query"]["block_number"]
        self.headers, self.attributes = {}, {}
        self.calls, self.bitcoin_calls, self.target_number_reads = [], [], 0
        checkpoints = [receipt["query"]["covering_checkpoint"]] + [p["checkpoint"] for p in receipt["proofs"]]
        for cp in checkpoints:
            self.attributes[cp["height"]] = cp["attributes_transaction"]
            for header in (cp["header"], cp["previous_header"]):
                if header: self.headers[int(header["number"], 16)] = header
        self.headers[self.target] = receipt["query"]["header"]
        for proof in receipt["proofs"]:
            for header in proof["ancestry"]:
                self.headers[int(header["number"], 16)] = header
        self.hashes = {b["height"]: b["hash"] for b in receipt["coverage"]["blocks"]}

    def rpc(self, method, params):
        self.calls.append((method, copy.deepcopy(params)))
        if method == "eth_chainId": return hex(743111 if self.mode == "wrong_network" else 43111)
        if method == "web3_clientVersion": return "controlled-retained-mainnet-view"
        if method == "eth_getBlockByHash":
            if self.mode == "hash_unavailable": return None
            header = next((h for h in self.headers.values() if h["hash"].lower() == params[0].lower()), None)
            result = copy.deepcopy(header)
            if result and self.mode == "hash_response_changed": result["hash"] = "0x" + "11" * 32
            return result
        number = int(params[0], 16)
        if method == "eth_getBlockByNumber":
            value = copy.deepcopy(self.headers[number])
            if number == self.target:
                self.target_number_reads += 1
                if self.mode == "number_unavailable": raise DataError("source_unavailable", "Controlled missing canonical header")
                if self.mode == "canonical_changed" or (self.mode == "changed_after_scan" and self.target_number_reads > 1):
                    value["hash"] = "0x" + "22" * 32
                if self.mode == "root_conflict": value["stateRoot"] = "0x" + "33" * 32
            return value
        if method == "eth_getTransactionByBlockNumberAndIndex":
            return copy.deepcopy(self.attributes[number])
        raise AssertionError("Unexpected controlled RPC method")

    def btc(self, path, as_json=True, secondary=False):
        self.bitcoin_calls.append(path)
        if path.startswith("/block-height/"): return self.hashes[int(path.rsplit("/", 1)[1])]
        tip = self.receipt["bitcoin_tip_after"]
        if path == "/blocks/tip/hash": return tip["hash"]
        if path == "/block/" + tip["hash"]: return {"id": tip["hash"], "height": tip["height"]}
        raise AssertionError("Unexpected controlled Bitcoin read")

    def block_raw(self, *args):
        raise AssertionError("New Bitcoin body forbidden in controlled hash validation")


def hash_receipt(receipt=REFERENCE):
    result = copy.deepcopy(receipt)
    result["query"].update({"input_type": "hash", "requested_block_hash": result["query"]["block_hash"]})
    return result
