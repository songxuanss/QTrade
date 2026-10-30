"""Pure MaiRui conversions: no network/client/catalog/cache dependency."""

import json
from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from qtrade_data_contracts.collection import CollectedBar, FactorPoint, instrument_key
from qtrade_data_contracts.normalization_input import (
    BarNormalizationInput,
    FactorNormalizationInput,
    NormalizationPayload,
)
from qtrade_data_core.collection import CollectionPart, CollectionResult
from qtrade_data_core.ports import DataFailure

ZONE = ZoneInfo("Asia/Shanghai")
MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "1d": 0}


def source_time(row: Mapping[str, object], period: str) -> datetime:
    local = datetime.fromisoformat(str(row["t"]))
    if local.tzinfo is not None:
        raise ValueError("source time must be local")
    return (local.replace(tzinfo=ZONE) - timedelta(minutes=MINUTES[period])).astimezone(UTC)


def normalize(part: CollectionPart, payload: NormalizationPayload) -> CollectionResult:
    """Interpret a complete frozen input; never acquire missing context.

    Registered v1/v2 outputs remain reproducible, including their flag behavior
    and the unverified 09:30 boundary. Ordinary-history v3 changes only numeric
    suspension-flag interpretation; Pro endpoints retain their own versions.
    """
    try:
        if payload.kind != part.request.dataset_kind:
            raise DataFailure("conflict")
        kind, market, _, _ = instrument_key(part.instrument_id)
        if market != "CN" or kind not in {"stock", "index"}:
            raise DataFailure("unsupported")
        if isinstance(payload, BarNormalizationInput):
            return bars(part, payload, index=kind == "index")
        return factors(part, payload)
    except (ValueError, TypeError, KeyError, ArithmeticError):
        raise DataFailure("unverified") from None


def _source_quality(
    part: CollectionPart, row: Mapping[str, object], *, index: bool
) -> Literal["valid", "suspect"]:
    if part.mapping_version != "mairui.history-bars.v3":
        # Existing frozen releases retain their exact v1/v2 behavior. In
        # particular Pro 1m uses endpoint-specific fields and is not silently
        # migrated to ordinary-history suspension semantics.
        return "suspect" if row.get("sf", 0) not in {0, "0", None} else "valid"
    if part.request.source != "mairui.history.bars":
        raise DataFailure("conflict")
    # Ordinary index history documents no stock suspension flag. Its absence
    # is inapplicable, not unknown stock status; retain any extra field in RAW.
    if index:
        return "valid"
    flag = row.get("sf")
    if flag is None:
        return "suspect"
    if isinstance(flag, bool) or not isinstance(flag, str | int | float | Decimal):
        raise DataFailure("unverified")
    # Exact archival encodes Decimal as text. Compare the numeric value, never
    # its spelling: 0, 0.0 and 0E-6 are the same ordinary-history source flag.
    value = Decimal(str(flag))
    if not value.is_finite():
        raise DataFailure("unverified")
    return "valid" if value.is_zero() else "suspect"


