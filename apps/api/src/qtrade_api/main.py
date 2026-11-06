"""Local API composition and infrastructure probes."""

import os

from fastapi import FastAPI

from qtrade_api.alerts import alert_evaluation_router, alert_rule_router
from qtrade_api.audit_query import audit_query_router
from qtrade_api.auth_bridge import auth_bridge
from qtrade_api.backtest_corporate_actions import (
    CollectorAdjustmentFactors,
    CollectorCorporateActions,
)
from qtrade_api.backtesting import backtesting_router
from qtrade_api.calendars import calendar_router
from qtrade_api.capabilities import capability_router
from qtrade_api.collector import collector_authorization_router, collector_router
from qtrade_api.data_campaigns import campaign_router
from qtrade_api.data_management import data_management_router
from qtrade_api.data_universes import universe_router
from qtrade_api.instruments import instrument_router
from qtrade_api.kill_switch import kill_switch_router
from qtrade_api.logout_bridge import logout_bridge
from qtrade_api.market_data import market_data_router
from qtrade_api.market_watch import market_watch_router
from qtrade_api.memberships import membership_router
from qtrade_api.portfolio import portfolio_router
from qtrade_api.realtime import realtime_router
from qtrade_api.risk import risk_router
from qtrade_api.runtime_controls import runtime_control_router
from qtrade_api.security import ApiSecurity
from qtrade_api.session_config import configure_sessions
from qtrade_api.sessions import session_router
from qtrade_api.strategies import strategy_router
from qtrade_api.workspaces import workspace_router
from qtrade_application.alert_jobs import AlertEvaluationSubmissionService
from qtrade_application.alerts import AlertEventService, AlertRuleService
from qtrade_application.artifacts import ManifestService
from qtrade_application.audit_query import AuditQueryService
from qtrade_application.backtesting import BacktestEngineIdentity, BacktestService
from qtrade_application.calendars import CalendarService
from qtrade_application.capabilities import CapabilityRegistry
from qtrade_application.data_management import DataManagementService
from qtrade_application.dataset_composition import DatasetCompositionService
from qtrade_application.identity import IdentityService
from qtrade_application.instruments import InstrumentService
from qtrade_application.kill_switch import KillSwitchService
from qtrade_application.logout import LogoutService
from qtrade_application.market_data import MarketDataService
from qtrade_application.market_watch import MarketWatchService
from qtrade_application.realtime import RealtimeMarketDataService
from qtrade_application.research_data import ResearchDataService
from qtrade_application.risk import RiskService
from qtrade_application.runtime_control import (
    RuntimeControlService,
    UnavailableRuntimeOrderControl,
)
from qtrade_application.strategies import StrategyService
from qtrade_application.strategy_lifecycle import StrategyLifecycleService
from qtrade_application.strategy_qualification import BacktestRunQualification
from qtrade_application.universe_memberships import UniverseMembershipService
from qtrade_application.workspaces import WorkspaceService
from qtrade_composition.backtest_queue import QueuedBacktestEngine
from qtrade_composition.collection_gateway import collection_gateway_from_env
from qtrade_composition.collector import CollectorImportStatus, collector_client_from_env
from qtrade_composition.data_catalog import (
    universe_catalog_from_env,
    universe_source_profiles_from_env,
)
from qtrade_composition.downloads import tick_download_policy_from_env
from qtrade_composition.ingestion import historical_ingestion_from_env, research_replay_from_env
from qtrade_composition.startup import validate_startup
from qtrade_data_postgres.universes import PostgresUniverses
from qtrade_object_store.s3 import S3ObjectStore
from qtrade_postgres.alert_events import PostgresAlertEventStore
from qtrade_postgres.alert_jobs import PostgresAlertEvaluationQueue
from qtrade_postgres.alerts import PostgresAlertRuleStore
from qtrade_postgres.audit import PostgresAuditStore
from qtrade_postgres.audit_query import PostgresAuditQuery
from qtrade_postgres.backtesting import PostgresBacktestRunStore
from qtrade_postgres.batches import PostgresBatchRepository
from qtrade_postgres.calendars import PostgresCalendarStore
from qtrade_postgres.collection_preferences import PostgresCollectionPreferences
from qtrade_postgres.datasets import PostgresDatasetRepository
from qtrade_postgres.identity import PostgresIdentityStore
from qtrade_postgres.ingestion import PostgresIngestionRepository
from qtrade_postgres.instruments import PostgresInstrumentStore
from qtrade_postgres.kill_switch import PostgresKillSwitchStore
from qtrade_postgres.logout import PostgresLogoutStore
from qtrade_postgres.market_data import TimescaleMarketData
from qtrade_postgres.memberships import PostgresMembershipStore
from qtrade_postgres.quality import PostgresQualityIssueStore
from qtrade_postgres.realtime import PostgresRealtimeHealthStore
from qtrade_postgres.risk import PostgresRiskDecisionStore, PostgresRiskLimitStore
from qtrade_postgres.runtime_control import PostgresExecutionRuntimeStore
from qtrade_postgres.strategies import PostgresStrategyStore
from qtrade_postgres.walk_forward import PostgresWalkForwardStore
from qtrade_postgres.workspaces import PostgresWorkspaceStore
from qtrade_runtime.health import create_health_app

