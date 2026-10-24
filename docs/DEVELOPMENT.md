# Development and checks

## Requirements

The backend uses Python 3.10+ and the standard library, including SQLite. Frontend dependencies are pinned in [package.json](../frontend/package.json) and [package-lock.json](../frontend/package-lock.json). Follow the manifest's Node version constraints. CI covers Windows/Linux with Python 3.10/3.12 and frontend checks with Node 24.

## Backend checks

```shell
python -B scripts/repository.py check
python -B -m unittest discover -s tests -v
```

Catalogue and main-app tests build isolated databases from the included body corpus. They do not require a pre-existing private catalogue or cache. Retained fixtures and controlled sources exercise proof checks, input binding, coverage, missing/corrupt data, interrupted work, source conflicts and accounting.

Collection controls additionally exercise hard process exits before/after body
commit, owned-lock recovery, resumed resource limits, metadata repair, indexed
reads during a source operation, read snapshots, changed views, cache eviction
and transient Windows ledger-reader locks. They use included bodies and
controlled RPC responses; see [the collection workflow](COLLECTION.md).

Some transport tests use loopback servers or subprocesses. Public Hemi/Bitcoin requests are not needed. Controlled source changes are not evidence of a naturally observed reorg.

Paired recovery checks cover committed WAL snapshots without source checkpoints,
external-ledger identity, original baselines, path migration, altered/missing
members, later usage/stops/clocks and restored acquisition interlocks:

```shell
python -B -m unittest discover -s tests -p test_paired_recovery.py -v
```

These tests use isolated fixtures and forbid public source reads. See the
[paired recovery runbook](PAIRED_RECOVERY.md) for operator commands and limits.

## Frontend checks and build

From `frontend/`:

```shell
npm ci --ignore-scripts --no-audit --no-fund
npm test
npm run typecheck
npm run build
```

Dependency installation may access the package registry. Frontend tests use supplied JSON and controlled responses.

The build writes generated assets to `web/`. Edit `frontend/src/`, not generated JavaScript/CSS. The Python bridge serves the supplied build; `npm run dev` watches and rebuilds while the Python server runs separately.

## Reference consumer

```shell
node --test catalogue_demo/demo.test.mjs
```

The separate catalogue consumer is an integration example. The main explorer remains the primary interface.

## Independent evidence verifiers

```shell
node scripts/verify-evidence.mjs demo_results/mainnet_5243117.json
node scripts/verify-input-binding.mjs demo_results/milestone3_march_later_hash_20261004T174823173306Z.json
node --test tests/verifier-cli.test.mjs
```

These tools recompute supplied-data checks without Python or network calls. Generated reports go to demo_results/. Hash-input tamper checks are reported only for receipts that actually declare a hash input.

## Automated configuration

[The workflow](../.github/workflows/checks.yml) defines backend checks on Windows/Linux with Python 3.10/3.12 and frontend checks with Node 24. It uses read-only repository permissions and pinned action commits. It has no deployment step.

Inspect the latest [hosted results](https://github.com/itsmeaokiji/traze/actions/workflows/checks.yml) for the commit being reviewed. CI results validate the configured tests; provider availability and operating capacity require separate measurements.

Action references: [checkout](https://github.com/actions/checkout), [setup-python](https://github.com/actions/setup-python), [setup-node](https://github.com/actions/setup-node).

## Change discipline

Keep input identity, proof validity, source observations, coverage and selection distinct. Preserve original receipts and accounting. Use keystone in public-facing language; existing internal receipt keys such as `checkpoint` remain compatible.

Run checks appropriate to the change. Record measured results and limits; a passing build or fixture test does not establish sustained provider capacity, complete history or production readiness.

When rebuilding the frontend, retain [dependency notices](../frontend/THIRD_PARTY_NOTICES.md). The combined notice in frontend/public/ is copied into web/ with the generated assets.
