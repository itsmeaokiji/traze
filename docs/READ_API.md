# Indexed read API (v1)

The explorer and other applications can read the same retained publication index.
Every endpoint below uses GET, requires no source node or BFG URL, and performs no
acquisition, source recheck, accounting reset or permanent receipt-file write.
The current server binds to loopback only. These interfaces do not configure an
Internet-facing deployment, authentication gateway or cross-origin browser access.

Start the server with an existing index:

```shell
python -B hemi_lookup_web.py --mode indexed-only
python -B scripts/read_index.py --url http://127.0.0.1:8765 --block 5243119
```

The example reads index metadata, one publication page and, optionally, one block
record. It prints a compact result; it does not save or acquire evidence. Prepare
the supplied catalogue first as described in [the quickstart](QUICKSTART.md).

The browser's [Timeline view](TIMELINE.md) at `/browse?view=timeline` groups one
20-record `/api/v1/publications` page by exact Bitcoin block hash and highlights
exact keystone identities. It reuses the existing filters and cursors; `view` is
a browser parameter, not an API filter. Page groups are not complete block or
keystone histories. No acquisition or source refresh is triggered by browsing.

## Contract

The base path is `/api/v1`; JSON responses contain `api_version: "v1"`.
Clients should tolerate additional response fields and unknown error codes.
Breaking changes to documented field meanings or endpoint semantics require a
new API version. A record URL identifies a record, not an immutable query snapshot
or a promise of permanent hosting or complete historical availability.

### GET /api/v1

Returns endpoint discovery, the serving policy and read limits. The readable
browser guide is at `/api-docs`.

### GET /api/v1/index

Returns:

- `index_kind`: `maintained` for `service.sqlite3`, otherwise `catalogue`.
  Once present, the maintained index is authoritative. Corruption does not cause
  silent fallback to an older catalogue.
- `counts`: processed bodies, supported/unsupported publication occurrences,
  associated/unassociated publications and retained Hemi header variants.
- `bitcoin_height_bounds`: minimum/maximum source-reported processed heights, or
  `null`. These bounds are **not** a contiguous covered interval.
- `source_observations_current: false`, `history_complete: false`, and a coverage
  qualification. No request establishes current canonical chain membership.
- `last_import_stored_at`: the latest stored import time, or `null`. This is not
  a live chain observation, collector heartbeat or guarantee of freshness.