validate_startup()
app: FastAPI = create_health_app("qtrade-api")

service, policy = configure_sessions(os.environ)
audit = PostgresAuditStore(os.environ["DATABASE_URL"]) if service is not None else None
app.include_router(session_router(service, policy))

identity_store = (
    IdentityService(PostgresIdentityStore(os.environ["DATABASE_URL"]))
    if service is not None
    else None
)
app.include_router(
    auth_bridge(
        identity_store,
        service,
        policy,
        secret=os.environ.get("QTRADE_BFF_SECRET"),
        issuer=os.environ.get("QTRADE_OIDC_ISSUER"),
        audit=audit,
    )
)
app.include_router(
    logout_bridge(
        LogoutService(PostgresLogoutStore(os.environ["DATABASE_URL"]))
        if service is not None
        else None,
        secret=os.environ.get("QTRADE_BFF_SECRET"),
        issuer=os.environ.get("QTRADE_OIDC_ISSUER"),
    )
)

workspaces = (
    WorkspaceService(PostgresWorkspaceStore(os.environ["DATABASE_URL"]))
    if service is not None
    else None
)
security = ApiSecurity(service, workspaces, policy, audit, os.environ.get("QTRADE_PUBLIC_ORIGIN"))
app.include_router(
    collector_authorization_router(workspaces, os.environ.get("COLLECTOR_SERVICE_TOKEN"))
)
collector_client = collector_client_from_env()
app.include_router(
    collector_router(
        collector_client,
        security,
        CollectorImportStatus(os.environ["DATABASE_URL"]) if service is not None else None,
        PostgresUniverses(
            os.environ.get("DATA_COLLECTION_DATABASE_URL", os.environ["DATABASE_URL"])
        )
        if service is not None
        else None,
        PostgresCollectionPreferences(os.environ["DATABASE_URL"]) if service is not None else None,
    )
)
app.include_router(workspace_router(workspaces, security))
app.include_router(
    audit_query_router(
        AuditQueryService(PostgresAuditQuery(os.environ["DATABASE_URL"]), audit)
        if audit is not None
        else None,
        security,
    )
)
runtime_controls = (
    RuntimeControlService(
        PostgresExecutionRuntimeStore(os.environ["DATABASE_URL"]),
        UnavailableRuntimeOrderControl(),
        audit,
    )
    if service is not None and audit is not None
    else None
)
app.include_router(runtime_control_router(runtime_controls, security))
kill_switch_store = (
    PostgresKillSwitchStore(os.environ["DATABASE_URL"]) if service is not None else None
)
kill_switch_service = (
    KillSwitchService(kill_switch_store, kill_switch_store, audit)
    if kill_switch_store is not None and audit is not None
    else None
)
app.include_router(kill_switch_router(kill_switch_service, security))
capabilities = CapabilityRegistry[object]()
app.include_router(capability_router(capabilities, security))
alert_rules = (
    AlertRuleService(PostgresAlertRuleStore(os.environ["DATABASE_URL"]))
    if service is not None
    else None
)
alert_events = (
    AlertEventService(PostgresAlertEventStore(os.environ["DATABASE_URL"]))
    if service is not None
    else None
)
app.include_router(alert_rule_router(alert_rules, security, audit, alert_events))
alert_evaluation_submission = (
    AlertEvaluationSubmissionService(PostgresAlertEvaluationQueue(os.environ["DATABASE_URL"]))
    if service is not None
    else None
)
app.include_router(alert_evaluation_router(alert_evaluation_submission, security, audit))
strategies: StrategyService | None
strategy_lifecycle: StrategyLifecycleService | None
backtests: BacktestService | None
# The membership router is mounted unconditionally; keep the optional service
# visible when sessions/OIDC are unconfigured (the Compose default) instead of
# raising NameError at import time.
membership_store: PostgresMembershipStore | None = None
membership_service: UniverseMembershipService | None = None
if service is not None:
    strategy_objects = S3ObjectStore.connect(
        bucket=os.environ.get("OBJECT_STORE_BUCKET", "qtrade"),
        endpoint_url=os.environ.get("OBJECT_STORE_URL"),
        allow_http=os.environ.get("QTRADE_ENV") == "local",
        access_key=os.environ.get("OBJECT_STORE_ACCESS_KEY"),
        secret_key=os.environ.get("OBJECT_STORE_SECRET_KEY"),
    )
    if os.environ.get("QTRADE_ENV") == "local":
        strategy_objects.ensure_bucket()
    strategy_store = PostgresStrategyStore(os.environ["DATABASE_URL"])
    strategies = StrategyService(strategy_store, strategy_objects)
    backtest_runs = PostgresBacktestRunStore(os.environ["DATABASE_URL"])
    strategy_lifecycle = StrategyLifecycleService(
        strategies,
        strategy_store,
        BacktestRunQualification(strategy_store, backtest_runs),
    )
    backtest_market_data = (
        TimescaleMarketData(os.environ["MARKET_DATABASE_URL"])
        if os.environ.get("MARKET_DATABASE_URL")
        else None
    )
    collector_actions = (
        CollectorCorporateActions(collector_client) if collector_client is not None else None
    )
    collector_factors = (
        CollectorAdjustmentFactors(collector_actions) if collector_actions is not None else None
    )
    membership_store = PostgresMembershipStore(os.environ["DATABASE_URL"])
    membership_service = UniverseMembershipService(membership_store)
    if os.environ.get("QTRADE_ENV") == "local" and backtest_market_data is not None:
        # API only freezes inputs and enqueues. The Worker owns source execution,
        # result publication, leases, retry and restart recovery.
        backtests = BacktestService(
            backtest_runs,
            strategies,
            strategy_store,
            PostgresDatasetRepository(os.environ["DATABASE_URL"]),
            ManifestService(strategy_objects),
            QueuedBacktestEngine(),
            strategy_objects,
            BacktestEngineIdentity(
                "qtrade-durable-worker",
                "1.0.0",
                "runner-batch.v1",
                os.environ.get("QTRADE_BACKTEST_IMAGE_DIGEST", "sha256:" + "0" * 64),
            ),
            market_data=backtest_market_data,
            corporate_actions=collector_actions,
            adjustment_factors=collector_factors,
            walk_forwards=PostgresWalkForwardStore(os.environ["DATABASE_URL"]),
            memberships=membership_store,
        )
    else:
        backtests = None
