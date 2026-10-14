"""Main React app with real catalogue/jobs and controlled chain replies only."""

import copy
import json
import shutil
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from hash_fixtures import HashSources, ORDINARY
from hemi_lookup.catalogue_lookup import recorded_lookup
from hemi_lookup.catalogue import build_catalogue
from hemi_lookup.engine import Engine
from hemi_lookup.errors import DeadlineError
from hemi_lookup.storage import BlockCache, Session
from hemi_lookup.web import Jobs, LocalServer


class ControlledSources(HashSources):
    def timestamp(self, stamp):
        block = next(row for row in ORDINARY["coverage"]["blocks"] if row["height"] == 965926)
        return {"lookup": {"height": 965926, "hash": block["hash"]},
                "block": {"id": block["hash"], "height": 965926}, "controlled_hint": True}


class ControlledJobs(Jobs):
    control_mode = "baseline"

    def public_operation(self, identity, operation, value):
        if operation == "refresh":
            fixture = copy.deepcopy(value)
        else:
            fixture = recorded_lookup(self.state_dir / "catalogue.sqlite3", value[0])
            fixture = copy.deepcopy(fixture or recorded_lookup(self.state_dir / "catalogue.sqlite3", 5243119))
        fixture["bitcoin_tip_after"] = ORDINARY["bitcoin_tip_after"]
        sources = ControlledSources(fixture, "canonical_changed" if self.control_mode == "conflict" else "baseline")
        if self.control_mode == "timeout":
            sources.rpc = lambda *args: (_ for _ in ()).throw(DeadlineError("Controlled metadata timeout"))
        with Session(self.state_dir, self.root) as session:
            engine = Engine(sources, session, BlockCache(self.state_dir / "blocks"),
                            progress=lambda message: self.event(identity, message, session))
            receipt = engine.refresh(value) if operation == "refresh" else engine.lookup(*value)
        receipt["validation"] = {"scope": "controlled_provider_responses", "live_public_reads": False}
        return receipt


if __name__ == "__main__":
    qa_root = (ROOT / "output/qa-temp-main-catalogue").resolve()
    assert qa_root.is_relative_to(ROOT)
    qa_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="browser_", dir=qa_root) as directory:
        root = Path(directory).resolve()
        assert root.is_relative_to(qa_root)
        state = root / ".hemi_lookup_data"
        blocks = state / "blocks"
        blocks.mkdir(parents=True)
        fixture_bodies = ROOT / "tests/fixtures/bitcoin_blocks"
        build_catalogue(ROOT, state / "catalogue.sqlite3", block_directory=fixture_bodies)
        for block in ORDINARY["coverage"]["blocks"]:
            shutil.copyfile(fixture_bodies / (block["hash"] + ".bin"), blocks / (block["hash"] + ".bin"))
        jobs = ControlledJobs(root)
        server = LocalServer(8767, jobs)
        def commands():
            for line in sys.stdin:
                mode = line.strip()
                if mode == "stop":
                    print(json.dumps({"public_chain_requests": 0, "temporary_response_bytes": jobs.resources()["total_response_bytes"], "active_job": jobs.active}), flush=True)
                    server.shutdown()
                    return
                if mode in ("baseline", "conflict", "timeout"):
                    jobs.control_mode = mode
                    print(json.dumps({"controlled_source_mode": mode}), flush=True)
        threading.Thread(target=commands, daemon=True).start()
        print(json.dumps({"url": "http://127.0.0.1:8767", "scope": "Actual main app with isolated catalogue and controlled replies; zero public-chain calls", "commands": ["baseline", "conflict", "timeout", "stop"]}), flush=True)
        try:
            server.serve_forever(poll_interval=.1)
        finally:
            server.server_close()
