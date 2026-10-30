from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated
from uuid import uuid4

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from qtrade_api.calendars import calendar_router
from qtrade_api.capabilities import capability_router
from qtrade_api.data_management import data_management_router
from qtrade_api.instruments import instrument_router
from qtrade_api.market_data import market_data_router
from qtrade_api.market_watch import market_watch_router
from qtrade_api.portfolio import portfolio_router
from qtrade_api.realtime import realtime_router
from qtrade_api.security import ApiSecurity
from qtrade_api.workspaces import workspace_router
from qtrade_application.calendars import CalendarService
from qtrade_application.capabilities import CapabilityRegistry
from qtrade_application.data_management import DataManagementService
from qtrade_application.instruments import InstrumentService
from qtrade_application.market_data import MarketDataService
from qtrade_application.market_watch import MarketWatchService
from qtrade_application.portfolio import PortfolioService
from qtrade_application.realtime import RealtimeMarketDataService
from qtrade_application.sessions import SessionService, TokenSet
from qtrade_application.workspaces import WorkspaceService
from qtrade_crypto.tokens import TokenCipher
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.capabilities import AdapterHealth, AdapterKind, CapabilityDescriptor
from qtrade_domain.datasets import PublishedDataset
from qtrade_domain.identity import Membership, Role, User, Workspace
from qtrade_domain.identity_port import LoginIdentity
from qtrade_domain.ingestion import CollectionRequest
from qtrade_domain.portfolio import PortfolioPosition, PortfolioSnapshot
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.quality import QualityIssue
from qtrade_domain.realtime import MarketStreamHealth, MarketStreamKey
from qtrade_mock.audit import MockAuditStore
from qtrade_mock.batches import MockBatchRepository
from qtrade_mock.calendars import MockCalendarStore
from qtrade_mock.datasets import MockDatasetRepository
from qtrade_mock.ingestion import MockIngestionRepository
from qtrade_mock.instruments import MockInstrumentStore
from qtrade_mock.portfolio import MockPortfolioStore
from qtrade_mock.ports import MockBroker, MockMarketData
from qtrade_mock.quality import MockQualityIssueStore
from qtrade_mock.realtime import MockRealtimeHealthStore
from qtrade_mock.sessions import MockSessionStore
from qtrade_mock.workspaces import MockWorkspaceStore
from qtrade_runtime.cookies import SessionCookie


@pytest.fixture
def setup():
    now = datetime.now(UTC)
    workspace = Workspace(uuid4(), "Team", "organization", None, False, False, now)
    other = replace(workspace, workspace_id=uuid4(), name="Private")
    users = {
        role: User(uuid4(), "https://sso.test", role.value, role.value, None, False, now, now)
        for role in Role
    }
    outsider = replace(users[Role.VIEWER], user_id=uuid4(), subject="outsider")
    members = tuple(
        Membership(workspace.workspace_id, user.user_id, role, True, None, now, now)
        for role, user in users.items()
    )
    external = Membership(other.workspace_id, outsider.user_id, Role.ADMIN, True, None, now, now)
    store = MockWorkspaceStore(
        tuple(users.values()) + (outsider,), (workspace, other), members + (external,)
    )
    service = WorkspaceService(store)
    sessions = SessionService(MockSessionStore(), TokenCipher({"test": b"a" * 32}, primary="test"))
    cookies = {
        role: sessions.create(
            LoginIdentity(user, workspace, next(m for m in members if m.user_id == user.user_id)),
            TokenSet("access"),
        )
        for role, user in users.items()
    }
    cookies["outsider"] = sessions.create(
        LoginIdentity(outsider, other, external), TokenSet("access")
    )
    app = FastAPI()
    audit = MockAuditStore()
    app.state.audit = audit
    security = ApiSecurity(
        sessions,
        service,
        SessionCookie("test", insecure_local=True),
        audit,
        "https://qtrade.test",
    )
    app.include_router(workspace_router(service, security))
    calendars = CalendarService(MockCalendarStore())
    app.include_router(calendar_router(calendars, security))
    market = MarketDataService(MockMarketData())
    app.state.market_data = market
    app.include_router(market_data_router(market, security))
    datasets = MockDatasetRepository()
    app.state.datasets = datasets
    app.include_router(market_watch_router(MarketWatchService(datasets, market), security))
    health_store = MockRealtimeHealthStore(store.access)
    app.state.market_stream_health = health_store
    app.include_router(realtime_router(RealtimeMarketDataService(health_store, ()), security))
    portfolio_store = MockPortfolioStore()
    app.state.portfolio_store = portfolio_store
    app.include_router(portfolio_router(PortfolioService(portfolio_store), security))
    task_store = MockIngestionRepository(store.access)
    batch_store = MockBatchRepository()
    quality_store = MockQualityIssueStore(store.access)
    app.state.ingestion_store = task_store
    app.state.batch_store = batch_store
    app.state.quality_store = quality_store
    app.state.data_management = DataManagementService(task_store, batch_store, quality_store)
    app.include_router(data_management_router(app.state.data_management, security, audit))
    instrument_store = MockInstrumentStore()
    app.state.instruments = instrument_store
    app.include_router(instrument_router(InstrumentService(instrument_store), security, calendars))
    registry = CapabilityRegistry[object]()
    app.state.capabilities = registry
    registry.register(
        CapabilityDescriptor(
            "paper",
            "Test paper broker",
            AdapterKind.BROKER,
            markets=("simulation",),
            order_types=("market", "limit"),
            trading_sessions=("continuous",),
            health=AdapterHealth.HEALTHY,
        ),
        MockBroker(),
    )
    app.include_router(capability_router(registry, security))
    writes = []

    # Exercise the reusable guard as a caller would; strategy persistence is task 7.1.
    @app.post("/api/v1/workspaces/{workspace_id}/test-strategy")
    def strategy(
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.STRATEGY_WRITE, mutate=True))
        ],
    ):
        writes.append(access.workspace.workspace_id)
        return {"created": True}

    return TestClient(app), store, service, users, workspace, other, cookies, writes