def bars(part: CollectionPart, payload: BarNormalizationInput, *, index: bool) -> CollectionResult:
    period = part.request.granularity
    if payload.slices[0].start != part.start or payload.slices[-1].end != part.end:
        raise DataFailure("conflict")
    selected = [
        dict(row)
        for chunk in payload.slices
        for row in chunk.rows
        if chunk.start <= source_time(row, period) < chunk.end
    ]
    if payload.calendar_days is None:
        raise DataFailure("unverified")
    calendar = [day.isoformat() for day in payload.calendar_days]
    mechanisms = payload.fetch_mechanisms
    selected.sort(key=lambda row: source_time(row, period))
    records: list[CollectedBar] = []
    seen: set[datetime] = set()
    for row in selected:
        timestamp = source_time(row, period)
        if timestamp in seen:
            raise DataFailure("unverified")
        seen.add(timestamp)
        bar = CollectedBar(
            instrument_id=part.instrument_id,
            timestamp=timestamp,
            granularity=period,
            open=Decimal(str(row["o"])),
            high=Decimal(str(row["h"])),
            low=Decimal(str(row["l"])),
            close=Decimal(str(row["c"])),
            volume=Decimal(str(row["v"])) * 100,
            value=Decimal(str(row["a"])) if row.get("a") is not None else None,
            quality=_source_quality(part, row, index=index),
        )
        if not index and bar.volume and bar.value:
            if (
                not bar.low * Decimal("0.97")
                <= bar.value / bar.volume
                <= bar.high * Decimal("1.03")
            ):
                raise DataFailure("unverified")
        records.append(bar)
    dates = {record.timestamp.astimezone(ZONE).date().isoformat() for record in records}
    expected = sorted(
        {
            day
            for day in calendar
            if part.start.astimezone(ZONE).date()
            <= date.fromisoformat(day)
            < (part.end - timedelta(microseconds=1)).astimezone(ZONE).date() + timedelta(days=1)
        }
    )
    # The observed 1m year slice includes an additional 09:30 label. Keep
    # that source observation; neither infer an auction nor discard it to
    # force a 240-bar day. Existing end-label conversion remains frozen.
    opening_labels = [
        str(row["t"])
        for row in selected
        if period == "1m" and datetime.fromisoformat(str(row["t"])).time() == time(9, 30)
    ]
    raw = tuple(json.loads(json.dumps(row, default=str)) for row in selected)
    return CollectionResult(
        raw,
        tuple(records),
        {"volume": "shares", "value": "CNY", "prices": "CNY" if not index else "index_points"},
        "interval_end_local_Asia/Shanghai" if period != "1d" else "local_trade_date",
        {
            "expected_dates": expected,
            "observed_dates": sorted(dates),
            "missing_dates": sorted(set(expected) - dates),
            "fetch_mechanisms": sorted(set(mechanisms)),
            "volume_multiplier": 100,
            "adjustment": "none",
            "opening_boundary_source_labels": opening_labels,
            "opening_boundary_semantics": "unverified" if opening_labels else "not_observed",
        }
        | (
            {
                "source_flag_semantics": (
                    "not_applicable_for_index"
                    if index
                    else "regular_history.suspension_indicator"
                ),
                "source_flag_unknown_count": (
                    0 if index else sum(row.get("sf") is None for row in selected)
                ),
            }
            if part.mapping_version == "mairui.history-bars.v3"
            else {}
        ),
        (
            "交易日覆盖不证明每根分钟线完整；停牌与来源缺档尚未完全区分。",
            "公司行为、交易状态及历史可知性尚未完成研究准入。",
        )
        + (
            (
                "来源1m含09:30额外观测；既有结束标签转换为09:29，其时段语义未确认，不能认证为常规一分钟区间。",
            )
            if opening_labels
            else ()
        ),
    )


def factors(part: CollectionPart, payload: FactorNormalizationInput) -> CollectionResult:
    if part.field != payload.definition.factor_id:
        raise DataFailure("conflict")
    rows = payload.rows
    definition = payload.definition
    records: list[FactorPoint] = []
    selected: list[dict[str, object]] = []
    if len(rows) > 365:
        raise DataFailure("unverified")
    for row, kind in zip(rows, payload.value_kinds, strict=True):
        day = datetime.strptime(str(row["trade_date"]), "%Y%m%d").date()
        observed = datetime.combine(day, datetime.min.time(), ZONE).astimezone(UTC)
        if not part.start <= observed < part.end:
            raise DataFailure("unverified")
        value = row.get("value")
        if (value is None) != (kind == "missing"):
            raise DataFailure("unverified")
        records.append(
            FactorPoint(
                instrument_id=part.instrument_id,
                factor_id=definition.factor_id,
                trade_date=day,
                value=Decimal(str(value))
                if kind == "number"
                else str(value)
                if value is not None
                else None,
                value_kind=kind,
                missing_reason=("provider_null" if definition.available else "not_available")
                if value is None
                else None,
                calc_version=definition.calc_version,
            )
        )
        selected.append(json.loads(json.dumps(row, default=str)))
    records.sort(key=lambda item: item.trade_date)
    if len({item.trade_date for item in records}) != len(records):
        raise DataFailure("unverified")
    return CollectionResult(
        tuple(selected),
        tuple(records),
        {"value": definition.unit},
        "local_trade_date",
        {
            "factor_definition": definition.model_dump(mode="json"),
            "null_points": sum(p.value is None for p in records),
            "availability_evidence": "unknown",
        },
        ("来源仅给交易日，历史公开可知时间未验证；不标为财务PIT。",),
    )
