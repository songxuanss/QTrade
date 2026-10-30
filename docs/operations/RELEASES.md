# Release configuration and rollback

`compose.yaml` is a local development deployment. It contains development credentials
and HTTP addresses; do not use it as a production deployment. A protected deployment
must provide its own Compose file, TLS termination, infrastructure credentials and
mounted secret files. No real broker is configured, so Live remains disabled.

## Process startup

Set `QTRADE_ENV` to `local`, `dev`, `test`, `staging` or `production`. Boolean settings
accept only `true`/`false`; a misspelling fails startup. Staging/production require:

- `QTRADE_SECRETS_FILE`: a regular JSON file owned by the process user or root,
  permissions 0400/0600, maximum 64 KiB, mounted read-only. Symlinks and files readable
  by other users are rejected. Separate service files may contain only their needed keys.
- HTTPS `QTRADE_OIDC_ISSUER`, `QTRADE_PUBLIC_ORIGIN`, `QTRADE_API_PUBLIC_ORIGIN`.
  The web BFF also needs private `QTRADE_API_ORIGIN`; internal HTTP is allowed there.
- `QTRADE_OIDC_CLIENT_ID`, a version in `QTRADE_CONFIG_VERSION`, secure cookies,
  `QTRADE_OIDC_ALLOW_HTTP=false`, and `QTRADE_LIVE_TRADING_ENABLED=false`.
- Session encryption keys, primary key ID, a 32-byte base64 transaction key and a BFF
  bridge secret of at least 32 characters. Use cryptographically generated secrets.
- Domain and market databases, cache and object store settings. Development defaults
  containing `local-only` are rejected. Object storage must satisfy the adapter's HTTPS
  policy; configure trusted certificates on each process rather than disabling TLS.

Secret JSON keys may include `DATABASE_URL`, `MARKET_DATABASE_URL`, `REDIS_URL`,
`OBJECT_STORE_ACCESS_KEY`, `OBJECT_STORE_SECRET_KEY`, `MAIRUI_LICENCE`,
`QTRADE_OIDC_CLIENT_SECRET`, `QTRADE_BFF_SECRET`, `QTRADE_OIDC_TRANSACTION_KEY`,
`QTRADE_SESSION_KEYS_JSON`, `QTRADE_SESSION_PRIMARY_KEY`, `SMTP_PASSWORD` and
`QTRADE_WEBHOOK_DESTINATIONS`. Other names are rejected. Do not put public environment
switches in the secret file. Mounted values override environment values. Never commit
secret files; `*.secrets.json`, `.env` and `.tools/` are ignored and excluded from images.

Apply domain and market migrations in a separate deployment job using
`python -m qtrade_composition.startup` in the configured application image, then start
the services. Protected API/Worker/Realtime startup performs read-only, exact migration
name/version/checksum verification; it does not repair an incomplete or newer schema.
Runner has no database or SSO secrets and validates only its isolated process settings.
Web rejects configuration before readiness and exits; a Next hook exception alone would
leave its process alive, so its startup hook explicitly exits on rejection.

## Immutable records

From a clean checkout, build using `.venv/bin/python scripts/build-release.py`.
Images carry `org.opencontainers.image.revision`. Bring up the configured Compose
deployment, run its business acceptance checks, and retain the evidence separately.
Process health alone does not establish trading or data readiness.
The Compose migration job uses the exact API image rather than its own mutable tag.
Release recording requires that job to have exited successfully with that image ID;
rollback also pins the migration job to the retained API image. This prevents a healthy
application from being recorded after an outdated migration job ran.

With the same database/settings available to the operator process:

```bash
.venv/bin/python scripts/release.py record .tools/releases/release-v1.json
```

The record includes the Git commit, actual immutable image IDs, both migration
histories, public configuration version, environment, release UUID and timestamp.
Recording also tags each image under a unique `qtrade-retained` release reference so
rebuilding the local `latest` tags cannot remove the rollback image. Keep those tags
and do not prune retained images before the release retention period expires.
All services must be healthy and their image revisions must match the clean checkout.
Protected service environments must also match the declared environment/configuration
version. Records are created exclusively, never overwritten, and contain a SHA-256
integrity check. This detects corruption; it is not a signature. Restrict operator write
access and retain records in controlled backup storage. Records do not contain secrets
and do not certify unfinished business acceptance or authorize Live orders.

## Rollback

Retain the prior image IDs and the prior secret/configuration version in controlled
storage. Restore that configuration before planning a rollback. For a protected Compose
deployment, set `QTRADE_COMPOSE_FILE` to the same operator-supplied base Compose file.

```bash
.venv/bin/python scripts/release.py plan-rollback .tools/releases/release-v1.json \
  --override .tools/releases/rollback-plan.json
.venv/bin/python scripts/release.py rollback .tools/releases/release-v1.json \
  --override .tools/releases/rollback-execution.json \
  --receipt .tools/releases/rollback-receipt.json
```

Each output path must be new. Planning verifies the record integrity, environment,
configuration version, exact current database migrations, retained images and effective
Compose settings. Live must remain disabled. Execution uses the recorded image IDs,
`--no-build` and health acceptance, then verifies the actual deployed images and writes
an immutable receipt. It never runs down migrations. If the schema changed, rollback
is refused: prepare a compatible forward fix or perform a separately validated restore.
After rollback, rerun browser business checks and inspect persistence before accepting
the release. If execution fails, retain its private operator diagnostics and recover
using the known-good record; a missing receipt is not successful rollback.

## Local verification

`.venv/bin/python scripts/verification-deployment.py startup` exercises actual process
rejection. The rollback drill is explicitly local and keeps Live disabled:

```bash
.venv/bin/python scripts/verification-deployment.py baseline \
  --record .tools/releases/baseline.json
# Build and deploy a different committed revision before the next command.
.venv/bin/python scripts/verification-deployment.py rollback \
  --record .tools/releases/baseline.json
```

The drill makes the local Compose API unavailable, restores the previous immutable
images, checks readiness, and compares hashes of persisted domain/market rows. It is
not a staging backup/restore exercise or a live-order acceptance test. The SSO/browser
verification deployment on port 3120 remains separate from this local Compose drill.
