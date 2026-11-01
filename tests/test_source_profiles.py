"""Dated evidence is separate from live auth and frozen plan payloads."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_freshness_bridge import setup

from qtrade_data_contracts.source_profiles import ASPECTS, SourceProfileCatalog
from qtrade_data_mairui.collection import MaiRuiCollection
from qtrade_data_mairui.research import MaiRuiFinancial
from qtrade_data_mairui.source_profiles import profiles


def test_complete_scoped_evidence_with_real_references_and_no_plan_mutation(tmp_path):
    provider = MaiRuiCollection(None, cache=tmp_path)
    before = [v.model_dump_json() for v in provider.datasets()]
    financial = MaiRuiFinancial(provider, "income").capabilities()[0].model_dump_json()
    rows = profiles(provider)
    assert len(rows) == 11
    assert all(row.state == "unconfigured" for row in rows if row.mapping_version)
    assert all(row.state == "not_connected" for row in rows if not row.mapping_version)
    rights = next(row for row in rows if row.kind == "rights_terms")
    assert rights.state == "not_connected"
    assert rights.source == "extension.rights_terms"
    assert any("hscp/jnpg" in (v.unblock_condition or "") for v in rights.facts)
    root = Path(__file__).resolve().parents[1]
    for row in rows:
        assert {v.aspect for v in row.facts} == set(ASPECTS)
        for v in row.facts:
            assert (root / v.reference).is_file()
            assert v.evidence != "unknown" or v.unblock_condition
    assert before == [v.model_dump_json() for v in provider.datasets()]
    assert financial == MaiRuiFinancial(provider, "income").capabilities()[0].model_dump_json()
    page = SourceProfileCatalog(schema_version=1, scope_id=uuid4(), profiles=rows)
    assert SourceProfileCatalog.model_validate_json(page.model_dump_json()) == page
    bad = page.model_dump(mode="json")
    bad["profiles"].append(bad["profiles"][0])
    with pytest.raises(ValidationError):
        SourceProfileCatalog.model_validate_json(json.dumps(bad))
    fact = rows[0].facts[-1]
    invalid = fact.model_dump(mode="json") | {"unblock_condition": None}
    with pytest.raises(ValidationError):
        type(fact).model_validate_json(json.dumps(invalid))
    for reference in ("https://private.invalid/token", "docs/../private.md"):
        invalid = fact.model_dump(mode="json") | {"reference": reference}
        with pytest.raises(ValidationError):
            type(fact).model_validate_json(json.dumps(invalid))
    bad = rows[0].model_dump(mode="json")
    bad["facts"][1] = bad["facts"][0]
    with pytest.raises(ValidationError):
        type(rows[0]).model_validate_json(json.dumps(bad))


def test_session_bridge_scope_permission_and_malformed_catalog(tmp_path, monkeypatch):
    client, root, state, calls, access, _ = setup(tmp_path, monkeypatch)
    state["source_profiles"] = SourceProfileCatalog(
        schema_version=1,
        scope_id=access.workspace.workspace_id,
        profiles=profiles(MaiRuiCollection(None, cache=tmp_path)),
    ).model_dump(mode="json")
    path = root.split("/publications/")[0] + "/source-profiles"
    response = client.get(path)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert calls[-1][0:2] == ("GET", "/v1/source-profiles")
    assert calls[-1][2]["actor_id"] == access.user_id
    calls.clear()
    assert client.get(path, headers={"role": "denied"}).status_code == 403
    assert not calls
    state["source_profiles"]["scope_id"] = str(uuid4())
    assert client.get(path).status_code == 503
    state["source_profiles"]["profiles"][0]["state"] = "available"
    assert client.get(path).status_code == 503


def test_collector_service_identity_read_revocation_no_provider_calls(tmp_path, monkeypatch):
    actor, scope = uuid4(), uuid4()
    token = "fixture-service-token-" + "x" * 32
    for key, value in {
        "COLLECTOR_SERVICE_TOKEN": token,
        "DATA_COLLECTION_DATABASE_URL": "postgresql://unused.invalid/unused",
        "COLLECTOR_ARCHIVE_ROOT": str(tmp_path),
        "COLLECTOR_SCOPE_GRANTS_JSON": json.dumps({f"{actor}:{scope}": "read"}),
    }.items():
        monkeypatch.setenv(key, value)
    for key in ("COLLECTOR_CONFIG_FILE", "COLLECTOR_AUTHORIZER_URL", "MAIRUI_LICENCE"):
        monkeypatch.delenv(key, raising=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("source profile read must not call supplier")

    monkeypatch.setattr(MaiRuiCollection, "_get", forbidden)
    from qtrade_collector.main import create_app

    app = create_app()
    client = TestClient(app)  # No lifespan: no database or background work needed.
    headers = {
        "Authorization": "Bearer " + token,
        "X-Data-Actor": str(actor),
        "X-Data-Scope": str(scope),
    }
    assert client.get("/v1/source-profiles").status_code == 401
    response = client.get("/v1/source-profiles", headers=headers)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert all(r["state"] != "configured" for r in response.json()["profiles"])
    app.state.collection_service.auth.grants.clear()
    assert client.get("/v1/source-profiles", headers=headers).status_code == 403
