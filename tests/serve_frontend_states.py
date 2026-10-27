"""Serve saved proofs with controlled result and progress states for visual tests.

No public reads or engine jobs. POST operations are rejected. Stdin commands
show simulated progress, return to idle, or stop the loopback helper.
"""

import copy
import json
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from hemi_lookup.storage import now
from hemi_lookup.web import Jobs, LocalServer, WebError


class VisualJobs(Jobs):
    def start(self, operation, payload):
        raise WebError("invalid_input", "Visual QA does not start public-source operations.")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="traze_visual_states_") as directory:
        root = Path(directory).resolve()
        assert root.is_relative_to(Path(tempfile.gettempdir()).resolve())
        samples = root / "demo_results"
        samples.mkdir()
        for name in ("mainnet_5243117.json", "mainnet_5243125.json", "scoped_no_match.json"):
            (samples / name).write_bytes((ROOT / "demo_results" / name).read_bytes())
        jobs = VisualJobs(root, mode="discovery")
        ordinary = json.loads((samples / "mainnet_5243117.json").read_text())
        negative = json.loads((samples / "scoped_no_match.json").read_text())

        def control(identity, label, base, mode="live_lookup", issue=None, conflict=False):
            receipt = copy.deepcopy(base)
            receipt.update(status="incomplete", mode=mode,
                           validation={"scope": "controlled_provider_responses", "live_public_reads": False})
            receipt["context"]["current_view_confirmed"] = False
            receipt["context"]["requested_block_identity"] = {"status": "unavailable"}
            receipt["selection"]["current_proof_index"] = None
            if not receipt["proofs"]:
                receipt["selection"]["best_observed_proof_index"] = None
            for proof in receipt["proofs"]:
                proof["current_bitcoin_depth_in_provider_view"] = None
            receipt["issues"] = [issue] if issue else []
            if issue and issue["code"] == "time_budget_exhausted":
                receipt["termination"] = {"reason": "time_budget_exhausted"}
                if mode != "live_metadata_refresh":
                    receipt["coverage"]["stop_reason"] = "time_budget_exhausted"
            if conflict:
                receipt["context"]["bitcoin_secondary"] = [{"height": 965931, "same": False}]
            path = samples / f"{label.lower().replace(' ', '-')}.json"
            path.write_text(json.dumps(receipt), encoding="utf-8")
            jobs.receipts[identity * 32] = {"path": path, "label": label, "origin": "saved_example"}

        control("a", "Controlled search timeout", ordinary, issue={"code": "time_budget_exhausted", "message": "Controlled search deadline"})
        control("b", "Controlled source recheck timeout", ordinary, mode="live_metadata_refresh", issue={"code": "time_budget_exhausted", "message": "Controlled source deadline"})
        control("c", "Controlled source conflict", ordinary, mode="live_metadata_refresh", issue={"code": "source_conflict", "message": "Controlled mismatch"}, conflict=True)
        control("d", "Controlled incomplete without proof", negative)
        control("e", "Controlled incomplete with proof", ordinary)
        jobs.receipts["0" * 32] = {"path": ROOT / "demo_results/mainnet_4028425_hash.json", "label": "March later-keystone case", "origin": "saved_example"}
        server = LocalServer(8767, jobs)

        def commands():
            for line in sys.stdin:
                command = line.strip()
                if command.startswith("progress-"):
                    operation = "refresh" if command == "progress-recheck" else "lookup"
                    message = "Gap at Bitcoin 965925: controlled diagnostic" if command == "progress-gap" else "Bitcoin 965931: 2111 txs, 7 HEMI candidates, 4 matches"
                    with jobs.guard:
                        jobs.active = "f" * 32
                        jobs.jobs[jobs.active] = {"id": jobs.active, "operation": operation, "block_number": 5243117, "block_input": 5243117,
                                                 "state": "running", "started_at": now(), "events": [{"at": now(), "message": message}], "receipt_id": None}
                    print(json.dumps({"controlled_progress": operation, "engine_jobs": 0, "public_reads": 0}), flush=True)
                elif command in ("idle", "stop"):
                    with jobs.guard:
                        jobs.active = None
                        jobs.jobs.clear()
                    print(json.dumps({"state": "idle", "server_jobs": 0, "engine_jobs": 0, "public_reads": 0,
                                      "temp_response_bytes": jobs.resources()["total_response_bytes"]}), flush=True)
                    if command == "stop":
                        server.shutdown()
                        return

        threading.Thread(target=commands, daemon=True).start()
        print(json.dumps({"url": "http://127.0.0.1:8767", "scope": "saved evidence and controlled display states; no public reads",
                          "commands": ["progress-search", "progress-recheck", "progress-gap", "idle", "stop"]}), flush=True)
        try:
            server.serve_forever(poll_interval=.1)
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
