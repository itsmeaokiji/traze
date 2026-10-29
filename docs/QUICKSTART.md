# Quickstart

Run the developer-run Traze publication explorer, search a Hemi mainnet block and
inspect its Bitcoin publication evidence. Requirements: Git and Python 3.10+
with SQLite. No private node, TBC/BFG service, API key or pip dependency is needed
for this demo. Node is needed only for frontend development.

## Get the source and prepare the examples

```shell
git clone https://github.com/itsmeaokiji/traze.git
cd traze
python --version
python -B scripts/repository.py check
python -B hemi_catalogue.py build --body-dir tests/fixtures/bitcoin_blocks
python -B hemi_catalogue.py inspect
```

Run commands from the repository root. If needed, substitute `python3` on
Linux/macOS or `py -3` on Windows. Confirm Python 3.10 or newer.

The catalogue build uses included historical evidence without external source
requests. Expected counts: **17 processed Bitcoin bodies, 22 publication records
with supported payloads, 10 with Hemi associations and 12 without them**. Inspect
failed/pending counts if the build is incomplete. These fixtures cover selected
historical periods, not complete chain history. See [example provenance](../demo_results/README.md).

## Start the app and search your own block

```shell
python -B hemi_lookup_web.py --mode discovery
```

Open [127.0.0.1:8765](http://127.0.0.1:8765/) after startup completes. Keep the
terminal running. Enter a Hemi mainnet block number or full `0x`-prefixed hash,
leave the optional Bitcoin range blank and select **Find publication**.

Discovery mode reuses compatible evidence first. When more evidence is needed,
it can query public Hemi and Bitcoin providers under finite resource limits.
It derives the covering keystone and estimates a Bitcoin search window; actual
payload, inclusion and ancestry checks determine applicable matches. The estimate
is a search heuristic, not a proof. No matching publication is guaranteed.

Optional historical input: **5515549** is outside the included catalogue. A
recorded cold run on October 27, 2026 found two applicable publications at
Bitcoin **971,542**, supporting keystones **5,515,550** and **5,515,575**.
This is a known-positive example, not a current-provider availability guarantee.

Select route nodes to inspect records, choose matching publications, copy a
summary or download JSON evidence. Downloads are optional; the local server
stores completed discovery results for reuse. After active work finishes, stop
the server with **Ctrl+C**. Restart with the same command to reuse local state.

## Understand the result

- **Found:** Applicable publication evidence is available. Inspect its proof
  checks, recorded source observations and coverage separately.
- **No match in checked range:** No applicable match was found in that checked
  scope. This does not establish global publication absence.
- **Incomplete:** Missing data, conflicting observations or an operating limit
  prevented a complete answer.

Browsing may show **Hemi evidence unavailable** for a decoded publication when
its Hemi association has not been completely linked. This status does not mean
an automatic background job is running. Separately, conflicting Bitcoin heights
can prevent an evidence link even when Hemi metadata is retained. Inspect the
record details for these distinct conditions.

Optional first-publication assessments and reference finality calculations have
additional requirements. Missing assessment evidence does not invalidate an
otherwise valid ordinary publication proof. See [search behavior and limits](SEARCH_GUIDE.md)
for source checks, continued searches, limits and detailed result meanings.

## Try included evidence without external source requests

After stopping the previous server, run:

```shell
python -B hemi_lookup_web.py --mode indexed-only
```

This is the default mode when no mode is specified. Search **5243119** after
building the catalogue. Its retained evidence includes covering keystone
**5,243,125** and four applicable proof records in Bitcoin **965,931**. Missing
indexed evidence does not trigger discovery. Original source times remain
historical. See [serving modes](SERVING.md).

## Export and verify evidence offline

Use **Download evidence JSON** in the app, or inspect an included export:

```shell
python -B hemi_lookup_cli.py verify demo_results/mainnet_5243117.json --json
```

The verifier checks supplied evidence without external source requests. It does
not establish current canonical membership. Optional independent Node tools and
versioned verification packages are described in [development](DEVELOPMENT.md),
[verification scope](VERIFICATION.md) and [terminology](GLOSSARY.md).

## Local state and live requests

Runtime evidence, cache and accounting live in `.hemi_lookup_data/`, excluded
from Git. Preserve them across restarts. Current per-operation limits are
**600 seconds, 40,000,000 response bytes, 600 requests, 20 new Bitcoin body
attempts and 256 Hemi ancestry links**. The default cumulative response ceiling
is **100,000,000 bytes**; prior usage still counts. Conservative reservations
can stop work before a nominal ceiling. These are application safeguards, not
Hemi protocol limits or daily user quotas.

See [resource limits and continuation](SEARCH_GUIDE.md#understand-a-live-result)
and [operator configuration](SEARCH_GUIDE.md#operator-selected-discovery-ceiling).

## Common setup issues

| Symptom | Action |
| --- | --- |
| Python is unavailable | Install/use Python 3.10+; try `py -3` or `python3`. |
| Script or fixture directory is missing | Run from the repository root. |
| Included input has no usable indexed evidence | Build with the explicit fixture directory and inspect failed/pending counts. |
| Default port is occupied | Start with `--port 8766` and open [127.0.0.1:8766](http://127.0.0.1:8766/). |
| Own input returns unavailable indexed evidence without searching sources | Start with `--mode discovery`; indexed-only mode does not acquire missing evidence. |
| Public provider is unavailable | Inspect coverage and the stopping reason, or try included evidence in indexed-only mode. |
| `session_locked` after interruption | Confirm every server/CLI lookup for this installation has stopped. Remove only `.hemi_lookup_data/session.lock`; preserve the ledger, catalogue, cache and evidence. Never remove an active lock. |

The Python process serves the website. `npm run dev` is an optional rebuild
watcher, not the app-start command. See [development](DEVELOPMENT.md).
