# Quickstart

This guide runs the local Traze developer demo so you can search your own Hemi mainnet block using public data. Selected historical examples are also included for reproducible, source-free inspection. The supplied frontend needs Python 3.10+ with SQLite support. There are no pip dependencies. Node is needed only to rebuild or test the frontend.

## Get the source

With Git installed, open a terminal and run:

```shell
git clone https://github.com/itsmeaokiji/traze.git
cd traze
python --version
```

Confirm that the interpreter is Python 3.10 or newer. If your system uses `python3` on Linux/macOS or `py -3` on Windows, substitute that command for `python` below. The standard Python distribution includes the required SQLite module.

Run the following commands from the repository root: the folder containing README.md and hemi_lookup_web.py. No private node, TBC/BFG service, API key, paid service or frontend build is required. Live discovery needs Internet access and available public Hemi/Bitcoin providers.

## Prepare the recorded catalogue

From the repository root:

```shell
python -B scripts/repository.py check
python -B hemi_catalogue.py build --body-dir tests/fixtures/bitcoin_blocks
python -B hemi_catalogue.py inspect
```

The build reads 17 included Bitcoin bodies and the two original receipt files. It creates the local SQLite catalogue without contacting Hemi or Bitcoin. Expected counts are **17 processed bodies, 22 supported publication occurrences, 10 associated and 12 unassociated**. Unassociated records lack sufficient checked Hemi metadata; they are not absent publications.

The body-directory option is explicit. Without it, the catalogue command retains its original local-cache behavior. Imports are atomic per body and resumable. Inspect the reported failed/pending counts rather than assuming a partial build succeeded.

## Start the app and search your own block

A clean setup uses the supplied historical fixture corpus. It does not include
an operator's private maintained index, lifetime ledger or later pilot data.
Searches first reuse available evidence and, in discovery mode, attempt bounded
public-data discovery when required.

```shell
python -B hemi_lookup_web.py --mode discovery
```

