# Website serving modes

The website and collector have separate responsibilities. The collector acquires
and checks data under an operator's finite policy. The website searches records
stored on the server and produces inspectable evidence packets. Visitors do not
need a node, local installation or mandatory evidence-file download when using
a future hosted service. The current server still binds only to loopback.

## Indexed-only mode

```shell
python -B hemi_lookup_web.py --mode indexed-only
```

This is the default for the CLI and the `Jobs` backend. Hemi number/hash lookups
use the managed `service.sqlite3` index or the retained `catalogue.sqlite3` index.
Available results retain their original observations, proof material and coverage.
The server recomputes proof checks through independent bounded read requests.
Ordinary indexed searches do not create browser jobs or permanent receipt files.
Opening exact record links, copying summaries and optional client-side JSON
downloads remain available. A download preserves the displayed evidence snapshot.

If no usable indexed evidence is available, the lookup reports
`indexed_evidence_unavailable`. A missing/corrupt index reports `index_unavailable`.
This includes unavailable or incomplete compiled
data; it is not a globally negative publication search or an absence proof.

The backend rejects source rechecks and forged legacy source operations with
`source_operations_disabled`. The read API rejects unknown query parameters,
including discovery flags and Bitcoin ranges. The frontend omits those source
controls. Neither a forged request nor a missing/corrupt index starts acquisition.
Changing an input or reloading the page never starts the collector. The workspace
response identifies this configuration as `serving_mode: "indexed-only"`.

An indexed search does not refresh source observations or reset resource
accounting. Proof checks and historical coverage stay separate from runner
status. The documented [read API](READ_API.md) also supplies paginated browsing,
exact Hemi/publication records and consistent per-request SQLite snapshots.

## Developer discovery mode

```shell
python -B hemi_lookup_web.py --mode discovery
```

This explicitly enables the developer demo's existing bounded discovery and
source-recheck workflow. Sufficient indexed evidence is still preferred unless
a new search or Bitcoin range is requested. Source operations retain the existing
session ledger, deadlines, data/request limits and collector exclusion rules.
This mode does not start a background collector or expand its allowance.

## Server-side state

```shell
python -B hemi_lookup_web.py --mode indexed-only --state-dir PATH_TO_SERVER_STATE --port 8765
```

Use the directory holding the intended service/catalogue databases and accounting.
Legacy developer-discovery receipts stay in its `receipts/` directory. Indexed
reads generate no permanent receipt files. Operators manage this state; visitors
can search without downloading evidence. Export is optional.
Preserve existing evidence and accounting. A separate review copy is a snapshot,
not an accounting reset or authority to acquire more data.

See [collection and recovery](COLLECTION.md) for finite collection, operator stops
and the distinction between a runner heartbeat and blockchain observations.

## Remaining public-beta work

The separate [production adapter and operating runbook](HOSTING.md) provides a
Waitress WSGI read surface, proxy/process templates, monitoring and consistent
backup/restore preparation. It does not enable the loopback developer bridge on
the Internet. Host selection and actual deployment validation remain separate.

Both modes retain the loopback server's host/origin checks. The read API uses five
independent processing slots, bounded work/response sizes and a 16-connection
transport boundary. Explicit discovery retains its single active source job.
This is not an Internet-facing deployment. An explicit
[ongoing collection policy](OPERATING.md) is implemented for controlled local
use; sustained workload validation, full retention policy, hosting-adapter
hardening, monitoring, backup/restore, hosting and independent-user validation
remain separate public-beta deliverables. JSON exports remain verifiable offline.
