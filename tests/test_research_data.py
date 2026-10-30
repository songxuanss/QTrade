"""Observed trade minutes preserve descending-provider order and immutable archive provenance."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from qtrade_application.batches import RawBatchService
from qtrade_application.datasets import DatasetReplayService
from qtrade_application.research_data import ResearchDataService
from qtrade_domain.authorization import WorkspaceAccess
from qtrade_domain.batches import BatchInput
from qtrade_domain.datasets import ReplayRequest
from qtrade_domain.identity import Role, Workspace
from qtrade_domain.ingestion import CollectionRequest, IngestionTask
from qtrade_domain.ports import BarQuery, ErrorCode, PortError
from qtrade_domain.raw_records import RawRecord
from qtrade_mairui.minute_bars import MaiRuiMinuteBars
from qtrade_mock.batches import MockBatchRepository
from qtrade_mock.datasets import MockDatasetRepository
from qtrade_mock.ports import MockMarketData, MockObjectStore
from qtrade_parquet.raw_records import ParquetRawRecordCodec


def test_tick_minutes_replay_across_pages_preserve_ohlc_shares_and_missing_minutes():
    now = datetime(2026, 9, 28, 1, 30, tzinfo=UTC)
    workspace = Workspace(uuid4(), "Research", "organization", None, False, False, now)
    access = WorkspaceAccess(workspace, uuid4(), Role.ADMIN)
    objects, repo, market = MockObjectStore(), MockDatasetRepository(), MockMarketData()
    raw = RawBatchService(MockBatchRepository(), objects, ParquetRawRecordCodec())
    batch_ids = []
    # Descending archive; one minute crosses a page boundary, another has no trades.
    rows = [
        [
            {"d": "2026-09-28", "t": "09:32:10", "p": 13, "v": 10},
            {"d": "2026-09-28", "t": "09:30:20", "p": 12, "v": 20},
        ],
        [
            {"d": "2026-09-28", "t": "09:30:20", "p": 11, "v": 30},
            {"d": "2026-09-28", "t": "09:30:00", "p": 10, "v": 40},
        ],
    ]
    for page in rows:
        batch_id = uuid4()
        batch_ids.append(batch_id)
        raw.publish(
            access,
            BatchInput(
                workspace.workspace_id,
                batch_id,
                "mairui.a-share.tick",
                "mairui-default",
                uuid4(),
                "600519.SH",
                "tick",
                now,
                tuple(RawRecord(index, json.dumps(row).encode()) for index, row in enumerate(page)),
            ),
        )
    replay = DatasetReplayService(repo, raw, market, objects, (MaiRuiMinuteBars(),))
    request = ReplayRequest(
        workspace.workspace_id,
        uuid4(),
        tuple(batch_ids),
        MaiRuiMinuteBars.version,
        now,
        now + timedelta(minutes=3),
        now,
    )
    dataset = replay.replay(access, request)
    assert dataset.metadata.granularity == "1m"
    bars = market.query_bars(
        BarQuery(
            workspace.workspace_id,
            request.dataset_id,
            "600519.SH",
            "1m",
            request.start,
            request.end,
        )
    ).bars
    assert len(bars) == 2
    assert (bars[0].open, bars[0].high, bars[0].low, bars[0].close) == tuple(
        map(Decimal, [10, 12, 10, 12])
    )
    assert bars[0].volume == Decimal(90)  # shares; never multiplied by a lot size
    assert bars[0].value == Decimal(970)
    assert bars[1].timestamp == now + timedelta(minutes=2)
    assert replay.replay(access, request) == dataset
    assert raw.read(access, batch_ids[0]).records[0].payload == json.dumps(rows[0][0]).encode()


def test_publication_rejects_incomplete_download_viewer_and_partial_minute_ranges():
    now = datetime(2026, 9, 28, 1, 30, tzinfo=UTC)
    workspace = Workspace(uuid4(), "Research", "organization", None, False, False, now)
    access = WorkspaceAccess(workspace, uuid4(), Role.ADMIN)
    request = CollectionRequest(
        workspace.workspace_id,
        uuid4(),
        "mairui.a-share.tick",
        "mairui-default",
        "600519.SH",
        "tick",
        now,
        now + timedelta(minutes=1),
        now,
        "raw-tick.v1",
    )
    task = IngestionTask(request, access.user_id, "queued", now)
    tasks = SimpleNamespace(get=lambda _workspace, _task: task)
    service = ResearchDataService(tasks, None)
    with pytest.raises(PortError) as error:
        service.publish(access, request.task_id)
    assert error.value.code == ErrorCode.CONFLICT
    with pytest.raises(PortError) as error:
        service.publish(replace(access, role=Role.VIEWER), request.task_id)
    assert error.value.code == ErrorCode.REJECTED
    task = replace(
        task,
        status="completed",
        collection_complete=True,
        batch_ids=(request.batch_id(0),),
        request=replace(request, start=now + timedelta(seconds=1)),
    )
    with pytest.raises(PortError) as error:
        service.publish(access, request.task_id)
    assert error.value.code == ErrorCode.INVALID_INPUT
