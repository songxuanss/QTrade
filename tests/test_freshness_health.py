"""Read-health semantics with controlled evidence, not live supplier observations."""

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_collection_freshness import NOW, evidence, expected, key, point

from qtrade_data_contracts.collection import SourceDataset
from qtrade_data_contracts.freshness_health import FreshnessHealthPage, FreshnessHealthQuery
from qtrade_data_contracts.freshness_watch import FreshnessWatch, watch_identity
from qtrade_data_core.freshness import evaluate
from qtrade_data_core.freshness_health import FreshnessHealth, HealthBatch, HealthRecord
from qtrade_data_core.ports import DataFailure
from qtrade_data_http.client import StaticScopeAuthorizer


def catalog(k, configured=True):
    return (
        SourceDataset(
            source=k.source,
            provider="controlled",
            name="Controlled",
            dataset_kind="bars",
            periods=("1d",),
            instrument_kinds=("stock",),
            markets=("CN",),
            configured=configured,
            evidence="configured",
            mapping_version=k.mapping_version,
        ),
    )


def setup(*, seconds=30, watch=None, snapshot=True, config=True):
    actor, k = uuid4(), watch.key if watch else key()
    value = evaluate(k, expected(), evidence(k, published=point()), NOW) if snapshot else None
    row = HealthRecord(k, "Fixture stock", value, watch, None, "not_applicable")
    repo = SimpleNamespace(
        page=lambda query, consumer: HealthBatch(NOW + timedelta(seconds=seconds), (row,), None)
    )
    auth = StaticScopeAuthorizer({f"{actor}:{k.scope_id}": "read"})
    service = FreshnessHealth(
        auth, repo, lambda: catalog(k, config) if config is not None else (), "qtrade-alerts"
    )
    return actor, k, service, repo, auth


@pytest.mark.parametrize("seconds,status", [(30, "current"), (901, "expired"), (-1, "clock_skew")])
def test_saved_snapshot_ages_and_future_clock_is_not_green(seconds, status):
    actor, k, service, _, _ = setup(seconds=seconds)
    page = service.read(actor, FreshnessHealthQuery(scope_id=k.scope_id))
    assert page.entries[0].effective_status == status
    assert page.entries[0].age_seconds == max(0, seconds)
    assert FreshnessHealthPage.model_validate_json(page.model_dump_json()) == page


@pytest.mark.parametrize("configured", [False, None])
def test_unconfigured_or_unregistered_source_never_borrows_healthy_snapshot(configured):
    actor, k, service, _, _ = setup(config=configured)
    page = service.read(actor, FreshnessHealthQuery(scope_id=k.scope_id))
    assert page.entries[0].snapshot.status == "current"
    assert page.entries[0].effective_status == "source_unconfigured"


def test_watch_without_check_is_unchecked_and_failed_attempt_remains_visible():
    k = key()
    watch = FreshnessWatch(
        schema_version=1,
        scope_id=k.scope_id,
        watch_id=watch_identity(k),
        actor_id=uuid4(),
        key=k,
        reference_publication_id=uuid4(),
        revision=1,
        enabled=False,
        interval_seconds=900,
        created_at=NOW,
        updated_at=NOW,
        next_due_at=NOW,
    )
    actor, _, service, _, _ = setup(watch=watch, snapshot=False)
    assert (
        service.read(actor, FreshnessHealthQuery(scope_id=k.scope_id)).entries[0].effective_status
        == "unchecked"
    )
    failed = watch.model_copy(
        update={
            "updated_at": NOW + timedelta(seconds=20),
            "last_attempt_at": NOW + timedelta(seconds=10),
            "last_error_code": "unavailable",
        }
    )
    actor, _, service, _, _ = setup(watch=failed)
    entry = service.read(actor, FreshnessHealthQuery(scope_id=k.scope_id)).entries[0]
    assert entry.effective_status == "check_failed" and entry.snapshot.status == "current"


def test_revocation_precedes_inventory_access():
    actor, k, service, repo, auth = setup()
    auth.grants.clear()
    repo.page = lambda *args: (_ for _ in ()).throw(AssertionError("unauthorized inventory access"))
    with pytest.raises(DataFailure, match="forbidden"):
        service.read(actor, FreshnessHealthQuery(scope_id=k.scope_id))


def test_wrong_scope_duplicate_or_out_of_cursor_rows_fail_closed():
    actor, k, service, repo, _ = setup()
    query = FreshnessHealthQuery(scope_id=k.scope_id)
    batch = repo.page(query, "qtrade-alerts")
    repo.page = lambda *args: HealthBatch(batch.read_at, batch.records * 2, None)
    with pytest.raises(DataFailure, match="unverified"):
        service.read(actor, query)
    repo.page = lambda *args: batch
    digest = service.read(actor, query).entries[0].key_sha256
    with pytest.raises(DataFailure, match="unverified"):
        service.read(actor, FreshnessHealthQuery(scope_id=k.scope_id, after_key=digest))
    row = batch.records[0]
    other = key()
    repo.page = lambda *args: HealthBatch(
        batch.read_at,
        (
            HealthRecord(
                other,
                row.instrument_name,
                evaluate(other, expected(), evidence(other, published=point()), NOW),
                None,
                None,
                "not_applicable",
            ),
        ),
        None,
    )
    with pytest.raises(DataFailure, match="unverified"):
        service.read(actor, query)
