# History witnesses and conditional first-height assessment

The offline `traze_evidence` command supports a separate version-2 packet that combines an existing anchored interval packet with authenticated Hemi history-contract account/storage witnesses. It requires Python 3.10 or newer and the standard library. It performs no RPC requests and needs no node, Traze service, cache, ledger or additional package.

The version-1 packet and its `verified_interval_evidence` result remain unchanged. The Explorer can display and export this conditional assessment. A separately versioned [snapshot extension](SNAPSHOT_ASSURANCE.md) binds a recorded source view and explicit historical-profile trust basis without changing version-2 policy or packet meanings.

## Run

Verify the mechanical evidence without accepting an external assumption policy:

```sh
python -B -m traze_evidence path/to/packet --hemi-block 5200117 --hemi-hash 0x2e9a22f7e0f7fa385b80c767ba5ab0e1939b140ea6a00780440933baeb6b48bd
```

A complete version-2 result is `verified_history_evidence`. Its `first_height_assessment.status` is `policy_not_accepted`; it does not infer absence before the anchor. A successful evidence check is deliberately distinct from accepting unverified external premises.

To request the separately qualified assessment, explicitly accept the supported policy as a caller:

```sh
python -B -m traze_evidence path/to/packet --hemi-block 5200117 --hemi-hash 0x2e9a22f7e0f7fa385b80c767ba5ab0e1939b140ea6a00780440933baeb6b48bd --accept-policy hemi-full-state-first-height-assumptions-v1
```

The expected number and full hash must be supplied independently of the packet. A packet cannot accept its own assumptions by adding a boolean or a policy-acceptance field. The command rejects unknown fields/profiles and only supports the fixed policy implemented by the verifier. Passing a history policy for a version-1 packet is rejected rather than silently upgrading it.

These single-line commands work in PowerShell and ordinary POSIX shells.

## What the policy assumes

`hemi-full-state-first-height-assumptions-v1` requires all of the following:

1. The selected Bitcoin branch is valid. The selected Hemi branch follows valid execution and rollup derivation under the declared rules. The checker does not independently establish either chain's consensus validity.
2. The expected history runtime persists, the correct complete parent hash is written before each supported block's transactions, and no later privileged transaction replaces that record. This invariant applies to every applicable publishing Q at or after the covering keystone in the accepted rule scope, including Q beyond the publisher already found.
3. The reviewed v1 codec, publication predicate, covering period and same-branch later-keystone support apply. Arbitrary future upgrades are excluded.
4. Constructing the reviewed full-width cross-chain commitment cycle is computationally infeasible. The analytical classical random-oracle model does not measure the world's historical hashing workload or establish a numerical confidence for concrete SHA-256/Keccak.

The last premise is explicit. A merely finite work bound or a 256-bit field is not automatically useful security. No historical global query envelope, confidence percentage or system-security-bit rating is supplied. Accepting the policy is an external trust/security choice, not another cryptographic proof produced by the checker.

The full assumption list is returned in every version-2 result, together with `accepted_by_caller` and `accepted_from_packet=false`.

## Packet layout

```text
packet/
  manifest.json                 # Version-2 wrapper and inline state witnesses
  interval/
    manifest.json               # An unchanged version-1 anchored interval manifest
    bitcoin/
      <bitcoin-block-hash>.bin
      ...
```

The fixed `interval/` subdirectory prevents a wrapper from selecting arbitrary paths. Its exact manifest checksum is bound by the outer manifest. Both manifests use bounded reads, duplicate-key rejection and strict supported fields. Evidence paths reject symlinks/junctions.

Outer schema: `traze-history-publication-evidence-v2`.

| Outer field | Meaning |
| --- | --- |
| `schema` | Exact version-2 schema identifier |
| `profile` | Exact fixed history/model profile from `traze_evidence.history.PROFILE` |
| `query` | Requested Hemi number and complete 0x-prefixed hash, matching the caller and interval packet |
| `interval_manifest_sha256` | SHA-256 of the raw `interval/manifest.json` bytes |
| `history_witnesses` | At most two covering/publishing state witnesses; one is enough when both states are the same block |

Each witness has `number`, `hash`, `proof`, `source` and `observed_at`. The first two identify an authenticated full header already present in the interval packet. Source/time are recorded provenance only, not signed provider attestations or a fresh canonicality check.

`proof` uses the EIP-1186 fields `address`, `accountProof`, `balance`, `codeHash`, `nonce`, `storageHash` and `storageProof`. Each storage row has `key`, `value` and `proof`. RPC metadata is cross-checked against authenticated bytes rather than trusted independently.

## History membership checks

The supported history account is `0x0000f90827f1c53a10cb7a02335b175320002935`, with runtime hash `0x6e49e66782037c0555897870e29fa5e552daf4719552131a0abce779daec0a5d` and an 8,191-slot ring.

For each required state:

- Authenticate account membership using `Keccak256(address)` and that header's complete state root.
- Decode the canonical account tuple and check the supported code hash, storage root, nonce and balance against RPC metadata.
- Authenticate the immediate parent's storage slot `(state_number-1) mod 8191`, keyed by `Keccak256(uint256(slot))`.
- Decode the canonical stored integer, restore omitted leading zero bytes losslessly to 32 bytes, and compare the complete parent hash.
- Optionally check the anchor's own slot while it is inside the current history window. That slot is not required forever and stale anchor-slot assertions are rejected.