def request_as(setup, role, method, path, *, csrf=True, json=None):
    client, _, _, _, _, _, cookies, _ = setup
    created = cookies[role]
    client.cookies.set("qtrade-session", created.cookie)
    headers = {"X-CSRF-Token": created.csrf} if csrf else {}
    return client.request(method, path, headers=headers, json=json)


def test_capability_api_is_scoped_and_rejects_live_and_unsupported_orders(setup):
    client, _, _, _, workspace, other, _, _ = setup
    root = f"/api/v1/workspaces/{workspace.workspace_id}/adapters"
    assert client.get(root).status_code == 401
    assert request_as(setup, Role.VIEWER, "GET", root).status_code == 200
    assert (
        request_as(
            setup, Role.ADMIN, "GET", f"/api/v1/workspaces/{other.workspace_id}/adapters"
        ).status_code
        == 404
    )
    response = request_as(setup, Role.VIEWER, "GET", root)
    assert response.json()[0]["live"] is False
    assert response.json()[0]["order_types"] == ["market", "limit"]
    path = root + "/paper/availability?kind=broker&market=simulation"
    assert request_as(setup, Role.VIEWER, "GET", path + "&order_type=limit").status_code == 200
    for option in [
        "order_type=stop_limit",
        "live=true",
        "trading_session=extended",
        "granularity=1m",
    ]:
        assert request_as(setup, Role.VIEWER, "GET", path + "&" + option).status_code == 422
    client.app.state.capabilities.health("paper", AdapterHealth.DEGRADED)
    assert request_as(setup, Role.VIEWER, "GET", path).status_code == 503
    assert request_as(setup, Role.VIEWER, "GET", root).json()[0]["health"] == "degraded"
    client.app.state.capabilities.health("paper", AdapterHealth.HEALTHY)
    assert request_as(setup, Role.VIEWER, "GET", path).status_code == 200


def test_instrument_http_import_query_conflict_and_denied_mutations(setup):
    import json
    from pathlib import Path

    client, _, _, _, workspace, other, _, _ = setup
    item = json.loads((Path(__file__).parent / "fixtures/contracts/Instrument.json").read_text())
    item["workspace_id"] = str(workspace.workspace_id)
    item["instrument_id"] = "MOCK:ABC/DEF"
    root = f"/api/v1/workspaces/{workspace.workspace_id}/instruments"
    payload = {"instruments": [item]}
    assert client.get(root).status_code == 401
    assert (
        request_as(
            setup, Role.ADMIN, "POST", root + "/import", csrf=False, json=payload
        ).status_code
        == 403
    )
    assert request_as(setup, Role.VIEWER, "POST", root + "/import", json=payload).status_code == 403
    assert request_as(setup, Role.ADMIN, "GET", root).json()["instruments"] == []
    response = request_as(setup, Role.ADMIN, "POST", root + "/import", json=payload)
    assert response.status_code == 200 and response.json()[0]["status"] == "created"
    assert (
        request_as(setup, Role.ADMIN, "POST", root + "/import", json=payload).json()[0]["status"]
        == "unchanged"
    )
    queried = request_as(setup, Role.VIEWER, "GET", root + "?market=mock&exchange=MOCK&name=abc")
    assert queried.json()["instruments"] == [item]
    assert request_as(setup, Role.VIEWER, "GET", root + "/MOCK:ABC/DEF").json() == item
    assert request_as(setup, Role.VIEWER, "GET", root + "/missing").status_code == 404
    assert (
        request_as(
            setup, Role.ADMIN, "GET", f"/api/v1/workspaces/{other.workspace_id}/instruments"
        ).status_code
        == 404
    )
    proposed = {**item, "tick_size": "0.02", "specification_version": 2}
    conflict = request_as(
        setup, Role.ADMIN, "POST", root + "/import", json={"instruments": [proposed]}
    )
    assert conflict.json()[0]["status"] == "conflict"
    (issue,) = request_as(setup, Role.VIEWER, "GET", root + "/conflicts").json()
    assert issue["existing"] == item and issue["proposed"] == proposed
    assert issue["issue_id"] == conflict.json()[0]["issue_id"] and issue["status"] == "pending"
    assert request_as(setup, Role.VIEWER, "GET", root).json()["instruments"] == [item]


def test_instrument_http_invalid_payload_has_safe_errors_and_no_partial_data(setup):
    client, _, _, _, workspace, _, cookies, _ = setup
    root = f"/api/v1/workspaces/{workspace.workspace_id}/instruments"
    for payload in [
        {"instruments": []},
        {"instruments": [{"access_token": "do-not-echo"}]},
        {"instruments": ["do-not-echo"]},
        {"unexpected": "do-not-echo"},
    ]:
        response = request_as(setup, Role.ADMIN, "POST", root + "/import", json=payload)
        assert response.status_code == 400 and "do-not-echo" not in response.text
        assert response.headers["Cache-Control"] == "no-store"
    client.cookies.set("qtrade-session", cookies[Role.ADMIN].cookie)
    response = client.post(
        root + "/import", content=b"x" * 500_001, headers={"X-CSRF-Token": cookies[Role.ADMIN].csrf}
    )
    assert response.status_code == 413
    assert request_as(setup, Role.ADMIN, "GET", root).json()["instruments"] == []


