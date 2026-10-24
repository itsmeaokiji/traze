# Roadmap

Updated October 24, 2026. This source version runs locally with indexed reads,
explicit developer discovery, publication browsing/timeline, a versioned read API,
reference finality models and opt-in historical/ongoing collection.

| Phase | Status | Intended outcome |
| --- | --- | --- |
| 1. Reusable Evidence Foundation | Complete within retained-data scope | Compiled publication records and correctly bound portable proof packets |
| 2. Demo Readiness and Production Plan | Complete within local-demo/planning scope | Reproducible demonstration, portable setup and a measured service plan |
| 3. Maintained Service Pilot | Complete within a measured 21-height / 2h39m local pilot | Automatic collection, checked associations, incremental indexing and durable recovery with measured operating costs |
| 4. Supported Public Beta | In progress; read service, collection controls and guarded recovery implemented locally; hosted gates open | Hosted explorer and integrations within declared coverage and operating limits |
| 5. Selected Improvements and Expansion | Selected timeline/reference-model/history capabilities implemented locally; further work open | Finite additions based on validated needs and feasible data |

Current source hosting and CI support reproducible development. The short pilot establishes a measured local envelope; it does not establish sustained capacity or a supported public service.

The current UI, Python parser/verifier and SQLite catalogue are reusable foundations. A finite collector now adds required Hemi metadata, incremental updates, durable live jobs and source/coverage observations. Indexed reads remain available during collection. These foundations still need sustained source-capacity, freshness and operating-envelope validation before public service release.

The ongoing controller supports explicit renewable quotas, saved progress,
bounded retries and cache retention independent of compiled evidence. A separate
index can retain its original authoritative accounting instead of starting a new
ledger. Controlled checks and short actual-source samples exercise these
mechanisms; longer recurring-policy capacity still needs measurement. See
[the operating guide](OPERATING.md).

Successor-work controls now retain eligible unfinished Hemi metadata/association
progress across operating periods while preserving original observation times
and requiring fresh source checks before cursor advancement. Controlled tests
cover continuation, changed views, terminal stops and atomic admission. Local
recorded-search profiling is not a hosted performance or sustained-capacity
guarantee. Historical-height and finite future-period checks validate continuation,
actual new body/publication visibility and an idle process restart within their
recorded samples. A later twenty-minute sample added three checked Bitcoin bodies
and five associated publications, including a post-initial-tip height, without
re-downloading completed bodies. It checked three of eight fixed heights; the
other five were not observed before closure. This is not full-range completion,
sustained throughput, real-host capacity or a continuously available feed.

The public-beta work is grouped into five batches:

1. Indexed serving and operator controls, implemented and locally checked.
2. Automatic collection and explicit operating policy, implemented with controlled validation; sustained live validation remains open.
3. Explorer browsing, stable record URLs, supported read API and visitor request isolation, implemented and checked locally with retained data.
4. Hosting/monitoring templates and guarded backup/recovery are prepared and locally checked; actual-host permissions, off-host retention, recovery ownership and the supported operating envelope remain open.
5. Independent-user validation and beta release within declared scope.

There is no hosted beta yet. These remaining gates determine when a release can be supported.

[Paired recovery](PAIRED_RECOVERY.md) now snapshots the bound index and external
ledger with original baselines and durable controls. Local new-location restore
and independent evidence checks pass. Later authoritative usage/clocks/stops
require a fresh complete snapshot; matching old copies cannot establish freshness.
Restored acquisition remains blocked pending a separate operating decision.

Conditional publication assurance now supports a separately versioned recorded
snapshot, explicit historical-profile/source premises and caller-selected
context acceptance. A public mainnet example is reproducible offline. This
does not provide unqualified global certification or automatic snapshot-context
collection for arbitrary searches. Broader protocol-era support, automatic
context preparation/acceptance management and independent-user validation remain
improvement work; see [snapshot assurance](SNAPSHOT_ASSURANCE.md).

The [publication timeline](TIMELINE.md), [finite historical backfill](HISTORICAL_BACKFILL.md)
and [reference calculator](FINALITY.md) are available locally. Reference arithmetic
and conditional recorded-block calculations do not establish equivalence to a
deployed Hemi finality handler or independent current-chain finality.

Further work can widen declared historical coverage, validate sustained collection,
improve integrations and automated assurance, or add miner/fee/reward workflows
once their data and rules are supported. These are not all prerequisites for an
initial beta, and a completed prototype feature does not by itself validate demand.

Maintenance continues after selected expansion work finishes.
