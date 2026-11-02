"""Versioned research gate rules, using controlled evidence only."""

from datetime import date
from uuid import uuid4

from test_research_evidence import NOW, evidence
from test_research_interface import ControlledSource, request

from qtrade_data_contracts.research import CorporateAction
from qtrade_data_core.research import ResearchRawPage, ResearchService
from qtrade_data_core.research_quality import evaluate
from qtrade_data_files.research import FileResearchArchive
from qtrade_data_http.client import StaticScopeAuthorizer


def check(tmp_path, update=None):
    archive, plan, _, raw, record = evidence(tmp_path)
    if update:
        record = record.model_copy(update=update)
    artifact = archive.standardize(plan, (raw,), (record,), NOW)
    return evaluate(plan, artifact, archive, NOW), archive, plan, artifact


def action_check(tmp_path, **terms):
    """Archive one corporate action through the same seam, then evaluate it."""
    req = request(
        scope_id=uuid4(), request_id=uuid4(), kind="corporate_action", date_axis="event_date"
    )
    provider = ControlledSource()
    provider.capability = provider.capability.model_copy(
        update={"kind": "corporate_action", "date_axes": ("event_date",)}
    )
    plan = ResearchService(
        StaticScopeAuthorizer({f"{req.actor_id}:{req.scope_id}": "manage"}), (provider,)
    ).preview(req)
    archive = FileResearchArchive(tmp_path)
    raw = archive.capture(
        plan,
        plan.partitions[0],
        ResearchRawPage(({"fixture": "corporate-action"},), None, None),
        NOW,
    )
    record = CorporateAction(
        scope_id=req.scope_id,
        source=req.source,
        instrument_id="600519.SH",
        collected_at=NOW,
        mapping_version=plan.capability.mapping_version,
        source_event_id="fixture-event",
        announcement_date=date(2015, 6, 1),
        terms_evidence="fixture:terms",
        **terms,
    )
    artifact = archive.standardize(plan, (raw,), (record,), NOW)
    return evaluate(plan, artifact, archive, NOW)


def test_rights_terms_do_not_report_a_missing_cash_payment(tmp_path):
    result = action_check(
        tmp_path,
        action="rights",
        status="implemented",
        record_date=date(2015, 6, 5),
        ex_date=date(2015, 6, 10),
        basis_shares="10",
        rights_shares="3",
        rights_price="5.50",
    )
    assert result.status == "warning"
    assert "knowledge_unknown" in {issue.code for issue in result.issues}
    assert "payment_unknown" not in {issue.code for issue in result.issues}


def test_cash_distribution_without_payment_date_still_warns(tmp_path):
    result = action_check(
        tmp_path,
        action="distribution",
        status="implemented",
        ex_date=date(2015, 6, 10),
        basis_shares="10",
        cash="1",
        cash_currency="CNY",
    )
    assert "payment_unknown" in {issue.code for issue in result.issues}


def test_sparse_financial_unknown_semantics_are_explicit_warnings(tmp_path):
    result, _, _, _ = check(tmp_path)
    assert result.status == "warning"
    assert {i.code for i in result.issues} >= {
        "knowledge_unknown",
        "unit_unknown",
        "period_unknown",
    }
    assert not any(i.code == "missing_trading_day" for i in result.issues)


def test_out_of_report_range_blocks_but_original_evidence_survives(tmp_path):
    result, archive, _, artifact = check(tmp_path, {"report_period": date(2014, 6, 30)})
    assert result.status == "blocked" and "record_range" in {i.code for i in result.issues}
    assert archive.raw(artifact.raw_batches[0])


def test_duplicate_standard_records_block(tmp_path):
    archive, plan, _, raw, record = evidence(tmp_path)
    artifact = archive.standardize(plan, (raw,), (record, record), NOW)
    assert evaluate(plan, artifact, archive, NOW).status == "blocked"


def test_missing_provider_page_blocks_publication(tmp_path):
    archive, plan, _, _, record = evidence(tmp_path)
    raw = archive.capture(
        plan, plan.partitions[0], ResearchRawPage(({"amount": "100"},), None, "next"), NOW
    )
    artifact = archive.standardize(plan, (raw,), (record,), NOW)
    assert "incomplete_pages" in {i.code for i in evaluate(plan, artifact, archive, NOW).issues}


def test_archive_digest_mismatch_is_blocking(tmp_path):
    _, archive, plan, artifact = check(tmp_path)
    raw = artifact.raw_batches[0]
    (
        archive.root / str(raw.scope_id) / str(raw.batch_id) / f"raw-{raw.raw_sha256}.parquet"
    ).write_bytes(b"corrupt")
    result = evaluate(plan, artifact, archive, NOW)
    assert result.status == "blocked" and result.issues[0].code == "archive_integrity"