def test_unauthenticated_and_cross_workspace_have_no_resource_data(setup):
    client, _, _, _, workspace, other, _, _ = setup
    root = "/api/v1/workspaces/"
    assert client.get(root + str(workspace.workspace_id)).status_code == 401
    for role, denied in [(Role.ADMIN, other.workspace_id), ("outsider", workspace.workspace_id)]:
        response = request_as(setup, role, "GET", root + str(denied))
        assert response.status_code == 404
        assert "Private" not in response.text and "Team" not in response.text
        assert response.headers["Cache-Control"] == "no-store"
    response = request_as(setup, Role.VIEWER, "GET", root.rstrip("/"))
    assert [item["workspace_id"] for item in response.json()] == [str(workspace.workspace_id)]
    assert response.json()[0]["permissions"] == ["workspace.read"]


@pytest.mark.parametrize("role", list(Role))
def test_each_role_reads_own_workspace_but_only_admin_manages_members(setup, role):
    _, store, _, users, workspace, _, _, _ = setup
    path = f"/api/v1/workspaces/{workspace.workspace_id}"
    assert request_as(setup, role, "GET", path).status_code == 200
    assert request_as(setup, role, "GET", path + "/members").status_code == (
        200 if role == Role.ADMIN else 403
    )
    target = users[Role.VIEWER].user_id
    before = store.memberships[(workspace.workspace_id, target)]
    response = request_as(
        setup, role, "PUT", path + f"/members/{target}", json={"role": "analyst", "active": True}
    )
    assert response.status_code == (200 if role == Role.ADMIN else 403)
    if role != Role.ADMIN:
        assert store.memberships[(workspace.workspace_id, target)] == before
    else:
        assert response.json()["role"] == "analyst"


def test_viewer_strategy_write_rejected_before_handler_and_analyst_allowed(setup):
    client, _, _, _, workspace, _, cookies, writes = setup
    path = f"/api/v1/workspaces/{workspace.workspace_id}/test-strategy"
    assert request_as(setup, Role.VIEWER, "POST", path).status_code == 403
    assert writes == []
    assert request_as(setup, Role.ADMIN, "POST", path, csrf=False).status_code == 403
    client.cookies.set("qtrade-session", cookies[Role.ADMIN].cookie)
    assert client.post(path, headers={"X-CSRF-Token": cookies[Role.VIEWER].csrf}).status_code == 403
    assert writes == []
    assert request_as(setup, Role.ANALYST, "POST", path).status_code == 200
    assert writes == [workspace.workspace_id]
    assert request_as(setup, Role.ADMIN, "POST", path).status_code == 200
    assert writes == [workspace.workspace_id, workspace.workspace_id]


def test_forged_browser_write_and_cross_session_csrf_do_not_mutate(setup):
    client, store, _, users, workspace, _, cookies, _ = setup
    path = f"/api/v1/workspaces/{workspace.workspace_id}/members/{users[Role.VIEWER].user_id}"
    before = dict(store.memberships)
    payload = {"role": "admin", "active": True}
    assert request_as(setup, Role.ADMIN, "PUT", path, csrf=False, json=payload).status_code == 403
    client.cookies.set("qtrade-session", cookies[Role.ADMIN].cookie)
    assert (
        client.put(
            path, json=payload, headers={"X-CSRF-Token": cookies[Role.VIEWER].csrf}
        ).status_code
        == 403
    )
    assert client.put(path, data={"role": "admin", "active": "true"}).status_code == 403
    assert store.memberships == before
    records = client.app.state.audit.records
    assert len(records) == 3
    assert all(
        record.actor_id == users[Role.ADMIN].user_id
        and record.workspace_id == workspace.workspace_id
        and record.action == "permission.denied"
        and record.result == "denied"
        and record.details.reason == ErrorCode.UNAUTHORIZED
        and record.details.permission == Permission.MEMBER_MANAGE
        and record.details.target_workspace_id == workspace.workspace_id
        for record in records
    )


def test_live_role_disable_archive_and_object_scope_are_rechecked(setup):
    _, store, service, users, workspace, other, _, _ = setup
    actor = users[Role.ANALYST].user_id
    with pytest.raises(PortError) as cross:
        service.authorize(
            actor, workspace.workspace_id, Permission.READ, object_workspace_id=other.workspace_id
        )
    assert cross.value.code == ErrorCode.NOT_FOUND
    key = (workspace.workspace_id, actor)
    store.memberships[key] = replace(store.memberships[key], role=Role.VIEWER)
    with pytest.raises(PortError):
        service.authorize(actor, workspace.workspace_id, Permission.STRATEGY_WRITE)
    store.users[actor] = replace(store.users[actor], disabled=True)
    assert service.accessible(actor) == ()
    assert (
        request_as(
            setup, Role.ANALYST, "GET", f"/api/v1/workspaces/{workspace.workspace_id}"
        ).status_code
        == 404
    )
    store.workspaces[workspace.workspace_id] = replace(workspace, archived=True)
    assert (
        request_as(
            setup, Role.ADMIN, "GET", f"/api/v1/workspaces/{workspace.workspace_id}"
        ).status_code
        == 404
    )


