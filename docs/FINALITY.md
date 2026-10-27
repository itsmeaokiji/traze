# Reference finality calculations

The standalone calculator recomputes named height models from retained publication
evidence. It does not call a finality endpoint, start collection, change receipts
or operate a miner. The website and read API use a separate bounded adapter for
this calculator. Ordinary receipt exports and proof meanings remain unchanged.

## Supported profiles

Let H be a source-reported Bitcoin publication height and T the explicitly selected
recorded Bitcoin tip height. Every result names its profile.

| Profile | Calculation | Threshold meaning |
| --- | --- | --- |
| `bfg-v2-464bcdf0-reference` | Effective confirmations = T - H | The pinned routine's `super_finality` is true at T - H >= 10 |
| `ama-signed-nine-block-model` | Signed counter = T - H - 9 | The signed-counter comparison reaches zero at T = H + 9 |

Ordinary inclusive Bitcoin depth is T - H + 1 and remains a separate field. For a
publication at 100 and tip 109, inclusive depth is 10, the signed comparison counter is zero,
and the pinned BFG arithmetic returns effective confirmations 9 with
`super_finality=false`. At tip 110, those values become 11, 1, and 10/true.
These are illustrative arithmetic cases, not observations of a deployed node.

The BFG profile pins revision
`464bcdf0af87fdcfd0544532f92ca4e094384574`: its
[calculation/selection source](https://github.com/hemilabs/heminetwork/blob/464bcdf0af87fdcfd0544532f92ca4e094384574/service/bfg/bfg.go)
and [API constant](https://github.com/hemilabs/heminetwork/blob/464bcdf0af87fdcfd0544532f92ca4e094384574/api/bfgapi/bfgapi.go).
The signed-counter comparison is defined here as T - H - 9. It reaches zero
at T = H + 9 and is not an alternative claim about the pinned binary. Its
existing profile ID is retained for compatibility with saved calculations.
Neither profile establishes equivalence with the deployed public endpoint.

The calculator does **not** emulate the whole BFG RPC handler. That handler queries
keystone validity/descendants and a Bitcoin backend, compares candidates in batches
and can stop after its ultrafinality threshold. The offline block model instead
uses a separately justified conditional first-publication block-height reference. Numerical formula
agreement alone is not proof of identical handler behavior.

## Run a publication calculation

With Python 3.10+ from the repository root:

```shell
python -B -m hemi_lookup.finality --receipt tests/fixtures/finality/recorded_5200117.json --hemi-block 5200117 --hemi-hash 0x2e9a22f7e0f7fa385b80c767ba5ab0e1939b140ea6a00780440933baeb6b48bd --tip-height 972289 --tip-hash 0000000000000000000177971cad696408cb11dfe1a2cb4833d6f0f177a5c6ac
```

This example recomputes six ordinary publication proofs and returns
`publication_calculations_only`. For the selected publication at Bitcoin 965,045,
the recorded tip gives inclusive depth 7,245, BFG effective confirmations 7,244
and the profile's `super_finality=true`. Those historical numbers are not the
current finality of Hemi. The Hemi-block calculation remains `not_established`
without first-publication reference evidence and caller acceptance.

Add `--profile ama-signed-nine-block-model` to calculate the signed-counter
signed model. It returns signed counter 7,235 for the same historical inputs;
it does not relabel BFG's effective-confirmation field.

## Conditional Hemi-block calculation

The conditional Hemi-block mode requires all of the following:

1. The ordinary receipt is recomputed and bound to the independently requested
   Hemi number/hash and Bitcoin tip height/hash. Supplied Hemi header metadata and
   Bitcoin block positions must be internally coherent. Saved success/selection
   flags never establish a reference.
2. The original version-3 [snapshot checker](SNAPSHOT_ASSURANCE.md) verifies actual
   evidence. The first-height policy and exact snapshot context are accepted
   separately by the caller. The new calculator does not change those rules.
3. The checked publication appears with the same height, block hash, transaction,
   position, output and publishing keystone in the receipt. Shared full Hemi
   headers match the authenticated packet metadata. A receipt cannot contradict
   the selected first height or extend the assessment beyond its Hemi domain.
4. Recorded source observations are internally consistent and the caller accepts
   both `hemi-finality-recorded-view-assumptions-v1` and the exact separately
   displayed source-context checksum. Neither option is read from receipt flags.

The source-context policy externally assumes that the recorded Bitcoin positions,
publication hashes and tip observations belong to one valid Bitcoin branch, and
that the Hemi identities and accepted snapshot describe a coherent valid Hemi
branch. Hashes bind inputs; they do not authenticate those assertions. First-height
premises are in addition to these source-view premises.

A forward-moving tip is recorded as `advanced_forward`. Its ancestry is still an
external assumption, not a verified Bitcoin header chain. A decreasing tip or
different hashes at one tip height prevents a successful block calculation.
Observation times require timezones and consistent operation order. A Hemi snapshot
may have been observed later than the receipt's Bitcoin tip; the output retains
both sets of times and does not certify a simultaneous cross-chain snapshot.

For the supplied public example, the first-height packet is
`examples/snapshot-first-height/packet`. Its independently documented snapshot
context is in the [example instructions](../examples/snapshot-first-height/README.md).
After reviewing that context and this source policy, run the ordinary command with:

```text
--first-height-packet examples/snapshot-first-height/packet
--accept-first-height-policy hemi-snapshot-first-height-assumptions-v1
--accept-first-context 840184d1dab42688a714b71deba8938cb97958d48191bda32619586df4b03b34
--accept-source-policy hemi-finality-recorded-view-assumptions-v1
--accept-source-context ed69ab5f419d09cc8e7db6c60dbe1cdfea866d7cf3952ecccc1c9973e952bd6c
```

These are arguments to the same command, not separate shell commands. The source
checksum belongs only to the exact supplied fixture and context contract. Review
it before acceptance; do not automatically accept a checksum supplied by an
untrusted packet. Modified receipt bytes require a new review and context choice.

On success the overall status is `conditional_block_calculation`, with block status
`calculated_under_accepted_recorded_assumptions`. The block reference remains
separate from `display_selection`: choosing another retained publication does not
silently replace the conditional first-publication block-height reference. Its model applies only
within the accepted Hemi snapshot and recorded Bitcoin-view assumptions.

## Output and failure meanings

The Python entry point is `hemi_lookup.finality.calculate_receipt`. Callers supply
their expected query/tip identities and optional policy/context choices; it returns
fresh JSON-compatible data under `traze-offline-finality-calculation-v1`.

- `publication_calculations` recomputes profile arithmetic for checked positive
  proofs at source-reported heights. It does not establish global height/branch
  validity or the first applicable publication.
- `source_context` records consistency, exact input identity, times, tip movement
  and external acceptance. Provider agreement is not independent consensus.
- `first_height_evidence` retains the actual checker's outcome, assumptions and
  snapshot domain. Invalid, missing, unsupported or unaccepted evidence prevents
  promotion while ordinary publication calculations remain separate.
- `block_calculation.values` is null when its requirements are not met. No positive
  proof yields `incomplete`, not zero finality. Contradictory input/proof identities
  raise a `FinalityError`; the CLI returns a structured invalid result.
- `assurance` keeps fresh membership, consensus/execution, deployed equivalence,
  complete-handler reproduction, unconditional global proof and reward claims
  false. Only the named profile arithmetic has been independently recomputed.

CLI exit codes are 0 for computed publication/conditional-block results, 2 for
no positive calculation, and 1 for invalid evidence/arguments handled by the
calculator. Standard argparse usage errors also use exit code 2.

## Bounds and validation limits

Receipts are limited to 8 MB, 128 proofs, 257 ancestry headers per proof and 4,096
combined ancestry headers. Input heights use a declared uint32 domain; boolean,
fractional, negative or out-of-range positions are rejected. JSON input rejects
duplicate keys and nonfinite values. The original packet file/proof/body bounds
remain in force. Each call captures its receipt inputs and recomputes verification;
no persistent success flag or new result cache is used.

The synchronous standalone command is not a website request handler. The website
adapter runs it in an owned child process, with at most two calculation workers,
a three-second calculation clock and the caller's existing read clock. Expiry
kills and reaps that child. Capacity rejection reports an unavailable calculation
without starting another child. No persistent calculation-success cache is used.

Reference vectors are labelled **code-traced expected values**. Neither the Go
reference service nor a live deployed finality endpoint was executed for those
vectors. Tests combine them with actual retained receipts, the original public
snapshot packet, offline CLI execution and malformed/altered-input controls.
Historical success does not establish arbitrary-input coverage, current finality,
sustained performance, PoP rewards or production readiness.

## Website and read API

Saved-result wrappers and `GET /api/v1/blocks/{number-or-hash}` include an additive
`finality` object with schema `traze-web-finality-assessment-v1`. The result page
shows **Reference finality calculation**, with the BFG reference as its primary
model and the separately labelled signed-counter arithmetic in expandable details. It names
the publication reference, recorded Bitcoin tip, source-observation time and
calculation scope. Reopening the page does not advance the tip or refresh sources.
The conditional Hemi-block reference never follows inspector selection.

`finality.calculation` contains the standalone result. `finality.comparison` holds
Signed-counter comparison values derived from the same checked publication identities and tip; this
does not execute the proof checker twice. `finality.input_binding` identifies the
original receipt and exact calculation-input checksums. Failed or unavailable
calculation inputs produce `calculation: null`, not zero finality. Incomplete
block-reference requirements leave `calculation.block_calculation.values: null`
while separately qualified publication arithmetic may remain available.

Generated indexed receipts can contain historical observations instead of a
top-level Bitcoin tip. The adapter chooses the latest completed, dated retained
observation with complete before/after tip identities. Conflicting hashes at one
tip height, backwards tips, malformed observations or missing dated tips prevent
selection. It records the original observation target/scope and the exact local
receipt overrides. These derived inputs explicitly retain an unconfirmed source
view for the generated target. They support publication-only arithmetic and can
never authorize a conditional block result. Neither index retrieval time nor the
largest observed height is substituted for a fresh Bitcoin tip.
An identity marked `not_rechecked` or `unavailable` is retained in the original
receipt but omitted from the derived calculator observations; a missing comparison
is not a contradictory source reply. Actual recorded conflicts remain separate
and continue to suspend block promotion.

The adapter accepts no visitor-supplied policy, tip, packet path or context
checksum. The local server and WSGI adapter share constructor/command-line
settings. Source assumptions are unaccepted by default. A conditional block
result needs the existing version-3 snapshot policy and exact snapshot context,
plus the separate source policy and exact recorded-source context:

```shell
python -B hemi_lookup_web.py --mode indexed-only --first-height-policy hemi-snapshot-first-height-assumptions-v1 --accept-context <reviewed-snapshot-context> --finality-source-policy hemi-finality-recorded-view-assumptions-v1 --accept-finality-source-context <reviewed-source-context>
```

These are operator choices, not a per-visitor form. Select checksums from
independently reviewed inputs; accepting arbitrary values copied from an
untrusted receipt does not establish their truth. The named source policy and
checksum must be configured together. A different receipt/context remains
publication-only rather than silently inheriting acceptance. The operator's
acceptance of a source context is separate from first-height acceptance.

Each child forbids network connections. Receipt input is capped at 8 MB; the
parent bounds IPC and output, and the child conservatively accounts required
packet reads against the caller's remaining 32 MB input allowance. An optional
calculation can remain unavailable when the supported packet does not fit the
time/input limits. The existing read API still enforces its total five-second
cooperative work clock, concurrency and response bounds. These are work limits,
not a public latency guarantee or immediate browser-disconnect cancellation.

## Copying and exporting a calculation

**Copy summary** includes the displayed calculation's scope, model, publication,
tip identity/time and qualifications. **Download calculation JSON** exports the
exact returned `finality` object from browser memory; it contains all checked
publication calculations and the separate block reference. It does not trigger
another request, recalculate on selection, or change **Download JSON**, which
continues to export the original receipt.

A calculation JSON is an assessment, not a standalone proof package. Retain its
matching original receipt and any separately downloaded first-height package.
To reconstruct the precise calculator receipt, apply the documented overrides
and check the canonical checksum before running the standalone calculator:

```python
import hashlib
import json

with open('receipt.json', encoding='utf-8') as stream:
    receipt = json.load(stream)
with open('calculation.json', encoding='utf-8') as stream:
    assessment = json.load(stream)
binding = assessment['input_binding']
canonical = lambda value: json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()
assert hashlib.sha256(canonical(receipt)).hexdigest() == binding['original_receipt_sha256']
receipt.update(binding['receipt_overrides'])
assert hashlib.sha256(canonical(receipt)).hexdigest() == binding['calculation_receipt_sha256']
with open('calculation-receipt.json', 'w', encoding='utf-8') as stream:
    json.dump(receipt, stream)
```

Run `python -B -m hemi_lookup.finality` on that reconstructed file with the exact
query and tip identities from the assessment. The default command computes
publication-only results. The conditional mode still requires the real packet
and separately selected policy/context flags; exported booleans or checksums do
not accept assumptions on a verifier's behalf. Neither copy nor export establishes
deployed compatibility, current canonicality or a certified simultaneous Hemi/
Bitcoin snapshot.
