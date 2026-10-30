# Instrument Master

An Instrument belongs to a Workspace and carries an explicit specification
version. Imports never overwrite an existing specification. An identical retry
returns `unchanged`; a differing proposal returns `conflict` and a stable pending
quality-issue ID. The issue retains both existing and proposed specifications.
Accepting a correction is a separate future quality-management operation.

The domain owns validation and the `InstrumentStorePort`. The application owns
permission/scope checks. Mock and PostgreSQL implementations provide the same
import/query behavior. HTTP uses the shared live session, workspace RBAC, and
session-bound CSRF guards; the PostgreSQL import transaction rechecks authority.

PostgreSQL migration 0003 separates immutable specifications, current references,
and conflict records. Workspace locks serialize concurrent imports and membership
mutations. Batch writes commit together or roll back together. The database rejects
specification UPDATE, DELETE, and TRUNCATE. Conflict retries canonicalize Decimal
representation so equivalent proposals share one issue.

Query defaults to enabled instruments. It supports exact identifier, market, and
exchange filters, casefolded name search, explicit disabled/all state selection,
and identifier pagination. Precision validation examines Decimal digits directly,
without float conversion or context rounding. Calendar IDs and IANA timezones are
preserved; actual trading sessions/market states are supplied by task 4.2.

Workspace API prefix: `/api/v1/workspaces/{workspace_id}/instruments`.

| Method/path | Behavior |
| --- | --- |
| `POST /import` | `data.manage` + CSRF; `{instruments: [...]}`; maximum 200 items/500 KB |
| `GET /` | Authorized read; identifier/name/exchange/market/enabled/limit/after filters |
| `GET /conflicts` | Preserved existing/proposed specifications and pending issue metadata |
| `GET /{instrument_id}` | Detail including disabled instruments; supports slash-bearing IDs |

Imports return `created`, `unchanged`, or `conflict` with an optional issue ID.
Invalid import errors do not echo submitted payloads. Cross-workspace routes return
404; authorization/CSRF failures return 403 and are audited by the shared guard.
Instrument import event correlation remains part of complete audit coverage in
task 9.1; quality issue handling/UI is task 4.7/4.10.

Verification includes Mock/real PostgreSQL behavior, eight-way import/conflict
concurrency, immutable storage, transaction failure rollback, current-role HTTP
authorization, forged writes, query filters, Decimal precision, and migration
upgrade/repeat/failure checks.