def test_last_admin_and_invalid_payloads_cannot_orphan_workspace(setup):
    _, store, _, users, workspace, _, _, _ = setup
    path = f"/api/v1/workspaces/{workspace.workspace_id}/members/{users[Role.ADMIN].user_id}"
    before = dict(store.memberships)
    assert (
        request_as(
            setup, Role.ADMIN, "PUT", path, json={"role": "viewer", "active": True}
        ).status_code
        == 409
    )
    for payload in [
        {"role": "owner", "active": True},
        {"role": "admin", "active": "true"},
        {"role": "admin", "active": True, "scope": "superuser"},
    ]:
        assert request_as(setup, Role.ADMIN, "PUT", path, json=payload).status_code == 422
    assert store.memberships == before


@pytest.mark.parametrize(
    "role,allowed,denied",
    [
        (Role.VIEWER, [Permission.READ], [p for p in Permission if p != Permission.READ]),
        (
            Role.ANALYST,
            [
                Permission.STRATEGY_WRITE,
                Permission.BACKTEST_RUN,
                Permission.PAPER_START,
                Permission.RUNTIME_CONTROL,
                Permission.ALERT_RULE_WRITE,
            ],
            [Permission.ORDER_SUBMIT, Permission.LIVE_APPROVE, Permission.MEMBER_MANAGE],
        ),
        (
            Role.TRADER,
            [
                Permission.ORDER_SUBMIT,
                Permission.LIVE_START,
                Permission.POSITION_CLOSE,
                Permission.RUNTIME_CONTROL,
            ],
            [Permission.LIVE_APPROVE, Permission.CREDENTIAL_MANAGE, Permission.STRATEGY_WRITE],
        ),
        (
            Role.RISK_MANAGER,
            [Permission.LIVE_APPROVE, Permission.RISK_CONFIGURE, Permission.KILL_SWITCH],
            [Permission.ORDER_SUBMIT, Permission.MEMBER_MANAGE],
        ),
        (
            Role.ADMIN,
            [
                Permission.MEMBER_MANAGE,
                Permission.CREDENTIAL_MANAGE,
                Permission.SYSTEM_MANAGE,
                Permission.DATA_MANAGE,
                Permission.STRATEGY_ARCHIVE,
                Permission.STRATEGY_WRITE,
                Permission.BACKTEST_RUN,
                Permission.PAPER_START,
                Permission.RUNTIME_CONTROL,
                Permission.ORDER_SUBMIT,
                Permission.LIVE_START,
                Permission.KILL_SWITCH,
            ],
            [Permission.LIVE_APPROVE],
        ),
    ],
)
def test_roles_are_explicit_nonhierarchical_grants(setup, role, allowed, denied):
    _, _, service, users, workspace, _, _, _ = setup
    for permission in allowed:
        service.authorize(users[role].user_id, workspace.workspace_id, permission)
    for permission in denied:
        with pytest.raises(PortError) as failure:
            service.authorize(users[role].user_id, workspace.workspace_id, permission)
        assert failure.value.code == ErrorCode.REJECTED


def test_calendar_http_scope_versions_sessions_state_and_validation(setup):
    import json
    from pathlib import Path

    client, _, _, _, workspace, other, _, _ = setup
    root = f"/api/v1/workspaces/{workspace.workspace_id}/calendars"
    item = json.loads(
        (Path(__file__).parent / "fixtures/contracts/CalendarSchedule.json").read_text()
    )
    item["workspace_id"] = str(workspace.workspace_id)
    assert client.post(root + "/import", json=item).status_code == 401
    assert request_as(setup, Role.VIEWER, "POST", root + "/import", json=item).status_code == 403
    assert (
        request_as(setup, Role.ADMIN, "POST", root + "/import", csrf=False, json=item).status_code
        == 403
    )
    assert request_as(setup, Role.ADMIN, "POST", root + "/import", json=item).status_code == 200
    query = "?calendar_id=" + item["calendar_id"]
    state = root + "/state" + query + "&at=2026-05-07T14:00:00Z"
    result = request_as(setup, Role.VIEWER, "GET", state)
    assert result.status_code == 200 and result.json()["is_open"] is True
    assert result.json()["active_session"]["opens_at"] == "2026-05-07T13:30:00Z"
    assert (
        request_as(setup, Role.VIEWER, "GET", state.replace("05-07", "05-08")).json()["is_open"]
        is False
    )
    sessions = root + "/sessions" + query + "&start=2026-05-07&end=2026-05-09"
    assert len(request_as(setup, Role.VIEWER, "GET", sessions).json()) == 1
    assert (
        request_as(
            setup, Role.VIEWER, "GET", state.replace("2026-05-07T14:00:00Z", "2026-05-07T14:00:00")
        ).status_code
        == 400
    )
    assert (
        request_as(setup, Role.VIEWER, "GET", state.replace("2026-05", "2027-05")).status_code
        == 503
    )
    assert (
        request_as(
            setup,
            Role.ADMIN,
            "POST",
            root + "/import",
            json={**item, "workspace_id": str(other.workspace_id)},
        ).status_code
        == 404
    )
    assert (
        request_as(
            setup,
            Role.ADMIN,
            "GET",
            state.replace(str(workspace.workspace_id), str(other.workspace_id)),
        ).status_code
        == 404
    )
    assert (
        request_as(
            setup,
            Role.ADMIN,
            "POST",
            root + "/import",
            json={**item, "calendar_version": 2, "weekdays": []},
        ).status_code
        == 200
    )
    assert request_as(setup, Role.VIEWER, "GET", state).json()["is_open"] is False
    assert request_as(setup, Role.VIEWER, "GET", state + "&version=1").json()["is_open"] is True
    assert (
        request_as(
            setup, Role.ADMIN, "POST", root + "/import", json={**item, "weekdays": []}
        ).status_code
        == 409
    )


