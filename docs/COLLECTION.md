# Publication collection

Traze includes an opt-in finite collector. It processes Bitcoin blocks ahead of
user searches, checks supported publication commitments against basic Hemi RPC
metadata, and stores reusable evidence in SQLite. Starting the website does not
start collection.

## Create and run a job

Use Python 3.10 or later, from the repository root. Inspect available settings:

```text
python -B -m hemi_lookup.collector --help
python -B -m hemi_lookup.collector create --help
```

Replace the height placeholders with the finite Bitcoin range you want to collect:

```text
python -B -m hemi_lookup.collector create --start FIRST_HEIGHT --end LAST_HEIGHT --batch-size 4
```

Creation saves a job and makes no source requests. Copy the returned `id`, then run:

```text
python -B -m hemi_lookup.collector run JOB_ID
python -B -m hemi_lookup.collector status
```

Run the same job ID to resume unfinished work. Completed bodies and job-local
metadata are reused. `--max-updates 1` runs one unfinished update. `--follow`
retries a job that is waiting for Bitcoin blocks, within its original range and
remaining allowance. It never expands the configured range. `--recheck` performs
new source-view checks within the same remaining job allowance.

## Storage and read availability

The managed index is `.hemi_lookup_data/service.sqlite3`; raw-download cache files
are in `.hemi_lookup_data/collection_blocks/`. Existing retained evidence remains
in `catalogue.sqlite3` and is still available to the main app. Queries prefer
usable managed evidence and then retained evidence. The original fixtures and
receipts are not rewritten.

Indexed reads and saved-result downloads remain available while the collector
holds the network-operation lock. A search needing new source requests receives
a busy result while another source operation owns that lock. SQLite read
transactions provide consistent packets while collection commits updates.

The website shows processed Bitcoin blocks, supported publications, keystone
associations, unresolved work and the last recorded progress. These counts are
separate: a processed block does not imply that every Hemi association is ready.

## Resource controls and recovery

Jobs persist their total response-data, request and body-attempt allowances.
Processing-time allowance applies separately to each update. Defaults are 40 MB
counted response data, 600 requests, 20 body attempts and 600 active seconds per
update, with the installation's 100-MB cumulative response ceiling. These are
operator controls, not protocol rules. Use explicit `create` options for an
accepted operating policy; the saved limits do not reset on resume.

Work uses bounded processing segments. A segment interrupted by process death
conservatively consumes its reserved time when actual elapsed time cannot be
recovered. Interrupted HTTP reservations also remain counted. Resume keeps the
remaining allowance and recovers only a stale session lock belonging to that
job, while holding the OS-owned collector lock. Other operation locks are left
intact. Preserve `session.json`; changing directories is not an accounting reset.

Complete bodies commit atomically. Verification packets retain transaction
bytes, Bitcoin header and Merkle proof independently of the download cache.
Missing Hemi data is explicit and can be retried without redownloading completed
Bitcoin bodies. Partial ancestry remains qualified.

## Evidence boundaries

RPC observations keep their original source and time. Source checks bypass the
metadata cache; changed or unavailable views qualify historical evidence rather
than discarding it. A retained proof may still verify against supplied headers
after a source-view change. No current chain membership is inferred from an
indexed read.

Readable source-time summaries use the first and last recorded time when there
are many observations. The exported packet retains individual RPC observation
times in its coverage records. Collected observations use `source_record_id`;
only imported original receipts use `receipt_sha256` for their file checksum.

Collection enumerates the formats supported by the current parser. It does not
establish complete history, globally first publication, Bitcoin consensus,
authoritative Hemi finality or PoP reward eligibility. Resource-limited,
unsupported and unresolved work remains explicit.

The first finite pilot and controlled recovery tests provide initial engineering
evidence. Sustainable tip-following, operating cost and public-service capacity
require further measured validation before a supported public beta.
