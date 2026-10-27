# Repository structure

| Path | Purpose |
| --- | --- |
| hemi_lookup/ | Public-data discovery, verification of supplied evidence, accounting and SQLite catalogue |
| traze_evidence/ | Standalone, offline interval and conditional first-height verifier required by the website |
| hemi_lookup_cli.py, hemi_lookup_web.py, hemi_catalogue.py | CLI, local browser server and catalogue entry points |
| frontend/ | React/TypeScript source, dependency manifest and asset notices |
| web/ | Supplied frontend build and distribution license text |
| scripts/ | Repository checks and independent Node evidence verifiers |
| catalogue_demo/ | Retained-data reference consumer |
| tests/ | Product regression tests, controlled source servers and historical fixtures |
| demo_results/ | Publication evidence examples and a scoped-negative packet |
| examples/snapshot-first-height/ | Optional public mainnet packet for offline version-3 snapshot verification |
| docs/ | Setup, development, verification, licensing and roadmap documentation |

[repository-files.json](../repository-files.json) defines the source distribution. Runtime cache, SQLite state, installed dependencies and generated reports are excluded.

```shell
python -B scripts/repository.py check
python -B scripts/repository.py copy --destination output/repository-candidate
```

The checker validates selected paths, required files, fixture fingerprints, local links, asset types and known publication-hygiene markers. An isolated Python process also imports the website, CLI, catalogue and standalone verifier using only selected local modules, with socket connections disabled and bytecode writes suppressed. Excluded workspace modules cannot satisfy missing release dependencies. This checks startup dependencies, not deployment readiness or every runtime path. Copy requires a new destination and preserves selected bytes.

The catalogue fixture corpus contains 17 historical bodies totaling 25,845,550 bytes. Catalogue import stores compact publication/proof records; these fixtures are sample coverage. Source-only metadata controls are under tests/fixtures/source-feasibility/.

The optional snapshot example contains ten separate Bitcoin bodies totaling
14,975,366 bytes, plus three manifests. It is portable public evidence rather
than installed runtime state. It does not change the quickstart's catalogue
counts and never accepts external assumptions on the reader's behalf.

Receipt JSON and binary fixtures disable Git text normalization. Preserve their exact bytes and source observations. Frontend notices accompany the generated build; see [licensing](LICENSING.md).

## Text and example checks

Text inspection covers selected UTF-8 files regardless of extension, including
deployment templates, SVGs and extensionless configuration. Unsupported nontext
data outside the reviewed binary formats fails the check. Credentials, personal
machine paths, working-session prose and development-only example bookkeeping
are rejected. These checks support review of the selected copy; they do not
rewrite Git history or replace review of newly introduced data formats.

[Public example provenance](../demo_results/public-example-provenance.json)
records source fingerprints. Historical observations and verification guarantees
remain separate from metadata normalization.
