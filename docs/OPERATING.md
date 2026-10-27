# Ongoing collection and operating policy

Traze can continue collection without an operator creating each finite job. The
controller follows a persisted Bitcoin cursor, creates bounded child jobs, waits
for future heights and retries transient source failures with bounded backoff.
It runs only after an explicit operator `run` command. Starting the website,
creating a policy or reading status never starts acquisition.

This is a local service controller. It does not install an operating-system
service or scheduler, provision hosting, or establish sustainable public-provider
capacity. Hosting and independent-user validation remain release requirements.
See the [hosting and recovery runbook](HOSTING.md) for separate supervision
templates, monitoring and consistent backups. Restored snapshots carry an
acquisition interlock until accounting, later stops and policy clocks are reconciled.

## Reuse a retained index and its original accounting

An index can live separately from the authoritative lifetime ledger. Prepare a
new working directory from an existing, stopped index without starting sources:

```shell
python -B -m hemi_lookup.service --state-dir /path/to/new-index --ledger-dir /path/to/original-accounting init --seed /path/to/retained-index/service.sqlite3
python -B -m hemi_lookup.service --state-dir /path/to/new-index report
```

Replace the paths. The ledger directory must already contain `session.json`.
`init` uses SQLite's backup API, records seed provenance and a pinned ledger
prefix, and does not overwrite its source, open an acquisition Session, create a
service policy or arm an operating clock. The destination must be new. An active
source writer or source restore interlock prevents setup. A previously bound
seed must retain the same authoritative ledger. For an unbound legacy snapshot
carrying an older ledger copy, that entire prefix must exist unchanged in the
selected authoritative ledger.

The persistent binding lives in the index's `meta` table. Subsequent `create`,
`run`, operator monitoring, usage and owned-session recovery use that ledger;
they do not create or trust a second `session.json` beside the index. An explicit
`--ledger-dir` can also bind an existing unbound working index during `create`.
Later commands may omit the flag. A conflicting flag, missing/reset ledger,
changed prefix, copied binding at another index path or mismatched service
identity prevents source work. An existing global source lock is respected;
recovery cannot clear another index's Session. Legacy combined state remains
supported. These are local consistency checks, not a tamper-proof audit log.

Index setup is not backup/restore, a new operating allowance or permission to
resume an earlier stopped service. Preserve the original ledger, index, policies,
clocks, controls and required validation baselines. The current single-directory
backup command deliberately refuses an externally bound index rather than
producing a misleading complete backup. The explicit
[paired recovery commands](PAIRED_RECOVERY.md) snapshot the bound pair and original
baselines, migrate paths into a new destination and leave acquisition blocked.
Compare with fresh authoritative accounting, clocks and stops before a separate
operating decision. Local recovery checks do not replace actual-host validation;
see [hosting](HOSTING.md).

## Configure an explicit policy

Supply a JSON object containing every required field. There is no built-in
response-data, request, body, cache or managed-state operating allocation.

| Field | Meaning | Required or default |
| --- | --- | --- |
| `period_seconds` | Length of a renewable operating period, in seconds | Required; 60–86,400 |
| `response_bytes` | Counted response data and conservative interrupted reservations per period | Required; includes reservation headroom and a 1 MB safety margin |
| `requests` | Admitted source-request attempts per period | Required |
| `body_attempts` | Raw Bitcoin body attempts per period, including unsuccessful attempts | Required |
| `cache_bytes` | Disposable raw-body cache capacity for this service | Required; at least 4,000,000 bytes |
| `state_bytes` | Admission high-water value for managed evidence/WAL/accounting | Required |
| `job_heights` | Maximum heights in a finite child job | Default 4; at most 200 |
| `poll_seconds` | Base interval while waiting or retrying | Default 60 |
| `backoff_max_seconds` | Maximum transient-error wait | Default 600; at most 3,600 and no smaller than polling |
| `segment_seconds` | Maximum processing segment | Default 60 |
| `active_seconds_per_update` | Maximum active processing time for a child update | Default 600; no smaller than a segment and at most 600 |

