"""MaiRui collection adapter. All vendor paths, units and response semantics stay here."""

import json
import logging
import re
from base64 import b64encode
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Lock, RLock
from time import monotonic
from typing import Any

import httpx

from qtrade_data_contracts.collection import (
    CollectionJobRequest,
    FactorDefinition,
    SourceDataset,
    digest,
    instrument_key,
)
from qtrade_data_contracts.factor_catalog import FactorCatalogSnapshot
from qtrade_data_contracts.normalization_input import (
    BarNormalizationInput,
    FactorNormalizationInput,
    NormalizationPayload,
)
from qtrade_data_contracts.planning import CollectionCostEstimate
from qtrade_data_core.collection import CollectionPart, CollectionResult, RawCapture
from qtrade_data_core.default_range import DefaultRange, natural_year_range
from qtrade_data_core.normalization_input import PreparedCapture
from qtrade_data_core.planning import TimePartition
from qtrade_data_core.ports import DataFailure
from qtrade_data_core.usage_execution import UsageAttempt, UsageGate

from .bounded_http import BoundedHttpClient, TransportFailure
from .factor_catalog import FactorCatalogCache
from .normalization import MINUTES, ZONE, source_time
from .normalization import normalize as normalize_input
from .response_json import decode_response

PERIODS = {"1m": "1", "5m": "5", "15m": "15", "30m": "30", "1h": "60", "1d": "d"}
logger = logging.getLogger(__name__)


def request_classification(path: str) -> tuple[str, int]:
    """Safe operation/credit labels; neither ledger nor evidence receives URLs."""
    if path.startswith("factor/history/"):
        return "factors.history", 5
    if path.startswith("factor/list"):
        return "metadata.factor_catalog", 1
    if path.startswith("tcalendar/"):
        return "metadata.calendar", 1
    if path in {"hslt/list", "hsindex/list", "bj/list/all"}:
        return "metadata.names", 1
    if "/financial/" in path or path.startswith("hscp/"):
        return "research.data", 1
    return "bars.history", 1


