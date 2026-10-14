"""Offline catalogue commands; the optional reference server binds to loopback."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from hemi_lookup.catalogue import Catalogue, build_catalogue
from hemi_lookup.errors import DataError

ROOT = Path(__file__).resolve().parent


def main(argv=None):
    parser = argparse.ArgumentParser(description="Traze retained-data publication catalogue")
    parser.add_argument("--database", type=Path, default=ROOT / ".hemi_lookup_data/catalogue.sqlite3")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="Compile the two retained corpora without chain requests")
    build.add_argument("--body-dir", type=Path,
                       help="Directory of retained hash-named Bitcoin bodies; defaults to the local cache")
    commands.add_parser("inspect", help="Read catalogue coverage and association counts")
    query = commands.add_parser("query", help="Export evidence for an exact retained Hemi hash")
    query.add_argument("hash")
    query.add_argument("--output", type=Path)
    serve = commands.add_parser("serve", help="Open a separate loopback-only sample consumer")
    serve.add_argument("--port", type=int, default=8768)
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            result = build_catalogue(ROOT, args.database, block_directory=args.body_dir)
        elif args.command == "serve":
            from hemi_lookup.catalogue_web import serve_catalogue
            serve_catalogue(args.database, ROOT, args.port)
            return 0
        else:
            with Catalogue(args.database, readonly=True) as catalogue:
                result = catalogue.summary() if args.command == "inspect" else catalogue.query(args.hash)
        output = json.dumps(result, indent=2) + "\n"
        if args.command == "query" and args.output:
            # Avoid overwriting historical receipts through an export command.
            with args.output.open("x", encoding="utf-8") as handle:
                handle.write(output)
            print(json.dumps({"saved": str(args.output), "status": result["status"], "proofs": len(result["proofs"])}))
        else:
            print(output, end="")
        return 0
    except (DataError, OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        print(json.dumps({"error": getattr(exc, "code", "catalogue_error"),
                          "message": str(exc) if isinstance(exc, DataError) else "Catalogue operation failed; check the local files and database"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