- Optional `collection`: read-only current collection state and `progress` for a
  maintained index, otherwise `null`. It shares the index-count SQLite snapshot.
  `scope` distinguishes `ongoing_service` from `finite_job`. An ongoing service
  supplies `service_start_height`, `checked_through_height`, `next_height` and
  `remaining_to_observed_tip`; missing checked coverage or usable observation
  times produce `null`, not an invented zero. `observed_bitcoin_tip` contains a
  recorded height/hash/time, response-time `age_seconds` and `clock_valid`.
  `current_job` accounts for its finite range, processed/checked bodies, pending
  metadata/ancestry and unsupported payloads; it is not whole-history accounting.
  The public form omits operator IDs, paths, policies and full per-height details.
  Zero remaining heights applies only to that recorded tip and declared service
  cursor. `controlled_replay` explicitly qualifies synthetic integration data.
  See [operating reports](OPERATING.md#freshness-and-coverage-reports).

### GET /api/v1/publications

Returns `items`, `page: {limit, next_cursor}`, applied `filters`, `index_kind`,
`retrieved_at` and source-observation qualifications. Summaries do not carry raw
transactions, ancestry arrays or full receipts, and do not verify every listed
proof. Open a Hemi block record to recompute its proof checks.

Each item has `id`, `record_url`, `bitcoin_block_hash`, `bitcoin_height`,
`height_conflict`, `txid`, `vout_index`, `keystone_height`, `keystone_id`, `state`
and `association_available`. `bitcoin_height` is `null` when no single reported
height is available. Unsupported payloads have no asserted keystone identifier.

The publication ID is the SHA-256 of the compact JSON array
`[bitcoin_block_hash, txid, vout_index]` with normalized lowercase hashes. Use the
returned ID rather than deriving identity from height, display order or a job ID.

Optional filters:

| Parameter | Meaning |
| --- | --- |
| `limit` | Page size, 1-50; default 20 |
| `cursor` | Opaque continuation from `page.next_cursor` |
| `keystone_height` | Exact positive source payload height |
| `bitcoin_height` | Exact nonnegative source-reported height |
| `state` | `all` (default), `associated`, `pending`, or `unsupported` |

`associated` means supplied Hemi metadata has been linked to the publication;
it does not mean current chain membership or authoritative Bitcoin finality.
`pending` means a publication with a supported payload has no complete supplied
Hemi association. The UI labels it **Hemi evidence unavailable**; the API value
does not imply that a background job is running. Reported-height conflicts are
a separate condition and can prevent an evidence link even for associated metadata.
`unsupported` means the parser cannot assert a supported keystone payload.

Results sort by descending reported Bitcoin sort height, then ascending
publication ID. Conflicting reported heights are not resolved into a canonical
height; ordering uses the largest recorded height only as a pagination key.

```shell
curl 'http://127.0.0.1:8765/api/v1/publications?limit=5&state=associated'
```

Follow `page.next_cursor` with the **same filters**. Cursors are bound to the list
kind, index kind and filters. Invalid/mismatched cursors return HTTP 400. Each page
is its own read snapshot; collection can add records between requests. Restart
from the first page to include new records sorted before a continuation. A cursor
is not a globally frozen multi-page export.

The browser's **Refresh index** action retains its applied filters/view and
requests a new first page plus index summary. It does not call acquisition
endpoints or promise a frozen snapshot across those separate HTTP responses.

### GET /api/v1/keystones

Uses the same filters/pagination and returns supported payloads grouped by
`height` and full payload identity, with `keystone_id`, `publication_count`,
`associated_count` and `publications_url`. Unsupported payloads are not grouped
into asserted keystones. Multiple keystone IDs may exist at one height; a height
link browses publications at that height rather than resolving competing views.
Ordering is height descending, then full payload ascending.

### GET /api/v1/blocks/{number-or-hash}

Accepts a positive supported Hemi height or full `0x`-prefixed block hash. A number
must have an unambiguous retained hash; it is not resolved through a live RPC.
The response contains:

- `record_url`: `/records/hemi/{exact-hash}`, safe to open in a new tab or after
  restarting the server with the same index.
- `receipt`: the JSON evidence export in the existing `hemi-publication-receipt-v1` format, including input
  binding, publication proofs, historical observations, coverage and issues.
- `verification`: checks recomputed from the supplied data. No stored success
  flag is trusted; a failed proof produces an error rather than a success packet.
- `id`: this generated view's identity, not a permanent job or receipt-file ID.
- `filename`: suggested name for an optional client-side receipt export.
- `finality`: an additive `traze-web-finality-assessment-v1` object. Supported
  named reference arithmetic uses a recorded Bitcoin tip, never the request time.
  `calculation` is the checked BFG reference result or `null`; `comparison` keeps
  signed-counter comparison values separately named. The block calculation can remain `null`
  while publication-only arithmetic is available. `input_binding` supplies exact
  receipt checksums and any explicit derived-input overrides. See the
  [calculation contract](FINALITY.md) for statuses, assumptions and reproduction.

The `finality` field does not change `verification`, the original `receipt` or
the meaning of `publication={id}`. Inspector selection never chooses the block
reference. Missing/invalid inputs, worker capacity or calculation time limits
can produce an unavailable assessment within an otherwise usable block response.
The request's overall read budget still applies. Source and first-height
acceptance require separately configured operator contexts; URL parameters cannot
choose policies, tips or acceptance. A generated indexed input with a retained
tip is publication-only and does not promote historical source observations to
confirmation for a new Hemi target. No result claims deployed Hemi equivalence or
current-chain finality.

Optional `publication={id}` requests a publication-focused view without dropping
other proofs from the receipt. `selected_publication_available` reports whether
that publication is in this view. The browser explicitly warns when it is absent.

```shell
curl 'http://127.0.0.1:8765/api/v1/blocks/5243119'
```

Saving just the response's `receipt` yields a portable packet for the existing
[offline verifier](VERIFICATION.md). The browser's Download evidence JSON action exports
the exact displayed packet from memory. Later index changes do not rewrite that
download, and ordinary searches create no server-side receipt file. Reopening a
record URL can produce more evidence or report changed/unavailable observations;
original historical source timestamps remain historical.

### GET /api/v1/publications/{id}

Returns `publication` (the summary), retained `data` (transaction bytes, output
index and inclusion material), `reported_bitcoin_heights`, `hemi_records` and
qualifications. `hemi_records` links exact published Hemi blocks with the
publication ID preserved in the URL. At most 20 metadata links and 50 height
observations are returned; explicit `*_truncated` fields report omitted entries.
This detail response is not a full Hemi receipt or a new proof-verification claim.

## Errors and admission

Errors use `{"error":{"code":"...","message":"..."}}`.

| HTTP | Typical codes | Meaning |
| --- | --- | --- |
| 400 | `invalid_filter`, `invalid_cursor`, `invalid_block` | Correct the request; duplicate/unknown query parameters are rejected |
| 404 | `indexed_evidence_unavailable`, `record_not_found`, `not_found` | Requested indexed evidence/route is unavailable; no global absence claim |
| 409 | `retained_view_conflict` | Retained hashes/header variants are ambiguous; no current-view resolution is invented |
| 422 | `invalid_proof`, `invalid_receipt` | Supplied evidence failed validation |
| 429 | `read_busy` | All five read slots are occupied; retry shortly |
| 503 | `index_unavailable`, `read_capacity`, `response_capacity` | Index unavailable/busy, or the request exceeds bounded work/response capacity |

HTTP 429/503 responses include `Retry-After: 1`. The transport also bounds active
connections to 16 and socket waits to five seconds. Busy transport admission can
return an empty HTTP 503 before JSON routing. Retry with backoff; do not assume
that retrying an oversized request unchanged will make it fit.

Read safeguards: at most five admitted API reads, 50 list items, a 2 KB URL,
eight query parameters, 8,000,000 serialized response bytes and 32,000,000
accounted decoded input bytes. SQLite work is limited to two million accounted
units: 1,000 per traced statement plus 1,000 per progress callback, conservatively
covering statements shorter than a callback interval. A cooperative five-second
budget is checked during SQL, indexed decoding and proof parsing. It is not a
hard end-to-end latency or physical-memory guarantee; serialization, scheduling
and socket delivery add overhead. An oversized result fails rather than silently
truncating a proof packet. Narrow lists with filters; an unusually large block
view may remain unavailable until a separately designed larger-evidence workflow.

The legacy `/api/state`, saved-example, job and source-operation endpoints are
private browser interfaces, not part of this versioned contract. In indexed-only
mode, legacy POST lookup returns HTTP 410 `read_api_required`; forged source
operations return HTTP 403 `source_operations_disabled`. Discovery mode retains
its explicit developer-only job workflow and finite source accounting.

## Verification and deployment boundaries

The production adapter additionally exposes `GET /api/v1/status`, a whitelist
of a local operator monitoring snapshot. `monitoring: "available"` means the
snapshot is at most 120 seconds old; recorded Bitcoin/Hemi observations can
still be stale or unavailable. This is separate from `/health/live` (process
response) and `/health/ready` (index readability). Status never queries sources.
These endpoints are provided by `hemi_lookup.public`, not the local demo bridge.

SQLite read transactions keep one response consistent while a collector commits.
Reads do not merge different databases or refresh provider observations. Passing
payload/inclusion/ancestry checks does not validate full Bitcoin chain consensus,
independently execute Hemi, prove globally earliest publication or calculate
authoritative Hemi Bitcoin finality. See [verification scope](VERIFICATION.md).

The [hosting candidate](HOSTING.md) supplies the production adapter and operating
tools. Actual host/permission/TLS validation, operating measurements, accepted
budget, off-host retention and independent-user validation remain necessary.
Local preparation alone does not establish a hosted public beta.
