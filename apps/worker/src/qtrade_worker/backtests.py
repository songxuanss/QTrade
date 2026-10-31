"""Durable backtest queue consumer.

The API only freezes inputs and inserts a queued run. This consumer leases one
run at a time, executes the stored strategy in the isolated Runner, and publishes
the immutable result. Leases make crashes/restarts recoverable; a cancellation
marks the durable run terminal so a worker cannot publish a late result.
"""

import asyncio
import hashlib
import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from qtrade_application.artifacts import ManifestService
from qtrade_contracts import BacktestRun
from qtrade_domain.backtesting import BacktestRunEvent, BacktestRunRecord
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_mock.backtest_engine import MockEngineAdapter
from qtrade_object_store.s3 import S3ObjectStore
from qtrade_postgres.backtesting import PostgresBacktestRunStore
from qtrade_postgres.market_data import TimescaleMarketData
from qtrade_postgres.strategies import PostgresStrategyStore
from qtrade_runner_adapter.strategy_program import IsolatedBatchStrategyProgram

logger = logging.getLogger(__name__)
LEASE_SECONDS = 300
MAX_ATTEMPTS = 3
POLL_SECONDS = 0.5


def _objects() -> S3ObjectStore:
    store = S3ObjectStore.connect(
        bucket=os.environ.get("OBJECT_STORE_BUCKET", "qtrade"),
        endpoint_url=os.environ.get("OBJECT_STORE_URL"),
        allow_http=os.environ.get("QTRADE_ENV") == "local",
        access_key=os.environ.get("OBJECT_STORE_ACCESS_KEY"),
        secret_key=os.environ.get("OBJECT_STORE_SECRET_KEY"),
    )
    if os.environ.get("QTRADE_ENV") == "local":
        store.ensure_bucket()
    return store


def _program_resolver(
    strategy_store: PostgresStrategyStore,
    objects: S3ObjectStore,
    runner_socket: str,
    cache: dict[tuple[str, int], IsolatedBatchStrategyProgram],
) -> Callable[[UUID, UUID, str, int], IsolatedBatchStrategyProgram]:
    def resolve(
        workspace_id: UUID,
        strategy_version_id: UUID,
        code_sha256: str,
        seed: int,
    ) -> IsolatedBatchStrategyProgram:
        cached = cache.get((code_sha256, seed))
        if cached is not None:
            return cached
        if not os.path.exists(runner_socket):
            raise PortError(ErrorCode.UNAVAILABLE, "strategy runner socket is unavailable")
        version = strategy_store.version_by_id(workspace_id, strategy_version_id)
        if version is None or version.code_sha256 != code_sha256:
            raise PortError(ErrorCode.CONFLICT, "strategy source no longer matches the manifest")
        try:
            source = objects.get(
                workspace_id,
                version.code_object_key,
                expected_sha256=version.code_sha256,
            ).decode("utf-8")
        except (OSError, UnicodeDecodeError):
            raise PortError(ErrorCode.UNAVAILABLE, "strategy source is unavailable") from None
        if hashlib.sha256(source.encode("utf-8")).hexdigest() != code_sha256:
            raise PortError(ErrorCode.CONFLICT, "strategy source check failed")
        program = IsolatedBatchStrategyProgram(runner_socket, source, seed=seed)
        cache[(code_sha256, seed)] = program
        return program

    return resolve


def _completed(record: BacktestRunRecord, result_key: str, revision: int) -> BacktestRun:
    return BacktestRun.model_validate(
        {
            **record.run.model_dump(),
            "status": "completed",
            "progress": 100,
            "result_object_key": result_key,
            "error_code": None,
        }
    )


def _failed(record: BacktestRunRecord, code: str, revision: int) -> BacktestRun:
    return BacktestRun.model_validate(
        {
            **record.run.model_dump(),
            "status": "failed",
            "progress": max(record.run.progress, 10),
            "result_object_key": None,
            "error_code": code,
        }
    )


async def consume_backtests() -> None:
    """Claim and execute durable runs until cancelled at process shutdown."""
    database_url = os.environ.get("DATABASE_URL")
    market_url = os.environ.get("MARKET_DATABASE_URL")
    if os.environ.get("QTRADE_ENV") != "local" or not database_url or not market_url:
        return
    while True:
        try:
            objects = await asyncio.to_thread(_objects)
            break
        except Exception:
            logger.exception("backtest object store unavailable; retrying")
            await asyncio.sleep(5)
    market = TimescaleMarketData(market_url)
    strategy_store = PostgresStrategyStore(database_url)
    manifests = ManifestService(objects)
    runs = PostgresBacktestRunStore(database_url)
    runner_socket = os.environ.get("QTRADE_STRATEGY_RUNNER_SOCKET", "/run/qtrade/runner.sock")
    programs: dict[tuple[str, int], IsolatedBatchStrategyProgram] = {}
    engine = MockEngineAdapter(
        market,
        objects,
        {},
        _program_resolver(strategy_store, objects, runner_socket, programs),
    )
    worker_id = uuid4()
    while True:
        now = datetime.now(UTC)
        try:
            await asyncio.to_thread(runs.fail_exhausted, now, MAX_ATTEMPTS)
            record = await asyncio.to_thread(
                runs.claim, worker_id, now, LEASE_SECONDS, MAX_ATTEMPTS
            )
            if record is None:
                await asyncio.sleep(POLL_SECONDS)
                continue
            run_id = record.run.run_id
            try:
                manifest = await asyncio.to_thread(manifests.get, record.run.workspace_id, run_id)
                job = await asyncio.to_thread(engine.submit, record.run, manifest)
                latest = await asyncio.to_thread(runs.get, record.run.workspace_id, run_id)
                if latest.run.status == "cancelled":
                    continue
                if job.status != "completed" or job.result is None:
                    raise PortError(
                        job.error_code or ErrorCode.UNAVAILABLE,
                        "backtest engine did not publish a result",
                    )
                revision = latest.revision + 1
                updated = BacktestRunRecord(
                    _completed(latest, job.result.key, revision),
                    latest.engine_job_id,
                    revision,
                )
                await asyncio.to_thread(
                    runs.update,
                    latest.revision,
                    updated,
                    BacktestRunEvent(
                        uuid4(),
                        updated.run.workspace_id,
                        run_id,
                        "completed",
                        100,
                        datetime.now(UTC),
                        revision=revision,
                    ),
                )
            except PortError as error:
                await _fail(runs, record, error.code.value)
            except Exception:
                logger.exception("backtest worker execution failed: %s", run_id)
                await _fail(runs, record, "engine_error")
        except PortError:
            logger.exception("backtest queue cycle failed")
            await asyncio.sleep(1)
        except Exception:
            logger.exception("backtest queue cycle failed")
            await asyncio.sleep(1)


async def _fail(runs: PostgresBacktestRunStore, record: BacktestRunRecord, code: str) -> None:
    """Publish a terminal failure without overwriting a concurrent cancellation."""
    try:
        latest = await asyncio.to_thread(runs.get, record.run.workspace_id, record.run.run_id)
        if latest.run.status in {"cancelled", "failed", "completed"}:
            return
        revision = latest.revision + 1
        updated = BacktestRunRecord(_failed(latest, code, revision), latest.engine_job_id, revision)
        await asyncio.to_thread(
            runs.update,
            latest.revision,
            updated,
            BacktestRunEvent(
                uuid4(),
                updated.run.workspace_id,
                record.run.run_id,
                "failed",
                updated.run.progress,
                datetime.now(UTC),
                code,
                revision,
            ),
        )
    except PortError:
        # A cancellation/another worker won the CAS; terminal state is authoritative.
        return
