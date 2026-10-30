"""Controlled publication proof from real files; no latest-supplier claim."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from test_independent_collection import bar, part, request

from qtrade_data_core.collection import CollectionResult
from qtrade_data_core.collection_quality import evaluate
from qtrade_data_core.freshness_evidence import DailyPublicationEvidence
from qtrade_data_core.ports import DataFailure
from qtrade_data_files.archive import FileCollectionArchive
from qtrade_data_http.client import StaticScopeAuthorizer


def evidence(
    tmp_path,
    *,
    hours=24,
    suspect=False,
    label_hour=0,
    start=None,
    record_days=1,
    capture_kind="history",
    captured_raw=None,
    monkeypatch=None,
    captured_at=None,
    published_at=None,
):
    start = start or datetime(2025, 9, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
    task = replace(
        part(
            request(
                source="mairui.history.bars",
                granularity="1d",
                start=start,
                end=start + timedelta(hours=hours),
            )
        ),
        mapping_version="fixture.daily.v1",
        state="completed",
        attempt=1,
        lease_token=uuid4(),
    )
    records = tuple(
        bar().model_copy(
            update={
                "timestamp": start + timedelta(days=i, hours=label_hour),
                "granularity": "1d",
                "quality": "suspect" if suspect else "valid",
            }
        )
        for i in range(record_days)
    )
    raw = tuple(
        {"c": "3.25", "t": (start + timedelta(days=i)).date().isoformat()}
        for i in range(record_days)
    )
    result = CollectionResult(
        raw,
        records,
        {"prices": "CNY", "volume": "shares", "value": "CNY"},
        "local_trade_date",
        {"adjustment": "none", "expected_dates": [row["t"] for row in raw]},
    )
    archive = FileCollectionArchive(tmp_path)
    import qtrade_data_files.archive as archive_module

    if captured_at is not None:
        assert monkeypatch is not None
        monkeypatch.setattr(archive_module, "datetime", SimpleNamespace(now=lambda _: captured_at))
    archive.capture_raw(task, capture_kind, captured_raw if captured_raw is not None else list(raw))
    checked_at = published_at or captured_at or datetime.now(UTC)
    quality = evaluate(task, result, "Asia/Shanghai", checked_at, source_captured=True)
    archive.record_quality(task, quality)
    if published_at is not None:
        monkeypatch.setattr(archive_module, "datetime", SimpleNamespace(now=lambda _: published_at))
    published = archive.publish(task, result)
    repo = SimpleNamespace(
        publication=lambda scope, pub: published, part_for_job=lambda *args: task
    )
    auth = StaticScopeAuthorizer({f"{task.request.actor_id}:{task.request.scope_id}": "read"})
    service = DailyPublicationEvidence(auth, repo, archive)
    return task, published, archive, service, auth


def verify(task, published, service):
    return service.verify(task.request.actor_id, task.request.scope_id, published.publication_id)


def test_verified_period_capture_time_and_digest_survive_restart(tmp_path):
    task, published, archive, service, _ = evidence(tmp_path)
    result = verify(task, published, service)
    assert result.published.complete_through.isoformat() == "2025-09-01"
    assert result.collected.observed_at <= result.published.observed_at
    assert result.published.observed_at == published.published_at
    assert result.collected.evidence_sha256 == result.published.evidence_sha256
    service.archive = FileCollectionArchive(tmp_path)
    assert verify(task, published, service) == result
    # Later retries/receipts do not retroactively change the old decision.
    archive.capture_raw(task, "history", [{"later": "not part of this publication"}])
    assert verify(task, published, service) == result


@pytest.mark.parametrize("changes", [{"hours": 12}, {"suspect": True}, {"label_hour": 12}])
def test_partial_or_suspect_or_non_daily_label_cannot_prove_complete_day(tmp_path, changes):
    task, published, _, service, _ = evidence(tmp_path, **changes)
    with pytest.raises(DataFailure, match="unverified"):
        verify(task, published, service)


def test_changed_units_with_same_record_hash_do_not_reuse_old_quality(tmp_path):
    task, published, archive, service, _ = evidence(tmp_path)
    bad = published.model_copy(
        update={"units": {"prices": "CNY", "volume": "lots", "value": "CNY"}}
    )
    service.repository.publication = lambda *args: bad
    # Even a trusted port reporting the changed manifest cannot make the old
    # quality decision certify different raw/units/evidence inputs.
    archive.recover = lambda *args: bad
    with pytest.raises(DataFailure, match="unverified"):
        verify(task, published, service)


@pytest.mark.parametrize("target", ["quality", "capture", "raw", "capture_identity"])
def test_missing_or_corrupted_evidence_not_certified(tmp_path, target):
    task, published, archive, service, _ = evidence(tmp_path)
    root = archive.root / str(published.scope_id) / str(published.part_id)
    if target == "quality":
        next(root.glob("quality-*.json")).unlink()
    elif target == "capture":
        next((root / "source-captures").glob("payload-*.json")).write_text("corrupted")
    elif target == "raw":
        (root / f"raw-{published.raw_sha256}.parquet").write_bytes(b"corrupted")
    else:
        path = next((root / "source-captures").glob("receipt-*.json"))
        body = json.loads(path.read_text()) | {"actor_id": str(uuid4())}
        path.write_text(json.dumps(body))
    with pytest.raises(DataFailure):
        verify(task, published, service)


def test_revoked_or_foreign_scope_cannot_open_archive(tmp_path):
    task, published, _, service, auth = evidence(tmp_path)
    calls = []
    service.repository.publication = lambda *args: calls.append("opened") or published
    with pytest.raises(DataFailure, match="forbidden"):
        service.verify(task.request.actor_id, uuid4(), published.publication_id)
    auth.grants.clear()
    with pytest.raises(DataFailure, match="forbidden"):
        verify(task, published, service)
    assert not calls


@pytest.mark.parametrize(
    "change,code",
    [
        ({"scope_id": uuid4()}, "unavailable"),
        ({"granularity": "5m"}, "unsupported"),
        ({"instrument_id": "stock:US:XNAS:AAPL"}, "unsupported"),
        ({"record_count": 20001}, "unsupported"),
    ],
)
def test_wrong_scope_or_unsupported_shape_rejected_before_archive(tmp_path, change, code):
    task, published, archive, service, _ = evidence(tmp_path)
    service.repository.publication = lambda *args: published.model_copy(update=change)

    def must_not_open(*args):
        raise AssertionError("unsupported or foreign publication opened archive")

    archive.recover = must_not_open
    with pytest.raises(DataFailure, match=code):
        verify(task, published, service)


def release_rule(day):
    from qtrade_data_contracts.freshness import FreshnessExpectation

    return FreshnessExpectation(
        complete_through=day.date(),
        available_at=day.replace(hour=17, minute=10),
        timezone="Asia/Shanghai",
        rule_version="fixture.release.v1",
        basis="controlled release rule",
    )


def test_same_day_without_verified_release_rule_is_not_certified(tmp_path, monkeypatch):
    day = datetime(2026, 10, 14, tzinfo=ZoneInfo("Asia/Shanghai"))
    task, pub, _, service, _ = evidence(
        tmp_path,
        start=day,
        monkeypatch=monkeypatch,
        captured_at=day.replace(hour=20),
        published_at=day.replace(hour=20),
    )
    with pytest.raises(DataFailure, match="unverified"):
        verify(task, pub, service)
    proof = service.verify(
        task.request.actor_id, pub.scope_id, pub.publication_id, expectation=release_rule(day)
    )
    assert proof.published.complete_through == day.date()


def test_pre_release_acquisition_does_not_become_complete_after_delayed_publish(
    tmp_path, monkeypatch
):
    day = datetime(2026, 10, 14, tzinfo=ZoneInfo("Asia/Shanghai"))
    task, pub, _, service, _ = evidence(
        tmp_path,
        start=day,
        monkeypatch=monkeypatch,
        captured_at=day.replace(hour=12),
        published_at=day + timedelta(days=1, hours=1),
    )
    with pytest.raises(DataFailure, match="unverified"):
        service.verify(
            task.request.actor_id, pub.scope_id, pub.publication_id, expectation=release_rule(day)
        )


def test_partial_latest_day_keeps_earlier_proven_period(tmp_path, monkeypatch):
    first = datetime(2026, 10, 13, tzinfo=ZoneInfo("Asia/Shanghai"))
    day = first + timedelta(days=1)
    task, pub, _, service, _ = evidence(
        tmp_path,
        start=first,
        hours=48,
        record_days=2,
        monkeypatch=monkeypatch,
        captured_at=day.replace(hour=12),
        published_at=day + timedelta(days=1, hours=1),
    )
    proof = service.verify(
        task.request.actor_id, pub.scope_id, pub.publication_id, expectation=release_rule(day)
    )
    assert proof.published.complete_through == first.date()
    assert pub.record_count == 2  # No immutable observations were deleted or rewritten.


@pytest.mark.parametrize(
    "kind,rows",
    [
        ("history", [{"c": "different value", "t": "2025-09-01"}]),
        ("cached_history", [{"c": "3.25", "t": "2025-09-01"}]),
    ],
)
def test_same_quantity_or_cache_read_time_not_original_acquisition_proof(tmp_path, kind, rows):
    task, pub, _, service, _ = evidence(tmp_path, capture_kind=kind, captured_raw=rows)
    with pytest.raises(DataFailure, match="unverified"):
        verify(task, pub, service)


def test_nested_history_rows_bound_by_content_when_receipt_count_unknown(tmp_path):
    task, pub, archive, service, _ = evidence(
        tmp_path, captured_raw={"data": {"list": [{"c": "3.25", "t": "2025-09-01"}]}}
    )
    assert archive.source_captures(task, pub.published_at)[0].response_rows is None
    assert verify(task, pub, service).published.complete_through.isoformat() == "2025-09-01"


def test_delayed_next_day_release_keeps_its_actual_date(tmp_path, monkeypatch):
    day = datetime(2026, 10, 14, tzinfo=ZoneInfo("Asia/Shanghai"))
    acquired = day + timedelta(days=1, hours=12)
    task, pub, _, service, _ = evidence(
        tmp_path, start=day, monkeypatch=monkeypatch, captured_at=acquired, published_at=acquired
    )
    delayed = release_rule(day).model_copy(
        update={"available_at": day + timedelta(days=1, hours=17)}
    )
    with pytest.raises(DataFailure, match="unverified"):
        service.verify(task.request.actor_id, pub.scope_id, pub.publication_id, expectation=delayed)
