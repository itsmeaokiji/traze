# First-publication assessment in the Explorer

The main **Publication found** result and **Publication order: Earliest observed
in this result** do not require this additional assessment. **First-publication
assessment not available** means that its additional evidence is unavailable for
this result; ordinary publication evidence can still be inspected and verified.
Display labels may normalize legacy diagnostic wording. Original JSON messages,
machine status codes and policy identifiers remain unchanged.

The website can prepare and consume the versioned history/interval evidence
checker. Normal users enter a Hemi block number or hash, select **Find
publication**, and inspect the result. A command-line tool or local download is
not required.

This feature is a conditional **first applicable Bitcoin block height**
assessment. It is not an unconditional independently established global proof,
transaction broadcast order, the complete co-first transaction set, independent
chain consensus verification or a Hemi Bitcoin-finality calculation.

## Search behaviour

In discovery mode, a normal lookup reuses compatible recorded data. When there
is an applicable publication without a complete first-publication verification package, the same source
job attempts to prepare the extra evidence. There is no second accounting run,
background collection, renewed deadline or hidden allowance increase.

The builder:

1. Selects an applicable publication and binds its exact requested block, output,
   transaction, Bitcoin block and publishing keystone identities.
2. Uses the Bitcoin-attributes account's historical nonce as a bounded **locator
   hint**, then authenticates an actual Bitcoin-attributes transaction in a full
   Hemi transaction body strictly before the covering keystone. The nonce is
   neither a proof nor an assumption that establishes the first-publication claim.
3. Reconstructs full supported Hemi headers and the anchor/publishing transaction
   bodies, and obtains all Bitcoin bodies from the authenticated anchor height
   through the selected publication height. Valid cached bodies are reused.
4. Requests the required history account and fresh-parent storage witnesses using
   `eth_getProof`. Proofs authenticate against the exact full header state roots.
5. Re-observes interval and route identities, then invokes the versioned checker
   in an owned, cancellable, process without external source requests.

Unsupported or unresolved earlier publication candidates prevent first-height
promotion. The builder does not classify them as irrelevant just because it
already has a positive publication. Incomplete data never establishes absence.

An optional archive-method failure is separate from a contradictory chain view.
It blocks the first-publication assessment without discarding independently checkable
ordinary publication evidence. Full Hemi/BTC identity conflicts suspend affected
assessments. The displayed receipt headers are matched to the full headers
authenticated by the first-publication checker; a weaker receipt flag cannot substitute.

**Recheck sources** retains its existing meaning: source observations are checked
again without a new interval scan. A new search is needed to attempt additional
evidence preparation. Opening a saved result does not recheck public sources.

In indexed-only and hosted modes, visitors only read retained packets. A missing
packet never starts public-source acquisition. Starting either website does not
start a collector or produce complete historical coverage.

## Operator policy

The server's product policy defaults to the reviewed, named conditional policy:

`hemi-full-state-first-height-assumptions-v1`

For evidence checks without accepting the external premises:

```shell
python -B hemi_lookup_web.py --mode discovery --first-height-policy evidence-only
```

The same `--first-height-policy` choice is available in `hemi_lookup.public`.
Only server configuration supplies this choice; browser input, receipt flags and
packet fields cannot accept assumptions on the operator's behalf.

The conditions include valid selected chains, valid Hemi execution and rollup
derivation, the reviewed protected-history/publication rules throughout the
accepted scope, and practical infeasibility of the reviewed full-width commitment
cycle. They are assumptions, not facts independently proved by a state witness.
There is no measured global historical hashing workload, confidence percentage or
system-security-bit claim. See [history assurance](HISTORY_ASSURANCE.md).

## Result states

| Result | Meaning |
| --- | --- |
| First applicable publication under supported rules | Complete interval/history evidence passed and the operator accepted the named assumptions. The conclusion refers to recorded selected branches. |
| First applicable publication under snapshot assumptions | Version-3 evidence binds a recorded snapshot/domain and the caller separately accepted its new policy and exact context checksum. Historical-profile and validating-source premises remain external. |
| First in checked interval | The complete interval passed, but the additional first-publication conditions are incomplete, unsupported or unaccepted. |
| First-publication evidence invalid | A proof, identity or other first-publication evidence check contradicted its claimed data. No conditional first-publication conclusion is accepted. |
| Assessment suspended | Changed/conflicting source observations, or an incomplete explicit source recheck, prevent using the first-publication assessment. |
| Assessment incomplete/not available | Required data or a supported profile is missing, preparation has not occurred, or a resource/provider limit stopped it. Ordinary search facts remain separate. |

