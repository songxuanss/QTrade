"""Explicit data-quality evidence and bounded OHLCV/calendar rules."""

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from statistics import median
from typing import Literal, Protocol, get_args
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from qtrade_contracts import Bar
from qtrade_domain.calendars import SessionWindow, TradingCalendar
from qtrade_domain.ports import ErrorCode, PortError

IssueCode = Literal[
    "missing",
    "duplicate",
    "out_of_order",
    "price_jump",
    "volume_anomaly",
    "calendar_deviation",
]
IssueStatus = Literal["open", "claimed", "closed"]
Severity = Literal["info", "warning", "critical"]
_INTERVALS = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "1d": timedelta(days=1),
}


@dataclass(frozen=True)
class QualityPolicy:
    max_price_change_ratio: Decimal = Decimal("0.25")
    volume_median_multiplier: Decimal = Decimal("10")
    volume_baseline_samples: int = 5
    volume_window: int = 20

    def __post_init__(self) -> None:
        if (
            not isinstance(self.max_price_change_ratio, Decimal)
            or not self.max_price_change_ratio.is_finite()
            or self.max_price_change_ratio <= 0
            or not isinstance(self.volume_median_multiplier, Decimal)
            or not self.volume_median_multiplier.is_finite()
            or self.volume_median_multiplier <= 1
            or type(self.volume_baseline_samples) is not int
            or not 2 <= self.volume_baseline_samples <= 100
            or type(self.volume_window) is not int
            or not self.volume_baseline_samples <= self.volume_window <= 500
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid data quality policy")


@dataclass(frozen=True)
class QualityIssue:
    issue_id: UUID
    workspace_id: UUID
    dataset_id: UUID
    code: IssueCode
    severity: Severity
    instrument_id: str
    granularity: str
    starts_at: datetime
    ends_at: datetime
    batch_ids: tuple[UUID, ...]
    evidence: str
    detected_at: datetime
    status: IssueStatus = "open"
    claimed_by: UUID | None = None
    closed_by: UUID | None = None
    closed_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("starts_at", "ends_at", "detected_at"):
            value = getattr(self, name)
            if not isinstance(value, datetime) or value.utcoffset() is None:
                raise PortError(ErrorCode.INVALID_INPUT, "quality issue timestamps must be aware")
            object.__setattr__(self, name, value.astimezone(UTC))
        if self.closed_at is not None:
            if not isinstance(self.closed_at, datetime) or self.closed_at.utcoffset() is None:
                raise PortError(ErrorCode.INVALID_INPUT, "closed timestamp must be aware")
            object.__setattr__(self, "closed_at", self.closed_at.astimezone(UTC))
        try:
            evidence = json.loads(self.evidence)
        except (TypeError, ValueError):
            evidence = None
        if (
            not all(
                isinstance(item, UUID)
                for item in (self.issue_id, self.workspace_id, self.dataset_id)
            )
            or self.code not in get_args(IssueCode)
            or self.severity not in get_args(Severity)
            or not 1 <= len(self.instrument_id) <= 256
            or self.granularity not in _INTERVALS
            or self.starts_at >= self.ends_at
            or not isinstance(self.batch_ids, tuple)
            or any(not isinstance(item, UUID) for item in self.batch_ids)
            or self.status not in get_args(IssueStatus)
            or not isinstance(evidence, dict)
            or self.evidence != json.dumps(evidence, sort_keys=True, separators=(",", ":"))
            or (
                self.status == "open"
                and (
                    self.claimed_by is not None
                    or self.closed_by is not None
                    or self.closed_at is not None
                )
            )
            or (
                self.status == "claimed" and (self.claimed_by is None or self.closed_by is not None)
            )
            or (self.status == "closed" and (self.closed_by is None or self.closed_at is None))
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid data quality issue")


def _evidence(value: dict[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def detect_quality_issues(
    workspace_id: UUID,
    dataset_id: UUID,
    bars: tuple[Bar, ...],
    calendars: dict[str, TradingCalendar],
    *,
    start: datetime,
    end: datetime,
    detected_at: datetime,
    policy: QualityPolicy | None = None,
) -> tuple[QualityIssue, ...]:
    """Analyze input order before persistence normalizes bars by timestamp."""
    policy = policy or QualityPolicy()
    if (
        start.utcoffset() is None
        or end.utcoffset() is None
        or detected_at.utcoffset() is None
        or start >= end
        or end - start > timedelta(days=366)
        or any(
            not isinstance(bar, Bar)
            or bar.workspace_id != workspace_id
            or bar.dataset_id != dataset_id
            or not start <= bar.timestamp < end
            for bar in bars
        )
    ):
        raise PortError(ErrorCode.INVALID_INPUT, "invalid quality scan scope")
    detected_at = detected_at.astimezone(UTC)
    groups: dict[tuple[str, str], list[Bar]] = {}
    for bar in bars:
        groups.setdefault((bar.instrument_id, bar.granularity), []).append(bar)
    found: list[QualityIssue] = []

    def add(
        code: IssueCode,
        instrument: str,
        granularity: str,
        left: datetime,
        right: datetime,
        affected: tuple[Bar, ...],
        detail: dict[str, object],
        severity: Severity = "warning",
    ) -> None:
        batch_ids = tuple(sorted({bar.batch_id for bar in affected}, key=str))
        payload = _evidence(detail)
        identity = sha256(
            f"{code}|{instrument}|{granularity}|{left.isoformat()}|{right.isoformat()}|{payload}".encode()
        ).hexdigest()
        found.append(
            QualityIssue(
                uuid5(dataset_id, "quality.v1:" + identity),
                workspace_id,
                dataset_id,
                code,
                severity,
                instrument,
                granularity,
                left,
                right,
                batch_ids,
                payload,
                detected_at,
            )
        )

    for (instrument, granularity), sequence in sorted(groups.items()):
        interval = _INTERVALS[granularity]
        calendar = calendars.get(instrument)
        session_windows: tuple[SessionWindow, ...] = ()
        if calendar is not None:
            timezone = ZoneInfo(calendar.timezone)
            local_start = start.astimezone(timezone).date()
            local_end = end.astimezone(timezone).date()
            calendar_start = max(local_start, calendar.effective_from)
            calendar_end = min(local_end + timedelta(days=1), calendar.effective_to)
            if calendar_start <= calendar_end:
                session_windows = tuple(
                    window
                    for offset in range((calendar_end - calendar_start).days + 1)
                    for window in calendar.windows(calendar_start + timedelta(days=offset))
                )
        by_time: dict[datetime, list[Bar]] = {}
        for bar in sequence:
            by_time.setdefault(bar.timestamp.astimezone(UTC), []).append(bar)
        duplicates = [(instant, rows) for instant, rows in by_time.items() if len(rows) > 1]
        for instant, rows in duplicates:
            add(
                "duplicate",
                instrument,
                granularity,
                instant,
                instant + interval,
                tuple(rows),
                {"records": len(rows), "source_batches": [str(row.batch_id) for row in rows]},
            )
        timestamps = [bar.timestamp.astimezone(UTC) for bar in sequence]
        for index in range(1, len(sequence)):
            if timestamps[index] < timestamps[index - 1]:
                add(
                    "out_of_order",
                    instrument,
                    granularity,
                    timestamps[index],
                    timestamps[index - 1] + interval,
                    (sequence[index - 1], sequence[index]),
                    {"previous": timestamps[index - 1], "current": timestamps[index]},
                )
        chronological = sorted(sequence, key=lambda item: item.timestamp.astimezone(UTC))
        for index, bar in enumerate(chronological):
            if index:
                previous = chronological[index - 1]
                ratio = abs(bar.close - previous.close) / previous.close
                if ratio > policy.max_price_change_ratio:
                    add(
                        "price_jump",
                        instrument,
                        granularity,
                        previous.timestamp,
                        bar.timestamp + interval,
                        (previous, bar),
                        {
                            "previous_close": previous.close,
                            "close": bar.close,
                            "change_ratio": ratio,
                            "threshold": policy.max_price_change_ratio,
                        },
                        "critical",
                    )
                history = [
                    item.volume
                    for item in chronological[max(0, index - policy.volume_window) : index]
                ]
                if len(history) >= policy.volume_baseline_samples:
                    baseline = Decimal(str(median(history)))
                    if baseline > 0 and bar.volume > baseline * policy.volume_median_multiplier:
                        add(
                            "volume_anomaly",
                            instrument,
                            granularity,
                            bar.timestamp,
                            bar.timestamp + interval,
                            (bar,),
                            {
                                "volume": bar.volume,
                                "median": baseline,
                                "multiplier": policy.volume_median_multiplier,
                            },
                        )
            if calendar is not None and not any(
                window.opens_at <= bar.timestamp.astimezone(UTC)
                and bar.timestamp.astimezone(UTC) + interval <= window.closes_at
                and (bar.timestamp.astimezone(UTC) - window.opens_at) % interval == timedelta(0)
                for window in session_windows
            ):
                add(
                    "calendar_deviation",
                    instrument,
                    granularity,
                    bar.timestamp,
                    bar.timestamp + interval,
                    (bar,),
                    {"reason": "bar outside trading sessions"},
                )
        if calendar is not None:
            expected: set[datetime] = set()
            for window in session_windows:
                if granularity == "1d":
                    if start <= window.opens_at < end:
                        expected.add(window.opens_at)
                else:
                    cursor: datetime = window.opens_at
                    while cursor + interval <= window.closes_at:
                        if start <= cursor < end:
                            expected.add(cursor)
                        cursor += interval
            missing_times = sorted(expected - set(timestamps))
            runs: list[list[datetime]] = []
            for missing in missing_times:
                if not runs or missing != runs[-1][-1] + interval:
                    runs.append([missing])
                else:
                    runs[-1].append(missing)
            for run in runs:
                add(
                    "missing",
                    instrument,
                    granularity,
                    run[0],
                    run[-1] + interval,
                    (),
                    {
                        "missing_count": len(run),
                        "first_missing": run[0],
                        "last_missing": run[-1],
                        "calendar_id": calendar.calendar_id,
                        "calendar_version": calendar.version,
                    },
                )
    return tuple(
        sorted(
            found,
            key=lambda item: (item.starts_at, item.instrument_id, item.code, str(item.issue_id)),
        )
    )


def apply_quality_flags(bars: tuple[Bar, ...], issues: tuple[QualityIssue, ...]) -> tuple[Bar, ...]:
    """Mark records implicated by a scan before exposing them to downstream consumers."""
    result: list[Bar] = []
    for bar in bars:
        quality = bar.quality
        for issue in issues:
            if (
                issue.instrument_id != bar.instrument_id
                or issue.granularity != bar.granularity
                or not issue.starts_at <= bar.timestamp < issue.ends_at
            ):
                continue
            if issue.code in {"duplicate", "calendar_deviation"}:
                quality = "quarantined"
                break
            if issue.code in {"price_jump", "volume_anomaly", "out_of_order"}:
                quality = "suspect"
        result.append(bar.model_copy(update={"quality": quality}))
    return tuple(result)


class QualityIssueStorePort(Protocol):
    def record_scan(self, actor_id: UUID, issues: tuple[QualityIssue, ...]) -> None: ...
    def list_issues(
        self, workspace_id: UUID, dataset_id: UUID | None = None, limit: int = 100
    ) -> tuple[QualityIssue, ...]: ...
    def claim_issue(
        self, actor_id: UUID, workspace_id: UUID, issue_id: UUID, now: datetime
    ) -> QualityIssue: ...
    def close_issue(
        self, actor_id: UUID, workspace_id: UUID, issue_id: UUID, now: datetime
    ) -> QualityIssue: ...


def claim(issue: QualityIssue, actor_id: UUID) -> QualityIssue:
    if issue.status != "open":
        raise PortError(ErrorCode.CONFLICT, "only open quality issues can be claimed")
    return replace(issue, status="claimed", claimed_by=actor_id)


def close(issue: QualityIssue, actor_id: UUID, now: datetime) -> QualityIssue:
    if issue.status != "claimed":
        raise PortError(ErrorCode.CONFLICT, "only claimed quality issues can be closed")
    return replace(issue, status="closed", closed_by=actor_id, closed_at=now)
