"""Serve the actual browser/engine with controlled retained views, no public reads."""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from hash_fixtures import ORDINARY, REFERENCE, BODY, HashSources
from hemi_lookup.engine import Engine
from hemi_lookup.storage import BlockCache
from hemi_lookup.web import Jobs, LocalServer


class BrowserSources(HashSources):
    def __init__(self, session):
        super().__init__(ORDINARY)

    def rpc(self, method, params):
        if method == "eth_getBlockByHash" and params[0] == REFERENCE["query"]["block_hash"]:
            self.target = REFERENCE["query"]["block_number"]
            self.mode = "canonical_changed"  # Deliberately simulated; not a live reorg.
        return super().rpc(method, params)


class BrowserEngine(Engine):
    def lookup(self, *args, **kwargs):
        result = super().lookup(*args, **kwargs)
        result["validation"] = {"scope": "controlled_provider_responses", "live_public_reads": False}
        return result


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="hemi_hash_ui_qa_") as directory:
        root = Path(directory).resolve()
        assert root.is_relative_to(Path(tempfile.gettempdir()).resolve())
        samples = root / "demo_results"
        samples.mkdir()
        (samples / "mainnet_5243117.json").write_text(json.dumps(ORDINARY), encoding="utf-8")
        jobs = Jobs(root)
        BlockCache(jobs.state_dir / "blocks").put(REFERENCE["proofs"][0]["bitcoin_block_hash"], BODY)
        with patch("hemi_lookup.web.PublicSources", BrowserSources), patch("hemi_lookup.web.Engine", BrowserEngine):
            server = LocalServer(8767, jobs)
            print(json.dumps({"url": "http://127.0.0.1:8767", "qa_scope": "controlled_provider_responses",
                              "matching_hash": ORDINARY["query"]["block_hash"],
                              "simulated_changed_hash": REFERENCE["query"]["block_hash"],
                              "unavailable_hash": "0x" + "44" * 32}), flush=True)
            try:
                server.serve_forever(poll_interval=.1)
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
