# Finite historical backfill and coverage accounting

Traze can populate a new working index from a retained catalogue and process
explicit historical Bitcoin intervals. This operator workflow is separate from
visitor searches. It uses public Bitcoin APIs and basic Hemi RPC, the existing
body parser and collector association checks. It requires no local full node,
TBC, BFG, background subscription, additional package or paid endpoint.

Prepare a new working index offline, then inspect its fixed plan:

```console
python scripts/backfill_index.py --index-dir /path/to/new-index prepare --seed /path/to/catalogue.sqlite3 --ledger-dir /path/to/original-state --repair-range 965032 965045 --range 950000 950011 --wall-seconds 900 --max-bytes 80000000 --max-requests 3000 --max-attempts 14
python scripts/backfill_index.py --index-dir /path/to/new-index report
```

These example intervals are finite examples, not automatically scheduled work.
Select an appropriate scope and resource allowance before acquiring data. The
original accounting directory must have a compatible existing `session.json`,
closed modern runs, matching prior validation records and no active source session.
Older pre-closure-format entries may be pinned unchanged when followed by modern
closed entries and no unsettled reservations; they are not rewritten as closed.
`prepare` preserves that ledger and the seed catalogue. It creates a SQLite
snapshot, immutable scope binding and source-free report state in the new index.
Repair intervals require one retained compiled body per height; they never admit
a new raw-body download. Intervals must be disjoint and total at most 200 heights.

Start or stop the finite job explicitly:

```console
python scripts/backfill_index.py --index-dir /path/to/new-index run
python scripts/backfill_index.py --index-dir /path/to/new-index stop
```

The original shared ledger accounts for all new HTTP work, including nomination,
fallback, failed attempts, Hemi metadata and final source rechecks. The one wall
clock starts immediately before source work. Session segments of at most ten
minutes share the same aggregate clock, response allowance, request cap and body
attempt cap. Downtime consumes wall time; rerunning cannot renew a closed scope.
Conservative reservations and response safety headroom remain enforced. An
operator stop is terminal. An unclosed accounting run or changed baseline requires
operator reconciliation; this workflow does not silently reset or repair it.

The collector reuses complete compiled bodies and retained association work.
New bodies have their header/transaction root parsed and checked; supported HEMI
payloads require matching reconstructed Hemi metadata. The optional fail-on-source-
conflict collector mode stops this batch on contradictory identities or ancestry.
Completed bodies and metadata survive a graceful Session-segment boundary.
Unavailable data remains classified, without choosing another range to produce
a positive example.

`report` reads SQLite and the original ledger without creating a Session, opening
a transport or writing evidence. Its JSON lists every required height, exact
retained hash variants, body status, parser-domain publications, association state,
planned ancestry work, source rechecks, errors and resource totals. A final copy
is saved as `coverage.json` after `run`. Inspect `state`, `terminal_issue`,
`accounting_complete` and `supported_domain_complete`; a successful report command
alone is not a complete acquisition result.

Keep these distinctions:

- **Complete accounting:** the closed attempt classifies required work, including
  incomplete or unavailable entries. It does not mean all data was obtained.
- **Supported-domain completeness:** every required body is compiled, every
  supported publication has an association, planned ancestry work is complete or
  explicitly retained, and recorded final source checks match without conflict.
  Unsupported candidates remain enumerated outside this supported format claim.
- **Association available:** a checked Hemi template matches an exact publication.
  This can precede completion of the wider ancestry needed by other Hemi inputs.
- **Historical source observation:** heights and tips come from recorded providers.
  Adjacent header links, matching source rechecks and offline inclusion checks do
  not independently validate either chain's consensus or establish lasting freshness.

The new index can be served by Traze in indexed-only mode for browsing, timeline,
number/hash inspection and portable evidence downloads. This does not automatically
enable live visitor discovery, accept a finality context, prove global first
publication, or establish coverage between disjoint historical intervals.
