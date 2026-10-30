# Identity audit boundary

Identity operations emit `auth.login`, `auth.logout`, `permission.denied`, and
`membership.change`. PostgreSQL writes successful session creation, revocation,
back-channel logout receipt consumption, and membership changes together with
their audit facts in the same transaction. An audit failure rolls back the
operation, including the logout replay receipt, so a safe retry remains possible.

OIDC callback failures are anonymous: unverified claims never supply actor or
Workspace IDs. The BFF sends an authenticated, empty-body failure notification
to `/internal/auth/login-failed`. The endpoint rejects browser callers and any
payload; it records a fixed reason rather than provider errors, codes, or tokens.
If recording fails, the callback returns an unavailable response and clears its
temporary transaction cookie.

Workspace guards audit both RBAC and session-bound CSRF denials before a business
handler runs. Cross-workspace attempts retain the attempted Workspace in typed
details, while their audit Workspace references the actor's current Workspace;
this avoids inventing resources or leaking whether a target exists.

`AuditRecord` and `AuditDetails` are immutable domain facts. Details allow only
UUID targets, session SHA-256 references, permission/error classifications, and
membership role/activation summaries. Arbitrary payloads and credential fields
are excluded. HTTP context is injected explicitly through application services
and storage ports; the domain has no HTTP dependency.

Trace UUIDs are generated at the server boundary. Source IP is the physical
connection address; forwarded headers and client-supplied trace headers are not
trusted. For BFF calls this is the BFF address, not a claim about the browser's
address. Direct service callers retain their explicit client context.

The database rejects audit UPDATE, DELETE, and TRUNCATE. Query/export is implemented
in task 9.2. Retention and complete trading/research event coverage remain separate
work; an audit query does not establish those integrations.

Administrators and risk managers can use `/workspace/audit`, backed by
`GET /api/v1/workspaces/{workspace_id}/audit` and `/audit/export`. Filters include
UTC start/end, actor UUID, target UUID, action, result and server trace UUID.
Ranges are limited to 31 days, queries to 200 events per page, and exports to
5,000 events; oversized exports fail rather than omit records. Pagination uses
descending event IDs. CSV export is itself audited before the response is sent.

The storage adapter verifies current membership and tenant scope. Query results
omit session digests and arbitrary JSON details, mask IPv4/IPv6 addresses to
network prefixes, and retain only typed business metadata. CSV cells that can
start spreadsheet formulas are escaped. Responses are not cached. HTTP trace
UUIDs match OpenTelemetry request spans; no client trace headers are trusted.

Verification covers actual PostgreSQL transaction rollback, append-only rules,
typed details, request context, role/CSRF denial, and production Next.js browser
flows for valid login/logout and invalid signature/state. Browser tests confirm
failed callbacks add an audit without mapping an identity or creating a session.