def test_instrument_market_state_uses_declared_calendar_and_timezone(setup):
    import json
    from pathlib import Path

    _, _, _, _, workspace, _, _, _ = setup
    root = f"/api/v1/workspaces/{workspace.workspace_id}"
    fixtures = Path(__file__).parent / "fixtures/contracts"
    calendar = json.loads((fixtures / "CalendarSchedule.json").read_text())
    instrument = json.loads((fixtures / "Instrument.json").read_text())
    for item in (calendar, instrument):
        item["workspace_id"] = str(workspace.workspace_id)
    instrument["calendar_id"] = calendar["calendar_id"]
    instrument["timezone"] = calendar["timezone"]
    assert (
        request_as(
            setup,
            Role.ADMIN,
            "POST",
            root + "/instruments/import",
            json={"instruments": [instrument]},
        ).status_code
        == 200
    )
    path = (
        root
        + "/instruments/market-state?instrument_id="
        + instrument["instrument_id"]
        + "&at=2026-05-07T14:00:00Z"
    )
    assert request_as(setup, Role.VIEWER, "GET", path).status_code == 404
    assert (
        request_as(setup, Role.ADMIN, "POST", root + "/calendars/import", json=calendar).status_code
        == 200
    )
    assert request_as(setup, Role.VIEWER, "GET", path).json()["is_open"] is True
    calendar["calendar_version"] = 2
    calendar["timezone"] = "Asia/Tokyo"
    assert (
        request_as(setup, Role.ADMIN, "POST", root + "/calendars/import", json=calendar).status_code
        == 200
    )
    assert request_as(setup, Role.VIEWER, "GET", path).status_code == 503
    assert request_as(setup, Role.VIEWER, "GET", path + "&version=1").json()["is_open"] is True
    instrument["instrument_id"] = "disabled"
    instrument["enabled"] = False
    assert (
        request_as(
            setup,
            Role.ADMIN,
            "POST",
            root + "/instruments/import",
            json={"instruments": [instrument]},
        ).status_code
        == 200
    )
    assert (
        request_as(
            setup,
            Role.VIEWER,
            "GET",
            path.replace(
                "instrument_id="
                + json.loads((fixtures / "Instrument.json").read_text())["instrument_id"],
                "instrument_id=disabled",
            ),
        ).status_code
        == 403
    )


def test_market_bar_http_scope_permissions_pagination_and_boundaries(setup):
    import json
    from pathlib import Path
    from uuid import uuid4

    client, _, _, _, workspace, other, _, _ = setup
    item = json.loads((Path(__file__).parent / "fixtures/contracts/Bar.json").read_text())
    item["workspace_id"] = str(workspace.workspace_id)
    root = f"/api/v1/workspaces/{workspace.workspace_id}/datasets/{item['dataset_id']}/bars"
    query = (
        root + "?instrument_id=MOCK:ABC&granularity=1m&start=2026-10-01T00:00:00Z"
        "&end=2026-10-01T00:02:00Z&limit=1"
    )
    assert client.get(query).status_code == 401
    assert request_as(setup, Role.VIEWER, "POST", root, json={"bars": [item]}).status_code == 403
    assert (
        request_as(setup, Role.ADMIN, "POST", root, csrf=False, json={"bars": [item]}).status_code
        == 403
    )
    second = {**item, "timestamp": "2026-10-01T00:01:00Z"}
    payload = {"bars": [second, item]}
    assert request_as(setup, Role.ADMIN, "POST", root, json=payload).status_code == 200
    assert request_as(setup, Role.ADMIN, "POST", root, json=payload).status_code == 200
    assert request_as(setup, Role.ADMIN, "POST", root, json={"bars": [item]}).status_code == 409
    page = request_as(setup, Role.VIEWER, "GET", query).json()
    assert page["timezone"] == "UTC" and page["bars"] == [item]
    assert request_as(setup, Role.VIEWER, "GET", query + "&cursor=" + page["next_cursor"]).json()[
        "bars"
    ] == [second]
    assert (
        request_as(setup, Role.VIEWER, "GET", query.replace("00:02", "00:01")).json()["next_cursor"]
        is None
    )
    assert (
        request_as(setup, Role.VIEWER, "GET", query.replace("MOCK:ABC", "missing")).json()["bars"]
        == []
    )
    assert (
        request_as(
            setup,
            Role.ADMIN,
            "GET",
            query.replace(str(workspace.workspace_id), str(other.workspace_id)),
        ).status_code
        == 404
    )
    assert (
        request_as(
            setup, Role.VIEWER, "GET", query.replace(item["dataset_id"], str(uuid4()))
        ).status_code
        == 404
    )
    assert (
        request_as(setup, Role.VIEWER, "GET", query.replace("00:00:00Z", "00:00:00")).status_code
        == 400
    )
    assert request_as(setup, Role.VIEWER, "GET", query + "&cursor=invalid").status_code == 400


