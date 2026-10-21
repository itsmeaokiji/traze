# Hosting options and operating assumptions

**Research date:** October 18, 2026. These are public list-price observations,
not accepted budgets or purchased services. Recheck region, stock, taxes and
checkout totals before ordering. No account setup or deployment is included.

The current SQLite design naturally fits one modest Linux VPS with persistent
local storage, separate reader/writer processes and off-host backups. Start
evaluation around two vCPUs and 4 GiB RAM to leave headroom for the reader,
collector, proxy, operating system and restore work. This is an engineering
starting assumption, not evidence of sustained capacity or measured minimum RAM.

| Option | Verified base information | Fit and limitation |
| --- | --- | --- |
| Hetzner CX23, Germany/Finland | Published June 2026 price table: EUR 5.49/month, excluding IPv4 and VAT | Low reference compute cost; the retrieved current plan table did not establish stock/specifications or a purchasable checkout. Those remain unverified, so this is not a guaranteed quote |
| DigitalOcean Basic, regular CPU | USD 24/month for 2 vCPU, 4 GiB RAM, 80 GiB SSD and 4,000 GiB transfer | Direct fit for the single-host candidate; more expensive compute, with explicit listed disk/transfer allowance. Backups, domain, taxes and any source-service fees remain additional |
| Render paid persistent service | Persistent disks cost USD 0.25/GB/month, plus paid compute; free web services cannot attach a persistent disk | A disk belongs to one service instance and cannot be shared with a separate worker service. Using separate reader/collector services would need a different state arrangement; not the first candidate for the present shared SQLite design |

Sources: [Hetzner price table](https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/),
[Hetzner current cloud page](https://www.hetzner.com/cloud/),
[DigitalOcean Basic prices](https://www.digitalocean.com/pricing/droplets),
[Render pricing](https://render.com/pricing),
[Render persistent-disk constraints](https://render.com/docs/disks),
and [Render free-service limits](https://render.com/docs/free).

**Recommendation:** evaluate the single-VPS route first. DigitalOcean provides
a directly documented 4 GiB reference plan; investigate the cheaper Hetzner
option only after confirming an available plan and its complete regional price.
Neither provider has been benchmarked for Traze. Keep provider choice separate
from accepting a service objective or collection budget.

## Full monthly cost

Use this complete equation rather than the compute sticker price:

`compute + IP + persistent storage + off-host backup + domain allocation + traffic overage + source access + tax`

Include an identified maintainer's time. A public Bitcoin/Hemi API being
reachable does not establish sustainable free indexing, an SLA or an entitlement
to heavy requests. Source access terms and real error/rate behavior need review
within a new finite collection scope. No TBC/BFG node, 1 TB disk or USD 199/month
service is required by this candidate, but that does not make hosted operation
free or establish its final price.

## Measurements required before accepting a budget

- Actual cold Bitcoin block ingestion speed, requests/retries and counted data;
  detection of new publication-bearing heights and catch-up behavior.
- Compiled evidence/ledger growth per day, disposable-cache high-water usage,
  WAL overhead, backup size and off-host transfer/retention cost.
- CPU/RAM under simultaneous read and collection workloads; reader/collector
  memory limits in the service templates are provisional containment values.
- Response traffic from real consumer tasks. For illustration only, 10,000
  evidence responses of 500,000 bytes each are 5 GB before other traffic;
  this is arithmetic, not a traffic forecast or capacity result.
- Actual backup duration, verified restore duration, lost-update exposure,
  deployment downtime and journal growth. No availability SLA is declared.

Before activation, record the selected host/region, monthly spending ceiling,
backup destination and retention, maintainer/recovery contact, explicit source
policy and test stopping time. Prepare an initial small hosted validation scope;
do not silently convert historical local quotas into recurring authorization.