Open [the local app](http://127.0.0.1:8765/). Keep this terminal running while using Traze. Use the app after startup completes.

Enter a Hemi mainnet block number or a full `0x`-prefixed block hash and select
**Find publication**. For example, **5200117** is a historical input outside the
supplied catalogue; you may choose another input. No transaction ID or manual
Bitcoin range is needed. Traze derives the covering keystone, estimates a
Bitcoin search window and checks candidate publications against the requested
Hemi block. The result reports the actual checked coverage and stopping reason.

Discovery mode first reuses compatible local evidence. It can contact public
providers when evidence is missing, including when a recorded positive needs
additional data for its separate first-publication assessment. **Run new lookup**
and **Recheck sources** also make live requests. One lookup and any stronger
evidence preparation share the same resource limits; they do not get separate
allowances. Use indexed-only mode below for source-free inspection.

Select nodes to inspect records. Expand the matching-publication list to choose a later-keystone proof. Use Copy summary or Download JSON to share evidence. Recorded source times remain historical; opening or selecting a record is not a fresh source check.

An explicit lookup in discovery mode can also attempt additional first-publication
checks using public sources, even when ordinary publication evidence is retained.
Use indexed-only mode below when you want inspection without source requests.

Browse offers table and timeline views of collected records. **Metadata pending**
means a publication has been decoded but its checked Hemi association is not yet
available. Opening the timeline does not start background acquisition. Configured
operator collection/backfill must complete that work before it can become usable
associated evidence; failed or unresolved work remains visible.

You do not need to download or manage evidence files to use the app. Completed
discovery results and reusable evidence are stored by the local server.

After the active lookup finishes, stop the server with **Ctrl+C**. Start it again with the same command to reuse its local catalogue and evidence.

## Understand a live result

An ordinary positive result identifies an applicable Bitcoin transaction and
shows the checked Hemi relationship, transaction inclusion and recorded source
observations. Select route nodes to inspect the records. A separate
**First applicable publication under supported rules** assessment appears only
when its additional evidence passes under the stated assumptions. See
[first-publication assessment](FIRST_PUBLICATION.md).

A separately prepared snapshot packet can produce **First applicable publication
under snapshot assumptions**, with an explicit snapshot boundary and caller trust
basis. A clean checkout does not preconfigure that acceptance or automatically
prepare such context for your chosen input. The optional
[recorded snapshot example](../examples/snapshot-first-height/README.md) demonstrates
portable version-3 verification offline; it is separate from ordinary website
discovery and is not silently installed as a search result.

An incomplete result means data, source availability or an operating limit
prevented completion. A scoped no-match applies only to checked coverage.
Neither establishes publication absence across all Bitcoin history. Accepting
an arbitrary input does not guarantee a matching publication, a completed
stronger assessment or a short search.

The current per-lookup ceilings are **600 seconds, 40,000,000 counted response
bytes, 600 requests, 20 new Bitcoin body attempts and 256 Hemi ancestry links**.
The installation also keeps a **100,000,000-byte cumulative response ledger**.
Conservative request reservations and a safety margin can stop work before a
nominal ceiling. These are client safeguards, not Hemi protocol limits or daily
user quotas. Preserve the ledger if a limit is reached; do not delete state to
obtain a new allowance. Review the source policy before further acquisition.

## Inspect the included examples without source requests

Stop the current server after its active lookup finishes, then run:

```shell
python -B hemi_lookup_web.py --mode indexed-only
```

This is also the command-line default when no mode is specified. It only reads
usable recorded evidence; an unavailable input does not start a source scan.
Ordinary indexed searches create no permanent per-query result files.

With the catalogue prepared, enter **5243119**. Traze retrieves covering keystone
5,243,125, four applicable proof records in Bitcoin block 965,931 and a shortest
supplied path of six parent links. Expand the matching-publication list to select
a later-keystone proof. Copy summary and optional JSON download preserve the
displayed evidence. Recorded source times remain historical; opening a saved
record is not a fresh source check. Saved results also include ordinary,
keystone and scoped-no-match examples.

The files in `demo_results/` and `tests/fixtures/` are selected public historical
chain evidence and controlled test inputs, documented in
[example provenance](../demo_results/README.md). They are included in Git for
reproducibility. They do not contain an installation's private cache, session
ledger or generated results. New local lookup data is not automatically added
to Git.

## Export and verify evidence offline

```shell
python -B hemi_catalogue.py query 0x9520c8410a38e2feaeb1878dc39c0fceb57d75cb4873dc89b4b1370a777293f9 --output evidence.json
python -B hemi_lookup_cli.py verify evidence.json --json
```

Export files are immutable by default: choose a new filename instead of overwriting an existing packet. Use `--json` when a script needs machine-readable verifier output.

With Node installed, the independent checker is available:

```shell
node scripts/verify-input-binding.mjs evidence.json
```

It writes new reports under `demo_results/`; these are generated outputs, not new source observations. See [verification scope](VERIFICATION.md).

## March and negative examples

The retained March period supports Hemi **4028426** through later keystone **4028450** in Bitcoin **941750**; six applicable proof records are available and the shortest supplied path has 24 parent links.

The saved scoped-negative packet for Hemi **5243117** covers Bitcoin **965925–965930**. Its positive packet has a publication at **965931**, outside that range. This illustrates bounded coverage, not a contradiction or global absence.

## Local state and live requests

The catalogue, session ledger, cached bodies and developer-discovery receipts live in `.hemi_lookup_data/`, which is excluded from the repository. Ordinary indexed searches create no permanent receipt file; optional JSON downloads preserve the displayed snapshot. Existing accounting and evidence must be preserved when resuming an installation.

Live lookup/refresh retain the prototype's response, request, body-attempt, ancestry and overall-time controls. Resource limits are operator safeguards, not protocol rules or personal daily quotas. Live source operations stop with qualified incomplete results when limits or unavailable data prevent completion.

This local server binds to loopback. [Optional finite collection](COLLECTION.md),
[historical backfill](HISTORICAL_BACKFILL.md) and an explicit
[ongoing operating policy](OPERATING.md) can populate a managed index; none starts
automatically. Operators moving an index and its original ledger should use the
[paired recovery workflow](PAIRED_RECOVERY.md). Ordinary visitors do not manage
these state directories. Public hosting and supported operation still require
the validation described in the [roadmap](ROADMAP.md).

## Common setup issues

| Symptom | Action |
| --- | --- |
| `python` is not found or opens a store prompt | Use an installed Python 3.10+ interpreter; try `py -3` on Windows or `python3` on Linux/macOS. |
| The script or fixture directory is not found | Change to the repository root and use the commands above. |
| No usable indexed evidence for the supplied example | Run the catalogue build with the explicit fixture directory, then inspect the processed, failed and pending counts. |
| The default port is occupied | Run `python -B hemi_lookup_web.py --mode discovery --port 8766` and open [127.0.0.1:8766](http://127.0.0.1:8766/). |
| Your chosen block reports unavailable indexed evidence without a source search | Confirm that the startup command includes `--mode discovery`. Indexed-only mode deliberately does not acquire missing data. |
| A public provider is unavailable | Inspect the result's coverage and stopping reason; the included-evidence example remains available without a provider check. |
| `session_locked` after an interrupted live lookup | First confirm that every Traze browser server and CLI lookup for this installation has stopped. Remove only `.hemi_lookup_data/session.lock`, then restart. Preserve `session.json`, the catalogue, cached bodies and receipts. Never remove a lock while another lookup is active. |

The website is served by the Python process. `npm run dev` is an optional frontend rebuild watcher, not the app-start command; see [development instructions](DEVELOPMENT.md).