Settings must be positive whole numbers; booleans and unknown fields are rejected.
The limits are operator choices, not Hemi protocol rules. Select them against
accepted source access, measured workload, storage and the intended service scope.
An undersized policy can repeatedly wait without enough headroom to admit a body.
The supplied historical test policies are synthetic; they are not production
recommendations or live operating allowances.

From the repository root, after creating your reviewed policy file:

```shell
python -B -m hemi_lookup.service --state-dir /path/to/state create --start BITCOIN_HEIGHT --policy /path/to/policy.json
python -B -m hemi_lookup.service --state-dir /path/to/state status SERVICE_ID
python -B -m hemi_lookup.service --state-dir /path/to/state run SERVICE_ID
python -B -m hemi_lookup.service --state-dir /path/to/state stop SERVICE_ID --reason "Operator ended this service"
```

Replace the uppercase identifiers and paths. `create` reports a new service ID
and saves its policy without source calls. `run` is the acquisition command; it
continues until stopped or blocked. `--max-cycles N` bounds updates for controlled
operation; reaching that count exits the process without stopping its saved
service. Another explicit `run` can recover that service. An operator `stop` or
Ctrl+C instead records a terminal stop and is never automatically resumed.

Only one collector/controller can own the state's runner lock. Child jobs belong
to their service: the finite collector's `run`, `follow` and recheck paths refuse
to execute them outside the operating policy. A stop request on an active child
also stops its service. Visitor searches remain indexed-only under the normal
[serving profile](SERVING.md).

## Period accounting and recovery

The first explicit run arms the saved UTC clock. Each period is anchored to that
original clock. A new child, process restart or changed calendar date does not
renew the current period's remaining allowance. A later configured period gets
its own allowance. Unused offline periods are not accumulated as credits.

The controller binds a finite child and its service in one SQLite transaction
before source work. Each child keeps its original range, deadline, limits and
usage. If its operating period ends, unfinished work remains recorded and a new
child starts from the unchanged contiguous cursor in the later period. Owned
stale-session recovery also handles a crash across that boundary. Previously
compiled bodies are reused. For overlapping processed heights, a successor can
also retain required Hemi metadata and partial/completed association progress
from its own service's immediately retired child. Original observation times
are preserved. Unrelated metadata, old clocks/allowances and ready/update states
are not inherited; operator-stopped or blocked services do not resume this way.

The successor still freshly checks the Bitcoin height mapping before reuse and
performs the normal uncached Hemi/Bitcoin checks before advancing its cursor.
A changed overlap hash blocks before another raw-body download; retained proof
material remains historical. Reuse is limited to unfinished overlapping work,
not a global cross-service cache or reactivation of an operator-stopped collection job.
Copied metadata consumes managed SQLite storage; retention remains subject to
the operating policy and the limits below.

`session.json` remains the lifetime ledger. Existing validation totals and runs
are preserved; operating sessions record their service, policy fingerprint and
period. Request reservations are recorded before admission to the transport.
Interrupted requests keep conservative accounting. The service checks its prior
ledger prefix and sums its recorded period usage across all children/sessions.
A mismatched context, changed policy, removed prior prefix or recorded future
period blocks work. This is consistency checking over operator-controlled local
files, not a cryptographic audit log or protection against a privileged operator.

The renewable policy does not amend the resource limits of an earlier finite collection job or
reset the installation's original totals. Ordinary finite lookups retain their
existing cumulative ceiling. Backward clock movement is rejected; the service
uses the system UTC clock and does not establish an independent time authority.
Preserve the clock, policy, ledger, database and control files together.

Transient failures keep their retry time across restarts. Source conflicts,
invalid evidence and managed-storage stops require operator review and remain
blocked on another `run`. There is no automatic reset/unpause command. Any
replacement operating scope must be deliberately configured after review while
retaining the earlier service, evidence and accounting.

