# Data quality context

`qtrade_domain.quality` owns the six deterministic checks and the issue state machine;
`DataQualityService` authorizes scans and lifecycle use cases; `QualityIssueStorePort` separates
PostgreSQL persistence from the domain. Evidence contains the measurements and source batch
references that explain each finding. Issue IDs derive from dataset identity plus finding
content, so retrying the same scan returns the same issue records.

Checks consume bars in source order before market-data persistence sorts them. The scanner
detects repeated natural keys, out-of-order timestamps, missing bars on the configured versioned
calendar grid, bars outside or misaligned with sessions, large close-to-close changes, and
volume outliers against a trailing median. Consecutive missing bars collapse to one range issue.
`apply_quality_flags` quarantines duplicate/calendar-deviation observations and marks price,
volume, and ordering anomalies suspect so a caller can keep them out of trusted calculations.
Thresholds are explicit `QualityPolicy` values; corporate actions and instrument-specific
adjustments can be added behind this policy seam.

Migration 0008 stores evidence separately from mutable handling state. Evidence and identity
cannot change or be removed; database guards allow only `open → claimed → closed`. Workspace
membership is checked under lock for scans and lifecycle changes. Browse queries are scoped to
workspace and dataset and ordered newest first. A future data workbench can add operator filters
without changing this domain contract.