The secure-MPT implementation checks canonical RLP/compact paths, hashed and short inline child references, used nodes, terminal values and bounded traversal. It accepts geth-style arrays containing hashed path nodes; inline children remain embedded in their parent. Proof order is immaterial, but redundant, duplicate or unused nodes are rejected.

The strict parent-based argument requires the Hemi anchor A to be **earlier than** the covering keystone K. `A=K` cannot automatically cover direct publication of Q=K. The ordinary full Hemi ancestry is checked by the interval verifier.

## Separate result facts

The result schema is `traze-history-verification-result-v2`:

- `interval_evidence` retains the complete version-1 result and its original guarantees.
- `history_evidence` reports the actual authenticated account/storage states and complete parent hashes. It explicitly says that every applicable state was not empirically checked.
- `policy` identifies external assumptions and whether the caller accepted them.
- `first_height_assessment` reports a conditional first applicable height only when the required evidence and caller policy are present.
- `assurance` separates `first_in_checked_interval`, `first_height_under_accepted_assumptions`, `pre_anchor_absence`, and the deliberately false `globally_first_proven` field.
- `scope` names the selected-rule meaning and explicitly excludes independent consensus validation, a fresh source check and unreviewed future upgrades.
- `issues` explains incomplete, invalid, unsupported or unaccepted parts.

`globally_first_proven=false` means no **unqualified independently established global guarantee** is asserted. It does not erase the separate `first_height_under_accepted_assumptions` conclusion when that qualified policy is accepted. The old version-1 field keeps its existing meaning.

A conditional success is `verified_conditional_first_height`. It combines computational exclusion before anchor C, complete directly checked absence in `[C,H-1]`, and an authenticated positive at H. It is stronger than selecting an earliest result in a timestamp-estimated window, but remains conditional on the explicit policy.

The conclusion is the first applicable Bitcoin **block height** supporting the requested Hemi block. A later-keystone publication can supply it. It is not necessarily the first direct publication of K, broadcast order, a unique transaction, a complete co-first set, independent finality or reward eligibility.

## Failure and exit semantics

| Exit | Meaning |
| --- | --- |
| 0 | Mechanical evidence passed; inspect the assessment and accepted-policy fields to distinguish evidence-only from conditional success |
| 2 | Invalid, contradictory or malformed evidence/identity; argparse also uses 2 for invalid command arguments |
| 3 | Incomplete or unsupported stronger evidence/profile |

When interval verification passed but a history witness is missing, unsupported or invalid, the version-2 result preserves those valid interval facts while blocking the stronger assessment. A missing witness is not an authenticated absence of a publication. Wrong roots/values or other proof contradictions cannot promote the result. An authentic account with another runtime is unsupported. Missing interval bodies or unresolved earlier candidates still fail the base interval contract.

No profile mismatch, source conflict, absent history proof, packet flag or accepted-policy option can override a failed mechanical check. Historical provenance remains historical; there is no implicit RPC refresh.

## Resource and trust limits

The existing interval caps remain: a 2,000,000-byte manifest, 4,096 Hemi headers, 64 Bitcoin bodies, 4,000,000 bytes per body and 64,000,000 aggregate body bytes. Both manifest caps apply independently. The outer manifest permits at most two required state witnesses. Each account/storage proof has at most 128 hashed nodes, 32,768 bytes per node, 200,000 decoded proof bytes and bounded RLP/trie depth. At most two storage rows are permitted per state.

These are local verification resource bounds. They are not adversary-work parameters in the computational security model. Full-state membership does not replay Hemi execution or rollup derivation, prove a profile's historical deployment, validate every privileged operation, select the most-work Bitcoin chain or make the hypothetical random-oracle model a concrete-hash theorem.

The parent-based argument does not require the anchor's own slot to survive indefinitely, but the implemented complete packet still has the 4,096-header ancestry cap. A successful synthetic membership check with an older anchor does not demonstrate an unlimited full-packet route. A real packet exceeding the verification envelope remains unsupported/incomplete rather than receiving a stronger assurance.

Normal Explorer visitors do not need this command or a local file download. Website integration is separate from this offline extension.

For website-managed snapshots, the display separates ordinary receipt completion and source-check times from `source_context.packet_prepared_at`, recorded only when a new packet snapshot is first published. Legacy snapshots without this field have no recorded packet preparation time; their receipt completion time must not be presented as one. Reopening or checking a retained packet does not update its observation times. Individual witness observations retain their own timestamps inside the packet. This contextual metadata does not select a chain or strengthen the assurance result.

## Primary references

- [EIP-1186 account/storage proofs](https://eips.ethereum.org/EIPS/eip-1186)
- [EIP-2935 historical block hashes](https://eips.ethereum.org/EIPS/eip-2935)
- [Pinned Hemi history system call](https://github.com/hemilabs/op-geth/blob/539f4e33c02028c8a4207621cd04fa8102e372a0/core/state_processor.go#L282)
- [Pinned runtime and fixed address](https://github.com/hemilabs/op-geth/blob/539f4e33c02028c8a4207621cd04fa8102e372a0/params/protocol_params.go#L249)
- [Pinned secure storage encoding](https://github.com/hemilabs/op-geth/blob/539f4e33c02028c8a4207621cd04fa8102e372a0/trie/secure_trie.go#L200)

Pinned references identify the supported interpretation; they are not historical execution certification. The computation model and all-Q invariant are explicitly external premises of the caller-accepted policy.