## Cursor, source changes and gaps

Only contiguous heights with processed bodies, a completed update and all
supported publication associations checked can advance `next_height`. A usable
subset can already be queried while other associations remain unresolved; that
does not advance past the gap. An empty supported-format result is distinct
from complete protocol-format coverage.

Before Bitcoin work in a new session, the controller checks the source's height
mapping for its last contiguous anchor. The next parsed or reused header must
link to the previous checked height, including across child boundaries. A
changed anchor or broken link blocks progress and qualifies retained evidence
as historical. It does not delete proofs, silently rewrite history or implement
automatic reorganization repair. It does not recheck every historical height on
each cycle or independently validate Bitcoin consensus/chainwork.

Read-only collection status distinguishes a saved service from an active runner.
Liveness still requires a matching owned OS lock and recent heartbeat. A process
that exited after `--max-cycles` is not shown as running. Coverage, last source
observation and activity timestamps retain their separate meanings. Status
polling never creates jobs or source sessions.

### Freshness and coverage reports

`service report` reads the current saved collection without a source request.
The website's indexed coverage and Browse views show the same progress. Browse's
**Refresh index** action reloads stored records, retains applied filters/view and
returns to the first page. It does not collect or recheck sources. Observation
age on Browse is the age at its last index response; use refresh for a new read.

For an ongoing service, `progress` separates:

- The latest recorded Bitcoin tip, its observation time and age. During an active
  update, a newer child observation is visible before the controller finishes.
- Service start, contiguous checked-through height and next cursor. There is no
  checked range before the first completed update. This cursor covers only this
  service's supported parser/source checks, not all historical index bounds.
- Remaining heights from that cursor through the **last observed** tip. Zero
  describes that observation; it does not establish current synchronization or
  canonical membership. Missing, future or invalid observation times leave the
  gap unavailable rather than zero.
- Current finite child's required/processed/checked heights, unprocessed heights,
  supported/unsupported payloads, pending Hemi metadata and pending ancestry work.
  Operator `report` additionally includes per-height details. These are current
  child counts, not an aggregate of every historical job or operating period.

A finite collection is labelled `finite_job`; its completed range does not
become an ongoing cursor-to-tip claim. Index counts can increase while a child
still lacks metadata or checked ancestry. Available keystone metadata does not
guarantee a complete path to every requested Hemi target. Public index responses
omit operator IDs, binding paths, policy details and raw per-publication issues.

An unavailable runner, terminal stop, blocked source view and a wait for future
heights remain distinct. A controlled replay is explicitly identified and must
not be presented as a new live observation. Freshness reports do not extend
first-publication, finality, full protocol-format or independent consensus claims.

## Cache and storage

Disposable raw bodies live in `state/service-blocks/SERVICE_ID/`. Cache eviction
removes only owned `.bin` files in that service directory. Compiled transaction,
header and Merkle-proof material stays in `service.sqlite3`; evidence exports do
not require the raw body to remain cached. Original fixture/receipt directories
and legacy caches are not pruned.

The controller checks `service.sqlite3`, its WAL/shared-memory files and the
bound original `session.json` before and after each bounded update. If their measured sum exceeds
`state_bytes`, no further update starts. The preceding update, SQLite overhead
and accounting writes can exceed this high-water value; it is an admission
threshold, not a hard filesystem quota. Raw cache is bounded separately. It does
not count or clean unrelated state directories, old-service caches, exported
receipts, backups, operating-system buffers or operator files. No evidence,
ledger history or unresolved work is deleted to make room. Full retention,
backup/restore, disk monitoring and hosted operations remain separate work.

Read [collection guarantees](COLLECTION.md) and [verification scope](VERIFICATION.md)
before treating a found record, source snapshot or gap as a stronger claim.
