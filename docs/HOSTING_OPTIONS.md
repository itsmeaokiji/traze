# Hosting requirements and cost planning

The current deployment design uses one Linux host with persistent local storage,
separate reader and collector processes, and off-host backups. See the
[hosting runbook](HOSTING.md). A supported public deployment has not been released.

## Evaluate a host

Confirm persistent disk support, process supervision, HTTPS proxying, filesystem
permissions, backup export and the source-access policy. The current shared
SQLite layout expects readers and the writer to access the same local storage.
Separate platform services may require a different state arrangement.

Two vCPUs and 4 GiB RAM are a starting evaluation assumption, not a measured
minimum or a capacity guarantee. Provider, region and plan selection need current
specifications and prices before any operating budget is accepted.

## Account for the full cost

Include compute, IP allocation, persistent storage, off-host backups, domain,
traffic overage, source access, tax and maintainer time. A public API being
reachable does not establish sustainable free indexing or a service commitment.
No provider price or purchased service is assumed by this guide.

## Measure before supported operation

- Cold-block ingestion, requests, retries, catch-up and publication freshness.
- Index/accounting growth, cache use, WAL size and backup transfer/retention.
- CPU/RAM and response traffic under simultaneous read and collection workloads.
- Backup and verified restore duration, lost-update exposure and restart behavior.
- Real source availability, rate behavior and sustainable access conditions.

Record the chosen host/region, spending ceiling, backup destination and retention,
maintainer/recovery contact, source policy and validation stopping time. Begin
with a finite hosted validation scope. Local test quotas do not automatically
authorize recurring operation. See [the roadmap](ROADMAP.md).
