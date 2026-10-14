# Repository structure

| Path | Purpose |
| --- | --- |
| hemi_lookup/ | Public-data discovery, supplied-data verification, accounting and SQLite catalogue |
| hemi_lookup_cli.py, hemi_lookup_web.py, hemi_catalogue.py | CLI, local browser server and catalogue entry points |
| frontend/ | React/TypeScript source, dependency manifest and asset notices |
| web/ | Supplied frontend build and distribution license text |
| scripts/ | Repository checks and independent Node evidence verifiers |
| catalogue_demo/ | Retained-data reference consumer |
| tests/ | Product regression tests, controlled source servers and historical fixtures |
| demo_results/ | Publication evidence examples and a scoped-negative packet |
| docs/ | Setup, development, verification, licensing and roadmap documentation |

[repository-files.json](../repository-files.json) defines the source distribution. Runtime cache, SQLite state, installed dependencies and generated reports are excluded.

```shell
python -B scripts/repository.py check
python -B scripts/repository.py copy --destination output/repository-candidate
```

The checker validates selected paths, required files, fixture fingerprints, local links, asset types and known publication-hygiene markers. Copy requires a new destination and preserves selected bytes.

The Bitcoin corpus contains 17 historical bodies totaling 25,845,550 bytes. Catalogue import stores compact publication/proof records; these fixtures are sample coverage. Source-only metadata controls are under tests/fixtures/source-feasibility/.

Receipt JSON and binary fixtures disable Git text normalization. Preserve their exact bytes and source observations. Frontend notices accompany the generated build; see [licensing](LICENSE_REVIEW.md).
