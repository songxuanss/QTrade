"""Pure category rules. Unknown evidence remains explicit; missing bars are never filled."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time
from typing import TYPE_CHECKING, Literal, Protocol
from uuid import uuid5
from zoneinfo import ZoneInfo

from qtrade_data_contracts.collection import CollectedBar, FactorPoint, digest, instrument_key
from qtrade_data_contracts.collection_quality import (
    CollectionQualityIssue,
    CollectionQualityResult,
    CollectionQualityRuleVersion,
    IssueCode,
)
from qtrade_data_core.ports import DataFailure

if TYPE_CHECKING:
    from qtrade_data_core.collection import CollectionPart, CollectionResult

RULE_VERSION: CollectionQualityRuleVersion = "collection-quality.v1"


class QualityEvaluator(Protocol):
    def __call__(
        self,
        part: CollectionPart,
        result: CollectionResult,
        timezone: str,
        checked_at: datetime,
        *,
        source_captured: bool,
    ) -> CollectionQualityResult: ...


def evaluate_v1(
    part: CollectionPart,
    result: CollectionResult,
    timezone: str,
    checked_at: datetime,
    *,
    source_captured: bool,
) -> CollectionQualityResult:
    zone = ZoneInfo(timezone)
    counts: Counter[tuple[IssueCode, Literal["block", "warning"]]] = Counter()

    def issue(code: IssueCode, severity: Literal["block", "warning"], count: int = 1) -> None:
        counts[code, severity] += count

    # Fingerprint normalized inputs rather than wall-clock time: retries of the
    # same evidence share a decision, while changed evidence gets a new identity.
    records = [row.model_dump(mode="json") for row in result.records]
    records_hash = digest(records)
    input_hash = digest(
        {
            "scope": str(part.request.scope_id),
            "part": str(part.part_id),
            "source": part.request.source,
            "mapping": part.mapping_version,
            "instrument": part.instrument_id,
            "field": part.field,
            "period": part.request.granularity,
            "start": part.start.isoformat(),
            "end": part.end.isoformat(),
            "timezone": timezone,
            "raw": result.raw,
            "records": records,
            "units": result.units,
            "time_label": result.source_time_convention,
            "evidence": result.evidence,
            "source_captured": source_captured,
        }
    )
    # Calendar acquisition alone cannot establish market observations. Requiring
    # the history capture also prevents an empty adapter result faking success.
    if not source_captured or (result.records and not result.raw):
        issue("raw_missing", "block")
    seen: set[object] = set()
    dates: set[date] = set()
    for row in result.records:
        try:
            type(row).model_validate_json(row.model_dump_json())
        except ValueError:
            issue("record_invalid", "block")
            continue
        if row.instrument_id != part.instrument_id:
            issue("identity_mismatch", "block")
        if isinstance(row, CollectedBar):
            if part.request.dataset_kind != "bars":
                issue("type_mismatch", "block")
            if row.granularity != part.request.granularity:
                issue("period_mismatch", "block")
            observed = row.timestamp
            if row.quality == "suspect":
                issue("source_suspect", "warning")
        elif isinstance(row, FactorPoint):
            if part.request.dataset_kind != "factors":
                issue("type_mismatch", "block")
            if row.factor_id != part.field:
                issue("identity_mismatch", "block")
            observed = datetime.combine(row.trade_date, time.min, zone)
            if row.value is None:
                issue("factor_null", "warning")
            if row.available_at is None or row.availability_evidence != "provider_timestamp":
                issue("knowledge_unknown", "warning")
            elif row.available_at > checked_at:
                issue("future_knowledge", "block")
        if not part.start <= observed < part.end:
            issue("range_mismatch", "block")
        if observed in seen:
            issue("duplicate", "block")
        seen.add(observed)
        dates.add(observed.astimezone(zone).date())
    if result.records and part.request.dataset_kind == "bars":
        kind, market, _, _ = instrument_key(part.instrument_id)
        price_unit = "index_points" if kind == "index" else "CNY" if market == "CN" else None
        if (
            not price_unit
            or result.units.get("prices") != price_unit
            or result.units.get("volume") != "shares"
            or result.units.get("value") != "CNY"
        ):
            issue("unit_mismatch", "block")
        if result.source_time_convention not in {
            "UTC",
            "local_trade_date",
            "interval_end_local_Asia/Shanghai",
        }:
            issue("time_label_unknown", "block")
        expected = result.evidence.get("expected_dates")
        if not isinstance(expected, list):
            issue("calendar_unknown", "warning")
        else:
            try:
                expected_dates = {date.fromisoformat(day) for day in expected}
                if any(
                    day < part.start.astimezone(zone).date()
                    or datetime.combine(day, time.min, zone) >= part.end
                    for day in expected_dates
                ):
                    raise ValueError
                missing = expected_dates - dates
                if dates - expected_dates:
                    issue("calendar_invalid", "block", len(dates - expected_dates))
                if missing:
                    issue("missing_trade_dates", "warning", len(missing))
            except (ValueError, TypeError):
                issue("calendar_invalid", "block")
        if part.request.granularity != "1d":
            issue("intraday_completeness_unknown", "warning")
    # Unknown factor semantics and legitimate nulls remain warnings; neither
    # makes a vendor factor historically knowable or certified accurate.
    if result.records and part.request.dataset_kind == "factors":
        definition = result.evidence.get("factor_definition")
        if not isinstance(definition, dict) or definition.get("calc_version") in {None, "unknown"}:
            issue("factor_definition_unknown", "warning")
        if result.units.get("value") in {None, "unknown", ""}:
            issue("factor_unit_unknown", "warning")
    issues = tuple(
        CollectionQualityIssue(code=code, severity=severity, affected_records=count)
        for (code, severity), count in sorted(counts.items())
    )
    status: Literal["blocked", "no_observations", "warning", "passed"] = (
        "blocked"
        if any(item.severity == "block" for item in issues)
        else "no_observations"
        if not records
        else "warning"
        if issues
        else "passed"
    )
    identity = digest(
        {
            "input": input_hash,
            "status": status,
            "issues": [item.model_dump(mode="json") for item in issues],
        }
    )
    return CollectionQualityResult(
        schema_version=1,
        quality_id=uuid5(part.part_id, identity),
        scope_id=part.request.scope_id,
        part_id=part.part_id,
        rule_version=RULE_VERSION,
        input_digest=input_hash,
        records_sha256=records_hash,
        record_count=len(records),
        checked_at=checked_at,
        status=status,
        issues=issues,
    )


# Static code registration, not a persistence-driven plugin mechanism. Adding a
# rule requires a reviewed evaluator and a matching closed contract literal.
_EVALUATORS: dict[CollectionQualityRuleVersion, QualityEvaluator] = {
    "collection-quality.v1": evaluate_v1,
}


def evaluator_for(rule_version: CollectionQualityRuleVersion) -> QualityEvaluator:
    try:
        return _EVALUATORS[rule_version]
    except KeyError:
        raise DataFailure("unsupported") from None


def evaluate(
    part: CollectionPart,
    result: CollectionResult,
    timezone: str,
    checked_at: datetime,
    *,
    source_captured: bool,
    rule_version: CollectionQualityRuleVersion = RULE_VERSION,
) -> CollectionQualityResult:
    """Dispatch explicitly; historical callers must supply their stored version."""
    return evaluator_for(rule_version)(
        part, result, timezone, checked_at, source_captured=source_captured
    )
