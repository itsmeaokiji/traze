"""Readable CLI plus JSON receipts. Lookup/refresh are live; verify is offline."""

import argparse
import json
import sys
from pathlib import Path

from .engine import Engine, covering
from .errors import DataError
from .network import PublicSources
from .proof import verify_receipt
from .storage import Session, BlockCache
from .inputs import block_input

ROOT = Path(__file__).resolve().parent.parent


def block_number(value):
    try:
        return block_input(value)
    except (ValueError, DataError) as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def bitcoin_range(value):
    try:
        start, end = map(int, value.split(":"))
        if start < 0 or end < start or end - start + 1 > 200:
            raise ValueError()
        return start, end
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use START:END, at most 200 declared Bitcoin heights") from exc


def load_receipt(path):
    if path.stat().st_size > 10_000_000:
        raise DataError("receipt_too_large", "Receipt exceeds the 10 MB input bound")
    return json.loads(path.read_text(encoding="utf-8"))


def write_receipt(path, receipt):
    # Immutable snapshots: never overwrite an existing receipt by default.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(receipt, handle, indent=2)


def readable(receipt, path):
    q, cov, selection = receipt["query"], receipt["coverage"], receipt["selection"]
    print(f"Result: {receipt['status']}")
    recovery = receipt.get("source_recovery")
    if recovery:
        print(f"Hemi RPC used: {recovery['hemi_rpc_used']} ({'fallback' if recovery['hemi_fallback_used'] else 'primary'})")
        timestamp_route = "Blockstream metadata recovery" if recovery["timestamp_recovery_used"] else "primary timestamp API" if recovery.get("timestamp_events") else "not needed for this operation"
        print(f"Timestamp route: {timestamp_route}")
        print("Source routes are provider-qualified; backend independence is not established.")
    print(f"Hemi mainnet block: {q['block_number']}  |  Covering keystone: {q.get('covering_keystone', 'unresolved')}")
    if q.get("block_hash"):
        print(f"{'Requested' if q.get('input_type') == 'hash' else 'Resolved'} Hemi block hash: {q['block_hash']}")
        identity = receipt.get("context", {}).get("requested_block_identity", {})
        print(f"Input identity in the Hemi RPC view: {identity.get('status', 'unavailable')}")
        if identity.get("current_hash") and identity["current_hash"] != q["block_hash"]:
            print(f"Current block reported at that height: {identity['current_hash']}")
    index = selection.get("current_proof_index")
    historical = False
    if index is None:
        index = selection.get("best_observed_proof_index")
        historical = index is not None
    if index is not None:
        proof = receipt["proofs"][index]
        print(f"{'Historical/unconfirmed current selection' if historical else 'Earliest observed verified publication'}:")
        print(f"  Publishing keystone: {proof['checkpoint']['height']} ({proof['relationship']})")
        print(f"  Bitcoin block: {proof['bitcoin_height']}")
        print(f"  Bitcoin txid: {proof['txid']}")
        print("  Payload, txid, header and Merkle inclusion checks: passed against provided headers")
        print(f"  Source-qualified current context: {receipt.get('context', {}).get('current_view_confirmed', False)}")
        if proof["relationship"] == "later_checkpoint_support":
            print("  Later support observed; direct-publication absence is not established.")
    elif receipt["status"] == "not_found_within_coverage":
        print("No matching publication in the checked range. Global absence is not established.")
    else:
        print("Incomplete/unconfirmed lookup. Missing data is not publication absence.")
    print(f"Fully scanned Bitcoin intervals: {cov['fully_scanned_intervals']}")
    print(f"Gaps: {len(cov['gaps'])}  |  Unresolved candidates: {len(cov['unresolved_candidates'])}  |  Unsupported payloads: {cov['unsupported_payloads']}")
    print(f"Stop reason: {cov['stop_reason']}")
    print("Global earliest publication: not proven. Public-provider views; no full SPV or independent Hemi execution.")
    metrics = receipt["resources"]
    print(f"HTTP response data: {metrics['response_bytes_this_run']:,} bytes this run; {metrics['response_bytes_session_with_prior_validation']:,} bytes combined")
    print(f"Bitcoin body attempts: {metrics['bitcoin_body_attempts']}  |  Cached bodies re-parsed: {metrics['cache_body_reads']}")
    if receipt["issues"]:
        for issue in receipt["issues"][:8]:
            print(f"Issue [{issue['code']}]: {issue['message']}")
    if recovery:
        for event in recovery.get("hemi_events", []) + recovery.get("timestamp_events", []):
            if event["kind"] in ("hemi_fallback_activated", "timestamp_primary_unavailable", "hemi_source_rejected", "hemi_alternate_unavailable"):
                print(f"Source event [{event['kind']}]: {event.get('source', '')} {event.get('reason', event.get('message', ''))}")
    print(f"JSON evidence: {path}")


