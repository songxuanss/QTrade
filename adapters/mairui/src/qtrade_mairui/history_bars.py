"""MaiRui unadjusted history, with original rows and calendar evidence retained."""

import json
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from threading import RLock
from time import monotonic
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx

from qtrade_contracts import Bar
from qtrade_contracts.models import BarGranularity, Granularity
from qtrade_domain.batches import ArchivedBatch
from qtrade_domain.ingestion import CollectionRequest, ProviderPage
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.raw_records import RawRecord

ZONE = ZoneInfo("Asia/Shanghai")
PERIODS = {"1m": "1", "5m": "5", "15m": "15", "30m": "30", "1h": "60", "1d": "d"}
MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60}


def symbol(value: str) -> str:
    value = value.upper().removeprefix("CN.")
    match = re.fullmatch(r"(\d{6})(?:\.(SH|SZ|BJ))?", value)
    if match is None:
        raise PortError(ErrorCode.UNSUPPORTED, "history supports A-share symbols")
    code, market = match.groups()
    inferred = "SH" if code.startswith("6") else "BJ" if code.startswith(("4", "8", "92")) else "SZ"
    if market is not None and market != inferred:
        raise PortError(ErrorCode.INVALID_INPUT, "symbol market mismatch")
    return f"{code}.{market or inferred}"


def bar_time(row: dict[str, object], granularity: str) -> datetime:
    value = datetime.fromisoformat(str(row["t"]))
    if value.tzinfo is not None:
        raise ValueError("expected local provider time")
    # Provider minute labels are interval ends (09:35 is the 09:30-09:35 Bar).
    return (value.replace(tzinfo=ZONE) - timedelta(minutes=MINUTES.get(granularity, 0))).astimezone(
        UTC
    )


class MaiRuiHistoricalBars:
    source = "mairui.a-share.bar"
    authorization_id = "mairui-default"
    transformation_version = "mairui.unadjusted-bars.v1"

    def __init__(self, licence: str, *, pro: bool = False, client: httpx.Client | None = None):
        if not licence:
            raise ValueError("MaiRui licence required")
        self._licence, self._pro = licence, pro
        self._client = client or httpx.Client(timeout=httpx.Timeout(15, connect=4))
        self._calendars: dict[int, tuple[float, list[date]]] = {}
        self._calendar_lock = RLock()

    def _get(self, path: str, params: dict[str, str] | None = None) -> object:
        try:
            response = self._client.get(
                f"https://api.mairuiapi.com/{path}/{self._licence}", params=params
            )
            if response.status_code in {401, 403}:
                raise PortError(ErrorCode.UNAUTHORIZED, "source entitlement unavailable")
            if response.status_code == 429:
                try:
                    retry_after = max(0.0, float(response.headers.get("Retry-After", "60")))
                except ValueError:
                    retry_after = 60.0
                raise PortError(
                    ErrorCode.RATE_LIMITED, "source rate limit", retry_after=retry_after
                )
            response.raise_for_status()
            return response.json()
        except PortError:
            raise
        except httpx.TimeoutException:
            raise PortError(ErrorCode.TIMEOUT, "history source timed out") from None
        except (httpx.HTTPError, ValueError):
            raise PortError(ErrorCode.UNAVAILABLE, "history source unavailable") from None

    def _calendar(self, year: int) -> list[date]:
        # Reuse validated evidence within one provider instance; bound memory and freshness.
        with self._calendar_lock:
            cached = self._calendars.get(year)
            if cached is not None and monotonic() - cached[0] < 300:
                return cached[1]
            calendar = self._get(f"tcalendar/list/{year}")
            if not isinstance(calendar, list) or not calendar:
                raise PortError(ErrorCode.INVALID_INPUT, "calendar evidence unavailable")
            try:
                days = [datetime.strptime(str(item), "%Y%m%d").date() for item in calendar]
                if any(day.year != year for day in days) or len(set(days)) != len(days):
                    raise ValueError
            except (ValueError, TypeError):
                raise PortError(ErrorCode.INVALID_INPUT, "invalid calendar evidence") from None
            if len(self._calendars) >= 16:
                self._calendars.pop(next(iter(self._calendars)))
            self._calendars[year] = (monotonic(), days)
            return days

    def fetch(self, request: CollectionRequest, cursor: str | None) -> ProviderPage:
        if request.source != self.source or request.authorization_id != self.authorization_id:
            raise PortError(ErrorCode.UNSUPPORTED, "history source mismatch")
        if request.granularity not in PERIODS or (request.granularity == "1m" and not self._pro):
            raise PortError(ErrorCode.UNSUPPORTED, "requested period requires source entitlement")
        instrument = symbol(request.instrument_id)
        offset = 0
        first = request.start.astimezone(ZONE).date().replace(day=1)
        try:
            if cursor:
                month, position = cursor.split(":")
                first, offset = date.fromisoformat(month), int(position)
                if first.day != 1 or offset < 0:
                    raise ValueError
        except (ValueError, TypeError):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid history continuation") from None
        left = datetime.combine(first, datetime.min.time(), ZONE).astimezone(UTC)
        if left >= request.end or first < request.start.astimezone(ZONE).date().replace(day=1):
            raise PortError(ErrorCode.INVALID_INPUT, "history continuation outside request")
        following = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
        right = datetime.combine(following, datetime.min.time(), ZONE).astimezone(UTC)
        begin, end = max(left, request.start), min(right, request.end)
        days = self._calendar(first.year)
        scoped = [day.isoformat() for day in sorted(days) if first <= day < following]
        path = "bj" if instrument.endswith(".BJ") else "hsstock"
        if request.granularity == "1m":
            path += "/pro"
        interval = timedelta(minutes=MINUTES.get(request.granularity, 0))
        rows = self._get(
            f"{path}/history/{instrument}/{PERIODS[request.granularity]}/n",
            {
                "st": begin.astimezone(ZONE).strftime("%Y%m%d%H%M%S"),
                "et": (end + interval - timedelta(microseconds=1))
                .astimezone(ZONE)
                .strftime("%Y%m%d%H%M%S"),
            },
        )
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid history rows")
        try:
            selected = sorted(
                (row for row in rows if begin <= bar_time(row, request.granularity) < end),
                key=lambda row: bar_time(row, request.granularity),
            )
        except (KeyError, ValueError, TypeError):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid history timestamps") from None
        if request.page_size < 2 or offset > len(selected):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid history page size or offset")
        page = selected[offset : offset + request.page_size - 1]
        envelopes: list[dict[str, object]] = [
            {
                "kind": "calendar",
                "days": scoped,
                "start": begin.isoformat(),
                "end": end.isoformat(),
                "provider": "mairui.tcalendar",
                "adjustment": "none",
                "volume_multiplier": 100,
                "source_time_convention": "close" if request.granularity != "1d" else "local-date",
            }
        ]
        envelopes.extend({"kind": "bar", "row": row} for row in page)
        next_offset = offset + len(page)
        continuation = (
            f"{first.isoformat()}:{next_offset}"
            if next_offset < len(selected)
            else f"{following.isoformat()}:0"
            if right < request.end
            else None
        )
        return ProviderPage(
            tuple(
                RawRecord(i, json.dumps(row, sort_keys=True, separators=(",", ":")).encode())
                for i, row in enumerate(envelopes)
            ),
            continuation,
        )


