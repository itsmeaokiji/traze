# Paired index and accounting recovery

Use the explicit paired commands when a maintained index pins an external
lifetime ledger. The ordinary `backup`, `verify` and `restore` commands retain
their combined-directory format and behavior. `backup` still refuses an
external binding rather than silently omitting its accounting.

Paired recovery uses Python's standard library. It does not contact Bitcoin or
Hemi providers, start a collector, clear an operator stop or renew a quota.

## Prepare a consistent snapshot

Stop every writer to both directories, including direct SQLite imports and
operator tools that change durable JSON. Keep the authoritative index, ledger,
original application reports and terminal controls. Do not remove existing lock
files to make a backup proceed. Read-only website access can continue.

```shell
python -B -m hemi_lookup.backup backup-pair --source /var/lib/traze-index --ledger-dir /var/lib/traze-accounting --application-root /opt/traze/current --destination /var/backups/traze/UNIQUE_PAIR --max-bytes 268435456
```

The index's saved binding must name exactly the supplied ledger. Both roots must
be distinct, non-overlapping directories. The tool acquires exclusion locks for
both roots, freezes SQLite writers and copies committed WAL content with the
SQLite backup API. Pinned read-only connections prevent closing the backup's
writer guards from checkpointing an idle source WAL. Where supported, SQLite's
checkpoint-on-close setting is also disabled on those guards.

The snapshot has three roles:

| Directory | Contents |
| --- | --- |
| `index/` | Maintained index, durable JSON and terminal controls |
| `ledger/` | Original lifetime accounting, retained receipts, durable databases and controls |
| `baselines/` | Exact original validation reports referenced by the ledger |

Disposable raw-body caches, runtime locks and heartbeat files are excluded.
Compiled publication proofs remain independently checkable without those caches.
Missing or changed original reports, unclosed modern source runs, reserved
requests, changed bindings and inconsistent terminal service controls prevent a
completed snapshot. Unknown durable files require review rather than omission.

Keep the emitted `manifest_sha256` separately from the snapshot, in a trusted
operator record. Subsequent commands require that checksum. A rewritten manifest
must not become trusted simply because it is stored beside the files it names.
Checksums are integrity checks, not digital signatures authenticating an unknown
sender or independent blockchain consensus.

Each operation has a sixty-second work limit and an explicit durable-payload
size ceiling; a snapshot contains at most 10,000 files. `--max-bytes` applies to
each snapshot, not to total host disk use. Allow separate space for the backup,
restore, temporary directories, SQLite sidecars and original state. Partial work
may remain in a uniquely named `.partial-*` directory for inspection. It is not
a completed backup or a target that can be overwritten.

## Verify and restore to a new location

Set `TRAZE_SNAPSHOT_SHA` to the separately retained 64-character checksum, then:

```shell
python -B -m hemi_lookup.backup verify-pair --source /var/backups/traze/UNIQUE_PAIR --expected-manifest-sha256 "$TRAZE_SNAPSHOT_SHA" --max-bytes 268435456
python -B -m hemi_lookup.backup restore-pair --source /var/backups/traze/UNIQUE_PAIR --destination /var/lib/traze-recovered --expected-manifest-sha256 "$TRAZE_SNAPSHOT_SHA" --max-bytes 268435456
```

The destination must not exist and must be outside the backup. The verifier
checks exact membership, file sizes/hashes, SQLite integrity, baseline reports,
ledger prefixes, policy identities, controls and semantic database identities.
Symlinks, traversal, duplicate members and inconsistent pairs are rejected.
Files are checked again while copying, before publication of the destination.

Restore rewrites only the primary index binding and its service location fields
to the new `index/` and `ledger/` paths. Evidence rows, original ledger bytes,
service IDs, policies, clocks, saved progress and stops retain their meanings.
Historical reports, source paths and secondary databases remain preserved
observations. `recovery-origin.json` records the old and new locations and the
verified snapshot identity. Recorded source path strings are compared as data,
so the restore does not try to access an old Windows path on a Linux host.

Both restored state roots receive `restore-guard.json`. The website can serve
retained evidence from `/var/lib/traze-recovered/index`; normal source sessions
and collector create/run remain blocked. Its original accounting baselines are
available under `/var/lib/traze-recovered/baselines`. There is no automatic
collector activation command in this workflow.

Check number/hash searches, the exact publication IDs, representative independent
JSON verification, accounting totals and stop controls after recovery. Previously
prepared optional assurance/finality artifacts outside these state roots are not
automatically migrated; do not infer their availability from ordinary receipt
verification. The snapshot does not establish current chain membership or fresh
provider observations.

## Reconcile later usage, clocks and stops

An older snapshot is not authority to discard usage or stops recorded after its
creation. Obtain a fresh stopped-writer snapshot of the same authoritative pair
and retain its checksum separately as `TRAZE_FRESH_SHA`:

```shell
python -B -m hemi_lookup.backup compare-pair --source /var/backups/traze/OLDER_PAIR --expected-manifest-sha256 "$TRAZE_SNAPSHOT_SHA" --authoritative /var/backups/traze/FRESH_PAIR --authoritative-manifest-sha256 "$TRAZE_FRESH_SHA" --max-bytes 268435456
```

`compare-pair` is read-only. It verifies both snapshots, requires the same source
pair/binding identity and checks that the later ledger retains the entire older
prefix. It rejects lost terminal controls, discarded services/policies, reduced
progress and reset/backward operating clocks. It reports later usage and changed
state components without changing either snapshot or a restored directory.

If state differs, the result is `fresh_snapshot_required`: restore the fresh
complete pair to another new destination. This preserves later usage, clocks,
stops and evidence together instead of merging selected fields into an older
database. Compare the resulting state before any separate operating decision.
Matching snapshots still leave acquisition blocked; the operator must establish
that the supplied authoritative snapshot is actually current. Two matching old
copies cannot prove freshness.

If later authoritative records are unavailable or contradictory, retain both
restore interlocks and serve only historical evidence. Do not infer zero later
usage, delete guards to work around a failure, reset an operating clock, or
restart a terminally stopped service. Establishing a new authorized operating
policy and source capacity is a separate decision.

## Local rehearsal versus hosted recovery

Local Windows snapshot/migration and controlled recovery checks do not establish
Linux filesystem permissions, real-host restart behavior, off-host backup
retention, machine-loss recovery, hosted load or zero data loss. Agree recovery
ownership, backup frequency and measured recovery targets for the actual host.
Use restricted operator storage and separately reviewed off-host copies.
