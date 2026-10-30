"""Research-specific versioned publication rules; sparse records are not daily bars."""

from collections import defaultdict
from datetime import date, datetime
from typing import Literal
from uuid import uuid5

from qtrade_data_contracts.collection import digest
from qtrade_data_contracts.research import (
    CorporateAction,
    FinancialStatement,
    MembershipInterval,
    ResearchObservation,
    SecurityIdentityChange,
)
from qtrade_data_contracts.research_archive import ResearchArtifactManifest
from qtrade_data_contracts.research_publication import ResearchQualityIssue, ResearchQualityResult
from qtrade_data_contracts.research_transport import ResearchPlan
from qtrade_data_core.ports import DataFailure
from qtrade_data_core.research_evidence import ResearchArchivePort

RULE_VERSION = "research-quality.v1"


def logical_key(record: ResearchObservation) -> tuple[str, ...]:
    base = (record.source, record.instrument_id, record.kind)
    if isinstance(record, FinancialStatement):
        return (*base, record.statement, record.report_period.isoformat())
    if isinstance(record, MembershipInterval):
        return (*base, record.universe_id, record.valid_from.isoformat())
    if isinstance(record, SecurityIdentityChange):
        return (*base, record.security_id, record.change, record.effective_date.isoformat())
    if isinstance(record, CorporateAction):
        return (*base, record.event_identity)
    return (
        *base,
        record.observation_date.isoformat(),
        record.adjustment,
        record.base_date.isoformat(),
    )


def evaluate(
    plan: ResearchPlan,
    artifact: ResearchArtifactManifest,
    archive: ResearchArchivePort,
    checked_at: datetime,
) -> ResearchQualityResult:
    issues: list[ResearchQualityIssue] = []

    def issue(
        code: str, reason: str, *, block: bool = False, record: ResearchObservation | None = None
    ) -> None:
        issues.append(
            ResearchQualityIssue(
                code=code,
                severity="block" if block else "warning",
                reason=reason,
                record_version=record.content_version if record else None,
            )
        )

    expected_axis = {
        "financial_statement": "report_period",
        "membership_interval": "effective_date",
        "security_identity_change": "effective_date",
        "corporate_action": "event_date",
        "adjustment_factor": "observation_date",
    }[artifact.capability.kind]
    if plan.request.date_axis != expected_axis:
        issue(
            "date_axis_unsupported",
            "当前规则不支持该类别的日期轴，不能静默改用另一日期。",
            block=True,
        )
    if artifact.plan_sha256 != plan.plan_sha256 or artifact.capability != plan.capability:
        issue("plan_mismatch", "产物与冻结计划不一致。", block=True)
    try:
        records = archive.records(artifact)
    except DataFailure:
        records = ()
        issue("archive_integrity", "原始或标准归档摘要/数量无法验证。", block=True)
    groups = defaultdict(list)
    for batch in artifact.raw_batches:
        key = (batch.instrument_id, batch.start, batch.end)
        groups[key].append(batch)
        if not any(
            p.instrument_id == batch.instrument_id and p.start == batch.start and p.end == batch.end
            for p in plan.partitions
        ):
            issue("raw_range", "原始批次不属于冻结分片。", block=True)
    for batches in groups.values():
        by_cursor = {b.cursor: b for b in batches}
        cursor = None
        seen: set[str | None] = set()
        if len(by_cursor) != len(batches):
            issue("duplicate_cursor", "来源页游标重复。", block=True)
        while True:
            if cursor in seen or cursor not in by_cursor:
                issue("incomplete_pages", "原始分页链存在缺页或循环。", block=True)
                break
            seen.add(cursor)
            cursor = by_cursor[cursor].next_cursor
            if cursor is None:
                if len(seen) != len(batches):
                    issue("orphan_page", "原始分页包含未连通页。", block=True)
                break
    versions = set()
    membership = defaultdict(list)
    day: date | None
    for record in records:
        if record.content_version in versions:
            issue("duplicate_record", "重复标准记录未去重。", block=True, record=record)
        versions.add(record.content_version)
        if (
            record.scope_id != artifact.scope_id
            or record.source != artifact.capability.source
            or record.kind != artifact.capability.kind
        ):
            issue("identity", "记录作用域、来源或类别错误。", block=True, record=record)
        if record.knowledge.status == "unknown":
            issue("knowledge_unknown", "历史可知时间未知，不能据此认证PIT。", record=record)
        elif record.knowledge.known_at and record.knowledge.known_at > record.collected_at:
            issue("future_knowledge", "可知时间晚于实际采集时刻。", block=True, record=record)
        if isinstance(record, FinancialStatement):
            day = record.report_period
            if record.period_semantics == "unknown":
                issue("period_unknown", "累计/单季期间语义未知，不进行隐式差分。", record=record)
            if any(m.original_unit.basis == "unknown" for m in record.metrics):
                issue("unit_unknown", "原始指标单位未知，未生成标准单位数值。", record=record)
            if record.disclosure_date is None:
                issue("disclosure_unknown", "披露日期未知。", record=record)
        elif isinstance(record, MembershipInterval):
            day = record.valid_from
            membership[(record.instrument_id, record.universe_id)].append(record)
        elif isinstance(record, SecurityIdentityChange):
            day = record.effective_date
        elif isinstance(record, CorporateAction):
            # Event selection convention must be frozen by the source mapping.
            day = record.announcement_date
            if day is None:
                issue(
                    "event_date_unknown",
                    "公告日期未知，不能确认事件日期筛选范围。",
                    block=True,
                    record=record,
                )
            if record.payment_date is None:
                issue("payment_unknown", "现金支付日未知，不能用于完整现金核算。", record=record)
        else:
            day = record.observation_date
            issue("adjustment_research_limit", "复权基准与研究可知前提须独立验证。", record=record)
        if day is not None and not any(
            instrument == record.instrument_id and start <= day < end
            for instrument, start, end in groups
        ):
            issue(
                "record_range", "记录时间不属于原始分片的半开日期范围。", block=True, record=record
            )
    for intervals in membership.values():
        ordered = sorted(intervals, key=lambda r: r.valid_from)
        for left, right in zip(ordered, ordered[1:], strict=False):
            if left.valid_from == right.valid_from:
                issue("member_revision", "相同成员生效日的修订版本分别保留，不合并有效区间。")
                continue
            if left.valid_until is None or left.valid_until > right.valid_from:
                issue("member_overlap", "成员有效区间重叠，须保留修订并明确版本选择。", block=True)
    status: Literal["passed", "warning", "blocked"] = (
        "blocked"
        if any(i.severity == "block" for i in issues)
        else "warning"
        if issues
        else "passed"
    )
    identity = digest(
        [RULE_VERSION, artifact.records_sha256, [i.model_dump(mode="json") for i in issues]]
    )
    return ResearchQualityResult(
        scope_id=artifact.scope_id,
        artifact_id=artifact.artifact_id,
        quality_id=uuid5(artifact.artifact_id, identity),
        rule_version=RULE_VERSION,
        records_sha256=artifact.records_sha256,
        status=status,
        issues=tuple(issues),
        checked_at=checked_at,
    )
