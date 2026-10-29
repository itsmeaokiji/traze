# Terminology

These terms describe the current application and evidence formats. Public labels
can be clearer than historical machine names; existing JSON keys, profile IDs,
CLI flags and API parameters remain compatible.

## Blocks, keystones and publications

| Term | Meaning in Traze |
| --- | --- |
| Requested Hemi block | The exact block number or hash being investigated. |
| Covering keystone | The keystone derived for the requested block under the implemented interval rules. It can differ from the keystone encoded by a matching publication. |
| Later keystone | A keystone after the covering keystone whose checked ancestry supports the requested block. Its publication does not prove that no earlier applicable publication exists. |
| Bitcoin publication | A Bitcoin transaction output carrying a supported Hemi publication payload. The transaction and its containing block remain separate records. |
| Publication record | One inclusion occurrence identified by containing Bitcoin block hash, transaction ID and output index. Counts are not necessarily counts of unique transaction IDs. |
| Supported payload | A payload recognized by the implemented codec. Recognition alone does not establish complete Hemi evidence, current chain membership or reward eligibility. |
| Hemi association | The checked relationship between a publication commitment and supplied Hemi keystone metadata; a requested-block result also needs its ancestry checks. |

Use **keystone** for Hemi protocol records. A processing **checkpoint** or SQLite
WAL checkpoint has a different meaning. Legacy JSON keys such as `checkpoint`
remain documented machine identifiers, not new public protocol terminology.

## Results and exported evidence

| Term | Meaning |
| --- | --- |
| Search result | The user-facing outcome for a requested Hemi block, including coverage and qualifications. |
| Publication found | The result contains applicable publication evidence. Source freshness, coverage and additional assessments remain separate. |
| Stored evidence | Reused evidence with its original source observations. This origin label does not imply a fresh source check. |
| JSON evidence export | A receipt-format JSON document containing recorded inputs, publication proofs and source/coverage facts. Developer APIs retain the `receipt` field name. |
| Verification package | A separately versioned evidence package containing manifests and supporting files for interval, history or snapshot assessment. It is not the ordinary JSON export. |
| Offline verification | Recomputing checks from supplied files without external source requests. This does not independently select the current canonical chains. |
| Indexed retrieval | Reading stored evidence from the running Traze service. It can use HTTP while making no new external source requests. It is not necessarily offline. |

Technical format descriptions can still use **receipt** and **packet** when
identifying those exact formats. Read [verification scope](VERIFICATION.md) and
[first-publication assessment](FIRST_PUBLICATION.md) before treating them as interchangeable.

## Availability, observations and coverage

| Label or term | Meaning |
| --- | --- |
| Hemi evidence available | The index retains associated Hemi metadata. Opening a usable Hemi evidence link recomputes the relevant proof checks; the label alone does not confirm current chain membership. Conflicting Bitcoin heights can still prevent that link. |
| Hemi evidence unavailable | No complete Hemi association has been linked to the supported publication. The API retains its `pending` value; no automatic background job is implied. |
| Source checks | Whether the recorded provider observations agreed on the checked identities, or were not rechecked for a stored-evidence result. Separate hosts do not establish independently operated infrastructure. |
| Indexed coverage | Data retained by the index, which may span disjoint periods and contain gaps. |
| Search coverage | The Bitcoin blocks and work checked for a particular search result. |
| Publication proof only | An inclusion proof is retained without establishing a complete scan of its containing Bitcoin block. |
| Earliest observed | The earliest applicable Bitcoin height among the available checked evidence; not an unconditional globally first claim. |
| Publication order | The displayed selection's ordering assurance, such as earliest observed, first in a checked interval or a qualified first-publication conclusion. |
| No match in checked range | No applicable match in the stated checked scope; not global publication absence. |

Index storage time, result retrieval time and source observation time are distinct.
Opening a saved result does not refresh its recorded source observations.

## Conditional assessments and finality models

A **first-publication assessment** concerns the first applicable Bitcoin
**block height** under its stated evidence, chain view, rules and assumptions.
It does not order multiple publication transactions within that same block.
Keep the displayed supported-rule or snapshot qualifications with the conclusion.

A **reference finality calculation** recomputes a named model using recorded
publication and tip heights. Inclusive Bitcoin depth, pinned BFG effective
confirmations and the signed-counter comparison are different quantities.
Neither a model threshold nor ordinary publication verification independently
establishes authoritative deployed Hemi finality. See [reference models](FINALITY.md).

**Bitcoin depth from source checks** is displayed only when source checks for
the result are confirmed and a depth value was recorded. **Bitcoin depth from
recorded tip** is the reference model's inclusive calculation, T - H + 1,
using its recorded tip height and source observation time. These fields can have
different availability. Neither is itself a Hemi finality value.
