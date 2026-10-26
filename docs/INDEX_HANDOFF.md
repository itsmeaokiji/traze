# Local search-to-index updates

Local discovery-mode searches save an immutable evidence receipt before queuing
an offline update to the same publication index used by Browse, Timeline and the
read API. An indexing failure does not discard a completed search. Opening an
included example does not queue an import. Existing local browser receipts can
be explicitly added using **Add saved evidence to index**.

## Evidence and coverage

Each queued receipt is bound to its SHA-256 digest. The ordinary receipt and
publication proof checks run before writable ingestion. Publication identity is
the containing Bitcoin block hash, transaction ID and output index. Repeated
observations retain their own receipt provenance without creating duplicate
publication records. Targeted associations reuse only matching, checked payload
metadata; unrelated pending records remain pending.

An inclusion proof establishes a transaction's inclusion against its supplied
Bitcoin header. It does not establish a complete scan of that block. Such a
record is stored and displayed as **Publication proof only**. Only a retained
raw body that passes the existing full-body parser can change the block's state
to `complete`. Missing or invalid bodies do not extend full-scan coverage. A
no-match receipt can contribute its recorded metadata and nominations without
creating an applicable publication or establishing an absence claim.

Bitcoin heights remain source-reported. Missing or conflicting heights are not
guessed; a publication record remains inspectable, but its Hemi evidence link
is withheld until the required position is unambiguous. Historical source dates
and qualifications are preserved. Neither opening an indexed record nor
updating the index rechecks canonical chain membership, recalculates independent
Hemi finality or upgrades first-publication assurance.

## Active index and recovery

The active index is `service.sqlite3` when present, otherwise `catalogue.sqlite3`
in the selected state directory. If neither exists, ingestion creates the
catalogue. An invalid maintained index requires operator review; it is never
silently bypassed in favor of another database. The additive
`receipt_ingestions` table records completed receipt digests. Existing receipt
formats and collector coverage rules are unchanged.

The queue lives in `index-updates/` beside the server's receipts. It uses the
existing single-writer lock and commits checked proof records and completed
bodies durably. Automatic work retries only eligible pending offline updates,
up to eight persisted attempts. Restarting the server preserves those attempts.
An explicit **Retry index update** can make another bounded attempt; it does
not resume a collector, renew an acquisition deadline, alter accounting or
contact a source. Invalid or changed receipts are rejected. Unavailable index
state and exhausted retries require explicit operator attention.

Each processing attempt uses the local read budget (five seconds and 32 MB), a
10 MB receipt bound, up to 512 block nominations and the existing raw-block
parser size bound. Partial progress can be reused on retry. Queue-status write
failure after a database commit does not discard that commit: status also checks
the receipt marker in the current active index. These are local safeguards, not
protocol limits or a throughput guarantee for a large installation.

The displayed body summary describes that receipt's index update. Later imports
can add full scans or associations; Browse reflects the active index after a
refresh. Neither a later import nor a refreshed Browse page rewrites an older
saved search's coverage.

Preserve receipts, the index, queue and accounting together during recovery.
Do not delete state to clear a pending update or obtain additional acquisition
allowance. Index-only/hosted readers do not run this local handoff worker or
expose its write action. No bulk migration or historical source acquisition is
started automatically.

## Local interface

- Saved-receipt responses may include additive `index_update` state.
- `GET /api/index-updates/<receipt-id>` reads the handoff status. It makes no
  public-source request.
- `POST /api/index-retry` accepts only `{"receipt_id":"<32-lowercase-hex-id>"}`.
  It requires the existing local Host, Origin and operation-token checks and
  discovery mode. It accepts no endpoint, file path, search range or quota.
- Browse publication summaries/details include additive `evidence_scope`:
  `publication_proof` or `full_block_scan`. Index counts include
  `proof_only_blocks`; `processed_bodies` continues to count complete scans only.

An already-open Browse page uses **Refresh index** to reload stored records.
Public read routes remain read-only. Large queues, sustained contention,
multi-host operation and representative live-source discovery are separate
validation concerns.
