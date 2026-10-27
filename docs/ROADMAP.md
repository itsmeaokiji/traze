# Roadmap

Updated October 27, 2026. Traze is a local developer explorer. A supported hosted
public beta has not been released.

## Available capabilities

| Area | Current behavior |
| --- | --- |
| Publication investigation | Number/hash input, retained evidence reuse and bounded public-data discovery |
| Evidence inspection | Connected keystone/publication route, source observations, proof checks and explicit coverage |
| Developer access | Versioned read API, stable record routes, portable receipts and offline verification |
| Indexed browsing | Publication table, timeline, filters and unresolved metadata visibility |
| Reference calculations | Named arithmetic models and separately qualified recorded-block calculations |
| Operator tools | Finite backfill, ongoing collection policies, monitoring templates and guarded backup/recovery |

Historical fixtures and controlled tests support reproducible development.
Finite local operation checks do not establish sustained throughput or a
continuously available service. Implemented tooling remains distinct from a
validated public deployment.

## Requirements for a supported public service

- Declare indexed historical coverage and account for gaps, unsupported records
  and unresolved Hemi associations.
- Measure sustained collection capacity, freshness, provider availability and
  operating costs under an explicit resource policy.
- Validate the chosen host, process supervision, permissions, HTTPS serving,
  monitoring and recovery from independently retained backups.
- Confirm the clone/run/search workflow with independent users and document the
  supported service envelope and remaining limitations.

See [ongoing collection](OPERATING.md), [historical backfill](HISTORICAL_BACKFILL.md)
and the [hosting runbook](HOSTING.md) for existing tools and their requirements.
[Paired recovery](PAIRED_RECOVERY.md) preserves the index/accounting binding and
keeps restored acquisition blocked until later authoritative state is reconciled.

## Further improvements

Potential work includes broader historical coverage, automated preparation of
supported assurance contexts, additional protocol-era validation, integration
examples and miner/fee/reward workflows where data and rules support them.

The existing [snapshot assessment](SNAPSHOT_ASSURANCE.md) remains conditional on
explicit caller-accepted premises. [Reference calculations](FINALITY.md) do not
establish equivalence to a deployed Hemi finality handler. Neither feature is an
unqualified global certification or an automatic current-chain determination.

Select expansions against demonstrated user needs and measured operating
capacity. Maintenance continues after individual features are completed.
