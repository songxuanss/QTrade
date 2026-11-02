"""Opt-in rights-issue terms from two public pages, reconciled before publishing.

Neither source is a licensed data feed. Eastmoney's data centre endpoint and
Sina's issue pages are public, undocumented and may change without notice, and
both sites reserve reproduction rights. This adapter therefore stays off by
default, archives the exact response bytes, and only emits an event when both
sources agree. See docs/data/RIGHTS-FEED-CONDITIONS.md.
"""

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from threading import Lock
from time import monotonic, sleep

import httpx
from pydantic import TypeAdapter

from qtrade_data_contracts.collection import digest, instrument_key
from qtrade_data_contracts.models import ExactDecimal
from qtrade_data_contracts.planning import CollectionCostEstimate
from qtrade_data_contracts.research import CorporateAction
from qtrade_data_contracts.research_transport import (
    ResearchCapability,
    ResearchPartition,
    ResearchPlanRequest,
)
from qtrade_data_contracts.source_profiles import (
    Aspect,
    CapabilityFact,
    EvidenceLevel,
    SourceProfile,
)
from qtrade_data_core.ports import DataFailure
from qtrade_data_core.research import ResearchRawPage
from qtrade_data_core.research_acquisition import ResearchSourceResponse, ResponseSink

SOURCE = "publicweb.corporate.rights"
MAPPING = "publicweb.rights.v1"
EASTMONEY_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
SINA_URL = (
    "https://vip.stock.finance.sina.com.cn/corp/go.php/vISSUE_ShareBonus/stockid/{code}.phtml"
)
EM_TERMS = "https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_IPO_ALLOTMENT"
SINA_TERMS = (
    "https://vip.stock.finance.sina.com.cn/corp/go.php/vISSUE_ShareBonus/stockid/{code}.phtml"
)
USER_AGENT = "qtrade-research/1.0 (+internal research; contact the workspace owner)"
PLACEHOLDER_DATES = frozenset({"1900-01-01", "0000-00-00"})
MAX_BODY_BYTES = 4_000_000


@dataclass(frozen=True)
class _Terms:
    announcement: date
    record: date
    ex: date
    listing: date | None
    ratio: Decimal
    price: Decimal


def code_of(identifier: str) -> str:
    """A-share Shanghai/Shenzhen code only; Beijing rights pages are unverified."""

    kind, market, venue, code = instrument_key(identifier)
    if (
        kind != "stock"
        or market != "CN"
        or venue not in {"XSHG", "XSHE", "SH", "SZ"}
        or not re.fullmatch(r"[0-9]{6}", code)
    ):
        raise DataFailure("unsupported")
    return code


def _decimal(value: object) -> Decimal:
    if isinstance(value, float):
        value = format(value, "f")
    try:
        return TypeAdapter(ExactDecimal).validate_python(value).normalize()
    except (ValueError, TypeError, InvalidOperation):
        raise DataFailure("unverified") from None


def _day(value: object) -> date:
    text = str(value).strip()[:10]
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        raise DataFailure("unverified") from None
    if text in PLACEHOLDER_DATES:
        raise DataFailure("unverified")
    return parsed


def _optional_day(value: object) -> date | None:
    text = str(value or "").strip()
    if text in {"", "-", "--"}:
        return None
    if text[:10] in PLACEHOLDER_DATES:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        raise DataFailure("unverified") from None


