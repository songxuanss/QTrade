# Immutable bar storage and queries

The application owns workspace authorization and accepts/returns versioned Bar contracts.
`MarketDataPort` is the store seam; vendor SQL and TimescaleDB details remain in the Adapter.
`MarketDataService` rejects pages outside the requested workspace, dataset, instrument,
granularity or time range, unsorted/duplicate timestamps, oversized pages and invalid cursors.

TimescaleMarketData stores immutable snapshots in a dedicated market database. Snapshot
creation, all bars and the canonical dataset hash commit in one transaction. Exact retries
are idempotent, while a changed snapshot conflicts. The canonical representation normalizes
Decimal scale and UTC offset without converting prices or volumes through floating point.
Versioned Bar JSON preserves batch/source references and quality state.

The Bar hypertable indexes workspace, dataset, instrument, granularity and timestamp. Queries
use [start,end), ascending timestamps and a context-bound keyset cursor. A cursor is not a
credential: all requests still pass the workspace guard. Cursor format version 1 binds scope
and filters; changing range/instrument/dataset requires a new query. Empty datasets and no-data
ranges return empty pages, while unknown datasets return not-found.

Database triggers reject update/delete/truncate and late bar insertion. An insert must share
its immutable snapshot's creation transaction. This enforces parent existence and prevents
late appends without the hypertable-FK first-chunk lock cycle detected by the concurrency test.

Compose runs separate PostgreSQL and pinned TimescaleDB services with separate volumes.
The migration job applies both checksum-locked catalogs before starting business processes.
`MARKET_DATABASE_URL` is server-side; readiness checks the extension and schema. Cache/market
outages fail readiness while liveness remains independent.

HTTP endpoints:

- POST `/api/v1/workspaces/{workspace_id}/datasets/{dataset_id}/bars`: DATA_MANAGE plus CSRF;
  body `{"bars":[...]}`; maximum 10,000 contracts and 5 MB. An empty immutable snapshot is allowed.
- GET the same endpoint: READ; instrument_id, granularity, aware start/end, limit and optional
  cursor. Response includes UTC boundaries, standard Bar contracts with quality/batch fields,
  and the next cursor.

Validation uses mock HTTP acceptance and actual PostgreSQL sessions/membership with actual
TimescaleDB storage. Six real-store cases cover concurrent retries/new chunks, semantic retry,
atomic COPY rollback, physical immutability/late appends, pagination/scope and authenticated
HTTP with immediate role changes. A 100,000-row / 100-instrument local sample returned 1,000
bars in 57.5 ms median and 162.3 ms nearest-rank p95 over ten adapter calls, including connection
and contract validation. See `docs/testing/market-data-adapter-2026-10-04.json`. The 1-second
local regression budget is a smoke guard; it does not establish production capacity.

Raw provider batches, immutable Parquet and replay/versioned Market Dataset manifests are
implemented separately in task 4.5. The present API imports an entire immutable Bar snapshot;
it is not an append API. Ingestion, quality detection and lineage build on the standard
contracts and the store seam in tasks 4.6–4.8.