def test_market_bar_http_invalid_payload_safe_error_and_empty_snapshot(setup):
    import json
    from pathlib import Path
    from uuid import uuid4

    _, _, _, _, workspace, _, _, _ = setup
    item = json.loads((Path(__file__).parent / "fixtures/contracts/Bar.json").read_text())
    item["workspace_id"] = str(workspace.workspace_id)
    root = f"/api/v1/workspaces/{workspace.workspace_id}/datasets/{item['dataset_id']}/bars"
    for payload in (
        {"bars": [item, item]},
        {"bars": [{**item, "workspace_id": str(uuid4())}]},
        {"bars": [{**item, "dataset_id": str(uuid4())}]},
        {"bars": [{**item, "secret": "private-sentinel"}]},
        {"bars": [{**item, "open": 10.0}]},
        {"bars": [item], "extra": "private-sentinel"},
        {"bars": "private-sentinel"},
    ):
        response = request_as(setup, Role.ADMIN, "POST", root, json=payload)
        assert response.status_code == 400 and "private-sentinel" not in response.text
    assert request_as(setup, Role.ADMIN, "POST", root, json={"bars": []}).status_code == 200
    query = (
        root + "?instrument_id=MOCK:ABC&granularity=1m&start=2026-10-01T00:00:00Z"
        "&end=2026-10-01T00:02:00Z"
    )
    assert request_as(setup, Role.VIEWER, "GET", query).json()["bars"] == []


def test_market_watch_returns_latest_scoped_bars_for_selected_instruments(setup):
    from decimal import Decimal

    from qtrade_contracts import Bar, MarketDataset

    client, _, _, users, workspace, other, _, _ = setup
    now = datetime.now(UTC).replace(microsecond=0)
    dataset_id, batch_id = uuid4(), uuid4()
    metadata = MarketDataset(
        schema_version=1,
        workspace_id=workspace.workspace_id,
        dataset_id=dataset_id,
        version=1,
        created_at=now,
        batch_ids=(batch_id,),
        instrument_ids=("MOCK:ABC",),
        granularity="1m",
        start=now - timedelta(hours=1),
        end=now + timedelta(days=30),
        transformation_version="test.v1",
        quality_report_key="datasets/report.json",
        content_sha256="a" * 64,
    )
    client.app.state.datasets.put(
        users[Role.ADMIN].user_id,
        PublishedDataset(metadata, "b" * 64, None, "c" * 64),
    )
    bars = tuple(
        Bar(
            schema_version=1,
            workspace_id=workspace.workspace_id,
            dataset_id=dataset_id,
            instrument_id="MOCK:ABC",
            specification_version=1,
            timestamp=now - timedelta(minutes=minutes),
            granularity="1m",
            open=Decimal(price),
            high=Decimal(price),
            low=Decimal(price),
            close=Decimal(price),
            volume=Decimal(10),
            batch_id=batch_id,
            quality="valid",
        )
        for minutes, price in ((10, "99"), (2, "100"), (1, "101"))
    )
    client.app.state.market_data.write_bars(
        WorkspaceAccess(workspace, users[Role.ADMIN].user_id, Role.ADMIN), dataset_id, bars
    )
    # The target publication must remain visible after >200 unrelated newer datasets.
    for index in range(205):
        unrelated = metadata.model_copy(
            update={
                "dataset_id": uuid4(),
                "created_at": now + timedelta(seconds=index + 1),
                "instrument_ids": ("MOCK:OTHER",),
            }
        )
        client.app.state.datasets.put(
            users[Role.ADMIN].user_id, PublishedDataset(unrelated, "b" * 64, None, "c" * 64)
        )
    root = f"/api/v1/workspaces/{workspace.workspace_id}/market-watch"
    query = root + "?instrument_id=MOCK%3AABC&instrument_id=MOCK%3AMISSING"
    assert client.get(root).status_code == 401
    response = request_as(setup, Role.VIEWER, "GET", query)
    assert response.status_code == 200
    first, second = response.json()
    assert first["instrument_id"] == "MOCK:ABC"
    assert first["available_granularities"] == ["1m"]
    assert first["latest"]["close"] == "101"
    assert first["previous"]["close"] == "100"
    assert second["latest"] is None
    chart = request_as(
        setup,
        Role.VIEWER,
        "GET",
        root + "/chart?instrument_id=MOCK%3AABC&granularity=1m&days=1",
    )
    assert chart.status_code == 200
    assert [item["close"] for item in chart.json()["bars"]] == ["99", "100", "101"]
    assert chart.json()["indicators"]["ma"] == [None, None, None]
    # Same instrument/period can have newer observations in another run. A
    # frozen result link must retain its requested dataset instead of merging it.
    newer_id = uuid4()
    newer_meta = metadata.model_copy(
        update={
            "dataset_id": newer_id,
            "created_at": now + timedelta(days=1),
            "start": now + timedelta(days=1),
            "end": now + timedelta(days=2),
        }
    )
    client.app.state.datasets.put(
        users[Role.ADMIN].user_id, PublishedDataset(newer_meta, "b" * 64, None, "c" * 64)
    )
    newer_bar = bars[-1].model_copy(
        update={
            "dataset_id": newer_id,
            "timestamp": now + timedelta(days=1),
            "open": Decimal("999"),
            "high": Decimal("999"),
            "low": Decimal("999"),
            "close": Decimal("999"),
        }
    )
    client.app.state.market_data.write_bars(
        WorkspaceAccess(workspace, users[Role.ADMIN].user_id, Role.ADMIN), newer_id, (newer_bar,)
    )
    chart_path = root + "/chart?instrument_id=MOCK%3AABC&granularity=1m&days=1"
    latest = request_as(setup, Role.VIEWER, "GET", chart_path)
    assert [row["close"] for row in latest.json()["bars"]] == ["999"]
    frozen = request_as(setup, Role.VIEWER, "GET", chart_path + f"&dataset_id={dataset_id}")
    assert frozen.status_code == 200 and frozen.json()["dataset_id"] == str(dataset_id)
    assert [row["close"] for row in frozen.json()["bars"]] == ["99", "100", "101"]
    assert {row["dataset_id"] for row in frozen.json()["bars"]} == {str(dataset_id)}
    assert (
        request_as(setup, Role.VIEWER, "GET", chart_path + f"&dataset_id={uuid4()}").status_code
        == 404
    )
    assert (
        request_as(
            setup,
            Role.VIEWER,
            "GET",
            chart_path.replace("granularity=1m", "granularity=5m") + f"&dataset_id={dataset_id}",
        ).status_code
        == 404
    )
    assert (
        request_as(
            setup,
            Role.VIEWER,
            "GET",
            chart_path.replace("MOCK%3AABC", "MOCK%3AOTHER") + f"&dataset_id={dataset_id}",
        ).status_code
        == 404
    )
    foreign_id = uuid4()
    foreign_meta = metadata.model_copy(
        update={"dataset_id": foreign_id, "workspace_id": other.workspace_id}
    )
    client.app.state.datasets.put(
        users[Role.ADMIN].user_id, PublishedDataset(foreign_meta, "b" * 64, None, "c" * 64)
    )
    assert (
        request_as(setup, Role.VIEWER, "GET", chart_path + f"&dataset_id={foreign_id}").status_code
        == 404
    )
    missing_chart = request_as(
        setup,
        Role.VIEWER,
        "GET",
        root + "/chart?instrument_id=MOCK%3AMISSING&granularity=1m&days=1",
    )
    assert missing_chart.status_code == 404
    assert (
        request_as(setup, Role.VIEWER, "GET", root + "?instrument_id=x&instrument_id=x").status_code
        == 400
    )
    assert (
        request_as(
            setup,
            Role.VIEWER,
            "GET",
            f"/api/v1/workspaces/{other.workspace_id}/market-watch?instrument_id=MOCK%3AABC",
        ).status_code
        == 404
    )


