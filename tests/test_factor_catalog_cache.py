"""Persistent metadata and provider failures are controlled fixtures, not live rights evidence."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event

import httpx
import pytest
from pydantic import ValidationError

from qtrade_data_contracts.collection import FactorDefinition, digest
from qtrade_data_contracts.factor_catalog import FactorCatalogSnapshot
from qtrade_data_core.ports import DataFailure
from qtrade_data_mairui.collection import MaiRuiCollection
from qtrade_data_mairui.factor_catalog import FactorCatalogCache


def definition():
    return FactorDefinition(
        factor_id="pe_ttm",
        name="PE",
        category="valuation",
        unit="unknown",
        description="",
        calc_version="unknown",
        update_frequency="unknown",
        available=True,
    )


def test_saved_catalog_survives_restart_and_does_not_establish_rights(tmp_path):
    cache = FactorCatalogCache(tmp_path)
    cache.save((definition(),))
    before = cache.snapshot(True)
    after = FactorCatalogCache(tmp_path).snapshot(True)
    assert before.definitions == after.definitions
    assert after.status == "fresh" and after.fetched_at == before.fetched_at
    assert after.permission_evidence == "not_established_by_catalog"
    assert (cache.path.stat().st_mode & 0o777) == 0o600
    assert cache.snapshot(False).status == "unconfigured"
    assert cache.snapshot(False).definitions == ()


def test_stale_and_failed_refresh_keep_prior_content_and_timestamp(tmp_path):
    cache = FactorCatalogCache(tmp_path)
    cache.save((definition(),))
    before = cache.snapshot(True)
    assert cache.snapshot(True, datetime.now(UTC) + timedelta(minutes=16)).status == "stale"
    cache.begin()
    assert cache.snapshot(True).refreshing
    cache.failed("timeout")
    after = cache.snapshot(True)
    assert after.status == "stale" and after.error_code == "timeout"
    assert after.content_sha256 == before.content_sha256 and after.fetched_at == before.fetched_at
    assert not after.refreshing


def test_legacy_import_checks_digest_and_preserves_unknown_retrieval_time(tmp_path):
    payload = [definition().model_dump(mode="json")]
    (tmp_path / f"factor-catalog-{digest(payload)}.json").write_text(json.dumps(payload))
    snapshot = FactorCatalogCache(tmp_path).snapshot(True)
    assert snapshot.status == "stale" and snapshot.fetched_at is None
    assert snapshot.definitions == (definition(),)
    for path in tmp_path.glob("*.json"):
        path.write_text(
            json.dumps(
                [definition().model_copy(update={"name": "tampered"}).model_dump(mode="json")]
            )
        )
    assert FactorCatalogCache(tmp_path).snapshot(True).definitions == ()


def test_corrupt_current_snapshot_does_not_fall_back_or_claim_freshness(tmp_path):
    cache = FactorCatalogCache(tmp_path)
    cache.save((definition(),))
    stored = json.loads(cache.path.read_text())
    stored["definitions"][0]["name"] = "tampered"
    cache.path.write_text(json.dumps(stored))
    after = FactorCatalogCache(tmp_path).snapshot(True)
    assert after.status == "unavailable" and after.error_code == "unverified"
    assert after.definitions == ()


@pytest.mark.parametrize("version", [True, "1", 2, None])
def test_snapshot_rejects_unknown_or_coerced_versions(tmp_path, version):
    cache = FactorCatalogCache(tmp_path)
    cache.save((definition(),))
    payload = cache.snapshot(True).model_dump(mode="json")
    payload["schema_version"] = version
    with pytest.raises(ValidationError):
        FactorCatalogSnapshot.model_validate_json(json.dumps(payload))


def test_catalog_reader_returns_during_provider_refresh_and_failure_preserves_legacy(tmp_path):
    payload = [definition().model_dump(mode="json")]
    (tmp_path / f"factor-catalog-{digest(payload)}.json").write_text(json.dumps(payload))
    entered, release = Event(), Event()

    def respond(request):
        entered.set()
        assert release.wait(5)
        raise httpx.ReadTimeout("fixture", request=request)

    provider = MaiRuiCollection(
        "fixture",
        pro=True,
        cache=tmp_path,
        client=httpx.Client(transport=httpx.MockTransport(respond)),
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        refresh = pool.submit(provider.factors)
        try:
            assert entered.wait(2)
            snapshot = pool.submit(provider.factor_catalog_snapshot).result(timeout=2)
            assert snapshot.refreshing and snapshot.status == "stale"
            assert snapshot.definitions == (definition(),)
        finally:
            release.set()
        with pytest.raises(DataFailure, match="timeout"):
            refresh.result(timeout=2)
    assert provider.factor_catalog_snapshot().error_code == "timeout"


def test_fresh_restart_avoids_vendor_call_but_stale_execution_revalidates(tmp_path):
    cache = FactorCatalogCache(tmp_path)
    cache.save((definition(),))
    calls = []

    def respond(request):
        calls.append("request")
        return httpx.Response(403)

    def provider():
        return MaiRuiCollection(
            "fixture",
            pro=True,
            cache=tmp_path,
            client=httpx.Client(transport=httpx.MockTransport(respond)),
        )

    assert provider().factors() == (definition(),)
    assert calls == []
    saved = json.loads(cache.path.read_text())
    saved["fetched_at"] = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    cache.path.write_text(json.dumps(saved))
    stale = provider()
    assert stale.factor_catalog_snapshot().status == "stale"
    with pytest.raises(DataFailure, match="unauthorized"):
        stale.factors()
    assert calls == ["request"]
    assert stale.factor_catalog_snapshot().definitions == (definition(),)


def test_oversized_provider_catalog_is_rejected_and_refresh_state_finishes(tmp_path):
    rows = [
        {"factor_id": f"factor_{i}", "name": "fixture", "category": "fixture"} for i in range(1001)
    ]
    provider = MaiRuiCollection(
        "fixture",
        pro=True,
        cache=tmp_path,
        client=httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=rows))
        ),
    )
    with pytest.raises(DataFailure, match="unverified"):
        provider.factors()
    snapshot = provider.factor_catalog_snapshot()
    assert not snapshot.refreshing and snapshot.error_code == "unverified"
    assert snapshot.definitions == ()


def test_atomic_replace_failure_keeps_previous_disk_and_memory_snapshot(tmp_path, monkeypatch):
    import qtrade_data_mairui.factor_catalog as module

    cache = FactorCatalogCache(tmp_path)
    cache.save((definition(),))
    previous = cache.path.read_bytes()

    def fail(*args):
        raise OSError("fixture storage failure")

    monkeypatch.setattr(module.os, "replace", fail)
    with pytest.raises(OSError):
        cache.save((definition().model_copy(update={"name": "new"}),))
    assert cache.path.read_bytes() == previous
    assert cache.snapshot(True).definitions == (definition(),)
    assert list(tmp_path.glob(".factor-catalog-*")) == []
