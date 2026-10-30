"""HTTP controls against isolated real PG, controlled policy, no live readiness claim."""

from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from test_collection_schedules import draft
from test_fair_dispatch_postgres import database as isolated_database
from test_fair_dispatch_postgres import root_database as root_fixture
from test_schedule_policy import PublicationFixture

from qtrade_collector.schedule_routes import register_schedule_routes
from qtrade_data_core.ports import DataFailure
from qtrade_data_core.schedule_policy import DailyPriceSchedulePolicy
from qtrade_data_core.schedule_runner import ScheduleRunner
from qtrade_data_http.client import StaticScopeAuthorizer
from qtrade_data_postgres.schedules import PostgresSchedules

database, root_database = isolated_database, root_fixture


def test_http_schedule_creation_control_roles_scope_and_persistence(database, tmp_path):
    from types import SimpleNamespace

    from qtrade_data_mairui.collection import MaiRuiCollection

    value = draft()
    auth = StaticScopeAuthorizer({f"{value.actor_id}:{value.scope_id}": "manage"})
    store = PostgresSchedules(database)
    app = FastAPI()
    app.state.collection_service = SimpleNamespace(
        plan_guard=SimpleNamespace(max_instruments=10000),
        catalog=lambda: MaiRuiCollection("fixture", cache=tmp_path / "cache").datasets(),
    )
    app.state.schedule_runner = ScheduleRunner(
        auth,
        store,
        DailyPriceSchedulePolicy(PublicationFixture()),
        lambda *args: None,
    )

    @app.exception_handler(DataFailure)
    async def failure(request, error):
        status = {"forbidden": 403, "conflict": 409, "not_found": 404}.get(error.code, 400)
        return JSONResponse(status_code=status, content={"detail": {"code": error.code}})

    register_schedule_routes(app, lambda: (value.actor_id, value.scope_id))
    client = TestClient(app)
    body = value.model_dump(mode="json")
    created = client.post("/v1/schedules", json=body)
    assert created.status_code == 201 and created.json()["state"] == "disabled"
    assert client.post("/v1/schedules", json=body).json() == created.json()
    assert len(client.get("/v1/schedules").json()) == 1
    path = f"/v1/schedules/{value.schedule_id}"
    assert client.post(path + "/enable", json={"revision": "b" * 64}).status_code == 409
    assert (
        client.post(path + "/enable", json={"revision": value.revision}).json()["state"]
        == "enabled"
    )
    assert PostgresSchedules(database).get(value.scope_id, value.schedule_id).state == "enabled"
    auth.grants[f"{value.actor_id}:{value.scope_id}"] = "read"
    assert client.get(path).status_code == 200
    assert client.post(path + "/disable", json={"revision": value.revision}).status_code == 403
    assert client.post("/v1/schedules", json=body).status_code == 403
    auth.grants[f"{value.actor_id}:{value.scope_id}"] = "manage"
    assert (
        client.post(path + "/disable", json={"revision": value.revision}).json()["state"]
        == "disabled"
    )
    assert client.get(f"/v1/schedules/{uuid4()}").status_code == 404
    assert client.post("/v1/schedules", json=body | {"scope_id": str(uuid4())}).status_code == 403
    assert client.post("/v1/schedules", json=body | {"automation_confirmed": 1}).status_code == 422
    assert client.get("/v1/schedules?limit=101").status_code == 422
    assert store.list(uuid4()) == ()
    with pytest.raises(DataFailure):
        store.list(value.scope_id, limit=True)
