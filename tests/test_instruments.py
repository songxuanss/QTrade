from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from qtrade_contracts import Instrument
from qtrade_domain.instruments import InstrumentQuery
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_mock.instruments import MockInstrumentStore


@pytest.fixture
def instrument():
    return Instrument.model_validate_json(
        (Path(__file__).parent / "fixtures/contracts/Instrument.json").read_text()
    )


def test_import_is_idempotent_and_conflicts_preserve_original_and_pending_issue(instrument):
    store = MockInstrumentStore()
    actor, workspace = uuid4(), instrument.workspace_id
    assert store.import_instruments(actor, workspace, (instrument,))[0].status == "created"
    assert store.import_instruments(actor, workspace, (instrument,))[0].status == "unchanged"
    changed = instrument.model_copy(update={"tick_size": instrument.tick_size * 2})
    first = store.import_instruments(actor, workspace, (changed,))[0]
    repeated = store.import_instruments(actor, workspace, (changed,))[0]
    assert first.status == "conflict" and first.issue_id == repeated.issue_id
    assert store.query(InstrumentQuery(workspace)).instruments == (instrument,)
    (issue,) = store.conflicts(workspace)
    assert issue.existing == instrument and issue.proposed == changed and issue.status == "pending"
    assert store.conflicts(uuid4()) == ()


def test_filters_state_pagination_and_workspace_isolation(instrument):
    store = MockInstrumentStore()
    workspace = instrument.workspace_id
    items = tuple(
        instrument.model_copy(update={"instrument_id": str(index), "enabled": index != 2})
        for index in range(3)
    )
    store.import_instruments(uuid4(), workspace, items)
    page = store.query(InstrumentQuery(workspace, limit=1))
    assert [item.instrument_id for item in page.instruments] == ["0"] and page.next_after == "0"
    page = store.query(InstrumentQuery(workspace, limit=1, after=page.next_after))
    assert [item.instrument_id for item in page.instruments] == ["1"] and page.next_after is None
    assert store.query(InstrumentQuery(uuid4(), enabled=None)).instruments == ()
    assert store.query(InstrumentQuery(workspace, enabled=False)).instruments == (items[2],)
    assert (
        store.query(
            InstrumentQuery(
                workspace,
                name=instrument.name.upper(),
                exchange=instrument.exchange,
                market=instrument.market,
            )
        ).instruments
        == items[:2]
    )
    assert store.query(InstrumentQuery(workspace, instrument_id="missing")).instruments == ()


def test_batch_validation_has_no_partial_write(instrument):
    store = MockInstrumentStore()
    workspace = instrument.workspace_id
    invalid = instrument.model_copy(
        update={"instrument_id": "invalid", "tick_size": Decimal("0.0000000000000000001")}
    )
    for batch in [
        (instrument, invalid),
        (instrument, instrument),
        (instrument, instrument.model_copy(update={"workspace_id": uuid4()})),
    ]:
        with pytest.raises(PortError) as failure:
            store.import_instruments(uuid4(), workspace, batch)
        assert failure.value.code == ErrorCode.INVALID_INPUT
        assert store.query(InstrumentQuery(workspace, enabled=None)).instruments == ()


def test_service_denies_viewer_import_and_cross_workspace_queries(instrument):
    from dataclasses import replace
    from datetime import UTC, datetime

    from qtrade_application.instruments import InstrumentService
    from qtrade_domain.authorization import WorkspaceAccess
    from qtrade_domain.identity import Role, Workspace

    workspace = Workspace(
        instrument.workspace_id, "Test", "organization", None, False, False, datetime.now(UTC)
    )
    viewer = WorkspaceAccess(workspace, uuid4(), Role.VIEWER)
    store = MockInstrumentStore()
    service = InstrumentService(store)
    with pytest.raises(PortError) as denied:
        service.import_instruments(viewer, (instrument,))
    assert denied.value.code == ErrorCode.REJECTED
    assert service.query(viewer, InstrumentQuery(workspace.workspace_id)).instruments == ()
    with pytest.raises(PortError) as cross:
        service.query(viewer, InstrumentQuery(uuid4()))
    assert cross.value.code == ErrorCode.NOT_FOUND
    admin = replace(viewer, role=Role.ADMIN)
    assert service.import_instruments(admin, (instrument,))[0].status == "created"
    assert service.query(viewer, InstrumentQuery(workspace.workspace_id)).instruments == (
        instrument,
    )
    with pytest.raises(PortError):
        service.query(
            replace(viewer, workspace=replace(workspace, archived=True)),
            InstrumentQuery(workspace.workspace_id),
        )


def test_precision_validation_does_not_round_large_decimal_values(instrument):
    from qtrade_domain.instruments import validate_import

    valid = instrument.model_copy(
        update={
            "tick_size": Decimal("123456789012345678901234567890.01"),
            "price_precision": 2,
        }
    )
    validate_import(valid.workspace_id, (valid,))
    invalid = valid.model_copy(update={"tick_size": Decimal("123456789012345678901234567890.001")})
    with pytest.raises(PortError):
        validate_import(invalid.workspace_id, (invalid,))
