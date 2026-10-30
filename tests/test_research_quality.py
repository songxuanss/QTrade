"""Versioned research gate rules, using controlled evidence only."""

from datetime import date

from test_research_evidence import NOW, evidence

from qtrade_data_core.research import ResearchRawPage
from qtrade_data_core.research_quality import evaluate


def check(tmp_path, update=None):
    archive, plan, _, raw, record = evidence(tmp_path)
    if update:
        record = record.model_copy(update=update)
    artifact = archive.standardize(plan, (raw,), (record,), NOW)
    return evaluate(plan, artifact, archive, NOW), archive, plan, artifact


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
