"""Conservative completeness assessment using archived source calendar evidence."""

import json
from datetime import datetime, timedelta

from qtrade_contracts import Bar
from qtrade_domain.batches import ArchivedBatch
from qtrade_domain.datasets import ReplayRequest
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_mairui.history_bars import ZONE, MaiRuiHistoricalBars


class MaiRuiBarQuality:
    transformation_version = MaiRuiHistoricalBars.transformation_version

    def assess(
        self, request: ReplayRequest, batches: tuple[ArchivedBatch, ...], bars: tuple[Bar, ...]
    ) -> dict[str, object]:
        expected: set[str] = set()
        evidence = []
        for batch in batches:
            for record in batch.records:
                row = json.loads(record.payload)
                if row.get("kind") != "calendar":
                    continue
                try:
                    left, right = (
                        datetime.fromisoformat(row["start"]),
                        datetime.fromisoformat(row["end"]),
                    )
                    if left < request.start or right > request.end or left >= right:
                        raise ValueError
                    days = row["days"]
                    if not isinstance(days, list):
                        raise ValueError
                    for day in days:
                        stamp = datetime.fromisoformat(day).replace(tzinfo=ZONE)
                        if stamp < request.end and stamp + timedelta(days=1) > request.start:
                            expected.add(day)
                    evidence.append((left, right))
                except (KeyError, ValueError, TypeError):
                    raise PortError(
                        ErrorCode.INVALID_INPUT, "invalid archived calendar scope"
                    ) from None
        cursor = request.start
        for left, right in sorted(set(evidence)):
            if left > cursor:
                raise PortError(ErrorCode.INVALID_INPUT, "calendar evidence has a gap")
            cursor = max(cursor, right)
        if cursor != request.end:
            raise PortError(ErrorCode.INVALID_INPUT, "calendar evidence does not cover request")
        observed = {bar.timestamp.astimezone(ZONE).date().isoformat() for bar in bars}
        outside = sorted(observed - expected)
        if outside:
            raise PortError(ErrorCode.INVALID_INPUT, "source Bars outside trading calendar")
        missing = sorted(expected - observed)
        return {
            "schema_version": 1,
            "scope": "one-instrument-request",
            "expected_trading_days": len(expected),
            "observed_trading_days": len(observed),
            "missing_trading_days": missing,
            "observed_start": min(bar.timestamp for bar in bars).isoformat(),
            "observed_end": max(bar.timestamp for bar in bars).isoformat(),
            "bar_count": len(bars),
            "source": MaiRuiHistoricalBars.source,
            "adjustment": "none",
            "volume_unit": "shares",
            "source_volume_multiplier": 100,
            "coverage_status": "missing-days" if missing else "observed-days",
            "research_readiness": "incomplete" if missing else "limited",
            "limitations": [
                "Missing dates are not classified as suspension without security-state evidence.",
                "Intraday completeness and research reference data are not verified.",
                "Source minute volumes are normalized by 100 and checked against turnover/price.",
                "Structural validation is not proof of strategy or execution readiness.",
            ],
        }
