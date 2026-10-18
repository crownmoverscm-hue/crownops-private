# Source synchronization

Target repository: `crownmoverscm-hue/crownops-private`, branch `main`.

The supplied archive represents GitHub commit `247844fc1308abf19eb5cc0e0c1efb2be621a581`. Releases through 0.11.0-rc1 were developed from that archive. Repository access was confirmed on October 18, 2026 UTC; `main` still matched that baseline. The validated source was published on `audit/crownops-0.11-workflow-release` in [pull request #60](https://github.com/crownmoverscm-hue/crownops-private/pull/60).

Use the PR, its current checks and Railway deployment history for the publishing outcome. The initial source package and binary patch are preparation evidence rather than a claim of production deployment. Compare any newer remote work before applying an older archive. Do not replace the company database, uploads, persistent volume or encryption key when updating source.

The intended release flow is a checked branch and PR, merge with the expected head, then terminal Railway success and public version/asset verification. No credential belongs in source or chat.
