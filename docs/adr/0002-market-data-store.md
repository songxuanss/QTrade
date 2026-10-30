---
status: accepted
---

# ADR 0002: Phase 1 market data store

## Context

The Phase 1 market/provider is not selected yet. For this decision the working workload is
standardized 1-minute, 5-minute, and daily OHLCV bars, with interactive per-instrument ranges
and bounded cross-section scans for research. A planning envelope of 2,000 instruments over
five years at 390 one-minute bars on 252 sessions/year is about 983 million rows per market
before daily/5-minute rollups. This is an explicit sizing assumption, not a commitment to a
market or provider. Tick and order-book storage are outside this first store decision.

## Considered options

- TimescaleDB: PostgreSQL-compatible hypertables, ordinary PostgreSQL operations and a direct
  fit for time-bounded instrument queries; broad analytical scans and bulk ingest may cost more.
- ClickHouse: strong columnar compression, bulk ingest, and cross-section scans; separate
  operational service and a less convenient fit for frequent small per-instrument range reads.

## Decision

Use TimescaleDB for the Phase 1 bar-store Adapter, with PostgreSQL still owning identity,
workspaces, permissions, audit, jobs, and configuration. All application/domain callers use
`MarketDataPort`; they never import TimescaleDB, ClickHouse, or SQL-specific types.

This decision follows the bar-centric interactive workload and the existing PostgreSQL
operational boundary, not a claim that TimescaleDB wins every benchmark. Reopen it before
production ingestion if the selected provider requires tick/order-book data, sustained
cross-section scans, or the measured retention envelope exceeds the tested capacity. The
Phase 0 probe below is directional and too small to certify the near-billion-row planning
case; load testing at the selected market's actual rate is a production-readiness gate.

## Verification

Run from the repository root with Docker:

```bash
.venv/bin/python scripts/benchmark-market-stores.py --rows 5000000 --repeats 5
```

The script creates uniquely named, loopback-only containers and removes them on exit. Images
are pinned in the script and their resolved digests are in
[`benchmarks/market-store-2026-10-04.json`](benchmarks/market-store-2026-10-04.json).
Container references follow the pinned upstream releases ([TimescaleDB image tags](https://hub.docker.com/r/timescale/timescaledb/tags), [ClickHouse image tags](https://hub.docker.com/r/clickhouse/clickhouse-server/tags)). On 2026-10-04, Intel N100 / 4 cores / 14 GiB RAM, warm-cache database execution medians were:

| Workload | TimescaleDB | ClickHouse |
| --- | ---: | ---: |
| 5M row CSV load | 40.89 s | 4.03 s |
| Single instrument range | 2.14 ms | 7 ms |
| Recent cross-section aggregation | 25.97 ms | 20 ms |
| Stored bytes including index/parts | 673.5 MB | 188.4 MB |

Timescale query timing uses `EXPLAIN ANALYZE`; ClickHouse uses `system.query_log` at millisecond
resolution. PostgreSQL and ClickHouse ingestion paths differ and the tested synthetic dataset
is deliberately small and warm-cache. These numbers characterize this probe only. The
versioned raw output, limitations, image digests, and host are in the JSON artifact.

The existing `MarketDataPort` defines only standard Bar query/write operations. The repository
architecture check passes and rejects database vendor dependencies at the business boundary.

## Consequences

A TimescaleDB production image/extension and a versioned OHLCV Adapter are needed in task 4.4;
the current Compose PostgreSQL image remains vanilla until that Adapter is implemented. Raw
provider batches remain immutable in object storage. The `MarketDataPort` contract preserves a
ClickHouse replacement seam if actual scale or grain requires it.
