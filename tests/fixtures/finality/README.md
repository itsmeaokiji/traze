# Finality calculation fixtures

`recorded_5200117.json` retains public-chain evidence from the recorded October 20,
2026 lookup of Hemi 5,200,117. It contains six publications in Bitcoin 965,045,
their raw transactions/inclusion material, supplied Hemi ancestry, source labels
and recorded source observations. Runtime job identifiers, resource ledgers and
installation bookkeeping are omitted. It is a derived test receipt, not a fresh
query or an assertion of current canonical membership.

The recorded Bitcoin tip moved from 972,287 to 972,289 during that lookup. The
last tip hash is
`0000000000000000000177971cad696408cb11dfe1a2cb4833d6f0f177a5c6ac`.
Recomputing at that named historical tip does not advance with wall-clock time.

The selected publishing keystone 5,200,225 and transaction
`00331c9acca31ab4533d9b2f10f3e9926e0279af3203c90245049de3387d1900`
match the separately included
[snapshot example](../../../examples/snapshot-first-height/README.md).
Its assumptions and later Hemi observation times remain explicit and separate.

`reference_vectors.json` records ten arithmetic cases traced to the pinned BFG
routine/constant and the supplied signed AMA model. The archived-source checksums
bind the reviewed public reference bytes. They are not outputs from an executed
Go service or a deployed mainnet endpoint. Height-zero and uint32-boundary vectors
are numerical controls, not actual publication claims.

Tests use these fixtures together with existing direct/later-keystone historical
receipts. Temporary corruption and metadata changes are controlled negative cases;
the original files are not edited. See [the calculation contract](../../../docs/FINALITY.md).

`website_assessment_5200117.json` is an actual source-free website-worker result
for `recorded_5200117.json` and the included version-3 snapshot packet, with the
fixture's exact snapshot/source contexts explicitly selected. It contains both
named arithmetic models and a conditional recorded block reference; it is not a
fresh or deployed-reference response. UI tests add controlled presentation-only
receipt metadata without changing the historical proofs. Packet input accounting
is specific to this retained example and is not a production cost benchmark.