class MaiRuiBarTransformation:
    version = MaiRuiHistoricalBars.transformation_version

    def output_granularity(self, source: Granularity) -> BarGranularity:
        if source == "tick" or source not in PERIODS:
            raise PortError(ErrorCode.INVALID_INPUT, "history transformation requires Bars")
        return source

    def transform(self, dataset_id: UUID, batches: tuple[ArchivedBatch, ...]) -> tuple[Bar, ...]:
        bars = []
        for batch in batches:
            metadata = batch.metadata
            if metadata.source != MaiRuiHistoricalBars.source:
                raise PortError(ErrorCode.INVALID_INPUT, "history provenance mismatch")
            granularity = self.output_granularity(metadata.granularity)
            for record in batch.records:
                try:
                    envelope = json.loads(record.payload)
                    if envelope.get("kind") == "calendar":
                        continue
                    if envelope.get("kind") != "bar":
                        raise ValueError
                    row = envelope["row"]
                    bar = Bar(
                        schema_version=1,
                        workspace_id=metadata.workspace_id,
                        instrument_id=metadata.instrument_id,
                        specification_version=1,
                        timestamp=bar_time(row, granularity),
                        granularity=granularity,
                        open=Decimal(str(row["o"])),
                        high=Decimal(str(row["h"])),
                        low=Decimal(str(row["l"])),
                        close=Decimal(str(row["c"])),
                        volume=Decimal(str(row["v"])) * 100,
                        value=Decimal(str(row["a"])),
                        batch_id=metadata.batch_id,
                        dataset_id=dataset_id,
                        quality="suspect" if row.get("sf", 0) != 0 else "valid",
                    )
                except (KeyError, TypeError, ValueError, ArithmeticError):
                    raise PortError(ErrorCode.INVALID_INPUT, "invalid source Bar") from None
                if bar.volume and bar.value is not None:
                    average = bar.value / bar.volume
                    if not bar.low * Decimal("0.97") <= average <= bar.high * Decimal("1.03"):
                        raise PortError(ErrorCode.INVALID_INPUT, "history volume unit mismatch")
                bars.append(bar)
        if not bars:
            raise PortError(ErrorCode.INVALID_INPUT, "history download contains no Bars")
        return tuple(bars)
