# Contributing

Traze is a developer-run Hemi–Bitcoin publication explorer. Original Traze code uses the [MIT License](LICENSE).

Read [development instructions](docs/DEVELOPMENT.md), [verification scope](docs/VERIFICATION.md) and [the roadmap](docs/ROADMAP.md).

## Reviewable changes

Describe the concrete problem, resulting behavior and relevant validation. Keep proof checks, source observations, input identity and coverage separate. Preserve original evidence and accounting.

Use professional English in product copy, errors, receipts and documentation. Use keystone for public-facing Hemi protocol language. Keep existing receipt keys compatible unless a versioned migration is explicitly selected.

Fixtures and controlled failures must be labelled. Automated tests should not depend on live Hemi/Bitcoin providers, private nodes, paid services or personal runtime state.

## Issue reports

Include the application version/status, operation, expected/actual result and a qualified evidence packet where appropriate. Include recorded source time and coverage. Review outbound files before sharing credentials, local configuration or unrelated data.

Open a GitHub issue with reproducible steps and the relevant evidence details. Remove credentials and unrelated local configuration from attachments.

## Release review

Run checks appropriate to the change, update relevant docs and describe material limitations. Service releases also need documented coverage, operating limits and maintenance procedures.
