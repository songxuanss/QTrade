from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from qtrade_application.market_data import MarketDataService
from qtrade_contracts import Bar
from qtrade_domain.authorization import WorkspaceAccess
from qtrade_domain.identity import Role, Workspace
from qtrade_domain.market_data import dataset_bars, dataset_digest
from qtrade_domain.ports import BarPage, BarQuery, ErrorCode, PortError
from qtrade_mock.ports import MockMarketData


@pytest.fixture
def data():
    first = Bar.model_validate_json(
        (Path(__file__).parent / "fixtures/contracts/Bar.json").read_text()
    )
    second = first.model_copy(update={"timestamp": first.timestamp + timedelta(minutes=1)})
    workspace = Workspace(
        first.workspace_id, "Data", "organization", None, False, False, datetime.now(UTC)
    )
    access = WorkspaceAccess(workspace, uuid4(), Role.ADMIN)
    query = BarQuery(
        first.workspace_id,
        first.dataset_id,
        first.instrument_id,
        "1m",
        first.timestamp,
        second.timestamp + timedelta(minutes=1),
        limit=1,
    )
    return first, second, access, query


def test_canonical_dataset_accepts_reordered_granularities_and_decimal_timezone_equivalence(data):
    first, _, _, _ = data
    other = first.model_copy(update={"granularity": "5m"})
    a = dataset_bars(first.workspace_id, first.dataset_id, (first, other))
    b = dataset_bars(first.workspace_id, first.dataset_id, (other, first))
    assert a == b
    equivalent = first.model_copy(
        update={
            "open": Decimal("10.000"),
            "timestamp": first.timestamp.astimezone(timezone(timedelta(hours=8))),
        }
    )
    assert dataset_digest((first,)) == dataset_digest((equivalent,))
    store = MockMarketData()
    store.write_bars(first.workspace_id, first.dataset_id, a)
    store.write_bars(first.workspace_id, first.dataset_id, b)
    with pytest.raises(PortError) as duplicate:
        dataset_bars(first.workspace_id, first.dataset_id, (first, equivalent))
    assert duplicate.value.code == ErrorCode.INVALID_INPUT


def test_application_enforces_write_permission_scope_and_paged_boundaries(data):
    first, second, access, query = data
    service = MarketDataService(MockMarketData())
    viewer = replace(access, role=Role.VIEWER)
    with pytest.raises(PortError) as denied:
        service.write_bars(viewer, first.dataset_id, (first,))
    assert denied.value.code == ErrorCode.REJECTED
    service.write_bars(access, first.dataset_id, (second, first))
    page = service.query_bars(viewer, query)
    assert page.bars == (first,)
    assert service.query_bars(viewer, replace(query, cursor=page.next_cursor)).bars == (second,)
    with pytest.raises(PortError) as missing:
        service.query_bars(viewer, replace(query, workspace_id=uuid4()))
    assert missing.value.code == ErrorCode.NOT_FOUND


@pytest.mark.parametrize("mutation", ["workspace", "range", "order", "limit", "empty_cursor"])
def test_adapter_invalid_pages_fail_closed(data, mutation):
    first, second, access, query = data
    if mutation == "workspace":
        page = BarPage((first.model_copy(update={"workspace_id": uuid4()}),), None)
    elif mutation == "range":
        page = BarPage((first.model_copy(update={"timestamp": query.end}),), None)
    elif mutation == "order":
        page = BarPage((second, first), None)
        query = replace(query, limit=2)
    elif mutation == "limit":
        page = BarPage((first, second), None)
    else:
        page = BarPage((), "cursor")

    class BadAdapter(MockMarketData):
        def query_bars(self, request: BarQuery) -> BarPage:
            return page

    with pytest.raises(PortError) as invalid:
        MarketDataService(BadAdapter()).query_bars(access, query)
    assert invalid.value.code == ErrorCode.UNAVAILABLE


@pytest.mark.parametrize(
    "fields",
    [
        {"limit": True},
        {"instrument_id": ""},
        {"granularity": "tick"},
        {"cursor": ""},
        {"start": "invalid"},
        {"dataset_id": "invalid"},
    ],
)
def test_invalid_bar_query_is_a_stable_port_error(data, fields):
    with pytest.raises(PortError) as invalid:
        replace(data[3], **fields)
    assert invalid.value.code == ErrorCode.INVALID_INPUT