else:
    strategies = None
    strategy_lifecycle = None
    backtests = None
app.include_router(strategy_router(strategies, security, audit, strategy_lifecycle))
app.include_router(backtesting_router(backtests, security, audit))
instruments = (
    InstrumentService(PostgresInstrumentStore(os.environ["DATABASE_URL"]))
    if service is not None
    else None
)

calendars = (
    CalendarService(PostgresCalendarStore(os.environ["DATABASE_URL"]))
    if service is not None
    else None
)
app.include_router(calendar_router(calendars, security))

app.include_router(instrument_router(instruments, security, calendars))

market_data = (
    MarketDataService(TimescaleMarketData(os.environ["MARKET_DATABASE_URL"]))
    if service is not None and os.environ.get("MARKET_DATABASE_URL")
    else None
)
app.include_router(market_data_router(market_data, security))
app.include_router(
    market_watch_router(
        MarketWatchService(PostgresDatasetRepository(os.environ["DATABASE_URL"]), market_data),
        security,
    )
)

realtime_health = (
    RealtimeMarketDataService(PostgresRealtimeHealthStore(os.environ["DATABASE_URL"]), ())
    if service is not None
    else None
)
app.include_router(realtime_router(realtime_health, security))
app.include_router(portfolio_router(None, security))
app.include_router(
    risk_router(
        RiskService(
            PostgresRiskDecisionStore(os.environ["DATABASE_URL"]),
            PostgresRiskLimitStore(os.environ["DATABASE_URL"]),
        )
        if service is not None
        else None,
        security,
    )
)

data_management = (
    DataManagementService(
        PostgresIngestionRepository(os.environ["DATABASE_URL"]),
        PostgresBatchRepository(os.environ["DATABASE_URL"]),
        PostgresQualityIssueStore(os.environ["DATABASE_URL"]),
        historical_ingestion_from_env(os.environ),
        download_policy=tick_download_policy_from_env(),
    )
    if service is not None
    else None
)
app.include_router(
    data_management_router(
        data_management,
        security,
        audit,
        ResearchDataService(
            PostgresIngestionRepository(os.environ["DATABASE_URL"]), research_replay_from_env()
        )
        if service is not None and os.environ.get("MARKET_DATABASE_URL")
        else None,
        PostgresDatasetRepository(os.environ["DATABASE_URL"]) if service is not None else None,
        research_replay_from_env()
        if service is not None and os.environ.get("MARKET_DATABASE_URL")
        else None,
        DatasetCompositionService(
            PostgresDatasetRepository(os.environ["DATABASE_URL"]),
            research_replay_from_env(),
            TimescaleMarketData(os.environ["MARKET_DATABASE_URL"]),
            universe_catalog_from_env(workspaces),
        )
        if service is not None and os.environ.get("MARKET_DATABASE_URL")
        else None,
    )
)

app.include_router(
    universe_router(
        universe_catalog_from_env(workspaces),
        security,
        universe_source_profiles_from_env(workspaces),
    )
)
app.include_router(membership_router(membership_service, security, audit))

app.include_router(
    campaign_router(
        collection_gateway_from_env(workspaces) if service is not None else None, security
    )
)
