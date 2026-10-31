"""Loopback OIDC provider + actual QTrade HTTP bridge/session modules for BFF tests.

Synthetic issuer only. Production never imports tests or uses these fixture keys.
"""

import base64
import json
import secrets
import sys
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from urllib.parse import parse_qs, unquote_plus, urlencode
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[2]
for directory in (
    "apps/api",
    "packages/application",
    "packages/contracts",
    "packages/data-contracts",
    "packages/data-core",
    "packages/domain",
    "packages/runtime",
    "adapters/crypto",
    "adapters/data_http",
    "adapters/mock",
):
    sys.path.insert(0, str(ROOT / directory / "src"))

import uvicorn  # noqa: E402
from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding, rsa  # noqa: E402
from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from starlette.responses import RedirectResponse  # noqa: E402

from qtrade_api.alerts import alert_rule_router  # noqa: E402
from qtrade_api.auth_bridge import auth_bridge  # noqa: E402
from qtrade_api.backtesting import backtesting_router  # noqa: E402
from qtrade_api.capabilities import capability_router  # noqa: E402
from qtrade_api.collector import collector_router  # noqa: E402
from qtrade_api.data_management import data_management_router  # noqa: E402
from qtrade_api.instruments import instrument_router  # noqa: E402
from qtrade_api.logout_bridge import logout_bridge  # noqa: E402
from qtrade_api.market_data import market_data_router  # noqa: E402
from qtrade_api.market_watch import market_watch_router  # noqa: E402
from qtrade_api.portfolio import portfolio_router  # noqa: E402
from qtrade_api.realtime import realtime_router  # noqa: E402
from qtrade_api.runtime_controls import runtime_control_router  # noqa: E402
from qtrade_api.security import ApiSecurity  # noqa: E402
from qtrade_api.sessions import session_router  # noqa: E402
from qtrade_api.strategies import strategy_router  # noqa: E402
from qtrade_api.workspaces import workspace_router  # noqa: E402
from qtrade_application.alerts import (  # noqa: E402
    AlertEventFacts,
    AlertEventService,
    AlertRuleService,
)
from qtrade_application.artifacts import ManifestService  # noqa: E402
from qtrade_application.backtesting import (  # noqa: E402
    BacktestEngineIdentity,
    BacktestService,
)
from qtrade_application.capabilities import CapabilityRegistry  # noqa: E402
from qtrade_application.data_management import DataManagementService  # noqa: E402
from qtrade_application.instruments import InstrumentService  # noqa: E402
from qtrade_application.logout import LogoutService  # noqa: E402
from qtrade_application.market_data import MarketDataService  # noqa: E402
from qtrade_application.market_watch import MarketWatchService  # noqa: E402
from qtrade_application.portfolio import PortfolioService  # noqa: E402
from qtrade_application.realtime import RealtimeMarketDataService  # noqa: E402
from qtrade_application.runtime_control import RuntimeControlService  # noqa: E402
from qtrade_application.sessions import SessionService, TokenSet  # noqa: E402
from qtrade_application.strategies import StrategyService  # noqa: E402
from qtrade_application.strategy_lifecycle import StrategyLifecycleService  # noqa: E402
from qtrade_application.workspaces import WorkspaceService  # noqa: E402
from qtrade_contracts import (  # noqa: E402
    BacktestDeterminism,
    BacktestEquityPoint,
    BacktestFill,
    BacktestMetrics,
    BacktestOrder,
    BacktestPosition,
    BacktestResult,
    BacktestSummary,
    Bar,
    Instrument,
    MarketDataset,
)
from qtrade_crypto.tokens import TokenCipher  # noqa: E402
from qtrade_data_core.ports import DataFailure  # noqa: E402
from qtrade_domain.alerts import AlertSeverity  # noqa: E402
from qtrade_domain.authorization import Permission  # noqa: E402
from qtrade_domain.capabilities import (  # noqa: E402
    AdapterHealth,
    AdapterKind,
    CapabilityDescriptor,
)
from qtrade_domain.datasets import PublishedDataset  # noqa: E402
from qtrade_domain.identity import Membership, Role, User, Workspace  # noqa: E402
from qtrade_domain.identity_port import LoginIdentity  # noqa: E402
from qtrade_domain.portfolio import PortfolioPosition, PortfolioSnapshot  # noqa: E402
from qtrade_domain.ports import ErrorCode, PortError  # noqa: E402
from qtrade_domain.realtime import MarketStreamHealth, MarketStreamKey  # noqa: E402
from qtrade_domain.runtime_control import ExecutionRuntime  # noqa: E402
from qtrade_domain.strategies import StrategyQualification  # noqa: E402
from qtrade_mock.alert_events import MockAlertEventStore  # noqa: E402
from qtrade_mock.alerts import MockAlertRuleStore  # noqa: E402
from qtrade_mock.backtesting import MockBacktestRunStore  # noqa: E402
from qtrade_mock.batches import MockBatchRepository  # noqa: E402
from qtrade_mock.datasets import MockDatasetRepository  # noqa: E402
from qtrade_mock.ingestion import MockIngestionRepository  # noqa: E402
from qtrade_mock.instruments import MockInstrumentStore  # noqa: E402
from qtrade_mock.portfolio import MockPortfolioStore  # noqa: E402
from qtrade_mock.ports import (  # noqa: E402
    MockBroker,
    MockMarketData,
    MockObjectStore,
    MockTradingEngine,
)
from qtrade_mock.quality import MockQualityIssueStore  # noqa: E402
from qtrade_mock.realtime import MockRealtimeHealthStore  # noqa: E402
from qtrade_mock.sessions import MockSessionStore  # noqa: E402
from qtrade_mock.strategies import (  # noqa: E402
    MockStrategyQualificationStore,
    MockStrategyStore,
)
from qtrade_mock.workspaces import MockWorkspaceStore  # noqa: E402
from qtrade_runtime.cookies import SessionCookie  # noqa: E402

