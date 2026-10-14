# Quickstart

This guide runs the local Traze prototype using included historical evidence. The supplied frontend needs Python 3.10+ with SQLite support. There are no pip dependencies. Node is needed only to rebuild or test the frontend.

## Get the source

With Git installed, open a terminal and run:

```shell
git clone https://github.com/itsmeaokiji/traze.git
cd traze
python --version
```

Confirm that the interpreter is Python 3.10 or newer. If your system uses `python3` on Linux/macOS or `py -3` on Windows, substitute that command for `python` below. The standard Python distribution includes the required SQLite module.

Run the following commands from the repository root: the folder containing README.md and hemi_lookup_web.py. The initial demonstration uses supplied files and requires no private node, TBC/BFG service, API key or frontend build.

## Prepare the recorded catalogue

From the repository root:

```shell
python -B scripts/repository.py check
python -B hemi_catalogue.py build --body-dir tests/fixtures/bitcoin_blocks
python -B hemi_catalogue.py inspect
```

The build reads 17 included Bitcoin bodies and the two original receipt files. It creates the local SQLite catalogue without contacting Hemi or Bitcoin. Expected counts are **17 processed bodies, 22 supported publication occurrences, 10 associated and 12 unassociated**. Unassociated records lack sufficient checked Hemi metadata; they are not absent publications.

The body-directory option is explicit. Without it, the catalogue command retains its original local-cache behavior. Imports are atomic per body and resumable. Inspect the reported failed/pending counts rather than assuming a partial build succeeded.

## Local browser app

```shell
python -B hemi_lookup_web.py
```

Open [the local app](http://127.0.0.1:8765/). Keep this terminal running while using Traze. Use the app after startup completes.

Enter **5243119** and select **Find publication**. With the catalogue prepared, Traze retrieves recorded evidence: covering keystone 5,243,125, four applicable proof records in Bitcoin block 965,931, and a shortest supplied path of six parent links.

Select nodes to inspect records. Expand the matching-publication list to choose a later-keystone proof. Use Copy summary or Download JSON to share evidence. Recorded source times remain historical; opening or selecting a record is not a fresh source check.

Saved results also provide ordinary, keystone and scoped-no-match examples.

After the active lookup finishes, stop the server with **Ctrl+C**. Start it again with the same command to reuse its local catalogue and evidence.

## Try live discovery

Enter another supported Hemi mainnet block number or hash. An input outside usable indexed evidence can trigger a bounded public-data search. **Run new lookup** and **Recheck sources** also make live requests. These operations require Internet access and available public providers; they are separate from the offline demonstration above.

Traze reports the actual search range and whether work completed. An incomplete result means the available data, source response or operating limits prevented completion. A scoped no-match applies to the checked range. Neither result establishes that a publication is absent from all Bitcoin history. Arbitrary-input acceptance does not guarantee a matching publication or a short search.

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

The catalogue, session ledger, cached bodies and new browser receipts live in `.hemi_lookup_data/`, which is excluded from the repository. Existing accounting and evidence must be preserved when resuming an installation.

Live lookup/refresh retain the prototype's response, request, body-attempt, ancestry and overall-time controls. Resource limits are operator safeguards, not protocol rules or personal daily quotas. Live source operations stop with qualified incomplete results when limits or unavailable data prevent completion.

This local server binds to loopback. Maintained collection and public hosting are [planned work](ROADMAP.md).

## Common setup issues

| Symptom | Action |
| --- | --- |
| `python` is not found or opens a store prompt | Use an installed Python 3.10+ interpreter; try `py -3` on Windows or `python3` on Linux/macOS. |
| The script or fixture directory is not found | Change to the repository root and use the commands above. |
| No usable indexed evidence for the supplied example | Run the catalogue build with the explicit fixture directory, then inspect the processed, failed and pending counts. |
| The default port is occupied | Run `python -B hemi_lookup_web.py --port 8766` and open [127.0.0.1:8766](http://127.0.0.1:8766/). |
| A public provider is unavailable | Inspect the result's coverage and stopping reason; the included-evidence example remains available without a provider check. |
| `session_locked` after an interrupted live lookup | First confirm that every Traze browser server and CLI lookup for this installation has stopped. Remove only `.hemi_lookup_data/session.lock`, then restart. Preserve `session.json`, the catalogue, cached bodies and receipts. Never remove a lock while another lookup is active. |

The website is served by the Python process. `npm run dev` is an optional frontend rebuild watcher, not the app-start command; see [development instructions](DEVELOPMENT.md).
