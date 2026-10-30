"""Build a causal proposal schedule without evaluating source in the application."""

import hashlib
import json
from dataclasses import dataclass

from qtrade_contracts import Bar
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.strategy_program import StrategyProgramPort, StrategySignal


@dataclass(frozen=True, slots=True)
class ReplayProposal:
    bar_index: int
    signal: StrategySignal


@dataclass(frozen=True, slots=True)
class StrategyReplay:
    code_sha256: str
    parameters_sha256: str
    history_sha256: str
    bar_count: int
    proposals: tuple[ReplayProposal, ...]


class StrategyReplayService:
    """Bind proposals to exact inputs and ordered history prefixes.

    This does not authorize orders or match fills. The caller must bind the
    result to its immutable manifest and use exchange close-availability times
    when dispatching proposals to an engine. Bar timestamps can label opens.
    No partially evaluated schedule is returned if any callback fails.
    """

    def __init__(self, program: StrategyProgramPort) -> None:
        self._program = program

    def replay(
        self, code: str, bars: tuple[Bar, ...], parameters: dict[str, object]
    ) -> StrategyReplay:
        if (
            not isinstance(code, str)
            or not code.strip()
            or len(code.encode()) > 1_000_000
            or not isinstance(bars, tuple)
            or not 1 <= len(bars) <= 1000
            or any(not isinstance(bar, Bar) or bar.quality != "valid" for bar in bars)
            or not isinstance(parameters, dict)
            or any(not isinstance(key, str) for key in parameters)
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid strategy replay inputs")
        scope = {(bar.workspace_id, bar.dataset_id, bar.granularity) for bar in bars}
        keys = [(bar.timestamp, bar.instrument_id) for bar in bars]
        if len(scope) != 1 or keys != sorted(keys) or len(set(keys)) != len(keys):
            raise PortError(ErrorCode.INVALID_INPUT, "strategy replay requires ordered scoped bars")
        try:
            frozen_parameters = json.dumps(
                parameters, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
            frozen_history = json.dumps(
                [bar.model_dump(mode="json") for bar in bars],
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError, RecursionError):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid strategy replay JSON") from None
        if len(frozen_parameters.encode()) > 65536 or len(frozen_history.encode()) > 1_000_000:
            raise PortError(
                ErrorCode.INVALID_INPUT, "strategy replay input exceeds execution limit"
            )
        proposals: list[ReplayProposal] = []
        known_instruments: set[str] = set()
        for index, bar in enumerate(bars):
            known_instruments.add(bar.instrument_id)
            # Fresh JSON parameters prevent an adapter from mutating future callbacks.
            signals = self._program.evaluate(code, bars[: index + 1], json.loads(frozen_parameters))
            if (
                not isinstance(signals, tuple)
                or len(signals) > 100
                or any(
                    not isinstance(signal, StrategySignal)
                    or signal.instrument_id not in known_instruments
                    for signal in signals
                )
            ):
                raise PortError(ErrorCode.REJECTED, "invalid strategy replay proposals")
            proposals.extend(ReplayProposal(index, signal) for signal in signals)
        return StrategyReplay(
            hashlib.sha256(code.encode()).hexdigest(),
            hashlib.sha256(frozen_parameters.encode()).hexdigest(),
            hashlib.sha256(frozen_history.encode()).hexdigest(),
            len(bars),
            tuple(proposals),
        )
