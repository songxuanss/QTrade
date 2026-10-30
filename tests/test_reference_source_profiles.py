"""Reference evidence discovery is current-authorized and never resolves members."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from qtrade_api.data_universes import universe_router
from qtrade_data_core.ports import DataFailure
from qtrade_data_core.source_profiles import SourceProfileDirectory
from qtrade_data_http.client import StaticScopeAuthorizer
from qtrade_data_mairui.reference import MaiRuiReference
from qtrade_data_mairui.source_profiles import reference_profile


def test_configured_and_unconfigured_evidence_never_claim_historical_intervals():
    for enabled in (False, True):
        value = reference_profile(enabled)
        assert value.source == "mairui.reference" and value.kind == "current_membership"
        assert value.state == ("configured" if enabled else "unconfigured")
        assert value.mapping_version == "mairui.hszg.v1"
        history = next(f for f in value.facts if f.aspect == "history")
        assert "不提供历史有效区间" in history.detail
        assert history.evidence == "sample_verified"
        assert all(f.unblock_condition for f in value.facts if f.evidence == "unknown")


def test_reference_read_current_auth_strict_revalidation_and_no_supplier(monkeypatch):
    actor, scope = uuid4(), uuid4()
    auth = StaticScopeAuthorizer({f"{actor}:{scope}": "read"})
    profile = reference_profile(False)

    def forbidden(*args, **kwargs):
        raise AssertionError("source notes cannot call the reference supplier")

    monkeypatch.setattr(MaiRuiReference, "_rows", forbidden)
    directory = SourceProfileDirectory(auth, (profile,))
    assert directory.read(actor, scope).profiles == (profile,)
    with pytest.raises(DataFailure, match="forbidden"):
        directory.read(actor, uuid4())
    malformed = profile.model_copy(update={"facts": ()})
    with pytest.raises(DataFailure, match="unverified"):
        SourceProfileDirectory(auth, (malformed,)).read(actor, scope)
    auth.grants.clear()
    with pytest.raises(DataFailure, match="forbidden"):
        directory.read(actor, scope)


def test_reference_http_notes_available_without_resolution_and_revoke(monkeypatch):
    actor, scope = uuid4(), uuid4()
    auth = StaticScopeAuthorizer({f"{actor}:{scope}": "read"})

    class Security:
        def workspace(self, permission, mutate=False):
            def check(role: str = Header("read")):
                if role == "denied":
                    raise HTTPException(403)
                return SimpleNamespace(user_id=actor, workspace=SimpleNamespace(workspace_id=scope))

            return check

    app = FastAPI()
    app.include_router(
        universe_router(None, Security(), SourceProfileDirectory(auth, (reference_profile(False),)))
    )
    client = TestClient(app)
    path = f"/api/v1/workspaces/{scope}/data/universes/source-profiles"
    response = client.get(path)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json()["scope_id"] == str(scope)
    assert response.json()["profiles"][0]["state"] == "unconfigured"
    assert client.get(path, headers={"role": "denied"}).status_code == 403
    auth.grants.clear()
    assert client.get(path).status_code == 403
