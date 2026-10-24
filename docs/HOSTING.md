# Hosting and recovery

The deployable candidate uses one Linux host with persistent local storage:
Caddy terminates HTTPS and proxies to Waitress on `127.0.0.1:8000`. The WSGI
application serves the compiled React assets and versioned indexed reads. A
separate private collector owns writes. SQLite WAL files remain on the same
local filesystem; do not place this shared state on a network filesystem or
start independent replicas against copied databases and call them one index.

This is a preparation candidate. Real-host supervision, permissions, TLS, public
load, collection throughput and recovery targets require deployment validation.
See [hosting options](HOSTING_OPTIONS.md) and the [read contract](READ_API.md).

## Runtime and directories

Python 3.10+ supports the application; use Python 3.12 for the initial deployment
candidate. Node is needed to rebuild the frontend, not to serve the compiled
assets. The ordinary local demo remains Python standard-library-only. The
production adapter has one optional hash-pinned dependency, Waitress 3.0.2:

```shell
python -m pip install --require-hashes --only-binary=:all: -r deploy/requirements.txt
python -B -m hemi_lookup.public --state-dir /var/lib/traze --origin https://traze.example.com --monitor-file /var/lib/traze-monitor/status.json
```

The example hostname is a placeholder. Use a reviewed hostname and host budget
before provisioning, DNS changes, certificate issuance or service activation.

| Location | Responsibility |
| --- | --- |
| `/opt/traze/current` | Reviewed immutable application release and built `web/` assets |
| `/opt/traze/venv` | Python runtime with the optional pinned dependency |
| `/var/lib/traze` | Persistent index, ledger, policies, controls and disposable cache |
| `/var/lib/traze-monitor` | Small private monitor report; only a whitelist is exposed |
| `/var/backups/traze` | Restricted local backups, copied to a separately reviewed off-host destination |
| `/etc/traze` | Root-owned hostname, thresholds and explicitly enabled collector configuration |

