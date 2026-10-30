"""Session-derived watch controls; fixtures are not deployed timer readiness."""

from uuid import UUID

from test_collection_freshness import NOW
from test_freshness_bridge import setup

from qtrade_data_contracts.freshness_watch import FreshnessWatch, watch_identity


def prepare(tmp_path, monkeypatch):
    client, root, state, calls, access, key = setup(tmp_path, monkeypatch)
    value = FreshnessWatch(
        schema_version=1,
        scope_id=key.scope_id,
        watch_id=watch_identity(key),
        actor_id=access.user_id,
        key=key,
        reference_publication_id=UUID(state["publication"]["publication_id"]),
        revision=1,
        enabled=False,
        interval_seconds=900,
        created_at=NOW,
        updated_at=NOW,
        next_due_at=NOW,
    )
    state["watch"] = value.model_dump(mode="json")
    return client, root, state, calls, access, value


def test_read_uses_scoped_publication_create_requires_manager_and_csrf_dependency(
    tmp_path, monkeypatch
):
    client, root, state, calls, access, value = prepare(tmp_path, monkeypatch)
    response = client.get(root + "/watch")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json() == state["watch"]
    assert calls[-1][1].startswith("/v1/freshness/watches/by-publication/")
    calls.clear()
    assert client.post(root + "/watch").status_code == 403 and not calls
    response = client.post(root + "/watch", headers={"role": "manage"})
    assert response.status_code == 200
    assert calls[-1][0:2] == ("POST", "/v1/freshness/watches")
    assert calls[-1][2]["body"] == {
        "publication_id": str(value.reference_publication_id),
        "interval_seconds": 900,
    }
    assert calls[-1][2]["scope_id"] == value.scope_id
    assert calls[-1][2]["actor_id"] == access.user_id


def test_control_binds_revision_identity_and_strict_boolean(tmp_path, monkeypatch):
    client, root, state, calls, access, value = prepare(tmp_path, monkeypatch)
    path = root.split("/publications/")[0] + f"/freshness/watches/{value.watch_id}/control"
    assert client.post(path, json={"revision": 1, "enabled": True}).status_code == 403
    assert (
        client.post(
            path, headers={"role": "manage"}, json={"revision": True, "enabled": True}
        ).status_code
        == 422
    )
    assert (
        client.post(
            path, headers={"role": "manage"}, json={"revision": 1, "enabled": "true"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            path, headers={"role": "manage"}, json={"revision": 1, "enabled": True}
        ).status_code
        == 503
    )
    state["watch"]["revision"] = 2
    state["watch"]["enabled"] = True
    response = client.post(path, headers={"role": "manage"}, json={"revision": 1, "enabled": True})
    assert response.status_code == 200
    assert calls[-1][2]["body"] == {"revision": 1, "enabled": True}
    state["watch"]["key"]["mapping_version"] = "wrong"
    assert (
        client.post(
            path, headers={"role": "manage"}, json={"revision": 2, "enabled": False}
        ).status_code
        == 503
    )


def test_mismatched_watch_key_not_returned(tmp_path, monkeypatch):
    client, root, state, *_ = prepare(tmp_path, monkeypatch)
    state["watch"]["key"]["mapping_version"] = "other"
    assert client.get(root + "/watch").status_code == 503


def test_later_watch_failure_invalidates_healthy_snapshot_inside_age_ttl(tmp_path, monkeypatch):
    from datetime import timedelta
    from types import SimpleNamespace

    import qtrade_api.collector_freshness as bridge

    client, root, state, _, _, _ = prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(
        bridge, "datetime", SimpleNamespace(now=lambda _: NOW + timedelta(seconds=30))
    )
    state["watch"]["updated_at"] = (NOW + timedelta(seconds=20)).isoformat()
    state["watch"]["last_attempt_at"] = state["watch"]["updated_at"]
    state["watch"]["last_error_code"] = "unavailable"
    response = client.get(root)
    assert response.status_code == 200
    view = response.json()
    assert view["snapshot"]["status"] == "current" and view["stale"] is False
    assert view["watch_issue"] == "unavailable"


def test_older_watch_failure_does_not_override_new_successful_manual_check(tmp_path, monkeypatch):
    from datetime import timedelta

    client, root, state, _, _, _ = prepare(tmp_path, monkeypatch)
    state["watch"]["updated_at"] = (NOW + timedelta(seconds=20)).isoformat()
    state["watch"]["last_attempt_at"] = state["watch"]["updated_at"]
    state["watch"]["last_error_code"] = "unavailable"
    state["snapshot"]["checked_at"] = (NOW + timedelta(seconds=30)).isoformat()
    state["watch"]["updated_at"] = (NOW + timedelta(seconds=40)).isoformat()
    assert client.get(root).json()["watch_issue"] is None
