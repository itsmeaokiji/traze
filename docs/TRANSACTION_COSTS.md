# Publication transaction costs

Open the Bitcoin publication node in a search result, or an indexed publication
detail, to inspect its transaction fee, virtual size and fee rate. These are
optional derived data, separate from ordinary publication-proof checks.

## Calculation

- Fee in satoshis = sum of referenced input values minus sum of output values.
- Weight = stripped serialization size times three plus full serialization size.
- Virtual size = weight divided by four, rounded up to the next integer.
- Fee rate = fee divided by virtual size, displayed with up to four decimals.

Transaction IDs are recomputed from raw bytes. Every previous transaction used
for an input amount must reproduce the referenced transaction ID and contain the
specified output. Duplicate inputs, negative fees, inconsistent references and
out-of-range amounts do not produce a successful cost calculation.

Costs apply to the whole transaction. Multiple Hemi publication outputs do not
each pay the full fee independently, and the app does not assign a fee share to
an individual keystone. Bitcoin transaction fees are distinct from PoP rewards.

For SegWit, the size and fee rate describe the supplied witness serialization.
Transaction-ID inclusion alone does not authenticate that witness; Bitcoin
witness-commitment verification is outside these checks. No UTXO validation,
current canonical membership, signature validity or reward eligibility is claimed.

## Retained data and unavailable results

The calculation uses raw transactions in the current receipt and retained indexed
publication records. It does not scan raw Bitcoin blocks during a result read,
download missing parents, start discovery or modify the original receipt.
Some parent transactions can exist in a raw cache without a compiled transaction
record; their fee data remains unavailable in this version. The maintained index
is preferred when present; an unusable maintained index does not fall back to an
older catalogue. A transaction-ID SQL index accelerates newly prepared indexes;
compatible older indexes remain readable within the same bounded work policy.

Missing previous outputs or a local capacity limit leaves the fee and fee rate
unavailable; a genuine zero fee is displayed as zero. Supplied-byte size can
remain available independently. An invalid cost assessment does not turn an
otherwise checkable publication into a no-match result. Opening a recorded result
does not refresh chain observations or renew any acquisition allowance.

Optional cost reads share a cooperative 0.25-second work budget and caps of
two MB of decoded input, 32 distinct transactions and 32 parent transactions.
Parent record size and SQL work are bounded too; the request's existing overall
read budget and deadline still apply.
These are local processing limits, not Hemi protocol parameters.

## API and exports

Saved and indexed Hemi-result wrappers add `transaction_costs` with schema
`traze-transaction-costs-v1`. Its `transactions` array binds each assessment to
the receipt's `proof_index`, exact `txid` and raw-byte SHA-256. Distinct supplied
witness serializations cannot share a size result merely because txids match.
Indexed publication detail adds one `transaction_cost` assessment.

`status` is `calculated`, `unavailable` or `invalid`. `fee_sats` is an integer
or null; size, weight and virtual-size fields may remain available when fee is
unknown. Consumers must not substitute zero for missing costs or sum duplicate
transaction fees across publication outputs. `source_requests` is zero and
`source_views_rechecked` is false.

Original receipt JSON and its offline verifier retain their previous meanings.
That export does not include the extra parent transactions required to reproduce
a cost calculation; the cost fields are separate response metadata. The Python
`hemi_lookup.costs.calculate` function can recompute costs with supplied raw
transactions without any network access.
