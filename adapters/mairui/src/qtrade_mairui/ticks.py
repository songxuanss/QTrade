"""Historical A-share tick adapter for MaiRui's day-archived endpoint.

MaiRui's historical tick archive starts on 2026-08-13. This adapter deliberately
does not claim Hong Kong coverage: the provider does not expose that endpoint.
"""

import json
import re
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from qtrade_domain.ingestion import CollectionRequest, HistoricalProviderPort, ProviderPage
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.raw_records import RawRecord


class MaiRuiAShareTickProvider(HistoricalProviderPort):
    source = "mairui.a-share.tick"
    authorization_id = "mairui-default"
    _archive_start = date(2026, 8, 13)
    _shanghai = ZoneInfo("Asia/Shanghai")

    def __init__(self, licence: str, *, client: httpx.Client | None = None):
        if not licence or len(licence) > 256:
            raise ValueError("MaiRui licence is required")
        self._licence = licence
        self._client = client or httpx.Client(timeout=httpx.Timeout(15, connect=4))

    @staticmethod
    def _symbol(instrument_id: str) -> tuple[str, bool]:
        match = re.fullmatch(r"(?:CN\.)?(\d{6})(?:\.(SH|SZ|BJ))?", instrument_id.upper())
        if not match:
            raise PortError(ErrorCode.UNSUPPORTED, "MaiRui tick source supports A-share symbols")
        return match.group(1), match.group(2) == "BJ"

    @staticmethod
    def _session_dates(start: datetime, end: datetime) -> tuple[date, ...]:
        first = start.astimezone(MaiRuiAShareTickProvider._shanghai).date()
        last = (
            (end - timedelta(microseconds=1)).astimezone(MaiRuiAShareTickProvider._shanghai).date()
        )
        return tuple(first + timedelta(days=i) for i in range((last - first).days + 1))

    def _load_day(
        self, instrument: str, day: date, *, beijing_market: bool
    ) -> list[dict[str, object]]:
        if beijing_market:
            path = f"/bj/stock/real/zbjy/{day:%Y%m%d}/{quote(instrument, safe='')}"
        else:
            path = f"/hsrl/zbjy/{day:%Y%m%d}/{quote(instrument, safe='')}"
        url = f"https://api.mairuiapi.com{path}/{quote(self._licence, safe='')}"
        try:
            response = self._client.get(url)
            if response.status_code == 429:
                raise PortError(ErrorCode.RATE_LIMITED, "market data provider rate limit")
            response.raise_for_status()
            body = response.json()
        except PortError:
            raise
        except httpx.TimeoutException:
            raise PortError(ErrorCode.TIMEOUT, "market data provider timed out") from None
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            code = ErrorCode.UNAUTHORIZED if status in {401, 403} else ErrorCode.UNAVAILABLE
            raise PortError(code, "market data provider request failed") from None
        except (httpx.HTTPError, ValueError):
            raise PortError(
                ErrorCode.UNAVAILABLE, "market data provider response unavailable"
            ) from None
        # MaiRui returns this object for weekend archive dates, rather than [].
        # Do not hide missing archives on weekdays or other provider errors.
        if day.weekday() >= 5 and body == {"error": "404:资源不存在，请检查日期与股票代码是否正确"}:
            return []
        if not isinstance(body, list) or any(not isinstance(row, dict) for row in body):
            raise PortError(ErrorCode.INVALID_INPUT, "market data provider returned invalid ticks")
        return body

    def fetch(self, request: CollectionRequest, cursor: str | None) -> ProviderPage:
        if (
            request.granularity != "tick"
            or request.source != self.source
            or request.authorization_id != self.authorization_id
        ):
            raise PortError(ErrorCode.UNSUPPORTED, "MaiRui adapter only supports A-share ticks")
        instrument, beijing_market = self._symbol(request.instrument_id)
        dates = self._session_dates(request.start, request.end)
        if dates[0] < self._archive_start:
            raise PortError(ErrorCode.UNSUPPORTED, "MaiRui tick archive begins on 2026-08-13")
        day_index, offset = (0, 0)
        if cursor:
            try:
                day_index, offset = (int(part) for part in cursor.split(":", maxsplit=1))
            except (ValueError, TypeError):
                raise PortError(ErrorCode.INVALID_INPUT, "invalid provider continuation") from None
            if not 0 <= day_index < len(dates) or offset < 0:
                raise PortError(ErrorCode.INVALID_INPUT, "invalid provider continuation")

        selected: list[dict[str, object]] = []
        while day_index < len(dates):
            rows = self._load_day(instrument, dates[day_index], beijing_market=beijing_market)
            row_index = offset
            while row_index < len(rows):
                row = rows[row_index]
                row_index += 1
                try:
                    timestamp = self._timestamp(row)
                    if not isinstance(row.get("p"), int | float) or not isinstance(
                        row.get("v"), int | float
                    ):
                        raise ValueError
                except (KeyError, ValueError, TypeError):
                    raise PortError(
                        ErrorCode.INVALID_INPUT, "provider returned malformed tick"
                    ) from None
                if request.start <= timestamp < request.end:
                    selected.append(row)
                if len(selected) == request.page_size:
                    next_cursor = (
                        f"{day_index}:{row_index}"
                        if row_index < len(rows)
                        else f"{day_index + 1}:0"
                        if day_index + 1 < len(dates)
                        else None
                    )
                    break
            else:
                day_index += 1
                offset = 0
                continue
            break
        else:
            next_cursor = None

        records = tuple(
            RawRecord(index, json.dumps(row, sort_keys=True, separators=(",", ":")).encode())
            for index, row in enumerate(selected)
        )
        return ProviderPage(records, next_cursor)

    @staticmethod
    def _timestamp(row: dict[str, object]) -> datetime:
        return (
            datetime.combine(date.fromisoformat(str(row["d"])), time.fromisoformat(str(row["t"])))
            .replace(tzinfo=MaiRuiAShareTickProvider._shanghai)
            .astimezone(UTC)
        )
