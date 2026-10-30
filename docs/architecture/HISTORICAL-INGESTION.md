# Historical collection boundary

Historical acquisition is an application service (`HistoricalIngestionWorker`) over a
provider port, immutable raw-batch service, dataset replay service, and ingestion repository
port. Domain state (`CollectionRequest`, `ProviderPage`, `StagedPage`, `IngestionTask`, and
`IngestionLease`) is independent of vendor SDKs and persistence. A provider implementation
translates its paging and retry signals into bounded `RawRecord` pages; it never writes the
market store directly.

Each `run_once` invocation handles one page or the final replay step. The repository lease
fences concurrent and stale workers. Before archiving, the worker durably stages the exact
page and collection timestamp; after the immutable Parquet batch is committed it advances a
deterministic page checkpoint. A restarted worker reuses a staged page instead of refetching
it. Batch IDs are deterministic per task/page, so retrying an archive converges on the same
object metadata. Replay uses the ordered batch list and a task-derived dataset ID.

The rate limiter is shared across tasks in a workspace, provider source, and license reference.
Provider `Retry-After` and bounded exponential backoff persist as `next_attempt_at`. Cursor
values are opaque and never logged; only SHA-256 hashes of consumed continuation tokens are
stored. A repeated token or cursor cycle archives the page that exposed it, then fails the task
without replaying a potentially incomplete dataset. Rerun retains accepted batches and cursor
history, while an explicit provider correction can continue from the checkpoint.

PostgreSQL migration 0007 stores requests, staged page bytes, cursor history, checkpoints,
retry state, and lease fences. Database triggers reject deletion and backward checkpoint
movement. The service currently has no selected external provider or deployment-specific
worker composition; provider credentials and the background access resolver must be configured
before a production polling process is enabled. The tested service contract uses injected
providers and real PostgreSQL/Timescale persistence.

Tests cover paging, workspace/source/license throttling, retry-after, exponential retry,
network failure, process reconstruction, exact staged-page reuse, idempotent archive retries,
repeated and cyclic cursors, workspace role revocation, lease expiry/fencing, and concurrent
claims. PostgreSQL and Timescale acceptance tests exercise persistent checkpoint recovery and
the complete archive-to-dataset path.