class MaiRuiCollection:
    def estimate(self, request: CollectionJobRequest, new_parts: int) -> CollectionCostEstimate:
        # Some ordinary parts cross a year: allow two history requests and two
        # calendar/catalog requests. Cache hits may use none. Preview metadata
        # already performed is separate; retry count is not predicted here.
        weight = 5 if request.dataset_kind == "factors" else 1
        return CollectionCostEstimate(
            calls_min=0,
            calls_max=4 * new_parts,
            credits_min=0,
            credits_max=(2 * weight + 2) * new_parts,
            accounting="internal_credit_safeguard",
            includes_retries=False,
            basis="按新增分段保守计历史及日历/目录请求；缓存可减少调用。"
            "不含预检已发生的元数据调用、重试及后续修订，不是供应商报价。",
        )

    def __init__(
        self,
        licence: str | None,
        *,
        pro: bool = False,
        cache: Path,
        pace: Callable[[], None] = lambda: None,
        client: httpx.Client | None = None,
        partition_max_days: int = 0,
        usage: UsageGate | None = None,
        request_timeout_seconds: float = 90,
    ):
        self.licence, self.pro, self.cache, self.pace = licence, pro, cache, pace
        if type(partition_max_days) is not int or partition_max_days < 0:
            raise ValueError("invalid configured partition days")
        self.partition_max_days = partition_max_days
        self.usage = usage
        if not 0 < request_timeout_seconds <= 300:
            raise ValueError("invalid supplier total timeout")
        self.request_timeout_seconds = request_timeout_seconds
        self.client = client or BoundedHttpClient()
        self.cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Only the registry uses a shared lock; it never covers provider I/O.
        # Each resource remains single-flight so concurrent callers cannot race
        # on the same cache file, while unrelated metadata/history can progress.
        self._locks_guard = Lock()
        self._resource_locks: dict[str, RLock] = {}
        self._factor_cache: tuple[float, tuple[FactorDefinition, ...]] | None = None
        self._factor_catalog = FactorCatalogCache(self.cache)
        initial = self._factor_catalog.snapshot(bool(licence and pro))
        if initial.status == "fresh" and initial.fetched_at:
            remaining = 900 - (datetime.now(UTC) - initial.fetched_at).total_seconds()
            self._factor_cache = monotonic() + remaining, initial.definitions
        self._calendars: dict[int, list[str]] = {}
        self._name_cache: dict[str, tuple[float, dict[str, str]]] = {}

    def _resource_lock(self, key: str) -> RLock:
        with self._locks_guard:
            return self._resource_locks.setdefault(key, RLock())

    def default_range(self, source: str, granularity: str, now: datetime) -> DefaultRange:
        """Daily defaults use the documented estimated release, not local coverage.

        Other categories require their own publication evidence. This estimate is
        an eligibility rule, never a claim that the supplier actually published.
        """
        if source != "mairui.history.bars" or granularity != "1d":
            raise DataFailure("unsupported")
        if not self.licence:
            raise DataFailure("unauthorized")
        if now.tzinfo is None:
            raise ValueError("explicit current timezone required")
        year = now.astimezone(ZONE).year
        days = tuple(date.fromisoformat(day) for day in self._calendar(year))
        if not any(datetime.combine(day, time(17, 10), ZONE) <= now for day in days):
            days += tuple(date.fromisoformat(day) for day in self._calendar(year - 1))
        return natural_year_range(
            now,
            days,
            timezone="Asia/Shanghai",
            release_time=time(17, 10),
            basis="https://www.mairuiapi.com/hsdata#history: daily estimated complete 17:10",
        )

    def partitions(
        self, request: CollectionJobRequest, start: datetime, end: datetime
    ) -> Iterator[tuple[datetime, datetime]]:
        # An optional operator cap only tightens the source strategy. Zero means
        # no operator cap; API limits (365 factor points) still apply.
        annual = request.source == "mairui.pro.bars" or request.granularity == "1d"
        source_days = 365 if request.dataset_kind == "factors" else 0 if annual else 7
        days = (
            min(source_days, self.partition_max_days)
            if source_days and self.partition_max_days
            else source_days or self.partition_max_days
        )
        return TimePartition("Asia/Shanghai", annual=annual, max_days=days).spans(start, end)

    def datasets(self) -> tuple[SourceDataset, ...]:
        common: dict[str, Any] = {
            "provider": "mairui",
            "configured": bool(self.licence),
            "evidence": "configured" if self.licence else "documented",
            "markets": ("CN",),
            "timezone": "Asia/Shanghai",
        }
        return (
            SourceDataset(
                **common,
                source="mairui.history.bars",
                name="MaiRui 日线/分钟历史",
                dataset_kind="bars",
                periods=("5m", "15m", "30m", "1h", "1d"),
                instrument_kinds=("stock", "index"),
                mapping_version="mairui.history-bars.v3",
                limitations=("普通接口实际历史范围须逐标的验证。",),
            ),
            SourceDataset(
                **{**common, "configured": bool(self.licence and self.pro)},
                source="mairui.pro.bars",
                name="MaiRui Pro 分钟历史",
                dataset_kind="bars",
                periods=("1m", "5m", "15m", "30m", "1h"),
                instrument_kinds=("stock", "index"),
                mapping_version="mairui.pro-bars.v1",
                limitations=(
                    "历史年片按年缓存；当前年使用普通历史增量。",
                    "实际权限和历史覆盖以下载结果为准。",
                ),
            ),
            SourceDataset(
                **{**common, "configured": bool(self.licence and self.pro)},
                source="mairui.factor.history",
                name="MaiRui 因子历史",
                dataset_kind="factors",
                periods=("1d",),
                instrument_kinds=("stock",),
                mapping_version="mairui.factor-history.v1",
                limitations=("历史可知时间未验证，不能据此声称财务PIT回测就绪。",),
            ),
        )

    def _get(
        self,
        path: str,
        params: dict[str, str] | None = None,
        *,
        pro: bool = False,
        capture: RawCapture | None = None,
    ) -> Any:
        if not self.licence:
            raise DataFailure("unauthorized")
        started = monotonic()
        status: int | None = None
        code: object = None
        attempt: UsageAttempt | None = None
        response_bytes = 0
        error_code: str | None = "unavailable"
        pace_wait_ms = 0
        transport_ms: int | None = None
        try:
            self.pace()
            pace_wait_ms = round((monotonic() - started) * 1000)
            operation, credits = request_classification(path)
            if self.usage:
                # Classify vendor routes here; core bookkeeping never sees URLs
                # or licence-bearing paths. Factor history is 5 operator credits.
                attempt = self.usage.begin(operation, credits)
            url = f"https://{'pro' if pro else 'api'}.mairuiapi.com/{path}/{self.licence}"
            if isinstance(self.client, BoundedHttpClient):
                budget = self.request_timeout_seconds
                if attempt:
                    budget = attempt.transport_budget(budget)
                try:
                    response = self.client.get(url, params=params, timeout=budget)
                except TransportFailure as failure:
                    # A deadline/oversize/disconnect is never a usable page.
                    # Reaped transport evidence survives temporary-file cleanup
                    # only through the same archive-first callback as success.
                    status = failure.response_status
                    response_bytes = failure.response_bytes
                    if capture and failure.response_body is not None:
                        capture(
                            "http_response",
                            {
                                "http_status": status,
                                "operation": operation,
                                "body_base64": b64encode(failure.response_body).decode("ascii"),
                                "complete": False,
                            },
                        )
                    raise
            else:
                # Injected clients are controlled tests; the production default
                # always uses the deadline-enforced disposable socket owner.
                response = self.client.get(url, params=params)
            measured = response.extensions.get("qtrade_transport_ms")
            if type(measured) is int and measured >= 0:
                transport_ms = measured
            status = response.status_code
            response_bytes = len(response.content)
            # Persist exact decoded HTTP bytes before status/JSON classification.
            # A receipt is private evidence, never a successful observation;
            # URLs, headers and credentials are not added to the envelope.
            if capture:
                capture(
                    "http_response",
                    {
                        "http_status": status,
                        "operation": operation,
                        "body_base64": b64encode(response.content).decode("ascii"),
                        "complete": True,
                    },
                )
            if status in {401, 403}:
                raise DataFailure("unauthorized")
            if status == 429:
                raise DataFailure("rate_limited")
            if status != 200 or len(response.content) > 64_000_000:
                raise DataFailure("unavailable")
            data = decode_response(response.content)
            if isinstance(data, dict):
                code = data.get("code")
                # Missing code is a plain data envelope. An explicit code must
                # be an integer: bool/decimal equality with 0/200 is not proof
                # of success, and containers must not escape as TypeError.
                if "code" in data and type(code) is not int:
                    raise DataFailure("unavailable")
                if code in {108, 401, 403}:
                    raise DataFailure("unauthorized")
                if code in {429}:
                    raise DataFailure("rate_limited")
                if code not in {None, 0, 200}:
                    raise DataFailure("unavailable")
            error_code = None
            return data
        except DataFailure as error:
            error_code = error.code
            if isinstance(error, TransportFailure):
                response_bytes = error.response_bytes
            raise
        except httpx.TimeoutException:
            error_code = "timeout"
            raise DataFailure("timeout") from None
        except (httpx.HTTPError, ValueError):
            error_code = "unavailable"
            raise DataFailure("unavailable") from None
        finally:
            try:
                if attempt:
                    try:
                        attempt.finish(response_bytes, error_code)
                    except Exception:
                        # A response is not usable until its charged attempt is
                        # durably finalized. Fail closed and log bookkeeping
                        # failure rather than falsely reporting provider success.
                        error_code = "unavailable"
                        raise DataFailure("unavailable") from None
            finally:
                logger.info(
                    "provider_request",
                    extra={
                        "event": "provider_request",
                        "source": "mairui",
                        "stage": "provider",
                        "http_status": status,
                        "response_bytes": response_bytes,
                        "pace_wait_ms": pace_wait_ms,
                        "transport_ms": transport_ms,
                        "usage_request_id": str(attempt.lease.request.request_id)
                        if attempt
                        else None,
                        "error_code": error_code,
                        "provider_code": code if type(code) is int else None,
                        "duration_ms": round((monotonic() - started) * 1000),
                    },
                )

    @staticmethod
    def _rows(data: Any) -> list[dict[str, Any]]:
        body = data.get("data") if isinstance(data, dict) else data
        rows = body.get("list") if isinstance(body, dict) else body
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise DataFailure("unverified")
        return rows

    @staticmethod
    def _symbol(identifier: str) -> tuple[str, bool]:
        kind, market, venue, code = instrument_key(identifier)
        venue = {"XSHG": "SH", "XSHE": "SZ", "XBSE": "BJ"}.get(venue, venue)
        if (
            market != "CN"
            or kind not in {"stock", "index"}
            or venue not in {"SH", "SZ", "BJ"}
            or not re.fullmatch(r"[0-9]{6}", code)
        ):
            raise DataFailure("unsupported")
        if kind == "index" and venue == "BJ":
            raise DataFailure("unsupported")
        return f"{code}.{venue}", kind == "index"

    def names(self, identifiers: tuple[str, ...]) -> dict[str, str]:
        results: dict[str, str] = {}
        groups = {
            "index"
            if self._symbol(value)[1]
            else "bj"
            if self._symbol(value)[0].endswith(".BJ")
            else "stock"
            for value in identifiers
        }
        snapshots: dict[str, dict[str, str]] = {}
        for group in groups:
            with self._resource_lock(f"names:{group}"):
                cached = self._name_cache.get(group)
                if cached is None or monotonic() >= cached[0]:
                    path = {"index": "hsindex/list", "stock": "hslt/list", "bj": "bj/list/all"}[
                        group
                    ]
                    rows = self._rows(self._get(path))
                    values: dict[str, str] = {}
                    for row in rows:
                        code = str(row.get("dm", "")).upper()
                        exchange = str(row.get("jys", "")).upper()
                        if "." in code:
                            code, exchange = code.split(".", 1)
                        if exchange not in {"SH", "SZ", "BJ"}:
                            continue
                        name = str(row.get("mc", "")).strip()
                        if name:
                            values[f"{code}.{exchange}"] = name
                    self._name_cache[group] = monotonic() + 900, values
                snapshots[group] = self._name_cache[group][1]
        for identifier in identifiers:
            symbol, index = self._symbol(identifier)
            group = "index" if index else "bj" if symbol.endswith(".BJ") else "stock"
            results[identifier] = snapshots[group].get(symbol, "名称未核验")
        return results

    def factors(self) -> tuple[FactorDefinition, ...]:
        with self._resource_lock("factors"):
            if self._factor_cache and monotonic() < self._factor_cache[0]:
                return self._factor_cache[1]
            self._factor_catalog.begin()
            try:
                return self._refresh_factors()
            except DataFailure as error:
                self._factor_catalog.failed(error.code)
                raise
            except (ValueError, TypeError):
                self._factor_catalog.failed("unverified")
                raise DataFailure("unverified") from None
            except OSError:
                self._factor_catalog.failed("unavailable")
                raise DataFailure("unavailable") from None

    def factor_catalog_snapshot(self) -> FactorCatalogSnapshot:
        """Fast cached read; it never requests the supplier or waits for refresh."""
        return self._factor_catalog.snapshot(bool(self.licence and self.pro))

    def _refresh_factors(self) -> tuple[FactorDefinition, ...]:
        rows = self._rows(self._get("factor/list"))
        if not rows:
            raise DataFailure("unavailable")
        if len(rows) > 1000:
            raise DataFailure("unverified")
        try:
            definitions = tuple(
                FactorDefinition(
                    factor_id=row["factor_id"],
                    name=row["name"],
                    category=row["category"],
                    unit=str(row.get("unit") or "unknown"),
                    description=str(row.get("description", "")),
                    calc_version=str(row.get("calc_version") or "unknown"),
                    update_frequency=str(row.get("update_freq") or "unknown"),
                    # Catalogue presence enables selection, not proof of account/history rights.
                    available=True,
                    unavailable_reason=None,
                )
                for row in rows
            )
        except (ValueError, KeyError, TypeError):
            raise DataFailure("unavailable") from None
        if len({item.factor_id for item in definitions}) != len(definitions):
            raise DataFailure("unavailable")
        metadata = [item.model_dump(mode="json") for item in definitions]
        path = self.cache / f"factor-catalog-{digest(metadata)}.json"
        path.write_text(json.dumps(metadata, ensure_ascii=False))
        path.chmod(0o600)
        evidence = {
            "catalog_sha256": digest(metadata),
            "evidence": "provider_catalog_response",
            "history_permission": "not_established_by_catalog",
            "empty_history": "no_observations_not_permission_denial",
            "unit_frequency_version": "provider_fields_or_explicit_unknown",
            "definitions": metadata,
        }
        evidence_path = self.cache / f"factor-evidence-{digest(evidence)}.json"
        evidence_path.write_text(json.dumps(evidence, ensure_ascii=False))
        evidence_path.chmod(0o600)
        self._factor_catalog.save(definitions)
        self._factor_cache = monotonic() + 900, definitions
        return definitions

    def _calendar(self, year: int, capture: RawCapture | None = None) -> list[str]:
        with self._resource_lock(f"calendar:{year}"):
            if year not in self._calendars:
                values = self._get(f"tcalendar/list/{year}", capture=capture)
                if capture:
                    capture("calendar", values)
                if not isinstance(values, list) or not values:
                    raise DataFailure("unavailable")
                try:
                    result = [
                        datetime.strptime(str(value), "%Y%m%d").date().isoformat()
                        for value in values
                    ]
                    if any(not value.startswith(str(year)) for value in result) or len(
                        set(result)
                    ) != len(result):
                        raise ValueError
                    self._calendars[year] = sorted(result)
                except ValueError:
                    raise DataFailure("unavailable") from None
            return self._calendars[year]

    def _full_year(
        self,
        root: str,
        instrument: str,
        period: str,
        year: int,
        index: bool,
        capture: RawCapture | None = None,
    ) -> list[dict[str, Any]]:
        key = digest([root, instrument, period, year])
        path = self.cache / f"year-{key}.json"
        with self._resource_lock(f"year:{key}"):
            if path.exists() and datetime.now(UTC).timestamp() - path.stat().st_mtime < 86400:
                try:
                    cached = json.loads(path.read_text(), parse_float=Decimal)
                    if cached["sha256"] != digest(cached["rows"]):
                        raise ValueError
                except (OSError, ValueError, KeyError):
                    path.unlink(missing_ok=True)
                else:
                    # Archive failures must not evict a valid cache or trigger more vendor calls.
                    if capture:
                        capture("cached_history", cached["rows"])
                    return self._rows(cached["rows"])
            payload = self._get(
                f"{root}/history/full/{instrument}/{period}" + ("" if index else "/n"),
                {"year": str(year)},
                pro=True,
                capture=capture,
            )
            if capture:
                capture("history", payload)
            rows = self._rows(payload)
            # Decimal to textual JSON preserves provider numeric precision in the cache.
            encoded = json.loads(json.dumps(rows, default=str))
            path.write_text(
                json.dumps({"sha256": digest(encoded), "rows": encoded}, separators=(",", ":"))
            )
            path.chmod(0o600)
            return rows

    _time = staticmethod(source_time)

    def normalize(self, part: CollectionPart, payload: NormalizationPayload) -> CollectionResult:
        return normalize_input(part, payload)

    def fetch_prepared(
        self, part: CollectionPart, *, capture: RawCapture, prepared: PreparedCapture
    ) -> CollectionResult:
        return self.fetch(part, capture=capture, prepared=prepared)

    def fetch(
        self,
        part: CollectionPart,
        *,
        capture: RawCapture | None = None,
        prepared: PreparedCapture | None = None,
    ) -> CollectionResult:
        try:
            if part.request.dataset_kind == "factors":
                return self._factors(part, capture, prepared)
            return self._bars(part, capture, prepared)
        except (ValueError, TypeError, KeyError, ArithmeticError):
            raise DataFailure("unverified") from None

    def _bars(
        self,
        part: CollectionPart,
        capture: RawCapture | None = None,
        prepared: PreparedCapture | None = None,
    ) -> CollectionResult:
        instrument, index = self._symbol(part.instrument_id)
        root = "hsindex" if index else "bj" if instrument.endswith(".BJ") else "hsstock"
        period = part.request.granularity
        pro = part.request.source == "mairui.pro.bars"
        cursor = part.start
        slices: list[dict[str, Any]] = []
        calendar: list[str] = []
        mechanisms: list[str] = []
        while cursor < part.end:
            year = cursor.astimezone(ZONE).year
            end = min(part.end, datetime(year + 1, 1, 1, tzinfo=ZONE).astimezone(UTC))
            calendar.extend(self._calendar(year, capture))
            if pro and year < datetime.now(ZONE).year:
                rows = self._full_year(root, instrument, PERIODS[period], year, index, capture)
                mechanisms.append("full_year")
            else:
                prefix = root + ("/pro" if period == "1m" else "")
                path = f"{prefix}/history/{instrument}/{PERIODS[period]}" + ("" if index else "/n")
                interval = timedelta(minutes=MINUTES[period])
                payload = self._get(
                    path,
                    {
                        "st": cursor.astimezone(ZONE).strftime("%Y%m%d%H%M%S"),
                        "et": (end + interval - timedelta(microseconds=1))
                        .astimezone(ZONE)
                        .strftime("%Y%m%d%H%M%S"),
                    },
                    capture=capture,
                )
                if capture:
                    capture("history", payload)
                rows = self._rows(payload)
                mechanisms.append("regular_history")
            slices.append({"start": cursor, "end": end, "rows": rows})
            cursor = end
        # Freeze the full acquired rows before timestamp selection/conversion.
        # A rejected timestamp must not lose the input needed by a fixed mapper.
        payload = BarNormalizationInput.model_validate_json(
            json.dumps(
                {
                    "kind": "bars",
                    "slices": slices,
                    "calendar_days": sorted(set(calendar)),
                    "fetch_mechanisms": sorted(set(mechanisms)),
                },
                default=str,
            )
        )
        if prepared:
            prepared(payload)
        return self.normalize(part, payload)

    def _factors(
        self,
        part: CollectionPart,
        capture: RawCapture | None = None,
        prepared: PreparedCapture | None = None,
    ) -> CollectionResult:
        first_day = part.start.astimezone(ZONE).date()
        last_day = (part.end - timedelta(microseconds=1)).astimezone(ZONE).date()
        if part.end <= part.start or (last_day - first_day).days + 1 > 365:
            raise DataFailure("invalid_input")
        definition = next((item for item in self.factors() if item.factor_id == part.field), None)
        if definition is None:
            raise DataFailure("unsupported")
        if not re.fullmatch(r"[a-zA-Z0-9_]+", str(part.field)):
            raise DataFailure("invalid_input")
        instrument, index = self._symbol(part.instrument_id)
        if index:
            raise DataFailure("unsupported")
        payload = self._get(
            f"factor/history/{part.field}/{instrument[:6]}",
            {
                "st": part.start.astimezone(ZONE).strftime("%Y%m%d"),
                "et": (part.end - timedelta(microseconds=1)).astimezone(ZONE).strftime("%Y%m%d"),
                "limit": "365",
            },
            capture=capture,
        )
        if capture:
            capture("factor_history", payload)
        rows = self._rows(payload)
        kinds = [
            "missing"
            if row.get("value") is None
            else "number"
            if isinstance(row["value"], int | Decimal) and not isinstance(row["value"], bool)
            else "signal"
            for row in rows
        ]
        payload = FactorNormalizationInput.model_validate_json(
            json.dumps(
                {
                    "kind": "factors",
                    "rows": rows,
                    "value_kinds": kinds,
                    "definition": definition.model_dump(mode="json"),
                },
                default=str,
            )
        )
        if prepared:
            prepared(payload)
        return self.normalize(part, payload)
