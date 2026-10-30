from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from qtrade_application.strategy_replay import StrategyReplayService
from qtrade_contracts import Bar
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.strategy_program import StrategySignal


@pytest.fixture
def bars():
    bar = Bar.model_validate_json(
        (Path(__file__).parent / "fixtures/contracts/Bar.json").read_text()
    )
    return (bar, bar.model_copy(update={"timestamp": bar.timestamp + timedelta(minutes=1)}))


class Program:
    def __init__(self):
        self.calls = []

    def evaluate(self, code, history, parameters):
        self.calls.append((code, history, parameters.copy()))
        parameters["window"] = 999
        return (StrategySignal(history[-1].instrument_id, "buy", Decimal(len(history))),)


def test_replay_never_exposes_future_and_freezes_parameters(bars):
    program = Program()
    parameters = {"window": 2}
    service = StrategyReplayService(program)
    replay = service.replay("immutable source", bars, parameters)
    assert [call[1] for call in program.calls] == [bars[:1], bars]
    assert [call[2] for call in program.calls] == [parameters, parameters]
    assert parameters == {"window": 2}
    assert [proposal.bar_index for proposal in replay.proposals] == [0, 1]
    assert [proposal.signal.quantity for proposal in replay.proposals] == [1, 2]
    assert service.replay("immutable source", bars, parameters) == replay
    assert service.replay("edited source", bars, parameters).code_sha256 != replay.code_sha256
    changed = (bars[0], bars[1].model_copy(update={"volume": Decimal(123)}))
    assert (
        service.replay("immutable source", changed, parameters).history_sha256
        != replay.history_sha256
    )
    assert (
        service.replay("immutable source", bars, {"window": 3}).parameters_sha256
        != replay.parameters_sha256
    )


@pytest.mark.parametrize("change", ["reverse", "duplicate", "workspace", "dataset", "quality"])
def test_invalid_history_rejected_before_execution(bars, change):
    history = {
        "reverse": bars[::-1],
        "duplicate": (bars[0], bars[0]),
        "workspace": (bars[0], bars[1].model_copy(update={"workspace_id": uuid4()})),
        "dataset": (bars[0], bars[1].model_copy(update={"dataset_id": uuid4()})),
        "quality": (bars[0], bars[1].model_copy(update={"quality": "suspect"})),
    }[change]
    program = Program()
    with pytest.raises(PortError):
        StrategyReplayService(program).replay("source", history, {})
    assert program.calls == []


def test_future_instrument_proposal_rejected(bars):
    future = bars[1].model_copy(update={"instrument_id": "FUTURE"})

    class InvalidProgram:
        def evaluate(self, code, history, parameters):
            return (StrategySignal("FUTURE", "buy", Decimal(100)),)

    with pytest.raises(PortError) as error:
        StrategyReplayService(InvalidProgram()).replay("source", (bars[0], future), {})
    assert error.value.code == ErrorCode.REJECTED


def test_failed_callback_returns_no_partial_schedule(bars):
    class FailingProgram(Program):
        def evaluate(self, code, history, parameters):
            if len(history) == 2:
                raise PortError(ErrorCode.TIMEOUT, "strategy Runner timed out")
            return super().evaluate(code, history, parameters)

    with pytest.raises(PortError) as error:
        StrategyReplayService(FailingProgram()).replay("source", bars, {})
    assert error.value.code == ErrorCode.TIMEOUT