Selecting another transaction/output does not transfer the assessment. The UI
and copyable summary distinguish a qualified conclusion from legacy
earliest-observed results. The legacy receipt's global-earliest flags retain
their existing false meaning; the new checker reports a separate conditional
conclusion and keeps `globally_first_proven=false`.

## Persistence, API and optional export

Packets are stored under the configured server-side state directory's
`first-publication/`. Route identities and content-addressed snapshots preserve
query/output bindings. A new generation never overwrites an older packet.
Opening a result checks its evidence and request bindings; stored receipt success
flags are not trusted. Prepared version-3 reads may reuse a process-local checker
result only for identical required bytes, verifier rules, requested Hemi identity
and operator policy/context. Required bytes are read and hashed again; receipt,
publication/header and source-conflict qualification still run for every read.
Original packet observation times remain separate from retrieval time.

The cache holds at most four entries / two MB of serialized results, no retained
body bytes or persistent success records. A miss fully verifies a private capture;
changed or missing evidence cannot inherit a result. Version-1/2 packets, disabled
reuse, occupied capture capacity and unavailable temporary storage use the
original bounded verifier. A detected rule change requires a service restart.
The existing caller deadline and read/export limits remain in force. This
optimization does not add source coverage, fresh chain observations or faster
initial discovery, and does not strengthen the conditional assessment.

Receipt and indexed-block responses add a `first_publication` assessment with
query/publication identities, checker result, assumptions, issues, recorded
context and an optional exact-snapshot download URL. Existing receipt schemas,
`verification` and **Download evidence JSON** keep their previous meanings.

**Download verification package** optionally returns a ZIP containing the exact
versioned packet, interval bodies, standard-library checker, license and instructions.
It is separate from the original receipt JSON. Only the matching displayed
publication exposes this action. The snapshot digest in its URL must still
match; a changed selection/view does not silently substitute a different packet.

Routes are server-controlled, reads without external source requests:

```text
/api/receipts/{id}/first-publication/{manifest-sha256}/download
/api/v1/blocks/{full-hemi-hash}/first-publication/{manifest-sha256}/download
```

Extract the package to run the included offline command. The evidence-only
command does not accept the external assumptions. Its README explains the
separate conditional-policy option. Downloading and verification are optional
for ordinary website users.

For prepared version-3 packets, the operator chooses
`--first-height-policy hemi-snapshot-first-height-assumptions-v1` and separately
supplies `--accept-context <canonical-context-sha256>`. The legacy policy does
not accept these additional premises. The website shows the recorded Hemi domain,
while the assumptions disclosure and portable packet retain the full identities,
times and external trust basis. No current Bitcoin membership check is implied.
See [the snapshot contract](SNAPSHOT_ASSURANCE.md) and the optional
[public packet example](../examples/snapshot-first-height/README.md). Automatic
context preparation for arbitrary live searches remains separate work.

## Bounds and remaining validation

The existing discovery ceilings still apply: 600 seconds, 40 MB counted response
data, 20 Bitcoin-body attempts, 600 requests, 256 Hemi ancestry links and the
existing installation-wide accounting ceiling. No exhausted historical ledger
is reset or automatically given more allowance.

The verifier also enforces its separate manifest/body/proof bounds, including
64 Bitcoin bodies and 4,096 full Hemi headers; a web lookup must satisfy both its
own smaller envelope and the verifier's envelope. Full-object Hemi RPC responses
are bounded to 2 MB. Account/storage RPCs use the existing bounded transport and
response accounting. No arbitrary endpoint or write RPC is enabled.

Each checker child has a 20-second local ceiling and observes the caller's
shorter original deadline. Indexed reads still use their existing five-second
request budget. An expired check terminates its owned child. Ordinary JSON
response bounds remain unchanged; verification-package downloads have a separate
bounded body set and a single concurrent export slot.

Saved-data/controlled replay validates integration, not current archive-provider
availability, fresh arbitrary-input success, universal protocol-era support or
production latency. These require separately scoped live validation. Public
hosting and sustained operating coverage remain separate release gates.
