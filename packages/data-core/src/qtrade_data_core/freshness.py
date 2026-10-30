"""Pure staged freshness; release expectations never derive from local coverage."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from qtrade_data_contracts.freshness import (
    FreshnessAction,
    FreshnessEvidence,
    FreshnessExpectation,
    FreshnessKey,
    FreshnessReason,
    FreshnessResult,
    FreshnessStage,
    FreshnessStatus,
)
from qtrade_data_core.default_range import DefaultRange
from qtrade_data_core.ports import DataFailure


def daily_expectation(
    key: FreshnessKey, window: DefaultRange, *, grace_seconds: int = 0
) -> FreshnessExpectation:
    """Use the provider's calendar/release rule, not a successful local task date.

    This is an expected release time. The source checkpoint still needs observed
    evidence; an estimated time alone does not certify provider availability.
    """
    if key.dataset_kind != "bars":
        raise DataFailure("unsupported")
    return FreshnessExpectation(
        complete_through=window.last,
        available_at=window.expected_available_at,
        timezone=window.timezone,
        rule_version="daily-price-freshness.v1",
        basis=window.basis,
        grace_seconds=grace_seconds,
    )


def evaluate(
    key: FreshnessKey,
    expectation: FreshnessExpectation | None,
    evidence: FreshnessEvidence,
    checked_at: datetime,
) -> FreshnessResult:
    if checked_at.utcoffset() is None:
        raise ValueError("explicit check clock required")
    if evidence.key != key:
        raise DataFailure("conflict")
    checkpoints = (evidence.source, evidence.collected, evidence.published)
    if any(point and point.observed_at > checked_at for point in checkpoints):
        raise DataFailure("unverified")
    if expectation is not None and expectation.available_at > checked_at:
        raise DataFailure("unverified")
    if expectation is not None and any(
        point
        and point.complete_through
        > point.observed_at.astimezone(ZoneInfo(expectation.timezone)).date()
        for point in checkpoints
    ):
        raise DataFailure("unverified")

    status: FreshnessStatus = "unknown"
    stage: FreshnessStage = "unknown"
    reason: FreshnessReason = "rule_unknown"
    action: FreshnessAction = "verify_rule"
    overdue = 0
    # Connectivity failure remains visible even when a cached publication looks
    # current. Unknown policy is never replaced by a weekday or local-date guess.
    if evidence.source_health == "unavailable":
        status, stage, reason, action = (
            "unavailable",
            "source",
            "source_unavailable",
            "check_source",
        )
    elif expectation is not None:
        cutoff = expectation.complete_through
        released = expectation.available_at + timedelta(seconds=expectation.grace_seconds)
        overdue = max(0, int((checked_at - released).total_seconds()))
        if evidence.published and evidence.published.complete_through >= cutoff:
            # A verified publication can prove the expected dataset is current;
            # it does not claim that the source's transport is currently healthy.
            status, stage, reason, action = "current", "none", "published_current", "none"
            overdue = 0
        elif evidence.source is None:
            reason, action = "source_unknown", "check_source"
            overdue = 0  # No measured overdue stage without source evidence.
        else:
            status = "within_grace" if checked_at < released else "late"
            if evidence.source.complete_through < cutoff:
                stage, reason, action = "source", "source_behind", "check_source"
            elif evidence.collected is None or evidence.collected.complete_through < cutoff:
                stage, reason, action = "collection", "collection_behind", "collect_missing"
            else:
                stage, reason, action = "publication", "publication_behind", "check_publication"
    return FreshnessResult(
        schema_version=1,
        scope_id=key.scope_id,
        key=key,
        checked_at=checked_at,
        expectation=expectation,
        evidence=evidence,
        status=status,
        stage=stage,
        reason=reason,
        overdue_seconds=overdue,
        action=action,
    )
