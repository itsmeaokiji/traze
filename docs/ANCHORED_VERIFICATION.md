# Anchored publication evidence

The `traze_evidence` package verifies a separately versioned packet containing full Hemi headers, authenticated Hemi transaction bodies and complete Bitcoin bodies for a finite interval. It runs with Python 3.10 or newer and the standard library. No node, RPC, pip package, Traze cache or accounting ledger is required.

This command is separate from the existing JSON evidence verifier using supplied headers. It does not change the Explorer UI or the guarantees of older receipts.

## Run

From a checkout containing `traze_evidence/`, or a portable distribution containing that package:

```sh
python -B -m traze_evidence path/to/packet \
  --hemi-block 5200117 \
  --hemi-hash 0x2e9a22f7e0f7fa385b80c767ba5ab0e1939b140ea6a00780440933baeb6b48bd
```

In PowerShell, use one line or replace the shell continuation character with a PowerShell backtick. Supply the expected number and hash independently of the packet to prevent attaching evidence for one block to another requested block.

The command reads files and prints JSON. It makes no requests and writes no cache, receipt, report or ledger. Redirect stdout to a file if a result record is wanted.

| Exit | Meaning |
| --- | --- |
| 0 | `verified_interval_evidence`: the supplied evidence satisfies the implemented profile and finite-coverage checks |
| 2 | `invalid`: contradictory, altered, malformed or unsupported packet/identity; incorrect command arguments also use argparse's exit 2 |
| 3 | `incomplete`: required evidence is missing/unreadable, an earlier candidate remains unresolved, or supplied source views conflict |

Exit 0 does **not** mean globally first publication was proven.

## Packet format

```text
packet/
  manifest.json
  bitcoin/
    <bitcoin-block-hash>.bin
    ...
```

Schema: `traze-anchored-publication-evidence-v1`.

| Manifest field | Content |
| --- | --- |
| `schema`, `profile`, `networks` | Exact versioned acceptance rules, Hemi chain 43111 and Bitcoin mainnet |
| `query` | Requested Hemi number and full hash |
| `anchor` | Hemi ancestor number, raw transaction index and referenced Bitcoin interval-start height |
| `publication` | Publishing keystone number and selected Bitcoin height, txid, transaction index and output index |
| `hemi_headers` | Contiguous full raw RLP headers, declared identities and numbers |
| `hemi_bodies` | Every ordered raw transaction in the anchor and publishing Hemi bodies |
| `bitcoin_blocks` | Contiguous height/hash/size/checksum/transaction-count manifest for the complete interval |
| `observations` | Optional recorded Bitcoin source views; these are not authenticated provider signatures |

All named fields are required; `observations` may be an empty array. Arbitrary file paths and assurance flags are not accepted in the manifest. Bitcoin filenames derive from validated block hashes. Checksums identify bytes; they do not authenticate a provider or prove consensus validity.

The portable packet contains the Bitcoin bodies themselves. Full Hemi transaction-trie reconstruction authenticates the relevant raw transactions; a separate supplied MPT branch or Bitcoin Merkle branch is unnecessary for this full-body format.

## Implemented profile

Profile: `hemi-v1-rlp21-isthmus-anchored-interval-v1`.

- Legacy Keccak-256 over canonical full Hemi RLP headers with exactly 21 supported fields, including field-width and integer checks.
- Full 32-byte Hemi ancestry connecting the requested block, the covering keystone, the anchor and the publishing keystone.
- Full ordered transaction-trie roots for the anchor and publishing bodies. Values are raw typed transactions, not transaction hashes.
- Type-0x7c Bitcoin Attributes transaction addressed to `0x8888888888888888888888888888888888888888`, with the reviewed `c94f1cca` calldata layout. Its literal internal-order Bitcoin hash must equal the interval-start header hash.
- Authenticated type-0x7e L1 attributes at publishing transaction index zero, with the Isthmus `098999be`/176-byte encoding. The L1 origin number is not a Bitcoin height.
- Exact reconstruction of the complete 76-byte abbreviated v1 keystone, including the previous keystone on the same Hemi branch.
- Noncoinbase HEMI publications found in the first data push after OP_RETURN, across all transaction outputs. PUSHDATA1/2/4 are recognized; a later push is not substituted for the first one.
- Covering period 25 and same-branch later-keystone support without a lag limit, as a declared historical interpretation. This does not execute op-node or establish equivalence with its finality rules.

