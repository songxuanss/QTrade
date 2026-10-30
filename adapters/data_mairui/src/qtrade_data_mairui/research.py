"""Ordinary historical financial reports; no invented units or PIT knowledge."""

from base64 import b64decode
from binascii import Error as Base64Error
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import TypeAdapter

from qtrade_data_contracts.collection import RawPayloadKind, digest
from qtrade_data_contracts.models import ExactDecimal
from qtrade_data_contracts.planning import CollectionCostEstimate
from qtrade_data_contracts.research import (
    CorporateAction,
    FinancialMetric,
    FinancialStatement,
    UnitEvidence,
)
from qtrade_data_contracts.research_acquisition import MAX_RESPONSE_BYTES
from qtrade_data_contracts.research_transport import (
    ResearchCapability,
    ResearchPartition,
    ResearchPlanRequest,
)
from qtrade_data_core.collection import RawCapture
from qtrade_data_core.ports import DataFailure
from qtrade_data_core.research import ResearchRawPage
from qtrade_data_core.research_acquisition import ResearchSourceResponse, ResponseSink
from qtrade_data_mairui.collection import MaiRuiCollection


def response_capture(sink: ResponseSink) -> RawCapture:
    """Bridge the private transport envelope; reject extra fields such as URLs."""

    def save(kind: RawPayloadKind, payload: object) -> None:
        if (
            kind != "http_response"
            or not isinstance(payload, dict)
            or set(payload) != {"http_status", "operation", "body_base64", "complete"}
            or payload["operation"] != "research.data"
            or not (
                type(payload["http_status"]) is int
                or (payload["http_status"] is None and payload["complete"] is False)
            )
            or type(payload["complete"]) is not bool
            or not isinstance(payload["body_base64"], str)
            or len(payload["body_base64"]) > (MAX_RESPONSE_BYTES * 4 // 3 + 4)
        ):
            raise DataFailure("unverified")
        try:
            body = b64decode(payload["body_base64"], validate=True)
        except (ValueError, Base64Error):
            raise DataFailure("unverified") from None
        sink(
            ResearchSourceResponse(
                "research.data", payload["http_status"], body, payload["complete"]
            )
        )

    return save


def research_estimate(parts: int) -> CollectionCostEstimate:
    # Both current adapters perform one unpaginated, one-credit request per
    # partition. Early failures may stop the run; retries are charged separately.
    return CollectionCostEstimate(
        calls_min=0,
        calls_max=parts,
        credits_min=0,
        credits_max=parts,
        accounting="internal_credit_safeguard",
        includes_retries=False,
        basis="当前来源每分段一次无分页请求、一次内部信用；提前失败可减少调用。"
        "不含重试及后续复核，不是供应商报价。",
    )


class MaiRuiFinancial:
    def estimate(self, request: ResearchPlanRequest, parts: int) -> CollectionCostEstimate:
        return research_estimate(parts)

    def __init__(
        self, transport: MaiRuiCollection, statement: Literal["balance", "income", "cashflow"]
    ):
        self.transport, self.statement = transport, statement
        self.source = "mairui.financial." + statement

    def capabilities(self) -> tuple[ResearchCapability, ...]:
        configured = bool(self.transport.licence)
        return (
            ResearchCapability(
                source=self.source,
                name="MaiRui 普通历史财务 " + self.statement,
                kind="financial_statement",
                configured=configured,
                evidence="sample",
                markets=("CN",),
                instrument_kinds=("stock",),
                date_axes=("report_period",),
                timezone="Asia/Shanghai",
                mapping_version="mairui.financial.v1",
                limitations=(
                    "披露日期仅日精度，不证明完整PIT；as-of不可用。",
                    "原始单位、会计范围和累计/单季口径未确认，不换算。",
                    "已验证600519的2015/2025样本，不承诺其他证券完整历史。",
                ),
                unblock_condition=None if configured else "配置现有MaiRui证书",
            ),
        )

    def partitions(self, request: ResearchPlanRequest) -> Iterator[ResearchPartition]:
        for identifier in request.instrument_ids:
            symbol, index = self.transport._symbol(identifier)
            if index or not symbol.endswith((".SH", ".SZ")):
                raise DataFailure("unsupported")
            cursor = request.start
            while cursor < request.end:
                end = min(date(cursor.year + 1, 1, 1), request.end)
                yield ResearchPartition(instrument_id=identifier, start=cursor, end=end)
                cursor = end

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
        return self._fetch(request, partition, cursor, response_capture(capture))

    def _fetch(
        self,
        request: ResearchPlanRequest,
        partition: ResearchPartition,
        cursor: str | None,
        capture: RawCapture | None,
    ) -> ResearchRawPage:
        if cursor is not None:
            raise DataFailure("invalid_input")
        symbol, index = self.transport._symbol(partition.instrument_id)
        if index or not symbol.endswith((".SH", ".SZ")):
            raise DataFailure("unsupported")
        rows = self.transport._rows(
            self.transport._get(
                f"hsstock/financial/{self.statement}/{symbol.split('.')[0]}",
                {
                    "st": partition.start.strftime("%Y%m%d"),
                    "et": (partition.end - timedelta(days=1)).strftime("%Y%m%d"),
                },
                capture=capture,
            )
        )
        # Exact Decimal JSON tokens become plain decimal text in RAW evidence.
        return ResearchRawPage(
            tuple(
                {k: format(v, "f") if isinstance(v, Decimal) else v for k, v in row.items()}
                | {"_instrument_id": partition.instrument_id}
                for row in rows
            ),
            None,
            None,
        )

    def normalize(
        self, request: ResearchPlanRequest, page: ResearchRawPage, collected_at: datetime
    ) -> tuple[FinancialStatement, ...]:
        records = []
        try:
            for row in page.rows:
                report = date.fromisoformat(str(row["jzrq"])[:10])
                disclosure = date.fromisoformat(str(row["plrq"])[:10]) if row.get("plrq") else None
                metrics = tuple(
                    FinancialMetric(
                        field_id=k,
                        name=k,
                        original_value=None
                        if v is None or v == "-"
                        else TypeAdapter(ExactDecimal).validate_python(v),
                        original_unit=UnitEvidence(),
                        standard_value=None,
                        standard_unit=UnitEvidence(),
                        normalization="unresolved",
                        missing_reason=(
                            "provider_null" if v is None else "not_reported" if v == "-" else None
                        ),
                    )
                    for k, v in sorted(row.items())
                    if k not in {"jzrq", "plrq", "_instrument_id"}
                )
                records.append(
                    FinancialStatement(
                        scope_id=request.scope_id,
                        source=self.source,
                        instrument_id=str(row["_instrument_id"]),
                        collected_at=collected_at,
                        mapping_version=self.capabilities()[0].mapping_version,
                        source_revision=digest(row),
                        statement=self.statement,
                        report_period=report,
                        disclosure_date=disclosure,
                        disclosure_precision="day" if disclosure else "unknown",
                        period_semantics="unknown",
                        metrics=metrics,
                    )
                )
        except (KeyError, ValueError, TypeError):
            raise DataFailure("unverified") from None
        return tuple(records)


class MaiRuiDistributions:
    """Finite dividend/bonus/transfer semantics; source has no native event ID."""

    source = "mairui.corporate.distributions"

    def estimate(self, request: ResearchPlanRequest, parts: int) -> CollectionCostEstimate:
        return research_estimate(parts)

    def __init__(self, transport: MaiRuiCollection):
        self.transport = transport

    def capabilities(self) -> tuple[ResearchCapability, ...]:
        configured = bool(self.transport.licence)
        return (
            ResearchCapability(
                source=self.source,
                name="MaiRui 分红送转（有限事件语义）",
                kind="corporate_action",
                configured=configured,
                evidence="sample",
                markets=("CN",),
                instrument_kinds=("stock",),
                date_axes=("event_date",),
                timezone="Asia/Shanghai",
                mapping_version="mairui.distributions.v1",
                limitations=(
                    "event_date按公告日筛选，不按除权或支付日。",
                    "来源无原生事件ID，身份按证券与公告日派生；同日歧义拒绝。",
                    "每10股税前人民币派息；现金支付日和历史可知时间未知。",
                    "仅分红、送股、转增，不证明完整配股、拆并股或现金核算就绪。",
                ),
                unblock_condition=None if configured else "配置现有MaiRui证书",
            ),
        )

    def partitions(self, request: ResearchPlanRequest) -> Iterator[ResearchPartition]:
        for identifier in request.instrument_ids:
            symbol, index = self.transport._symbol(identifier)
            if index or not symbol.endswith((".SH", ".SZ")):
                raise DataFailure("unsupported")
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
        return self._fetch(request, partition, cursor, response_capture(capture))

    def _fetch(
        self,
        request: ResearchPlanRequest,
        partition: ResearchPartition,
        cursor: str | None,
        capture: RawCapture | None,
    ) -> ResearchRawPage:
        if cursor is not None:
            raise DataFailure("invalid_input")
        symbol, index = self.transport._symbol(partition.instrument_id)
        if index or not symbol.endswith((".SH", ".SZ")):
            raise DataFailure("unsupported")
        rows = self.transport._rows(
            self.transport._get(f"hscp/jnfh/{symbol.split('.')[0]}", capture=capture)
        )
        return ResearchRawPage(
            tuple(
                {k: format(v, "f") if isinstance(v, Decimal) else v for k, v in row.items()}
                | {"_instrument_id": partition.instrument_id}
                for row in rows
            ),
            None,
            None,
        )

    def normalize(
        self, request: ResearchPlanRequest, page: ResearchRawPage, collected_at: datetime
    ) -> tuple[CorporateAction, ...]:
        records: dict[str, CorporateAction] = {}

        def event_date(value: object) -> date | None:
            return None if value in (None, "", "-", "--") else date.fromisoformat(str(value))

        try:
            for row in page.rows:
                announcement = event_date(row.get("sdate"))
                if announcement is None:
                    raise DataFailure("unverified")
                if not request.start <= announcement < request.end:
                    continue

                def term(v: object) -> Decimal | None:
                    return (
                        None
                        if v in (None, "", "-", "--")
                        else TypeAdapter(ExactDecimal).validate_python(v)
                    )

                cash = term(row.get("send"))
                record = CorporateAction(
                    scope_id=request.scope_id,
                    source=self.source,
                    instrument_id=str(row["_instrument_id"]),
                    collected_at=collected_at,
                    mapping_version=self.capabilities()[0].mapping_version,
                    source_revision=digest(row),
                    source_event_id=digest([row["_instrument_id"], announcement.isoformat()]),
                    action="distribution",
                    announcement_date=announcement,
                    record_date=event_date(row.get("edate")),
                    ex_date=event_date(row.get("cdate")),
                    listing_date=event_date(row.get("hdate")),
                    status="implemented" if row.get("line") == "实施" else "unknown",
                    cash=cash,
                    cash_currency="CNY" if cash is not None else None,
                    cash_tax_basis="before_tax" if cash is not None else "unknown",
                    basis_shares=Decimal(10),
                    bonus_shares=term(row.get("give")),
                    transfer_shares=term(row.get("change")),
                    terms_evidence="https://www.mairuiapi.com/hsdata#hscp/jnfh",
                )
                previous = records.get(record.source_event_id)
                if previous is not None and previous.content_version != record.content_version:
                    raise DataFailure("unverified")
                records[record.source_event_id] = record
        except (KeyError, ValueError, TypeError):
            raise DataFailure("unverified") from None
        return tuple(records.values())
