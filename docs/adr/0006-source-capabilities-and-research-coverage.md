# ADR 0006: Separate collection capability from research completeness

## Status

Accepted (2026-10-09).

## Context

The recent tick archive has a different historical depth and entitlement from
MaiRui's daily/minute history. A source name or a successfully downloaded file
cannot establish full research coverage. Provider-specific rules embedded in the
UI or replay application would make adding or replacing sources expensive.

## Decision

Describe configured collection capabilities through `HistoricalSource` and
validate them before creating a task. Production configuration enables unadjusted
daily, 5/15/60-minute history; 1-minute history requires explicitly configured Pro
entitlement. This flag does not grant a subscription: actual provider failures
remain visible task failures. Tick range policy applies to ticks only.

Keep raw source rows and the source's trading calendar in immutable archived
batches. Provider adapters interpret local times, minute close labels, and volume
units. The generic replay application consumes `DatasetQualityPort`, keyed by
transformation version, without importing provider SDKs. Coverage is included in
the checksum-verified immutable quality report and available through a scoped read.

Count observed trading dates against calendar evidence, reporting missing dates
without assuming suspension. A day containing one Bar is an observed day, not
proof of a complete intraday session. Classify these datasets as limited research
inputs even when all expected days are observed. Existing transformations without
an assessment report unverified coverage.

Do not backfill an unavailable history with synthetic ticks or silently overwrite
a published dataset. Historical state, corporate actions/adjustment factors,
historical universe/constituents and point-in-time financial revisions remain
separate prerequisites. Future PIT queries must distinguish publication time,
capture time and revision identity; newly fetched revised financial values must
not be presented as values known before capture without supporting evidence.

## Consequences

Users can download specified older daily/minute ranges and inspect meaningful
coverage without confusing collection success with complete quantitative research.
New sources and assessments replace adapters through narrow ports. This delivery
does not certify historical universe completeness, adjusted returns, real engine
backtests, realtime subscriptions or broker execution.

## Verification

Real deployed browser download and Worker flows cover 600519.SH, Beijing dates
2025-09-01 through 2025-09-05, daily and 5-minute data; original batches, report
checksums and matching Timescale counts are independently checked. Fixture tests
cover pagination, raw retention, timezone/volume semantics, missing days, calendar
conflicts, malformed source data and unavailable entitlement. Coverage of other
years, instruments and boards requires further real sample acceptance.