These templates and backup commands assume combined state. A locally prepared
index may instead pin an external original accounting directory, as described in
[ongoing collection](OPERATING.md#reuse-a-retained-index-and-its-original-accounting).
The writer and private operator monitor must retain access to that ledger and
its original validation baseline. The read API does not expose the binding.
Do not treat copying just that index directory as migration of its accounting.
The combined-directory backup command still refuses this layout with
`external_accounting_backup_requires_review`. Use the explicit
[paired backup and recovery workflow](PAIRED_RECOVERY.md) for a consistent
external-ledger snapshot, guarded path migration and comparison with later
authoritative accounting, clocks and stops. This workflow is locally validated;
real-host recovery, permissions and off-host retention still require deployment
validation. No automatic hosted migration or collector activation follows from
`init` or a restore.

Create distinct `traze-read` and `traze-write` users with a shared `traze-state`
group. The reader needs group read/traverse access, never state-directory write
permission. Use the writer's `0027` umask for database sidecars and JSON files.
In WAL mode, the readable `-wal` and `-shm` files must exist before a filesystem
read-only reader opens the database. Check this after boot and writer restart.
An immutable SQLite URI is unsuitable for a changing index. Backups use DELETE
journal mode so a restored index can be opened read-only without old sidecars.
Restricted backup files are `0600`; grant the intended reader group access to
the restored target after operator inspection, while retaining the backup's
restrictive permissions. Test actual Linux permissions before public serving.

## Public boundary

`hemi_lookup.public` is separate from the developer browser bridge. It exposes:

- `GET`/`HEAD` website assets, Search/Browse/API pages and exact record URLs.
- Existing `/api/v1` indexed reads, plus `/api/v1/status`.
- Minimal `/api/state` browser configuration with no saved files, jobs, source
  token or accounting values. This remains a private UI contract.
- `/health/live` for process response and `/health/ready` for index readability.

No receipt-file, job, discovery, refresh, collector or operator route is exposed.
An exact configured Host and matching browser Origin are required; server-side
read clients can omit Origin. No permissive CORS or wildcard proxy trust is used.
Forwarded headers cannot change the configured origin. The backend binds only
to loopback; expose HTTPS through the provided Caddy template and firewall only
the intended ports. Keep SSH restricted to operators and port 8000 private.

The existing five independent read slots and SQL/input/proof/output budgets
remain. One Waitress process has six threads, 32 connections, a 16-entry socket
backlog, 8 KiB header cap and 1 KiB body cap. The application rejects request
bodies and non-read methods, limits URLs to 2 KiB and applies a global token
bucket of 20 API/health requests per second with burst 40. These are defensive
settings, not measured sustained capacity, a per-user guarantee or DDoS defense.
Caddy also bounds header/body reads and response writes. A client may need
backoff on 429/503. Do not multiply processes without revisiting aggregate
resource and admission bounds.

WSGI logs contain event/category/status/response bytes and processing duration;
they omit block inputs, raw URLs, query strings and client addresses. The template
does not enable Caddy access logging. Bound journal retention on the chosen host
and review any added proxy logs before collecting visitor data.
Built asset URLs use immutable browser caching; pages and API responses use
`no-store`. Deploy frontend assets and the HTML entry point as one reviewed release.

## Process supervision

`deploy/` contains Caddy and systemd templates. Copying a template does not start
a process. After an explicitly reviewed deployment:

1. Install the reviewed release/runtime, service accounts and persistent state;
   configure `/etc/traze/host.env` from `deploy/host.env.example`.
2. Validate Caddy configuration and native systemd units on the chosen Linux
   host. Verify permission boundaries, socket limits and journal/storage limits.
   Install unit/config files as root-owned `0644`, not executable or writable by
   the service users. Apply `deploy/caddy-traze.conf` as a drop-in for the official
   Caddy service so it reads `/etc/traze/host.env`; otherwise the hostname variable
   is unavailable to Caddy. The admin API is disabled, so configuration changes
   require a Caddy service restart rather than its API-based reload command.
3. Start the reader and verify readiness, exact record links and proof exports.
   Configure Caddy with the same hostname/origin and validate HTTPS externally.
4. The private collector has a separate environment file and activation marker.
   Supply a deliberately reviewed service ID and finite operating policy. Do
   not use historical test IDs or quotas. Start it only within the new operating
   authorization. Stops remain terminal; exit code 2 is not automatically retried.
5. Enable the local monitor timer only when adopting that operating setup.
   Its checks run every 30 seconds and do not contact chain sources. No external
   notification destination is configured by these templates.

Reader restarts do not start collection. A collector killed by the service manager
may retain interrupted reservations and an owned stale source lock; use the
existing [recovery procedure](COLLECTION.md) rather than deleting accounting.
The collector service is blocked when `restore-guard.json` exists.

## Health and monitoring

```shell
python -B -m hemi_lookup.operations --state-dir /var/lib/traze --min-free-bytes 2147483648 --observation-max-age-seconds 1800 --output /var/lib/traze-monitor/status.json
```

The example thresholds are proposals, not source allowances or accepted service
objectives. The command returns 2 when a monitored check needs review. It records
disk free, index readability, bounded ledger totals, collector/owned-lock state,
source-view status, latest recorded observation age and restore interlock.
The public view strips private IDs, paths, raw error/stop messages and accounting.
A snapshot older than 120 seconds is unavailable, even if its saved success
fields were true. A recent monitor report can correctly report stale sources.
`/health/ready` can remain successful while collection is stopped or observations
are stale: retained data is still readable. None of these checks proves chain
synchronization, complete coverage or current canonical membership. Operational
fields are observations across files, not an atomic evidence-proof transaction.

## Consistent backup and restore

Before backup, stop every writer, including catalogue imports and monitoring
that writes inside the chosen state directory. Prefer a bounded collector exit
or deliberate maintenance stop; an operator stop is terminal. Never delete a
stop marker to restart the same service. The tool acquires collector and source
exclusion locks and SQLite writer locks, then uses SQLite's backup API so committed
WAL records are included with consistent ledger/control files.

```shell
python -B -m hemi_lookup.backup backup --source /var/lib/traze --destination /var/backups/traze/UNIQUE_SNAPSHOT --max-bytes 268435456
python -B -m hemi_lookup.backup verify --source /var/backups/traze/UNIQUE_SNAPSHOT --max-bytes 268435456
python -B -m hemi_lookup.backup restore --source /var/backups/traze/UNIQUE_SNAPSHOT --destination /var/lib/traze-restored --max-bytes 268435456
```

Size ceilings are explicit operator disk limits. Choose a new destination;
existing state is never overwritten. Failed work may leave a uniquely named
`.partial-*` directory for inspection. Do not treat it as a completed backup.
The tool rejects traversal, symlinks, unclassified files, duplicate members,
checksum changes and database integrity failures. A manifest is an integrity
inventory, not a signature authenticating a backup from an untrusted party.

Durable SQLite indexes, JSON evidence/accounting/policies and terminal controls
are retained. Disposable raw caches and runtime locks/heartbeats are omitted;
compiled transaction/Merkle/header proofs do not require raw caches. Store
encrypted off-host copies under operator-only access. Local copies alone do not
cover server loss. Retain original exported proof packets and historical ledgers;
there is no automatic evidence/ledger pruning or unreviewed retention timer.

After restore, compare manifest counts, exact publication IDs, original ledger
bytes, policies, clocks and stop markers. Search by number/hash and independently
verify representative packets. The restored website can serve records, but
`restore-guard.json` blocks all normal source sessions and service start/create.
No automatic acquisition-arming command is supplied. Reconcile all post-snapshot
usage and later operator stops against authoritative records before deciding
how a writer can resume. Restoring an older ledger must not create allowance.
If that later state cannot be recovered, keep serving historical evidence and
make a separate reviewed recovery/operating decision.

Preserve any original validation baselines required by a migrated ledger.
Legacy discovery state can depend on reports from its original application root;
do not clear `prior_validation_bytes` or rewrite checkpoints to bypass a mismatch.
A hosted read-only snapshot does not need acquisition activation. Moving its
collector to a new release/root requires a compatible, reviewed baseline and
accounting migration in addition to clearing the recovery review.

Agree backup frequency, off-host retention, restore ownership and measured
recovery-point/recovery-time objectives before supported operation. A daily
backup, for example, permits up to a day of index changes to be lost; accounting
and later stops need additional reconciliation rather than automatic replay.

## Deployment acceptance

The hosted candidate still needs native service/permission validation, domain/TLS
checks, bounded outside-client reads, actual boot/restart/restore measurements,
and a newly scoped sustained collection test with real source/storage/traffic
costs. Choose a maintainer and accepted operating envelope. Independent-user beta
validation remains the release gate after these operating checks.

Primary implementation references: [Waitress arguments](https://docs.pylonsproject.org/projects/waitress/en/latest/arguments.html),
[Caddy server options](https://caddyserver.com/docs/caddyfile/options),
[Caddy reverse proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy),
and [SQLite backup API](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup).
