"""Serve the compiled frontend against controlled sources and retained proof data.

No public reads. The first lookup waits for a stdin `release` command so an
actual browser reload can demonstrate reconnection to the same server job.
`stop` releases any wait, drains the controlled operation, and shuts down.
"""

import argparse
import copy
import json
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from serve_recovery_controls import RecoveryFactory, BrowserEngine, ORDINARY, REFERENCE, BODY
from hemi_lookup.storage import BlockCache
from hemi_lookup.web import Jobs, LocalServer


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    gate = threading.Event()

    class PausedEngine(BrowserEngine):
        def lookup(self, *args, **kwargs):
            self.progress("Waiting for the controlled QA release")
            if not gate.wait(45):
                raise RuntimeError("Controlled QA release was not received")
            return super().lookup(*args, **kwargs)

    with tempfile.TemporaryDirectory(prefix="traze_frontend_step1_qa_") as directory:
        root = Path(directory).resolve()
        assert root.is_relative_to(Path(tempfile.gettempdir()).resolve())
        samples = root / "demo_results"
        samples.mkdir()
        for name in ("mainnet_5243117.json", "mainnet_5243125.json", "scoped_no_match.json"):
            (samples / name).write_bytes((ROOT / "demo_results" / name).read_bytes())
        jobs = Jobs(root, mode="discovery")
        # Make the March saved case permanently available in this test menu.
        jobs.receipts["a" * 32] = {
            "path": ROOT / "demo_results/milestone3_march_later_hash_20261004T174823173306Z.json",
            "label": "March later-keystone case", "origin": "saved_example",
        }
        timeout = copy.deepcopy(ORDINARY)
        timeout["status"] = "incomplete"
        timeout["context"]["current_view_confirmed"] = False
        timeout["context"]["requested_block_identity"] = {"status": "unavailable"}
        timeout["selection"]["current_proof_index"] = None
        for proof in timeout["proofs"]:
            proof["current_bitcoin_depth_in_provider_view"] = None
        timeout["coverage"]["stop_reason"] = "time_budget_exhausted"
        timeout["issues"] = [{"code": "time_budget_exhausted", "message": "Controlled overall deadline expired"}]
        timeout["validation"] = {"scope": "controlled_provider_responses", "live_public_reads": False}
        path = samples / "timeout.json"
        path.write_text(json.dumps(timeout), encoding="utf-8")
        jobs.receipts["b" * 32] = {"path": path, "label": "Controlled timeout", "origin": "saved_example"}
        BlockCache(jobs.state_dir / "blocks").put(REFERENCE["proofs"][0]["bitcoin_block_hash"], BODY)
        with patch("hemi_lookup.web.PublicSources", RecoveryFactory()), patch("hemi_lookup.web.Engine", PausedEngine):
            server = LocalServer(args.port, jobs)

            def commands():
                for line in sys.stdin:
                    if line.strip() == "release":
                        gate.set()
                        print(json.dumps({"qa_gate": "released", "server_jobs": len(jobs.jobs)}), flush=True)
                    elif line.strip() == "stop":
                        gate.set()
                        limit = time.monotonic() + 5
                        while jobs.active is not None and time.monotonic() < limit:
                            time.sleep(.01)
                        print(json.dumps({"server_jobs": len(jobs.jobs), "public_reads": 0,
                                          "temp_response_bytes": jobs.resources().get("total_response_bytes")}), flush=True)
                        server.shutdown()
                        return

            threading.Thread(target=commands, daemon=True).start()
            print(json.dumps({"url": f"http://127.0.0.1:{server.server_port}",
                              "scope": "saved evidence and controlled provider replies; no public reads",
                              "hash": ORDINARY["query"]["block_hash"], "commands": ["release", "stop"]}), flush=True)
            try:
                server.serve_forever(poll_interval=.1)
            except KeyboardInterrupt:
                gate.set()
            finally:
                server.server_close()
