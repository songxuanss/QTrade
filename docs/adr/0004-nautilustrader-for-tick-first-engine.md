# ADR 0004: Select NautilusTrader for the tick-first engine adapter

## Status

Accepted for Phase 1 adapter development (2026-10-05).

## Context

QTrade's initial market is mainland China A-shares. Users need to select and
download bounded tick-data ranges; Hong Kong data is out of scope for the first
version. QTrade already owns market-data acquisition, normalized contracts,
backtest manifests, and result storage. The engine must consume local immutable
datasets through an isolated runner and return QTrade contracts.

The LEAN integration seam and canonical result converter were prototyped, and
their unit tests pass. End-to-end execution was not verified: the pinned official
amd64 image pull remained without observable progress for about 30 minutes and
the published image is approximately 13 GB compressed. This fails the Phase 1
operability and feedback-time criteria for a repeatable local acceptance run.
The run was stopped without claiming that LEAN's engine semantics failed.

The market-specific part is also an adapter responsibility. LEAN can ingest
custom data, but QTrade would need to implement and maintain the A-share calendar,
instrument metadata, lot/price-limit rules, and data bridge either way. Nautilus
provides a lower-level backtest API for custom data streams and explicit venue,
instrument, and execution configuration, which better matches QTrade's tick-first
boundary. This is a fit decision, not a claim that Nautilus ships a turnkey China
A-share venue adapter.

## Decision

Use NautilusTrader behind `TradingEnginePort` for Phase 1 tick-first backtests.
Keep QTrade canonical contracts and `BacktestExecutionModel` as the authority
for supported A-share assumptions. Implement a dedicated `adapters/nautilus`
anti-corruption layer and a resource-limited runner. Do not expose Nautilus types
through domain, API, database, or UI modules.

Do not adopt or ship the LEAN prototype. Re-open this decision only if the
Nautilus adapter cannot pass the same manifest replay, read-only data, failure
mapping, result normalization, and deterministic-repeatability acceptance tests,
or if the supported target expands to markets where LEAN's native venue coverage
materially reduces integration risk.

## Considered options

- **LEAN:** mature multi-asset engine with a broad ecosystem and custom-data
  extension points. The pinned image's size and stalled pull blocked the local
  end-to-end acceptance loop; A-share venue semantics would still be QTrade-owned.
- **NautilusTrader:** event-driven Rust core with Python strategy/control APIs,
  a low-level engine that accepts custom data streams, and explicit simulated
  venue/execution configuration. Requires a dedicated adapter and does not supply
  a turnkey A-share venue.
- **Keep only MockEngine:** fastest for deterministic integration tests, but it
  would leave users without the intended production-grade tick replay engine.

## Verification

- LEAN container contract tests and result-converter unit tests passed before the
  evaluation code was removed from the production tree.
- LEAN image digest:
  `sha256:1ed1d6b42f75dd713e538e14b9cfb325ef9ac90f894630f34144c438bc428a97`.
- The 30-minute image pull did not produce a locally inspectable image; therefore
  no LEAN algorithm execution, read-only mount probe, or repeated result hash is
  claimed.
- Nautilus official docs describe low-level `BacktestEngine` support for direct
  custom-data loading and explicit engine/venue setup:
  [Backtest APIs and Repeated Runs](https://nautilustrader.io/docs/latest/concepts/backtesting/apis-and-runs/).
  Custom data and Parquet catalog constraints are documented in
  [Data](https://nautilustrader.io/docs/latest/concepts/data/).
- Runner acceptance tests remain required before enabling Nautilus in a deployed
  environment. This ADR authorizes adapter implementation, not live trading.

## Consequences

The runner, input manifest, result contract, and cost-model boundary remain
engine-neutral. Nautilus-specific translation belongs in one adapter package.
QTrade continues to own A-share market rules, data licensing, calendar semantics,
and result interpretation. The LEAN prototype is intentionally not retained as
an unvalidated production option. `TradingEnginePort` remains replaceable if
Nautilus fails the listed acceptance tests.