def test_market_stream_websocket_authenticates_workspace_and_origin(setup):
    client, _, _, _, workspace, _, cookies, _ = setup
    client.cookies.set("qtrade-session", cookies[Role.VIEWER].cookie)
    path = f"/api/v1/workspaces/{workspace.workspace_id}/market-streams/ws/mock-feed/MOCK%3AABC"
    with client.websocket_connect(path, headers={"Origin": "https://qtrade.test"}) as socket:
        assert socket.receive_json()["status"] == "unconfigured"
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(path, headers={"Origin": "https://evil.test"}):
            pass


def test_portfolio_summary_is_workspace_scoped_and_readable(setup):
    client, _, _, _, workspace, other, _, _ = setup
    now = datetime.now(UTC)
    root = f"/api/v1/workspaces/{workspace.workspace_id}/portfolio"
    assert request_as(setup, Role.VIEWER, "GET", root).status_code == 404
    client.app.state.portfolio_store.put(
        PortfolioSnapshot(
            workspace.workspace_id,
            "PAPER-01",
            "USD",
            Decimal("1000"),
            (PortfolioPosition("MOCK:ABC", Decimal("2"), Decimal("10"), Decimal("12")),),
            Decimal("1100"),
            1,
            now,
            "mock",
        )
    )
    summary = request_as(setup, Role.VIEWER, "GET", root)
    assert summary.status_code == 200
    assert summary.json()["equity"] == "1024"
    assert summary.json()["gross_exposure"] == "24"
    assert summary.json()["open_orders"] == 1
    assert (
        request_as(
            setup,
            Role.VIEWER,
            "GET",
            f"/api/v1/workspaces/{other.workspace_id}/portfolio",
        ).status_code
        == 404
    )


def test_market_stream_health_http_is_workspace_scoped_and_viewable(setup):
    client, _, _, users, workspace, other, _, _ = setup
    now = datetime.now(UTC)
    key = MarketStreamKey(workspace.workspace_id, "mock-feed", "MOCK:ABC")
    health = MarketStreamHealth(
        key,
        "degraded",
        now,
        heartbeat_at=now,
        latest_lag_ms=2500,
        max_lag_ms=2500,
    )
    client.app.state.market_stream_health.put(users[Role.ADMIN].user_id, health)
    root = f"/api/v1/workspaces/{workspace.workspace_id}/market-streams"
    assert client.get(root).status_code == 401
    listed = request_as(setup, Role.VIEWER, "GET", root)
    assert listed.status_code == 200 and listed.json()[0]["status"] == "degraded"
    single = request_as(
        setup,
        Role.VIEWER,
        "GET",
        root + f"/{key.source}/{key.instrument_id}",
    )
    assert single.status_code == 200 and single.json()["latest_lag_ms"] == 2500
    assert (
        request_as(
            setup,
            "outsider",
            "GET",
            f"/api/v1/workspaces/{other.workspace_id}/market-streams/{key.source}/{key.instrument_id}",
        ).status_code
        == 404
    )


