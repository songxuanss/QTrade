"""Lease heartbeat and publication guards for the durable backtest queue."""

import asyncio
from unittest.mock import patch
from uuid import uuid4

from qtrade_domain.backtesting import BacktestRunRecord
from qtrade_worker import backtests


class Store:
    def __init__(self, renewals):
        self.renewals = list(renewals)
        self.calls = 0

    def renew(self, *_args, **_kwargs):
        self.calls += 1
        return self.renewals.pop(0) if self.renewals else False


def test_heartbeat_renews_until_ownership_is_lost():
    record = BacktestRunRecord(
        backtests.BacktestRun.model_validate_json(
            (
                __import__("pathlib").Path(__file__).parent / "fixtures/contracts/BacktestRun.json"
            ).read_text()
        ),
        None,
        2,
    )
    store = Store([True, True, False])

    async def scenario():
        lost = asyncio.Event()
        with patch.object(backtests, "LEASE_SECONDS", 0.6):
            task = asyncio.create_task(backtests._heartbeat(store, record, uuid4(), lost))
            for _ in range(20):
                if lost.is_set():
                    break
                await asyncio.sleep(0.1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        return lost.is_set()

    assert asyncio.run(scenario())
    assert store.calls >= 3


def test_heartbeat_stops_immediately_when_renewal_fails():
    record = BacktestRunRecord(
        backtests.BacktestRun.model_validate_json(
            (
                __import__("pathlib").Path(__file__).parent / "fixtures/contracts/BacktestRun.json"
            ).read_text()
        ),
        None,
        2,
    )
    store = Store([False])

    async def scenario():
        lost = asyncio.Event()
        with patch.object(backtests, "LEASE_SECONDS", 0.6):
            await asyncio.wait_for(backtests._heartbeat(store, record, uuid4(), lost), 2)
        return lost.is_set()

    assert asyncio.run(scenario())
    assert store.calls == 1