The publication set is an explicit output-inspection predicate. It does not establish BFG reward eligibility, input signature validity, miner payments or agreement with every historical deployed configuration.

The narrow profile intentionally rejects other header and L1-attributes versions. A passing result is not evidence that this format supports every Hemi era. Unknown versions need a reviewed profile rather than permissive field-count acceptance.

## Complete interval checks

Every supplied Bitcoin body is parsed in full. The verifier checks canonical CompactSize, witness framing, coinbase positioning, the transaction Merkle root with duplicate-sibling mutation rejection, header identity, its stated proof-of-work target, contiguous full-hash parent links and BIP34 height.

The interval begins with the authenticated Bitcoin reference C and ends at selected positive publication H. Complete absence is checked in `[C, H-1]`; a verified selected output at H supplies the positive upper bound.

Earlier supported candidates below the covering Hemi height can be excluded. A supported candidate at or above the covering height that could be a valid keystone produces `incomplete` until additional association evidence resolves it. An unsupported earlier HEMI format also produces `incomplete`. The verifier does not silently assume either candidate is irrelevant.

Candidates at the selected height do not all have to be resolved to verify one positive publication at that height. The result does not claim the complete set of co-first transactions.

## Separate assurance fields

| Result field | Meaning |
| --- | --- |
| `anchor_reference.literal_reference_verified` | The authenticated Hemi transaction contains the actual interval-start Bitcoin hash |
| `assurance.first_in_checked_interval` | One verified applicable publication at H and no unresolved applicable candidate in the complete earlier checked interval |
| `assurance.globally_first_proven` | Always false in this version |
| `assurance.pre_anchor_absence` | Requires a separate computational starting-bound argument |
| `source_observations.recorded_agreement` | Supplied observations are internally consistent with the supplied headers, or were not supplied |

The checker cannot establish that no circular/anticipatory commitment was ever constructed. The bounded random-oracle starting-bound model, its global query/label budgets and its application to real SHA-256/Keccak are separate premises. A successful offline run must not be used as proof of that theorem or converted into a confidence percentage.

The checker also does not select the most-work Bitcoin chain, execute Hemi, authenticate source responses, perform a fresh canonicality check, validate Bitcoin scripts/UTXOs or difficulty history, or recalculate Hemi Bitcoin finality. BIP34 binds the supplied coinbase's encoded height; it does not independently select the global chain.

Normal Explorer visitors do not need to run this command or save files. Backend reuse and Explorer export/integration are separate changes.

## Verification limits

This version caps the manifest at 2,000,000 bytes, full Hemi headers at 4,096, the Bitcoin interval at 64 bodies, each body at 4,000,000 bytes and aggregate body data at 64,000,000 bytes. All caps apply together. A packet may hit the byte cap before a count cap.

RLP nesting, integer widths, declared counts and permitted filenames are bounded. Symlink/junction evidence paths and paths escaping the packet directory are rejected. These are local verification resource controls; they do not change collector budgets or represent cryptographic adversary limits.

This profile supports post-BIP34 mainnet heights, beginning at 227,931 as specified by [Bitcoin Core's mainnet parameters](https://github.com/bitcoin/bitcoin/blob/v28.0/src/kernel/chainparams.cpp#L85). Stated-target checks are not independent difficulty validation.

## Primary protocol references

- [Pinned Hemi v1 codec](https://github.com/hemilabs/heminetwork/blob/bf771ced8cfd2fb782c3b0d6d0bc8ea87b1e7cc1/hemi/hemi.go)
- [Pinned first-push publication parser](https://github.com/hemilabs/heminetwork/blob/bf771ced8cfd2fb782c3b0d6d0bc8ea87b1e7cc1/hemi/pop/pop.go)
- [Reviewed Hemi keystone construction](https://github.com/hemilabs/op-geth/blob/539f4e33c02028c8a4207621cd04fa8102e372a0/core/blockchain.go)
- [Bitcoin Attributes encoding](https://github.com/hemilabs/op-geth/blob/539f4e33c02028c8a4207621cd04fa8102e372a0/core/types/btc_attributes_deposit_data.go)

Pinned references make this profile reviewable. They do not prove that every historical node used that revision. Historical rule applicability remains explicitly declared in the result.