class _SinaRightsTable(HTMLParser):
    """Select ``table#sharebonus_2`` explicitly; never index tables by position."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.found = False
        self._inside = False
        self._depth = 0
        self._header = False
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            if self._inside:
                self._depth += 1
            elif dict(attrs).get("id") == "sharebonus_2":
                self._inside = True
                self.found = True
                self._depth = 1
            return
        if not self._inside:
            return
        if tag == "thead":
            self._header = True
        elif tag == "tr":
            self._row = []
        elif tag == "td" and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            if self._inside:
                self._depth -= 1
                if self._depth <= 0:
                    self._inside = False
            return
        if not self._inside:
            return
        if tag == "thead":
            self._header = False
        elif tag == "td":
            if self._cell is not None and self._row is not None:
                self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr":
            if self._row and not self._header:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._inside and self._cell is not None:
            self._cell.append(data)


class _BoundedHttp:
    """Per-host paced GET with an overall deadline and a decoded size cap.

    HTTPX read timeouts reset on every chunk, so the deadline is also checked
    while streaming. Redirects are not followed: a redirect is a layout change,
    not silently another source.
    """

    def __init__(
        self,
        *,
        timeout: float = 20.0,
        spacing: float = 1.0,
        maximum: int = MAX_BODY_BYTES,
        client: httpx.Client | None = None,
    ):
        if not 0 < timeout <= 120 or not 0 <= spacing <= 60 or not 1 <= maximum <= 64_000_000:
            raise ValueError("invalid public-feed transport bounds")
        self.timeout, self.spacing, self.maximum = timeout, spacing, maximum
        self._client = client
        self._lock = Lock()
        self._last: dict[str, float] = {}

    def get(self, url: str, params: dict[str, str] | None = None) -> tuple[int, bytes]:
        host = httpx.URL(url).host or ""
        with self._lock:
            previous = self._last.get(host)
            if previous is not None:
                wait = self.spacing - (monotonic() - previous)
                if wait > 0:
                    sleep(wait)
            self._last[host] = monotonic()
        client = self._client or httpx.Client(
            timeout=httpx.Timeout(self.timeout, connect=5), follow_redirects=False
        )
        try:
            deadline = monotonic() + self.timeout
            with client.stream(
                "GET", url, params=params, headers={"User-Agent": USER_AGENT}
            ) as response:
                status = response.status_code
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > self.maximum:
                        raise DataFailure("unavailable")
                    if monotonic() > deadline:
                        raise DataFailure("timeout")
                    chunks.append(chunk)
                return status, b"".join(chunks)
        except httpx.TimeoutException:
            raise DataFailure("timeout") from None
        except (httpx.HTTPError, ValueError):
            raise DataFailure("unavailable") from None
        finally:
            if self._client is None:
                client.close()


class PublicWebRightsTerms:
    """Eastmoney primary plus Sina confirmation; disagreement fails closed."""

    source = SOURCE

    def __init__(self, *, configured: bool, http: _BoundedHttp | None = None):
        self.configured = configured
        self.http = http or _BoundedHttp()

    def estimate(self, request: ResearchPlanRequest, parts: int) -> CollectionCostEstimate:
        return CollectionCostEstimate(
            calls_min=0,
            calls_max=2 * parts,
            credits_min=0,
            credits_max=0,
            accounting="internal_credit_safeguard",
            includes_retries=False,
            basis="每个分段固定两次公开页请求（东财一次、新浪一次）；不计内部信用预算，"
            "不含重试。公开页无 SLA，不是供应商报价或授权数据订阅。",
        )

    def capabilities(self) -> tuple[ResearchCapability, ...]:
        limitations = (
            "来源为东财公开接口与新浪公开页，非授权数据源；仅限内部研究，不得再分发或对外展示。",
            "东财主源、新浪交叉校验；比例、配股价格、登记日、除权日或公告日任一不一致即失败。",
            "无原生事件ID，身份按证券与除权日派生；同一除权日多事件拒绝发布。",
            "缴款只有起止窗口，不虚构单一支付日；历史可知时间未知，不支持as-of查询。",
            "1993年前记录不可靠，1900-01-01等占位日期一律拒绝，不做最佳猜测。",
        )
        return (
            ResearchCapability(
                source=self.source,
                name="公开页配股条款（东财主源 + 新浪校验）",
                kind="corporate_action",
                configured=self.configured,
                evidence="sample",
                markets=("CN",),
                instrument_kinds=("stock",),
                date_axes=("event_date",),
                timezone="Asia/Shanghai",
                mapping_version=MAPPING,
                history_start=date(1991, 5, 31),
                limitations=limitations,
                unblock_condition=None
                if self.configured
                else "设置COLLECTOR_PUBLIC_RIGHTS_FEED=true，并确认公开页仅限内部研究",
            ),
        )

    def partitions(self, request: ResearchPlanRequest) -> Iterator[ResearchPartition]:
        # Both sources return the whole per-instrument history in one response.
        for identifier in request.instrument_ids:
            code_of(identifier)
            yield ResearchPartition(instrument_id=identifier, start=request.start, end=request.end)

    def fetch(
        self, request: ResearchPlanRequest, partition: ResearchPartition, cursor: str | None
    ) -> ResearchRawPage:
        return self._fetch(request, partition, cursor, None)

    def fetch_with_capture(
        self,
        request: ResearchPlanRequest,
        partition: ResearchPartition,
        cursor: str | None,
        capture: ResponseSink,
    ) -> ResearchRawPage:
        return self._fetch(request, partition, cursor, capture)

    def _fetch(
        self,
        request: ResearchPlanRequest,
        partition: ResearchPartition,
        cursor: str | None,
        capture: ResponseSink | None,
    ) -> ResearchRawPage:
        if cursor is not None:
            raise DataFailure("invalid_input")
        if not self.configured:
            raise DataFailure("unauthorized")
        identifier = partition.instrument_id
        code = code_of(identifier)
        rows: list[dict[str, object]] = []
        for name, url, params in (
            (
                "eastmoney",
                EASTMONEY_URL,
                {
                    "reportName": "RPT_IPO_ALLOTMENT",
                    "columns": "ALL",
                    "filter": f'(SECURITY_CODE="{code}")',
                    "pageNumber": "1",
                    "pageSize": "500",
                    "source": "WEB",
                    "client": "WEB",
                },
            ),
            ("sina", SINA_URL.format(code=code), None),
        ):
            status, body = self.http.get(url, params)
            # Archive the exact bytes before any decode or interpretation.
            if capture is not None:
                capture(ResearchSourceResponse("research.data", status, body, True))
            if status != 200:
                raise DataFailure("rate_limited" if status == 429 else "unavailable")
            rows.extend(
                self._eastmoney_rows(body, identifier)
                if name == "eastmoney"
                else self._sina_rows(body, identifier)
            )
        return ResearchRawPage(tuple(rows), None, None)

    @staticmethod
    def _eastmoney_rows(body: bytes, identifier: str) -> list[dict[str, object]]:
        try:
            document = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise DataFailure("unverified") from None
        if not isinstance(document, dict):
            raise DataFailure("unverified")
        if document.get("success") is not True:
            if document.get("code") == 9201:
                return []
            raise DataFailure("unverified")
        result = document.get("result")
        if result is None:
            return []
        if not isinstance(result, dict) or result.get("pages") != 1:
            raise DataFailure("unverified")
        values = result.get("data")
        if not isinstance(values, list):
            raise DataFailure("unverified")
        rows: list[dict[str, object]] = []
        for value in values:
            if not isinstance(value, dict):
                raise DataFailure("unverified")
            header: dict[str, object] = {"_source": "eastmoney", "_instrument_id": identifier}
            rows.append(header | value)
        return rows

    @staticmethod
    def _sina_rows(body: bytes, identifier: str) -> list[dict[str, object]]:
        try:
            text = body.decode("gb18030")
        except UnicodeDecodeError:
            raise DataFailure("unverified") from None
        parser = _SinaRightsTable()
        parser.feed(text)
        parser.close()
        if not parser.found:
            raise DataFailure("unverified")
        fields = (
            "announcement_date",
            "ratio",
            "price",
            "basis_shares",
            "ex_date",
            "record_date",
            "pay_start",
            "pay_end",
            "listing_date",
            "raise_funds",
        )
        rows: list[dict[str, object]] = []
        for values in parser.rows:
            if len(values) == 1 and "没有数据" in values[0]:
                continue
            if len(values) not in {10, 11}:
                raise DataFailure("unverified")
            header: dict[str, object] = {"_source": "sina", "_instrument_id": identifier}
            mapped: dict[str, object] = dict(zip(fields, values, strict=False))
            rows.append(header | mapped)
        return rows

    def normalize(
        self, request: ResearchPlanRequest, page: ResearchRawPage, collected_at: datetime
    ) -> tuple[CorporateAction, ...]:
        eastmoney: dict[date, _Terms] = {}
        sina: dict[date, _Terms] = {}
        for row in page.rows:
            origin = row.get("_source")
            terms = (
                self._eastmoney_terms(row)
                if origin == "eastmoney"
                else self._sina_terms(row)
                if origin == "sina"
                else None
            )
            if terms is None:
                raise DataFailure("unverified")
            if not request.start <= terms.announcement < request.end:
                continue
            bucket = eastmoney if origin == "eastmoney" else sina
            if terms.ex in bucket:
                raise DataFailure("unverified")
            bucket[terms.ex] = terms
        if not eastmoney and not sina:
            return ()
        if eastmoney.keys() != sina.keys():
            # A one-sided event is unconfirmed, not a publishable observation.
            raise DataFailure("unverified")
        records: list[CorporateAction] = []
        for ex in sorted(eastmoney):
            left, right = eastmoney[ex], sina[ex]
            if (
                left.announcement != right.announcement
                or left.record != right.record
                or left.ratio != right.ratio
                or left.price != right.price
            ):
                raise DataFailure("unverified")
            listing = left.listing if left.listing == right.listing else None
            records.append(
                CorporateAction(
                    scope_id=request.scope_id,
                    source=self.source,
                    instrument_id=str(page.rows[0]["_instrument_id"]),
                    collected_at=collected_at,
                    mapping_version=MAPPING,
                    source_revision=digest(
                        [ex.isoformat(), format(left.ratio, "f"), format(left.price, "f")]
                    ),
                    source_event_id=digest([str(page.rows[0]["_instrument_id"]), ex.isoformat()]),
                    action="rights",
                    announcement_date=left.announcement,
                    record_date=left.record,
                    ex_date=ex,
                    # Payment is a window; a single date would be invented.
                    payment_date=None,
                    listing_date=listing,
                    status="implemented" if listing is not None else "unknown",
                    basis_shares=Decimal(10),
                    rights_shares=left.ratio,
                    rights_price=left.price,
                    terms_evidence=SINA_TERMS.format(
                        code=code_of(str(page.rows[0]["_instrument_id"]))
                    )
                    + " | "
                    + EM_TERMS,
                )
            )
        return tuple(records)

    @staticmethod
    def _eastmoney_terms(row: dict[str, object]) -> _Terms:
        try:
            listing = _optional_day(row.get("LISTING_DATE"))
            return _Terms(
                announcement=_day(row["FIRST_NOTICE_DATE"]),
                record=_day(row["EQUITY_RECORD_DATE"]),
                ex=_day(row["EX_DIVIDEND_DATE"]),
                listing=listing,
                ratio=_decimal(row["PLACING_RATIO"]),
                price=_decimal(row["ISSUE_PRICE"]),
            )
        except (KeyError, TypeError):
            raise DataFailure("unverified") from None

    @staticmethod
    def _sina_terms(row: dict[str, object]) -> _Terms:
        try:
            return _Terms(
                announcement=_day(row["announcement_date"]),
                record=_day(row["record_date"]),
                ex=_day(row["ex_date"]),
                listing=_optional_day(row["listing_date"]),
                ratio=_decimal(row["ratio"]),
                price=_decimal(row["price"]),
            )
        except (KeyError, TypeError):
            raise DataFailure("unverified") from None


def source_profile(*, configured: bool) -> SourceProfile:
    """Dated editorial evidence for the opt-in public-page feed."""

    reference = "docs/data/RIGHTS-FEED-CONDITIONS.md"

    def fact(
        aspect: Aspect,
        detail: str,
        evidence: EvidenceLevel,
        unblock: str | None = None,
    ) -> CapabilityFact:
        return CapabilityFact(
            aspect=aspect,
            detail=detail,
            evidence=evidence,
            reference=reference,
            as_of=date(2026, 11, 1),
            unblock_condition=unblock,
        )

    return SourceProfile(
        source=SOURCE,
        name="公开页配股条款（东财主源 + 新浪校验）",
        kind="rights_terms",
        mapping_version=MAPPING,
        markets=("CN",),
        periods=(),
        instrument_kinds=("stock",),
        timezone="Asia/Shanghai",
        state="configured" if configured else "unconfigured",
        facts=(
            fact(
                "history",
                "东财自1991-05-31起；600036的2010/2013与000002的1997/2000双源一致，"
                "但1993年前记录不可靠，不认证全历史完整。",
                "sample_verified",
            ),
            fact(
                "units",
                "配股比例为每10股配股股数，配股价为每股人民币；缴款仅为起止窗口，"
                "不虚构单一支付日。",
                "sample_verified",
            ),
            fact(
                "adjustment",
                "配股条款不自动生成复权因子；回测由显式公司行为账本应用，与复权价格口径分别表达。",
                "documented",
            ),
            fact(
                "time",
                "event_date按公告日筛选；除权、登记与上市日期取来源原值，"
                "历史可知时间未知，不支持as-of。",
                "documented",
            ),
            fact(
                "updates",
                "公开页无SLA与更新承诺；仅在显式触发时采集，不推断每日更新。",
                "unknown",
                unblock="取得来源更新时间规则与持续观测证据",
            ),
            fact(
                "permission",
                "公开页无鉴权，访问成功不等于获得复制、存储或展示许可；仅限内部研究。",
                "sample_verified",
            ),
            fact(
                "quota",
                "无公布配额；按主机至少1秒间隔、单次有限响应大小自我保护，不是供应商承诺。",
                "unknown",
                unblock="取得来源对自动访问频率的书面说明",
            ),
            fact(
                "license",
                "东财与新浪均要求书面许可方可复制或再分发；当前仅内部研究，"
                "不得导出为对外产品数据。",
                "documented",
            ),
        ),
    )
