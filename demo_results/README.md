# Included evidence examples

These public examples are derived from historical captures. Development-run
identifiers, installation-wide usage counters and stage bookkeeping are omitted.
Proof data, source observations and material methodology remain historical.
[Provenance fingerprints](public-example-provenance.json) identify the original
captures and the current public copies. Generated verifier reports are excluded
from source control.

| File | Meaning |
| --- | --- |
| mainnet_5243117.json | September ordinary-block discovery; four applicable proofs in Bitcoin 965931 |
| mainnet_5243125.json | Direct-keystone historical example |
| scoped_no_match.json | Same ordinary target, checked Bitcoin range 965925-965930; no global absence claim |
| mainnet_4028425_automatic.json | March automatic discovery, requested 4028425 and later support at Bitcoin 941750 |
| mainnet_4028425_hash.json | Exact-hash March example using a retained Bitcoin body; not a cold discovery benchmark |
| main_catalogue_5243119.json | Offline catalogue packet for an additional ordinary target |
| mainnet_5243118_catalogue.json | Offline catalogue packet used by the separate reference consumer |

The two catalogue packets are rebuilt offline from the included corpus. A new
packet creation time or a different derived target does not update the original
source-observation times or establish current canonical membership.

The [17-body manifest](../tests/fixtures/bitcoin_blocks/manifest.json) binds the
unchanged raw Bitcoin fixture bytes and the current public import receipts.
Mathematical checks and source observations are distinct. Captured metadata used
only by recovery tests is documented in [source test fixtures](../tests/fixtures/source-feasibility/README.md).

Use the [quickstart](../docs/QUICKSTART.md) and [verification guide](../docs/VERIFICATION.md).
Negative packets contain no positive proof to verify; successful parsing of such
a packet is not a cryptographic absence proof.
