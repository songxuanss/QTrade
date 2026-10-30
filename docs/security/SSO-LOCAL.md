# Isolated sibling SSO integration

Requires the sibling `../sso` checkout, its locked dependencies, and the local
QTrade Compose PostgreSQL. The provider CSP fix is tracked in that repository.

```bash
UV_PROJECT_ENVIRONMENT="$PWD/.tools/sso-venv" .tools/uv/bin/uv sync \
  --project ../sso --locked --no-dev --python "$PWD/.venv/bin/python"
.tools/sso-venv/bin/python scripts/sso-local.py prepare
.tools/sso-venv/bin/python scripts/sso-local.py serve
```

The setup creates two randomly named test databases and migrates the actual SSO
and QTrade schemas. It seeds one test owner and a confidential development client:

| Setting | Value |
| --- | --- |
| Issuer | `http://127.0.0.1:3121` |
| QTrade origin | `http://127.0.0.1:3120` |
| Callback | `http://127.0.0.1:3120/auth/callback` |
| Back-channel logout | `http://127.0.0.1:3120/auth/backchannel-logout` |
| Post-logout redirect | `http://127.0.0.1:3120/` |
| Scopes | `openid email profile offline_access` |

Generated client ID, secret, test password, and database URLs are stored in
`.tools/sso-local/state.json`, excluded from Git and created with mode 0600.
They are local integration credentials. Do not copy them to deployed environments.
The local HTTP and insecure-cookie exceptions must remain explicit test settings.

Stop the server before cleanup:

```bash
.tools/sso-venv/bin/python scripts/sso-local.py cleanup
```

Cleanup accepts only the two recorded random test database names with the expected
prefixes, drops them, and removes the private configuration. It does not reset the
normal QTrade or SSO databases.

Verified: actual migrations, client/user seeding, real HTTP discovery, PKCE S256
declaration, RSA JWKS, server shutdown, and isolated database cleanup. The full
browser flow below also passed against the checked-out sibling provider.

## Real browser integration test

After preparing the isolated state and building `apps/web`, run:

```bash
cd apps/web
npx playwright test --config playwright.sso.config.ts
```

The test starts the actual sibling SSO, actual PostgreSQL-backed QTrade API, and
production standalone frontend on loopback ports 3121, 3122, and 3120. Traces,
screenshots, and videos are disabled for this credential-bearing integration flow.
The test reads generated credentials only from the private state file.

The initial browser run exposed a provider CSP defect: the consent document's
`form-action 'self'` policy blocked the registered QTrade callback. Commit
`22a35cf` in the sibling `sso` repository now allows only the origin from a
pending, recent, enabled-client transaction. Other templates and invalid,
expired, consumed, denied, malformed, or unregistered redirects retain the
self-only policy. Ten focused provider tests cover those boundaries.

## Reviewable sibling fix

`docs/integration/sso-csp.patch` preserves the reviewable provider change. To
rerun the CSP boundary tests against an isolated temporary copy:

```bash
.tools/sso-venv/bin/python scripts/selftest-sso-csp.py
# With freshly prepared isolated databases and an existing production web build:
.tools/sso-venv/bin/python scripts/selftest-sso-csp.py --browser
```

The temporary-copy test verifies CSP negative cases. The Playwright integration
test was also run against the committed sibling checkout and verified browser
login, callback, persisted session, two-tab access, signed SSO global logout,
and redirect to SSO after revocation. The isolated databases and credential file
were removed after the run.

## Resume the existing authenticated 13000 deployment

For the already prepared persistent local deployment, use the saved identity and
business data rather than preparing new databases:

```bash
.venv/bin/python scripts/verification-local.py resume
# Explicit restart of the owned native SSO/API/business Worker only:
.venv/bin/python scripts/verification-local.py restart
```

This profile uses Docker Web13000, native SSO3121/API3122/Worker3123, and independent
collector18100. The helper discovers existing Compose dependency addresses and
starts only existing stopped containers; it does not build images or reset data.
A private 0600 startup journal survives native-process exit and preserves identity
keys, database names and the object bucket. Keep the ignored private state files
on this machine; never commit or print them. Missing dependencies, foreign ports,
wrong process ownership and concurrent recovery return fixed errors without
killing external programs. A readiness timeout retains the registered process;
run resume again to inspect the same process rather than restarting on timeout.

See [actual recovery evidence](../testing/COLLECTION-RUNTIME-RECOVERY-2026-10-18.md).
This Linux loopback helper does not replace production orchestration, cross-host
deployment or the separately required isolated backup/restore verification.
