# Snapshot-bound publication assessment

Version 3 adds a recorded Hemi source view and an explicit historical-profile
trust basis to unchanged version-2 history evidence. It is a conditional first
applicable Bitcoin **block-height** assessment within a specified Hemi branch
snapshot. It is not a current-chain certificate, unique first transaction,
independent finality calculation or independently certified historical execution.
Version-1 and version-2 packets, policies and result meanings remain unchanged.

## External acceptance

The verifier requires both caller options for a conditional conclusion:

```text
--accept-policy hemi-snapshot-first-height-assumptions-v1 --accept-context <canonical-context-sha256>
```

The context checksum must be independently selected by the caller or server
operator. A checksum binds a choice of context; it does not authenticate a source
or establish that its assertions are true. Reading it from an untrusted packet
and accepting it without review defeats that trust boundary. Packet fields never
set either caller option. No option supplies missing or contradictory evidence.

Without acceptance, a mechanically valid packet returns
`verified_snapshot_evidence`, not a conditional first-height conclusion. The old
history policy does not accept the new snapshot premises. A mismatching caller
context checksum rejects the stronger assessment. The website only shows its
qualified label after the same checks and caller acceptance pass.

## Format and checks

```text
packet/
  manifest.json                   # traze-snapshot-publication-evidence-v3
  history/
    manifest.json                 # unchanged version-2 manifest bytes
    interval/
      manifest.json               # unchanged version-1 manifest bytes
      bitcoin/<block-hash>.bin
```

The outer manifest contains `schema`, the fixed `profile`, `query`,
`history_manifest_sha256` and `assessment_context`. The context schema is
`traze-first-height-snapshot-context-v1`; `traze_evidence.snapshot` defines its
strict supported fields and exact reference-profile basis. Canonical context
identity is SHA-256 of UTF-8 JSON with sorted keys, compact separators, ASCII
escaping and finite JSON numbers. Raw nested manifest checksums remain separate.

Checks bind the query and publication/output to independently requested evidence,
the strict Hemi/Bitcoin anchor to the authenticated interval, and the source's
requested/publisher full hashes and parent/state-root/timestamp metadata to
authenticated Hemi headers. The selected snapshot must include the publisher,
and its identity must match the recorded number recheck. Observation times need
timezones and consistent order. Context/network/profile/domain contradictions
block a stronger conclusion. Missing context is incomplete. The original interval
and history witnesses are still checked, not replaced by RPC claims.

The domain includes every eligible same-branch keystone from the covering
keystone through the selected snapshot, including those after the found publisher.
The selected source view records a latest-block selection followed by a number
recheck. These provenance records are not signed attestations. A supplied snapshot
hash is not independently recomputed or linked through all post-publisher headers.
Reported block time is not a verified wall-clock cutoff. Future snapshots,
arbitrary forks and unreviewed rule changes are outside this version's domain.

## What remains assumed

The operator explicitly accepts coherent valid-chain behavior by the selected
validating RPC; historical applicability of the reviewed execution, derivation,
deposit-origin, upgrade and protected-history regime over the full domain; valid
retained Bitcoin branch selection; the supported publication predicate; and
practical hardness of the reviewed typed SHA/Keccak extractor commitment graph,
including zero-output self-loops. A client version is provenance, not historical
deployment attestation. These assumptions are not established by consistent RPC
answers, two history witnesses or a checksum. No numerical security confidence or
measured global historical computation envelope is asserted.

On conditional success, `first_height_under_accepted_assumptions` and
`snapshot_bound_under_accepted_assumptions` are true. `globally_first_proven`
stays false, as do independent execution/consensus/current-membership/finality
guarantees. The first-height inference before the Bitcoin anchor and the complete
direct interval checks remain distinct. Original Bitcoin observation times are
retained; a Hemi snapshot is not a fresh Bitcoin membership check.

## Offline use and server configuration

Verify with independently known requested block number and full hash:

```text
python -B -m traze_evidence <packet> --hemi-block <number> --hemi-hash <full-hash>
```

Add both acceptance options only after reviewing the external premises and exact
context. `--accept-context` alone does not accept the policy. Existing exit
semantics remain: 0 means mechanical success (inspect policy/assessment fields),
2 invalid, 3 incomplete/unsupported. The verifier performs zero network requests.

`python -B -m hemi_lookup.snapshot_packet --help` describes the offline wrapper
for an existing history packet and recorded context file. It copies only bounded
fixed evidence paths, verifies the result, refuses an existing output directory,
and does not accept policy or acquire source data. It does not automatically
collect fresh snapshot/profile context for arbitrary searches.

Local and public read adapters accept the new policy via `--first-height-policy`
and the separately selected checksum via `--accept-context`. Existing defaults
retain their version-2 behavior. The source-free evidence worker receives those
options from server configuration, never from a visitor request or receipt.
Source conflicts suspend the stronger label. Downloaded packages include all
three manifests, the exact bodies, verifier source and these instructions; the
recipient independently chooses whether to accept the assumptions and context.