def parser():
    p = argparse.ArgumentParser(description="Read-only Hemi mainnet publication lookup demo; public data and bounded searches.")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("lookup", "refresh"):
        child = sub.add_parser(name, help="Live lookup" if name == "lookup" else "Fresh source-view checks of a saved receipt; no block-body scan")
        if name == "lookup":
            child.add_argument("block", type=block_number, help="Hemi block number or full 0x-prefixed block hash")
            child.add_argument("--bitcoin-range", type=bitcoin_range, help="Optional explicit height scope; automatic extension is disabled")
            child.add_argument("--no-cache", action="store_true", help="Force fresh Bitcoin body reads; budget still applies")
        else:
            child.add_argument("receipt", type=Path)
        child.add_argument("--output", type=Path, help="New JSON receipt path; existing files are never overwritten")
        child.add_argument("--state-dir", type=Path, default=ROOT / ".hemi_lookup_data")
        child.add_argument("--max-mb", type=float, default=40, help="Per-run counted response MB, maximum 40; combined session cap is 100")
        child.add_argument("--max-blocks", type=int, default=20, help="Bitcoin body attempts, 0–20; zero permits cache/metadata only")
        child.add_argument("--max-ancestry", type=int, default=256, help="Checked Hemi parent-link budget, maximum 512; not a protocol limit")
        child.add_argument("--json", action="store_true", help="JSON receipt on stdout; progress goes to stderr")
        child.add_argument("--quiet", action="store_true")
    verifier = sub.add_parser("verify", help="Offline mathematical evidence verification; no source refresh")
    verifier.add_argument("receipt", type=Path)
    verifier.add_argument("--json", action="store_true")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "verify":
            result = verify_receipt(load_receipt(args.receipt))
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                print(f"Offline verification: {result['status']} ({result['proofs_checked']} proofs)")
                print("Current chain membership was not rechecked. Negative coverage is not a cryptographic non-inclusion proof.")
            return 0
        if not 2 <= args.max_mb <= 40 or not 0 <= args.max_blocks <= 20 or not 1 <= args.max_ancestry <= 512:
            raise DataError("invalid_limits", "Use 2–40 MB, 0–20 body attempts, and 1–512 ancestry links")
        if args.output and args.output.exists():
            raise DataError("output_exists", "Choose a new output path; receipts are immutable snapshots")
        with Session(args.state_dir, ROOT, int(args.max_mb * 1_000_000), args.max_blocks) as session:
            cache = BlockCache(args.state_dir / "blocks")
            sources = PublicSources(session)
            progress = (lambda message: None) if args.quiet else (lambda message: print(message, file=sys.stderr, flush=True))
            engine = Engine(sources, session, cache, args.max_ancestry, getattr(args, "no_cache", False), progress)
            if args.command == "lookup":
                receipt = engine.lookup(args.block, args.bitcoin_range)
            else:
                receipt = engine.refresh(load_receipt(args.receipt))
            q = receipt["query"]
            identity = f"hash-{q['requested_block_hash'][2:]}" if q.get("input_type") == "hash" else f"block-{q['block_number']}"
            output = args.output or args.state_dir / "receipts" / f"{identity}-{session.run['id']}.json"
            write_receipt(output, receipt)
            if args.json:
                print(json.dumps(receipt, indent=2))
                print(f"JSON evidence: {output}", file=sys.stderr)
            else:
                readable(receipt, output)
            return 0 if receipt["status"] in ("found", "not_found_within_coverage") else 2
    except (DataError, ValueError, KeyError, TypeError, OSError) as exc:
        print(f"Error [{getattr(exc, 'code', 'invalid_data')}]: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
