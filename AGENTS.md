# QTrade delivery checks

User requirement: testing must comprehensively cover the main business flows end to end.

The non-negotiable constraints live in `CONSTITUTION.md`; `scripts/check_constitution.py` enforces the automated articles in CI. Behavior changes also need an `openspec/changes/` delta (mark pure refactors `[no-spec-change]`).

- Before declaring a main feature ready, exercise its UI through the deployed API, real persistence, and required background workers. Verify the final business result after refresh, not only request acceptance or service health.
- Keep a coverage matrix for login/permissions, strategy versions, data collection, backtest/report, alerts, and connected trading controls. Record which flows used real integrations, fixtures, or were blocked; passing fixtures do not establish live integration readiness.
- Include rejection paths, role restrictions, persistence, and desktop/mobile UI checks for the changed flow. Never include credentials in logs, screenshots, test reports, or commits.
- A download flow requires task creation, Worker consumption, provider response, persisted nonempty raw batches, and visible completion. A queued task alone is not success.
- External failures and unconnected capabilities must be reported explicitly. Do not mark incomplete flows done or expand to live orders without the required project controls.

## Git delivery

- Run project Git commits in the designated Ubuntu 24 environment. Use the locked project toolchain in an isolated local environment; Ubuntu 26 remains an optional deployment verification target.
- Preserve the existing repository history. If Git metadata is missing, recover the original checkout or clone the original remote before committing; do not initialize a replacement history.
- Commit each coherent, verified milestone as it is completed instead of accumulating all changes for a final commit. Include the relevant tests and evidence, and accurately state any remaining acceptance limitations.
- Review the staged diff for credentials and generated/private files before each commit. Do not mix unrelated user changes into milestone commits.