ISSUER = "http://127.0.0.1:3111"
CALLBACK = "http://127.0.0.1:3110/auth/callback"
BFF_SECRET = "browser-fixture-bff-secret-1234567890123456"
WEB_ORIGIN = "http://127.0.0.1:3110"
CLIENT_SECRET = "browser-fixture-client-secret"
private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
wrong = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def number(value):
    return b64(value.to_bytes((value.bit_length() + 7) // 8, "big"))


workspace_store = MockWorkspaceStore((), (), ())
instrument_store = MockInstrumentStore()
dataset_store = MockDatasetRepository()
market_store = MockMarketData()
portfolio_store = MockPortfolioStore()


class FixtureRuntimeStore:
    def __init__(self):
        self.rows = {}

    def add(self, runtime):
        self.rows[(runtime.workspace_id, runtime.runtime_id)] = runtime

    def get(self, workspace_id, runtime_id):
        return self.rows.get((workspace_id, runtime_id))

    def list(self, workspace_id, limit=100):
        return tuple(runtime for (scope, _), runtime in self.rows.items() if scope == workspace_id)[
            :limit
        ]

    def transition(self, runtime, *, expected_revision, state, updated_at, audit):
        current = self.get(runtime.workspace_id, runtime.runtime_id)
        if current is None or current.revision != expected_revision:
            raise PortError(ErrorCode.CONFLICT, "runtime changed")
        updated = replace_runtime(runtime, state, updated_at)
        store.audit.append(audit)
        self.add(updated)
        return updated


def replace_runtime(runtime, state, updated_at):
    return ExecutionRuntime(
        runtime.workspace_id,
        runtime.runtime_id,
        runtime.account_id,
        runtime.strategy_version_id,
        runtime.mode,
        state,
        runtime.revision + 1,
        updated_at,
    )


class FixtureRuntimeOrders:
    def cancel_open_orders(self, _workspace_id, _runtime_id):
        return 2

    def close_positions(self, _workspace_id, _runtime_id):
        return 1


fixture_runtimes = FixtureRuntimeStore()
fixture_runtime_orders = FixtureRuntimeOrders()


class IdentityFixture:
    def __init__(self):
        self.identities = {}
        self.calls = 0

    def initialize(self, identity):
        self.calls += 1
        key = (identity.issuer, identity.subject)
        if key not in self.identities:
            now = datetime.now(UTC)
            user, workspace = uuid4(), uuid4()
            self.identities[key] = LoginIdentity(
                User(
                    user,
                    identity.issuer,
                    identity.subject,
                    identity.display_name,
                    identity.email,
                    False,
                    now,
                    now,
                ),
                Workspace(workspace, "Personal", "personal", user, False, False, now),
                Membership(workspace, user, Role.ADMIN, True, user, now, now),
            )
            mapped = self.identities[key]
            workspace_store.users[mapped.user.user_id] = mapped.user
            workspace_store.workspaces[mapped.personal_workspace.workspace_id] = (
                mapped.personal_workspace
            )
            workspace_store.memberships[(mapped.membership.workspace_id, mapped.user.user_id)] = (
                mapped.membership
            )
            fixture_runtimes.add(
                ExecutionRuntime(
                    mapped.personal_workspace.workspace_id,
                    uuid4(),
                    uuid4(),
                    uuid4(),
                    "paper",
                    "running",
                    1,
                    now,
                )
            )
            instrument = json.loads((ROOT / "tests/fixtures/contracts/Instrument.json").read_text())
            instrument["workspace_id"] = str(mapped.personal_workspace.workspace_id)
            instrument_store.import_instruments(
                mapped.user.user_id,
                mapped.personal_workspace.workspace_id,
                (Instrument.model_validate_json(json.dumps(instrument)),),
            )
            dataset_id, batch_id = uuid4(), uuid4()
            metadata = MarketDataset(
                schema_version=1,
                workspace_id=mapped.personal_workspace.workspace_id,
                dataset_id=dataset_id,
                version=1,
                created_at=now,
                batch_ids=(batch_id,),
                instrument_ids=("MOCK:ABC",),
                granularity="1m",
                start=now - timedelta(days=1),
                end=now,
                transformation_version="browser-fixture.v1",
                quality_report_key="datasets/browser-fixture.json",
                content_sha256="a" * 64,
            )
            dataset_store.put(
                mapped.user.user_id,
                PublishedDataset(metadata, "b" * 64, None, "c" * 64),
            )
            points = [
                (minutes, str(64 + index)) for index, minutes in enumerate(range(60, 22, -1))
            ] + [(2, "100"), (1, "101")]
            bars = tuple(
                Bar(
                    schema_version=1,
                    workspace_id=mapped.personal_workspace.workspace_id,
                    dataset_id=dataset_id,
                    instrument_id="MOCK:ABC",
                    specification_version=1,
                    timestamp=now - timedelta(minutes=minutes),
                    granularity="1m",
                    open=Decimal(price),
                    high=Decimal(price) + Decimal("0.5"),
                    low=Decimal(price) - Decimal("0.5"),
                    close=Decimal(price),
                    volume=Decimal(100 + minutes),
                    batch_id=batch_id,
                    quality="valid",
                )
                for minutes, price in points
            )
            market_store.write_bars(mapped.personal_workspace.workspace_id, dataset_id, bars)
            health_store.put(
                mapped.user.user_id,
                MarketStreamHealth(
                    MarketStreamKey(
                        mapped.personal_workspace.workspace_id, "mock-feed", "MOCK:ABC"
                    ),
                    "healthy",
                    now,
                    now,
                ),
            )
            portfolio_store.put(
                PortfolioSnapshot(
                    mapped.personal_workspace.workspace_id,
                    "DEMO-PAPER-01",
                    "USD",
                    Decimal("25000"),
                    (
                        PortfolioPosition("MOCK:ABC", Decimal("10"), Decimal("99"), Decimal("101")),
                        PortfolioPosition("MOCK:XYZ", Decimal("5"), Decimal("200"), Decimal("190")),
                    ),
                    Decimal("30000"),
                    2,
                    now,
                    "mock",
                )
            )
        return self.identities[key]


identity = IdentityFixture()
store = MockSessionStore()
sessions = SessionService(store, TokenCipher({"test": b"t" * 32}, primary="test"))
policy = SessionCookie("test", insecure_local=True)
app = FastAPI()


@app.middleware("http")
async def market_chart_failure_fixture(request, call_next):
    if (
        request.url.path.endswith("/market-watch/chart")
        and request.query_params.get("granularity") == "1h"
    ):
        return JSONResponse({"detail": "fixture unavailable"}, status_code=503)
    return await call_next(request)


app.include_router(session_router(sessions, policy))
app.include_router(
    auth_bridge(identity, sessions, policy, secret=BFF_SECRET, issuer=ISSUER, audit=store.audit)
)


class LogoutFixture:
    def __init__(self):
        self.seen = set()

    def consume_and_revoke(self, event, now, *, audit_context=None):
        key = (event.issuer, event.jti)
        if key in self.seen:
            raise PortError(ErrorCode.CONFLICT, "replayed")
        self.seen.add(key)
        count = 0
        for mapped in identity.identities.values():
            if mapped.user.issuer == event.issuer and mapped.user.subject == event.subject:
                count += store.revoke_user(mapped.user.user_id, now, audit_context=audit_context)
        return count


app.include_router(logout_bridge(LogoutService(LogoutFixture()), secret=BFF_SECRET, issuer=ISSUER))


@app.get("/fixture/logout-token")
def fixture_logout_token(jti: str, mode: str = "valid"):
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": "client",
        "sub": "browser-subject",
        "iat": now,
        "exp": now + 120,
        "jti": jti,
        "events": {"http://schemas.openid.net/event/backchannel-logout": {}},
    }
    header = b64(json.dumps({"alg": "RS256", "kid": "test"}).encode())
    content = (header + "." + b64(json.dumps(claims).encode())).encode()
    key = wrong if mode == "signature" else private
    return {
        "logout_token": content.decode()
        + "."
        + b64(key.sign(content, padding.PKCS1v15(), hashes.SHA256()))
    }


workspace_service = WorkspaceService(workspace_store)
fixture_alert_event_service = AlertEventService(MockAlertEventStore())
capabilities = CapabilityRegistry[object]()
capabilities.register(
    CapabilityDescriptor(
        "test-broker",
        "测试交易通道",
        AdapterKind.BROKER,
        markets=("simulation",),
        order_types=("market", "limit"),
        trading_sessions=("continuous",),
        health=AdapterHealth.HEALTHY,
    ),
    MockBroker(),
)
app.include_router(
    capability_router(capabilities, ApiSecurity(sessions, workspace_service, policy))
)
app.include_router(
    alert_rule_router(
        AlertRuleService(MockAlertRuleStore()),
        ApiSecurity(sessions, workspace_service, policy, store.audit, WEB_ORIGIN),
        store.audit,
        fixture_alert_event_service,
    )
)
app.include_router(
    workspace_router(workspace_service, ApiSecurity(sessions, workspace_service, policy))
)
security = ApiSecurity(sessions, workspace_service, policy, store.audit, WEB_ORIGIN)
app.include_router(
    runtime_control_router(
        RuntimeControlService(fixture_runtimes, fixture_runtime_orders, store.audit),
        security,
    )
)
fixture_strategy_store = MockStrategyStore()
fixture_strategy_qualifications = MockStrategyQualificationStore()
fixture_objects = MockObjectStore()
fixture_strategy_service = StrategyService(fixture_strategy_store, fixture_objects)
fixture_strategy_lifecycle = StrategyLifecycleService(
    fixture_strategy_service, fixture_strategy_store, fixture_strategy_qualifications
)
app.include_router(
    strategy_router(fixture_strategy_service, security, store.audit, fixture_strategy_lifecycle)
)
fixture_trading_engine = MockTradingEngine()
fixture_run_store = MockBacktestRunStore()
fixture_backtests = BacktestService(
    fixture_run_store,
    fixture_strategy_service,
    fixture_strategy_store,
    dataset_store,
    ManifestService(fixture_objects),
    fixture_trading_engine,
    fixture_objects,
    BacktestEngineIdentity("browser-fixture", "1", "mock.v1", "sha256:" + "0" * 64),
    market_data=market_store,
)
app.include_router(backtesting_router(fixture_backtests, security, store.audit))
health_store = MockRealtimeHealthStore(
    lambda actor, workspace: workspace_service.authorize(actor, workspace, Permission.DATA_MANAGE)
)
app.include_router(realtime_router(RealtimeMarketDataService(health_store, ()), security))
market = MarketDataService(market_store)
app.include_router(market_data_router(market, security))
app.include_router(instrument_router(InstrumentService(instrument_store), security))
app.include_router(market_watch_router(MarketWatchService(dataset_store, market), security))
app.include_router(portfolio_router(PortfolioService(portfolio_store), security))
app.include_router(
    data_management_router(
        DataManagementService(
            MockIngestionRepository(
                lambda actor, workspace: workspace_service.authorize(
                    actor, workspace, Permission.READ
                )
            ),
            MockBatchRepository(),
            MockQualityIssueStore(
                lambda actor, workspace: workspace_service.authorize(
                    actor, workspace, Permission.READ
                )
            ),
        ),
        security,
        store.audit,
    )
)


class FixtureCollectorClient:
    """Deterministic UI projection only; never presented as provider evidence."""

    def request(self, method, path, *, actor_id, scope_id, body=None, params=None, **kwargs):
        if path == "/v1/catalog":
            return [
                {
                    "source": "mairui.pro.bars",
                    "name": "MaiRui Pro · A 股行情",
                    "provider": "MaiRui",
                    "dataset_kind": "bars",
                    "periods": ["1m", "5m", "1d"],
                    "instrument_kinds": ["stock", "index"],
                    "markets": ["CN"],
                    "timezone": "Asia/Shanghai",
                    "configured": True,
                    "evidence": "浏览器 UI fixture；不是供应商可用性证据",
                    "limitations": ["真实来源权限、覆盖和更新时间仍需部署验收。"],
                },
                {
                    "source": "mairui.factor.history",
                    "name": "MaiRui · 历史因子",
                    "provider": "MaiRui",
                    "dataset_kind": "factors",
                    "periods": ["1d"],
                    "instrument_kinds": ["stock"],
                    "markets": ["CN"],
                    "timezone": "Asia/Shanghai",
                    "configured": True,
                    "evidence": "浏览器 UI fixture；不是供应商可用性证据",
                    "limitations": ["因子定义版本与研究可知时间未知。"],
                },
            ]
        if path == "/v1/factors":
            return [
                {
                    "factor_id": "pe_ttm",
                    "name": "市盈率 TTM",
                    "category": "估值",
                    "unit": "倍",
                    "calc_version": "unknown",
                    "description": "浏览器 fixture 定义。",
                    "update_frequency": "daily",
                    "available": True,
                    "unavailable_reason": None,
                }
            ]
        if path == "/v1/factor-catalog":
            return {
                "schema_version": 1,
                "source": "mairui.factor.history",
                "status": "fresh",
                "definitions": [
                    {
                        "factor_id": "pe_ttm",
                        "name": "市盈率 TTM",
                        "category": "估值",
                        "unit": "倍",
                        "calc_version": "unknown",
                        "description": "浏览器 fixture 定义。",
                        "update_frequency": "daily",
                        "available": True,
                        "unavailable_reason": None,
                    }
                ],
                "content_sha256": "a" * 64,
                "fetched_at": "2026-01-02T00:00:00Z",
                "last_attempt_at": "2026-01-02T00:00:00Z",
                "refreshing": False,
                "error_code": None,
                "permission_evidence": "not_established_by_catalog",
            }
        if path == "/v1/jobs":
            return [fixture_collector_job(scope_id)]
        if path.endswith("/quality") or path.endswith("/processing-failures"):
            return []
        if path.startswith("/v1/jobs/"):
            return fixture_collector_job(scope_id)
        if path == "/v1/names":
            return {"601288.SH": "农业银行"}
        raise DataFailure("unavailable")


def fixture_collector_job(scope_id):
    return {
        "job_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "state": "running",
        "request": {
            "schema_version": 1,
            "scope_id": str(scope_id),
            "actor_id": "00000000-0000-4000-8000-000000000001",
            "correlation_id": "fixture-correlation",
            "request_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "source": "mairui.pro.bars",
            "dataset_kind": "bars",
            "instrument_ids": ["601288.SH"],
            "granularity": "1d",
            "start": "2025-01-01T00:00:00+08:00",
            "end": "2026-01-01T00:00:00+08:00",
            "fields": [],
            "mode": "missing",
        },
        "created_at": "2026-01-02T00:00:00Z",
        "counts": {"running": 1, "waiting": 1, "failed": 1},
        "total_parts": 3,
        "offset": 0,
        "next_offset": None,
        "instrument_names": {
            "601288.SH": "农业银行",
            "600519.SH": "贵州茅台",
            "600036.SH": "招商银行",
        },
        "field_definitions": [],
        "parts": [
            {
                "part_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                "instrument_id": "601288.SH",
                "instrument_name": "农业银行",
                "field": None,
                "start": "2025-01-01",
                "end": "2026-01-01",
                "state": "running",
                "attempt": 1,
                "error_code": None,
                "next_attempt_at": "2026-01-02T00:00:00Z",
                "publication": None,
            },
            {
                "part_id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                "instrument_id": "600519.SH",
                "instrument_name": "贵州茅台",
                "field": None,
                "start": "2025-01-01",
                "end": "2026-01-01",
                "state": "waiting",
                "attempt": 0,
                "error_code": "scope_daily",
                "wait_reason": "scope_daily",
                "next_attempt_at": "2026-01-03T00:00:00Z",
                "publication": None,
            },
            {
                "part_id": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
                "instrument_id": "600036.SH",
                "instrument_name": "招商银行",
                "field": None,
                "start": "2025-01-01",
                "end": "2026-01-01",
                "state": "failed",
                "attempt": 2,
                "error_code": "unavailable",
                "next_attempt_at": "2026-01-02T00:00:00Z",
                "publication": None,
            },
        ],
    }


class FixturePreferences:
    def get(self, actor, scope):
        return None

    def put(self, actor, scope, draft):
        return None


app.include_router(
    collector_router(
        FixtureCollectorClient(),
        security,
        None,
        None,
        FixturePreferences(),
    )
)

codes = {}
exchanges = 0


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/fixture/audit")
def fixture_audit():
    return [
        {"action": item.action, "result": item.result}
        for item in store.audit.records
        if item.action.startswith("alert.") or item.action == "permission.denied"
    ]


@app.get("/fixture/viewer-session")
def viewer_session(workspace_id: UUID):
    workspace = workspace_store.workspaces.get(workspace_id)
    if workspace is None:
        raise HTTPException(404)
    now = datetime.now(UTC)
    user_id = uuid4()
    user = User(user_id, ISSUER, "viewer-" + str(user_id), "Viewer", None, False, now, now)
    membership = Membership(workspace_id, user_id, Role.VIEWER, True, None, now, now)
    workspace_store.users[user_id] = user
    workspace_store.memberships[(workspace_id, user_id)] = membership
    result = sessions.create(
        LoginIdentity(user, workspace, membership),
        TokenSet("fixture-viewer-token"),
    )
    return {"cookie": result.cookie, "csrf_token": result.csrf}


@app.get("/fixture/risk-manager-session")
def risk_manager_session(workspace_id: UUID):
    workspace = workspace_store.workspaces.get(workspace_id)
    if workspace is None:
        raise HTTPException(404)
    now = datetime.now(UTC)
    user_id = uuid4()
    user = User(user_id, ISSUER, "risk-" + str(user_id), "Risk Manager", None, False, now, now)
    membership = Membership(workspace_id, user_id, Role.RISK_MANAGER, True, None, now, now)
    workspace_store.users[user_id] = user
    workspace_store.memberships[(workspace_id, user_id)] = membership
    result = sessions.create(
        LoginIdentity(user, workspace, membership), TokenSet("fixture-risk-token")
    )
    return {"cookie": result.cookie, "csrf_token": result.csrf, "user_id": str(user_id)}


@app.post("/fixture/strategy-qualification")
def fixture_strategy_qualification(
    workspace_id: UUID, strategy_id: UUID, version: int, observations: int = 0
):
    from qtrade_domain.ports import PortError

    if fixture_strategy_store.get(workspace_id, strategy_id) is None:
        raise HTTPException(404)
    run_id = uuid4()
    try:
        fixture_strategy_qualifications.set_for_version(
            workspace_id,
            strategy_id,
            version,
            StrategyQualification(run_id, True, observations, 20),
        )
    except PortError as error:
        raise HTTPException(400, str(error)) from None
    return {"backtest_run_id": str(run_id), "observations": observations}


@app.post("/fixture/remove-strategy")
def fixture_remove_strategy(workspace_id: UUID, strategy_id: UUID):
    fixture_strategy_store.remove_for_fixture(workspace_id, strategy_id)
    fixture_strategy_qualifications.remove_for_fixture(workspace_id, strategy_id)
    return {"ok": True}


@app.post("/fixture/strategies/reset")
def fixture_reset_strategies():
    fixture_strategy_store.clear()
    fixture_strategy_qualifications.clear()
    return {"ok": True}


@app.get("/fixture/runtime-controls")
def fixture_runtime_controls(workspace_id: UUID):
    return {
        "runtimes": [
            {"runtime_id": str(item.runtime_id), "state": item.state, "revision": item.revision}
            for item in fixture_runtimes.list(workspace_id)
        ],
        "audit_actions": [
            record.action
            for record in store.audit.records
            if record.workspace_id == workspace_id and record.action.startswith("runtime.")
        ],
    }


@app.get("/fixture/datasets")
def fixture_datasets(workspace_id: UUID):
    return [
        {
            "dataset_id": str(item.metadata.dataset_id),
            "start": item.metadata.start.isoformat(),
            "end": item.metadata.end.isoformat(),
        }
        for item in dataset_store.list(workspace_id)
    ]


@app.post("/fixture/backtests/{run_id}/advance")
def fixture_advance_backtest(run_id: UUID, workspace_id: UUID, progress: int):
    try:
        job = fixture_trading_engine.advance(workspace_id, run_id, progress)
        return {"status": job.status, "progress": job.progress}
    except PortError as error:
        raise HTTPException(409, str(error)) from None


@app.post("/fixture/backtests/{run_id}/complete")
def fixture_complete_backtest(run_id: UUID, workspace_id: UUID):
    run = fixture_run_store.get(workspace_id, run_id).run
    template = BacktestResult.model_validate_json(
        (ROOT / "tests/fixtures/contracts/BacktestResult.json").read_text()
    )
    manifest = ManifestService(fixture_objects).get(workspace_id, run_id)
    result = template.model_copy(
        update={
            "workspace_id": workspace_id,
            "dataset_id": run.dataset_id,
            "strategy_version_id": run.strategy_version_id,
            "determinism": BacktestDeterminism(
                engine="browser-fixture",
                seed=manifest.random_seed,
            ),
            "equity_curve": (
                BacktestEquityPoint(
                    timestamp=run.start,
                    cash=Decimal("9500"),
                    equity=Decimal("10000"),
                    positions=(
                        BacktestPosition(
                            instrument_id="MOCK:ABC",
                            quantity=Decimal("25"),
                            market_value=Decimal("500"),
                        ),
                    ),
                    benchmark_equity=Decimal("10000"),
                ),
                BacktestEquityPoint(
                    timestamp=run.end,
                    cash=Decimal("9500"),
                    equity=Decimal("10500"),
                    positions=(
                        BacktestPosition(
                            instrument_id="MOCK:ABC",
                            quantity=Decimal("25"),
                            market_value=Decimal("1000"),
                        ),
                    ),
                    benchmark_equity=Decimal("10500"),
                ),
            ),
            "fills": (
                BacktestFill(
                    instrument_id="MOCK:ABC",
                    side="buy",
                    filled_quantity=Decimal("50"),
                    filled_at=run.end,
                    price=Decimal("20"),
                    fee=Decimal("1"),
                ),
            ),
            "orders": (
                BacktestOrder(
                    instrument_id="MOCK:ABC",
                    side="buy",
                    quantity=Decimal("100"),
                    filled_quantity=Decimal("50"),
                    submitted_at=run.start,
                    filled_at=None,
                    price=Decimal("20"),
                    status="partially_filled_end_of_data",
                ),
            ),
            "summary": BacktestSummary(
                initial_cash=run.initial_cash,
                final_equity=Decimal("10500"),
                total_return=Decimal("0.05"),
                ending_drawdown=Decimal("0"),
                sample_count=2,
            ),
            "metrics": BacktestMetrics(
                max_drawdown=Decimal("0"),
                annualized_return=Decimal("0.05"),
                annualized_volatility=Decimal("0.1"),
                sharpe_ratio=Decimal("1.2"),
                sortino_ratio=None,
                benchmark_return=Decimal("0.05"),
            ),
        }
    )
    content = result.model_dump_json().encode()
    result = fixture_objects.put(
        workspace_id,
        f"runs/{run_id}/result.json",
        content,
        content_type="application/vnd.qtrade.backtest-result+json",
        expected_sha256=sha256(content).hexdigest(),
    )
    try:
        return fixture_trading_engine.complete(workspace_id, run_id, result).status
    except PortError as error:
        raise HTTPException(409, str(error)) from None


@app.post("/fixture/backtests/reset")
def fixture_reset_backtests():
    fixture_run_store.clear()
    fixture_trading_engine.clear()
    return {"ok": True}


@app.post("/fixture/alert-sources")
def fixture_alert_sources(workspace_id: UUID):
    occurred_at = datetime.now(UTC)
    facts = AlertEventFacts(
        workspace_id,
        "browser-source-event",
        "source.health",
        AlertSeverity.WARNING,
        "Source health check",
        "Synthetic integration event",
        occurred_at,
    )
    return [
        method(facts).source.value
        for method in (
            fixture_alert_event_service.market,
            fixture_alert_event_service.risk,
            fixture_alert_event_service.data,
            fixture_alert_event_service.system,
        )
    ]


@app.get("/.well-known/openid-configuration")
def metadata():
    return {
        "issuer": ISSUER,
        "authorization_endpoint": ISSUER + "/authorize",
        "token_endpoint": ISSUER + "/token",
        "jwks_uri": ISSUER + "/jwks",
        "response_types_supported": ["code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic"],
        "code_challenge_methods_supported": ["S256"],
    }


@app.get("/jwks")
def jwks():
    public = private.public_key().public_numbers()
    return {
        "keys": [
            {
                "kty": "RSA",
                "kid": "test",
                "use": "sig",
                "alg": "RS256",
                "n": number(public.n),
                "e": number(public.e),
            }
        ]
    }


@app.get("/authorize")
def authorize(request: Request):
    params = request.query_params
    if (
        params.get("client_id") != "client"
        or params.get("redirect_uri") != CALLBACK
        or params.get("code_challenge_method") != "S256"
        or not params.get("state")
        or not params.get("nonce")
    ):
        raise HTTPException(400, "invalid authorization request")
    code = secrets.token_urlsafe(32)
    codes[code] = {
        "nonce": params["nonce"],
        "challenge": params["code_challenge"],
        "mode": params.get("mode", "valid"),
        "expires": time.time() + 60,
    }
    return RedirectResponse(
        CALLBACK + "?" + urlencode({"code": code, "state": params["state"]}), status_code=303
    )


@app.post("/token")
async def token(request: Request):
    global exchanges
    exchanges += 1
    try:
        authorization = request.headers.get("Authorization", "")
        credentials = base64.b64decode(authorization.removeprefix("Basic ")).decode().split(":", 1)
        if [unquote_plus(value) for value in credentials] != ["client", CLIENT_SECRET]:
            raise ValueError("invalid client")
        form = parse_qs((await request.body()).decode())
        code = form["code"][0]
        entry = codes.pop(code)
        if (
            entry["expires"] <= time.time()
            or b64(sha256(form["code_verifier"][0].encode()).digest()) != entry["challenge"]
        ):
            raise ValueError("invalid code verifier")
    except (ValueError, KeyError):
        raise HTTPException(400, "invalid grant") from None
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": "client",
        "sub": "browser-subject",
        "iat": now,
        "exp": now + 300,
        "nonce": entry["nonce"],
        "name": "Browser user",
        "sid": "browser-provider-session",
    }
    header = b64(json.dumps({"alg": "RS256", "kid": "test"}).encode())
    payload = b64(json.dumps(claims).encode())
    content = (header + "." + payload).encode()
    key = wrong if entry["mode"] == "signature" else private
    signed = content.decode() + "." + b64(key.sign(content, padding.PKCS1v15(), hashes.SHA256()))
    return {
        "access_token": "bff-private-access",
        "refresh_token": "bff-private-refresh",
        "id_token": signed,
        "token_type": "Bearer",
        "expires_in": 300,
    }


@app.get("/stats")
def stats():
    return {
        "exchanges": exchanges,
        "mapped": identity.calls,
        "sessions": len(store.sessions),
        "failed_logins": sum(
            record.action == "auth.login" and record.result == "failure"
            for record in store.audit.records
        ),
    }


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=3111, log_level="warning")
