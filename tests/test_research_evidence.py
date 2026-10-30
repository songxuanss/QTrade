"""Controlled raw/standard storage evidence, not a live supplier/publication claim."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from test_research_contracts import metric, statement
from test_research_interface import ControlledSource, request

from qtrade_data_core.ports import DataFailure
from qtrade_data_core.research import ResearchRawPage, ResearchService
from qtrade_data_files.research import FileResearchArchive
from qtrade_data_http.client import StaticScopeAuthorizer

NOW = datetime(2026, 10, 12, tzinfo=UTC)


def evidence(tmp_path):
    req = request(scope_id=uuid4(), request_id=uuid4())
    provider = ControlledSource()
    auth = StaticScopeAuthorizer({f"{req.actor_id}:{req.scope_id}": "manage"})
    plan = ResearchService(auth, (provider,)).preview(req)
    archive = FileResearchArchive(tmp_path)
    page = ResearchRawPage(({"amount": "100", "report_period": "2015-06-30"},), None, None)
    raw = archive.capture(plan, plan.partitions[0], page, NOW)
    record = statement(
        scope_id=req.scope_id,
        mapping_version=plan.capability.mapping_version,
        metrics=(metric("100"),),
        collected_at=NOW,
    )
    return archive, plan, page, raw, record


def test_raw_survives_normalization_failure_and_restart(tmp_path):
    archive, plan, page, raw, record = evidence(tmp_path)
    with pytest.raises(DataFailure, match="unverified"):
        archive.standardize(
            plan, (raw,), (record.model_copy(update={"source": "wrong-source"}),), NOW
        )
    restarted = FileResearchArchive(tmp_path)
    assert restarted.raw(raw) == page.rows
    assert restarted.capture(plan, plan.partitions[0], page, NOW + timedelta(days=1)) == raw
    assert not list(archive.root.glob("*/*/artifact-manifest.json"))


def test_standard_immutable_lineage_and_semantic_version(tmp_path):
    archive, plan, page, raw, record = evidence(tmp_path)
    manifest = archive.standardize(plan, (raw,), (record,), NOW)
    assert archive.standardize(plan, (raw,), (record,), NOW + timedelta(days=1)) == manifest
    assert FileResearchArchive(tmp_path).records(manifest) == (record,)
    assert manifest.raw_batches == (raw,) and manifest.record_count == 1
    later = record.model_copy(update={"collected_at": NOW + timedelta(days=1)})
    recollected = archive.standardize(plan, (raw,), (later,), NOW + timedelta(days=1))
    assert recollected.artifact_id != manifest.artifact_id
    assert recollected.semantic_sha256 == manifest.semantic_sha256
    changed = statement(
        scope_id=record.scope_id,
        mapping_version=record.mapping_version,
        metrics=(metric("101"),),
        collected_at=NOW,
    )
    revision = archive.standardize(plan, (raw,), (changed,), NOW)
    assert revision.semantic_sha256 != manifest.semantic_sha256
    assert archive.records(manifest) == (record,)


@pytest.mark.parametrize("target", ["raw", "standard"])
def test_corruption_blocks_reads_without_publishing(tmp_path, target):
    archive, plan, _, raw, record = evidence(tmp_path)
    manifest = archive.standardize(plan, (raw,), (record,), NOW)
    path = (
        archive.root / str(raw.scope_id) / str(raw.batch_id) / f"raw-{raw.raw_sha256}.parquet"
        if target == "raw"
        else archive.root
        / str(manifest.scope_id)
        / str(manifest.artifact_id)
        / f"records-{manifest.records_sha256}.parquet"
    )
    path.write_bytes(b"controlled-corruption")
    with pytest.raises(DataFailure, match="unverified"):
        archive.records(manifest)


def test_empty_raw_is_evidence_not_nonempty_standard_success(tmp_path):
    archive, plan, _, _, record = evidence(tmp_path)
    raw = archive.capture(plan, plan.partitions[0], ResearchRawPage((), None, None), NOW)
    assert raw.raw_record_count == 0 and archive.raw(raw) == ()
    with pytest.raises(DataFailure, match="invalid_input"):
        archive.standardize(plan, (raw,), (record,), NOW)


def test_cross_scope_lineage_and_unselected_partition_rejected(tmp_path):
    archive, plan, page, raw, record = evidence(tmp_path)
    with pytest.raises(DataFailure, match="conflict"):
        archive.standardize(
            plan, (raw.model_copy(update={"scope_id": UUID(int=999)}),), (record,), NOW
        )
    with pytest.raises(DataFailure, match="conflict"):
        archive.capture(
            plan, plan.partitions[0].model_copy(update={"instrument_id": "601288.SH"}), page, NOW
        )


@pytest.mark.parametrize(
    "kind,axis",
    [
        ("financial_statement", "report_period"),
        ("membership_interval", "effective_date"),
        ("security_identity_change", "effective_date"),
        ("corporate_action", "event_date"),
        ("adjustment_factor", "observation_date"),
    ],
)
def test_all_research_types_archive_through_same_seam(tmp_path, kind, axis):
    from datetime import date

    from qtrade_data_contracts.research import (
        AdjustmentFactor,
        CorporateAction,
        MembershipInterval,
        SecurityIdentityChange,
    )

    req = request(scope_id=uuid4(), request_id=uuid4(), kind=kind, date_axis=axis)
    provider = ControlledSource()
    provider.capability = provider.capability.model_copy(
        update={"kind": kind, "date_axes": (axis,)}
    )
    plan = ResearchService(
        StaticScopeAuthorizer({f"{req.actor_id}:{req.scope_id}": "manage"}), (provider,)
    ).preview(req)
    common = dict(
        scope_id=req.scope_id,
        source=req.source,
        instrument_id="600519.SH",
        collected_at=NOW,
        mapping_version=plan.capability.mapping_version,
    )
    if kind == "financial_statement":
        record = statement(**common)
    elif kind == "membership_interval":
        record = MembershipInterval(
            **common,
            universe_id="fixture-pool",
            valid_from=date(2015, 6, 1),
            interval_evidence="fixture:membership",
        )
    elif kind == "security_identity_change":
        record = SecurityIdentityChange(
            **common,
            security_id="fixture-security",
            change="name",
            effective_date=date(2015, 6, 1),
            name="受控名称",
            change_evidence="fixture:identity",
        )
    elif kind == "corporate_action":
        record = CorporateAction(
            **common,
            source_event_id="fixture-event",
            action="distribution",
            status="implemented",
            basis_shares="10",
            cash="1",
            cash_currency="CNY",
            terms_evidence="fixture:terms",
        )
    else:
        record = AdjustmentFactor(
            **common,
            observation_date=date(2015, 6, 1),
            adjustment="forward",
            factor="0.9",
            base_date=date(2026, 10, 9),
            base_value="1",
            algorithm_version="fixture.v1",
            convention_evidence="fixture:adjustment",
        )
    archive = FileResearchArchive(tmp_path)
    raw = archive.capture(
        plan, plan.partitions[0], ResearchRawPage(({"fixture": kind},), None, None), NOW
    )
    artifact = archive.standardize(plan, (raw,), (record,), NOW)
    assert FileResearchArchive(tmp_path).records(artifact) == (record,)


def test_corrupt_or_replaced_manifest_cannot_be_recovered_as_valid(tmp_path):
    import json

    archive, plan, page, raw, record = evidence(tmp_path)
    path = archive.root / str(raw.scope_id) / str(raw.batch_id) / "raw-manifest.json"
    path.write_text(json.dumps(raw.model_dump(mode="json") | {"request_id": str(UUID(int=999))}))
    with pytest.raises(DataFailure, match="conflict"):
        archive.capture(plan, plan.partitions[0], page, NOW)
    path.write_text("invalid-json")
    with pytest.raises(DataFailure, match="unverified"):
        archive.capture(plan, plan.partitions[0], page, NOW)


def test_concurrent_archive_writers_freeze_one_manifest(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    _, plan, page, _, record = evidence(tmp_path / "setup")
    root = tmp_path / "competing"

    def capture(index):
        return FileResearchArchive(root).capture(
            plan, plan.partitions[0], page, NOW + timedelta(seconds=index)
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        batches = list(pool.map(capture, range(3)))
    assert batches[0] == batches[1] == batches[2]

    def standardize(index):
        return FileResearchArchive(root).standardize(
            plan, (batches[0],), (record,), NOW + timedelta(seconds=index)
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        artifacts = list(pool.map(standardize, range(3)))
    assert artifacts[0] == artifacts[1] == artifacts[2]
