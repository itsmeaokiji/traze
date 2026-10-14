# Included evidence examples

These files contain historical publication evidence and a scoped-negative example. Original JSON bytes are preserved. Generated verifier reports are excluded from source control.

| File | Meaning |
| --- | --- |
| mainnet_5243117.json | Original September ordinary-block discovery; four applicable proofs in Bitcoin 965931 |
| mainnet_5243125.json | Original direct-keystone example |
| scoped_no_match.json | Same ordinary target, checked Bitcoin range 965925–965930; no global absence claim |
| milestone2_automatic_20261004T170034660491Z.json | Original March automatic discovery, requested 4028425 and later support at Bitcoin 941750 |
| milestone3_march_later_hash_20261004T174823173306Z.json | Exact-hash March example used in UI controls |
| main_catalogue_5243119.json | Derived catalogue packet for an additional ordinary target; historical source views, not new confirmation |
| phase1_5243118.json | Derived catalogue packet for the separate reference consumer |

Original observations and qualifications are inside each packet. A current-looking packet creation time or derived target does not update the original source-check time.

The [17-body manifest](../tests/fixtures/bitcoin_blocks/manifest.json) binds the raw fixture bytes and the two import receipts. Mathematical checks and source observations are distinct.

Captured metadata used exclusively by recovery tests is documented in [source test fixtures](../tests/fixtures/source-feasibility/README.md).

Use the [quickstart](../docs/QUICKSTART.md) and [verification guide](../docs/VERIFICATION.md). Negative packets contain no positive proof to verify; verifier success for such a packet is not a cryptographic absence proof.
