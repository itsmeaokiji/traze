# Evidence and verification

Traze links a requested Hemi block to an applicable Bitcoin publication through supplied Hemi metadata and ancestry. A publication transaction carries a reviewed Hemi keystone commitment.

## Separate guarantees

| Layer | What Traze checks or records |
| --- | --- |
| Payload | Reconstructs the reviewed 76-byte abbreviated keystone and compares publication data |
| Transaction and inclusion | Recomputes transaction identity, parses publication outputs and checks Merkle inclusion against the supplied Bitcoin header |
| Header | Checks header identity and the stated proof-of-work target; this is not a validated full header chain |
| Hemi association | Checks the supplied keystone metadata and parent links to the exact requested block |
| Source observations | Records provider views, identity checks, conflicts and observation times |
| Coverage | Records processed ranges, missing bodies, unsupported formats and unresolved candidates |

Proof checks on supplied evidence, source-view agreement and search completeness answer different questions. Distinct API hosts do not prove independent infrastructure.

## Result meanings

- **Found:** At least one applicable positive proof is available, with the packet's source and coverage qualifications.
- **Scoped no-match:** No applicable match in the checked scope; global absence is not established.
- **Incomplete:** Missing, unsupported, conflicting or resource-limited work prevents a complete answer.

Retained mathematics can remain valid when a current source check fails. A source conflict alone is not proof of a live reorg.

## Selection and time

The default selects the earliest observed applicable Bitcoin height within available evidence. It does not establish globally first publication. A later keystone can support an earlier requested block without establishing that no direct publication exists elsewhere.

Viewing another proof changes the displayed record, not the receipt's original selection, source time or search coverage. JSON export preserves the packet; the readable summary is explanatory context.

The route is visible by default. Select a chain node or open **Inspect record details** for exact identifiers and source links. **Verification details** and **Scanned Bitcoin blocks** expose the longer checks and per-block table. The range, stopping reason and unresolved coverage issues remain visible without opening them. Missing scan coverage is different from an unresolved Hemi association or an unsupported payload.

## Boundaries

Traze does not independently execute Hemi, validate the entire Bitcoin header/chainwork/UTXO state, establish authoritative Hemi finality-counter equivalence, or verify PoP reward eligibility. Bitcoin depth and proof validity for supplied evidence are not substitutes for those guarantees.

The current codec and rules were reviewed against pinned [heminetwork keystone source](https://github.com/hemilabs/heminetwork/blob/bf771ced8cfd2fb782c3b0d6d0bc8ea87b1e7cc1/hemi/hemi.go) and [publication codec](https://github.com/hemilabs/heminetwork/blob/bf771ced8cfd2fb782c3b0d6d0bc8ea87b1e7cc1/hemi/pop/pop.go). Reviewed source is not automatic evidence of every future deployed format.

## Fixtures

[The corpus manifest](../tests/fixtures/bitcoin_blocks/manifest.json) records source receipts, historical observation times, byte sizes and SHA256 fingerprints for 17 retained bodies. Checksums bind file bytes; parser and proof checks are separate.

[Example notes](../demo_results/README.md) distinguish original receipts, derived catalogue packets and controlled test data. Offline verification makes no new claim about current canonical membership.
