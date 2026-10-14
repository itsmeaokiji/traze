# Traze

**Trace Hemi blocks to Bitcoin publications.**

Inspect keystone connections and Bitcoin transactions, with clear sources and checkable evidence.

Traze is a Hemi–Bitcoin publication explorer that connects Hemi blocks and keystones to applicable Bitcoin publication transactions, with inspectable evidence, recorded source observations, and clear search coverage.

**Status:** Local prototype with an opt-in finite collector, durable jobs and reusable indexed evidence. Sustained service operation and public hosting remain planned work.

![Recorded Hemi-to-Bitcoin publication route](docs/images/publication-route.jpg)

*Recorded example: Hemi 5,243,119 → covering keystone 5,243,125 → Bitcoin 965,931. This screenshot shows historical evidence and its qualifications, not a new chain check.*

## What you can do

- Start with a supported Hemi mainnet block number or hash.
- Retrieve usable indexed historical evidence or attempt bounded public-data discovery.
- Inspect direct/later keystone relationships, Bitcoin transactions and containing blocks.
- See proof checks, recorded source observations, coverage and missing work separately.
- Select matching proof records and copy identifiers or a plain-text evidence summary.
- Export JSON evidence and recompute its checks offline.

The intended first workflow is developer investigation: start with a Hemi block from logs or an explorer, identify an applicable supporting Bitcoin transaction, explain the relationship and share checkable evidence.

## Local browser app

With Git and Python 3.10+:

```shell
git clone https://github.com/itsmeaokiji/traze.git
cd traze
python -B scripts/repository.py check
python -B hemi_catalogue.py build --body-dir tests/fixtures/bitcoin_blocks
python -B hemi_lookup_web.py
```

Open [127.0.0.1:8765](http://127.0.0.1:8765/), enter **5243119** and select **Find publication**. The supplied build needs no Node installation or pip dependencies. The included fixtures prepare a reusable catalogue without live chain calls. Keep the terminal running while using the app; stop it with Ctrl+C after the active lookup finishes.

Follow [the quickstart](docs/QUICKSTART.md) for March/negative examples, exporting evidence, optional ports and the difference between recorded retrieval and live operations.

## Verify evidence offline

```shell
python -B hemi_lookup_cli.py verify demo_results/mainnet_5243117.json --json
```

Positive checks operate on supplied data. A zero-proof negative packet does not contain a cryptographic absence proof.

Read [verification scope](docs/VERIFICATION.md) and [example provenance](demo_results/README.md).

## Stack and data

Python standard-library discovery/verification, SQLite catalogue and immutable JSON receipts form the backend. React + TypeScript + Vite and custom SVG/CSS form the frontend.

The demonstrated live path uses basic public Hemi RPC and configured public Bitcoin APIs. It requires no private node, TBC/BFG access, API key or paid service for this demo. External-source availability and sustainable production access remain operating questions.

The fixture corpus contains 17 historical Bitcoin bodies totaling 25,845,550 bytes across two disjoint periods. The supplied fixture catalogue compiles 22 supported publication occurrences, with 10 associated and 12 unassociated records. This is declared sample coverage, not complete Hemi/Bitcoin history.

An optional finite collector can acquire additional Bitcoin blocks ahead of user searches, check Hemi associations and update a managed evidence index. It retains job progress and resource accounting on resume. Indexed searches remain available during collection. Starting the website does not start collection; see [collection and recovery](docs/COLLECTION.md).

## Evidence boundaries

Use **earliest observed** for current publication selection. A scoped no-match applies to checked coverage. Missing data or an unavailable source is not global publication absence.

Traze does not independently execute Hemi, validate full Bitcoin consensus/chainwork/UTXO state, calculate authoritative Hemi finality counters or verify PoP reward eligibility. Raw Bitcoin depth and provided-data proof validity have narrower meanings.

Runtime cache, session accounting and generated receipts stay local in `.hemi_lookup_data/`. Preserve existing accounting and evidence when resuming. Live lookup/recheck remains bounded; see [the quickstart](docs/QUICKSTART.md#local-state-and-live-requests).

## Development

```shell
python -B -m unittest discover -s tests -v
node --test catalogue_demo/demo.test.mjs
```

For frontend development, from `frontend/`:

```shell
npm ci --ignore-scripts --no-audit --no-fund
npm test
npm run typecheck
npm run build
```

Follow [development instructions](docs/DEVELOPMENT.md) and [contribution guidance](CONTRIBUTING.md). Tests use included evidence and controlled sources; dependency installation may access npm.

## Roadmap

The reusable evidence foundation, local demonstration and initial finite collector are implemented. Initial acquisition and recovery controls support further maintained-service validation; sustained source capacity and public-service readiness remain open. A supported public beta and selected expansions follow the pilot.

See [the roadmap](docs/ROADMAP.md), [repository contents](docs/REPOSITORY.md) and [change notes](CHANGELOG.md). Current automated results are available in [Repository checks](https://github.com/itsmeaokiji/traze/actions/workflows/checks.yml).

## License and notices

Original Traze code is licensed under the [MIT License](LICENSE), Copyright (c) 2026 Neru. Third-party code and assets retain their applicable terms; the project license does not grant trademark rights. See [licensing](docs/LICENSE_REVIEW.md), [third-party notices](THIRD_PARTY_NOTICES.md) and [frontend notices](frontend/THIRD_PARTY_NOTICES.md).