def test_data_management_api_lists_sources_and_audits_admin_mutations(setup):
    from datetime import timedelta

    client, _, _, users, workspace, other, _, _ = setup
    now = datetime.now(UTC)
    task_store = client.app.state.ingestion_store
    request = CollectionRequest(
        workspace.workspace_id,
        uuid4(),
        "provider",
        "local-license-reference",
        "TEST:ABC",
        "1m",
        now - timedelta(days=2),
        now - timedelta(days=1),
        now,
        "provider.bar.v1",
    )
    task_store.create(users[Role.ADMIN].user_id, request)
    lease = task_store.claim(
        users[Role.ADMIN].user_id,
        uuid4(),
        workspace.workspace_id,
        request.task_id,
        now,
        now + timedelta(minutes=1),
    )
    assert lease is not None
    task_store.schedule(
        lease,
        ErrorCode.UNAVAILABLE,
        now + timedelta(seconds=30),
        now,
        terminal=True,
    )
    draft = request.batch(0, now, ())
    batch = draft.metadata(object_key="batches/example.parquet", content_sha256="a" * 64)
    client.app.state.batch_store.put(users[Role.ADMIN].user_id, batch)
    issue = QualityIssue(
        uuid4(),
        workspace.workspace_id,
        request.dataset_id,
        "missing",
        "warning",
        request.instrument_id,
        request.granularity,
        request.start,
        request.end,
        (),
        '{"missing_count":4}',
        now,
    )
    client.app.state.quality_store.record_scan(users[Role.ADMIN].user_id, (issue,))

    root = f"/api/v1/workspaces/{workspace.workspace_id}/data"
    assert client.get(root + "/tasks").status_code == 401
    tasks = request_as(setup, Role.VIEWER, "GET", root + "/tasks")
    assert tasks.status_code == 200 and tasks.json()[0]["status"] == "failed"
    assert "cursor" not in tasks.json()[0] and "local-license-reference" not in tasks.text
    sources = request_as(setup, Role.VIEWER, "GET", root + "/sources")
    assert sources.status_code == 200 and sources.json() == []
    batches = request_as(setup, Role.VIEWER, "GET", root + "/batches")
    assert batches.status_code == 200 and batches.json()[0]["record_count"] == 0
    issues = request_as(setup, Role.VIEWER, "GET", root + "/quality-issues")
    assert issues.status_code == 200 and issues.json()[0]["issue_id"] == str(issue.issue_id)

    rerun_path = root + f"/tasks/{request.task_id}/rerun"
    denied = request_as(setup, Role.VIEWER, "POST", rerun_path)
    assert denied.status_code == 403
    rerun = request_as(setup, Role.ADMIN, "POST", rerun_path)
    assert rerun.status_code == 200 and rerun.json()["status"] == "queued"
    claim_path = root + f"/quality-issues/{issue.issue_id}/claim"
    claimed = request_as(setup, Role.ADMIN, "POST", claim_path)
    assert claimed.status_code == 200 and claimed.json()["status"] == "claimed"
    closed = request_as(setup, Role.ADMIN, "POST", root + f"/quality-issues/{issue.issue_id}/close")
    assert closed.status_code == 200 and closed.json()["status"] == "closed"
    audit = client.app.state.audit.records
    assert any(record.action == "data.collection.rerun" for record in audit)
    assert any(record.action == "data.quality.claim" for record in audit)
    assert any(record.action == "data.quality.close" for record in audit)
    assert any(record.result == "denied" for record in audit)
    assert (
        request_as(
            setup,
            Role.ADMIN,
            "GET",
            f"/api/v1/workspaces/{other.workspace_id}/data/tasks",
        ).status_code
        == 404
    )


def test_download_policy_is_scoped_and_configured_excess_creates_no_tasks(setup):
    from qtrade_domain.download_policy import DownloadRangePolicy

    client, _, _, _, workspace, other, _, _ = setup
    root = f"/api/v1/workspaces/{workspace.workspace_id}/data"
    assert client.get(root + "/download-policy").status_code == 401
    assert request_as(setup, Role.VIEWER, "GET", root + "/download-policy").json() == {
        "max_days": None
    }
    assert (
        request_as(
            setup,
            Role.ADMIN,
            "GET",
            f"/api/v1/workspaces/{other.workspace_id}/data/download-policy",
        ).status_code
        == 404
    )
    client.app.state.data_management.download_policy = DownloadRangePolicy(7)
    assert request_as(setup, Role.VIEWER, "GET", root + "/download-policy").json() == {
        "max_days": 7
    }
    before = request_as(setup, Role.ADMIN, "GET", root + "/tasks").json()
    payload = {
        "source": "mairui.a-share.tick",
        "authorization_id": "mairui-default",
        "instrument_id": "600519.SH",
        "start": "2026-09-14T01:30:00Z",
        "end": "2026-09-22T07:00:00Z",
    }
    response = request_as(setup, Role.ADMIN, "POST", root + "/tasks", json=payload)
    assert response.status_code == 422
    assert response.json()["detail"] == {"code": "download_range_exceeded", "max_days": 7}
    assert request_as(setup, Role.ADMIN, "GET", root + "/tasks").json() == before
