# ADR 0005: Publish derived minute data separately from raw tick downloads

## Status

Accepted (2026-10-06).

## Context

A completed tick download previously exposed only immutable raw batches. Market
charts and the existing research dataset reader require Bars, so a successful
download could not be used from those pages. Publishing a dataset must not make
raw archive completion depend on an aggregation or engine becoming available.

## Decision

Keep raw tick collection and research publication as separate user operations.
`ResearchDataService.publish(access, task_id)` accepts only a completed, scoped
MaiRui tick task, verifies raw objects through the existing replay module, and
publishes a deterministic dataset identity derived from the task and transformation
version. An identical retry reuses the existing dataset; a full redownload remains
a separate task and dataset. All batch references and quality reports remain immutable.

The transformation declares its output granularity. The replay module validates
that declared granularity independently of the source granularity, enabling tick
aggregation without referring to provider types. Provider parsing stays inside
the MaiRui adapter. Existing standard Bar replay retains its original provenance
checks and does not accept tick archives.

For Phase 1 publish observed 1-minute OHLCV only. MaiRui's historical trade endpoint
reports volume in shares; do not convert it using the board lot. Sort timestamps
ascending and reverse archive order for ties consistently across pages. The
provider has second-resolution timestamps, so tie ordering is a deterministic
archive convention, not evidence of exchange sequence. Do not interpolate gaps,
create empty trading minutes, or imply a real-time connection. Require minute-aligned
request bounds; incomplete downloads and empty trade archives cannot be published.

Market display may include downloaded identifiers absent from the master catalog,
labelled as downloaded historical data. Such display entries do not become trusted
instrument specifications or authorise execution. Real execution must separately
validate the instrument rules and engine readiness.

## Consequences

The UI can independently retry publication and show the dataset in market queries
and research selections. Failure leaves the verified raw download available.
Publication remains separate from backtest engine execution; availability of a
research dataset does not establish that an engine passed its acceptance criteria.

## Verification

Business tests cover descending archive order, ties crossing batch boundaries,
OHLC and share volume, absent minutes, unchanged raw payloads, idempotent replay,
incomplete downloads, role rejection and nonaligned ranges. Deployed UI acceptance
must also cover publication, refresh, chart periods, saved watchlist operations,
actual Timescale queries, and desktop/mobile accessibility before delivery.

Provider reference: [MaiRui historical trade endpoint](https://www.mairui.club/hsdata).
