"""Resource-limited qtrade.Strategy callback bridge over the Worker-only socket."""

import json
import re
from decimal import Decimal
from pathlib import Path

import httpx
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from qtrade_contracts import Bar
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.strategy_program import StrategySignal

_MARKER = "QTRADE_SIGNALS_V1 "
MAX_HISTORY_BARS = 1000
MAX_SIGNALS = 100
_HARNESS = r"""
import json, sys, types, random
from datetime import datetime
from decimal import Decimal

payload = json.load(sys.stdin)
random.seed(0)
def number_text(value):
    return format(value, "f") if isinstance(value, Decimal) else str(value)
module = types.ModuleType("qtrade")
class Strategy:
    def __init__(self):
        self.parameters = {}
        self._signals = []
        self._bar = None
    def on_bar(self, bar):
        pass
    def on_start(self):
        pass
    def order(self, side, quantity, *, instrument_id=None, order_type="market",
              limit_price=None, stop_price=None, time_in_force="day"):
        if len(self._signals) >= 100:
            raise ValueError("signal limit")
        self._signals.append({
            "instrument_id": instrument_id or self._bar.instrument_id,
            "side": side, "quantity": number_text(quantity), "order_type": order_type,
            "limit_price": number_text(limit_price) if limit_price is not None else None,
            "stop_price": number_text(stop_price) if stop_price is not None else None,
            "time_in_force": time_in_force,
        })
    def buy(self, quantity, **options):
        self.order("buy", quantity, **options)
    def sell(self, quantity, **options):
        self.order("sell", quantity, **options)
module.Strategy = Strategy
sys.modules["qtrade"] = module
namespace = {"__name__": "qtrade_user_strategy"}
exec(compile(payload["code"], "<strategy>", "exec"), namespace)
classes = [v for v in namespace.values() if isinstance(v, type)
           and v is not Strategy and issubclass(v, Strategy)
           and v.__module__ == "qtrade_user_strategy"]
if len(classes) != 1:
    raise ValueError("one strategy class required")
program = classes[0]()
program.parameters = payload["parameters"]
program.on_start()
for raw in payload["history"]:
    values = dict(raw)
    for key in ("open", "high", "low", "close", "volume", "value"):
        if values.get(key) is not None:
            values[key] = Decimal(values[key])
    values["timestamp"] = datetime.fromisoformat(values["timestamp"].replace("Z", "+00:00"))
    bar = types.SimpleNamespace(**values)
    program._signals = []
    program._bar = bar
    program.on_bar(bar)
print("QTRADE_SIGNALS_V1 " + json.dumps(program._signals, separators=(",", ":"), allow_nan=False))
"""


class IsolatedStrategyProgram:
    def __init__(self, socket_path: str = "/run/qtrade/runner.sock"):
        if not Path(socket_path).is_absolute():
            raise PortError(ErrorCode.INVALID_INPUT, "absolute Runner socket path required")
        self._socket_path = socket_path

    def evaluate(
        self, code: str, history: tuple[Bar, ...], parameters: dict[str, object]
    ) -> tuple[StrategySignal, ...]:
        if (
            not isinstance(code, str)
            or not code.strip()
            or len(code.encode("utf-8")) > 1_000_000
            or not isinstance(parameters, dict)
            or not isinstance(history, tuple)
            or not 1 <= len(history) <= MAX_HISTORY_BARS
            or any(not isinstance(bar, Bar) or bar.quality != "valid" for bar in history)
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid strategy evaluation input")
        first = history[0]
        identities = [(bar.timestamp, bar.instrument_id) for bar in history]
        if (
            identities != sorted(identities)
            or len(set(identities)) != len(identities)
            or any(
                (bar.workspace_id, bar.dataset_id, bar.granularity)
                != (first.workspace_id, first.dataset_id, first.granularity)
                for bar in history
            )
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "strategy history must be one ordered dataset")
        payload = {
            "code": code,
            "history": [bar.model_dump(mode="json") for bar in history],
            "parameters": parameters,
        }
        try:
            encoded = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()
        except (ValueError, TypeError, RecursionError):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid strategy parameters") from None
        if len(encoded) > 1_000_000:
            raise PortError(
                ErrorCode.INVALID_INPUT, "strategy evaluation input exceeds Runner limit"
            )
        carrier: dict[str, str] = {}
        TraceContextTextMapPropagator().inject(carrier)
        # Only correlation IDs cross the socket; never propagate baggage/tracestate.
        headers = {"traceparent": carrier["traceparent"]} if "traceparent" in carrier else {}
        try:
            with httpx.Client(
                transport=httpx.HTTPTransport(uds=self._socket_path),
                base_url="http://runner",
                timeout=8,
            ) as client:
                response = client.post(
                    "/execute", json={"code": _HARNESS, "input": payload}, headers=headers
                )
                response.raise_for_status()
                result = response.json()
        except httpx.TimeoutException:
            raise PortError(ErrorCode.TIMEOUT, "strategy Runner timed out") from None
        except (httpx.HTTPError, ValueError):
            raise PortError(ErrorCode.UNAVAILABLE, "strategy Runner unavailable") from None
        if not isinstance(result, dict) or result.get("status") != "succeeded":
            raise PortError(ErrorCode.REJECTED, "strategy execution failed")
        output = result.get("output")
        if not isinstance(output, str) or len(output.encode()) > 65536:
            raise PortError(ErrorCode.UNAVAILABLE, "invalid strategy Runner result")
        markers = [line[len(_MARKER) :] for line in output.splitlines() if line.startswith(_MARKER)]
        try:
            proposals = json.loads(markers[-1]) if markers else None
            if not isinstance(proposals, list) or len(proposals) > MAX_SIGNALS:
                raise ValueError
            signals = tuple(_signal(value) for value in proposals)
        except (ValueError, TypeError, KeyError, PortError, ArithmeticError):
            raise PortError(ErrorCode.REJECTED, "invalid strategy order proposals") from None
        if any(
            signal.instrument_id not in {bar.instrument_id for bar in history} for signal in signals
        ):
            raise PortError(ErrorCode.REJECTED, "strategy proposes an unavailable instrument")
        return signals


def _signal(value: object) -> StrategySignal:
    keys = {
        "instrument_id",
        "side",
        "quantity",
        "order_type",
        "limit_price",
        "stop_price",
        "time_in_force",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("invalid proposal fields")
    if any(
        not isinstance(value[key], str)
        for key in ("instrument_id", "side", "quantity", "order_type", "time_in_force")
    ) or any(
        value[key] is not None and not isinstance(value[key], str)
        for key in ("limit_price", "stop_price")
    ):
        raise ValueError("invalid proposal value types")
    return StrategySignal(
        value["instrument_id"],
        value["side"],
        _number(value["quantity"]),
        value["order_type"],
        _number(value["limit_price"]) if value["limit_price"] is not None else None,
        _number(value["stop_price"]) if value["stop_price"] is not None else None,
        value["time_in_force"],
    )


def _number(value: str) -> Decimal:
    if len(value) > 128 or not re.fullmatch(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?", value):
        raise ValueError("invalid proposal decimal encoding")
    return Decimal(value)


MAX_BATCH_BARS = 5000
_BATCH_MARKER = "QTRADE_BATCH_SIGNALS_V1 "
_BATCH_HARNESS = r"""
import json, sys, types, random
from datetime import datetime
from decimal import Decimal

payload = json.load(sys.stdin)
random.seed(int(payload.get("seed", 0)))
def number_text(value):
    return format(value, "f") if isinstance(value, Decimal) else str(value)
module = types.ModuleType("qtrade")
class Strategy:
    def __init__(self):
        self.parameters = {}
        self._signals = []
        self._bar = None
    def on_bar(self, bar):
        pass
    def on_start(self):
        pass
    def order(self, side, quantity, *, instrument_id=None, order_type="market",
              limit_price=None, stop_price=None, time_in_force="day"):
        if len(self._signals) >= 100:
            raise ValueError("signal limit")
        self._signals.append({
            "instrument_id": instrument_id or self._bar.instrument_id,
            "side": side, "quantity": number_text(quantity), "order_type": order_type,
            "limit_price": number_text(limit_price) if limit_price is not None else None,
            "stop_price": number_text(stop_price) if stop_price is not None else None,
            "time_in_force": time_in_force,
        })
    def buy(self, quantity, **options):
        self.order("buy", quantity, **options)
    def sell(self, quantity, **options):
        self.order("sell", quantity, **options)
module.Strategy = Strategy
sys.modules["qtrade"] = module
namespace = {"__name__": "qtrade_user_strategy"}
exec(compile(payload["code"], "<strategy>", "exec"), namespace)
classes = [v for v in namespace.values() if isinstance(v, type)
           and v is not Strategy and issubclass(v, Strategy)
           and v.__module__ == "qtrade_user_strategy"]
if len(classes) != 1:
    raise ValueError("one strategy class required")
program = classes[0]()
program.parameters = payload["parameters"]
program.on_start()
batches = []
for index, raw in enumerate(payload["bars"]):
    values = dict(raw)
    for key in ("open", "high", "low", "close", "volume", "value"):
        if values.get(key) is not None:
            values[key] = Decimal(values[key])
    values["timestamp"] = datetime.fromisoformat(values["timestamp"].replace("Z", "+00:00"))
    bar = types.SimpleNamespace(**values)
    program._signals = []
    program._bar = bar
    program.on_bar(bar)
    batches.append({"index": index, "signals": program._signals})
print("QTRADE_BATCH_SIGNALS_V1 " + json.dumps(batches, separators=(",", ":"), allow_nan=False))
"""


class IsolatedBatchStrategyProgram:
    """Execute a stored strategy against a full ordered bar series once.

    The strategy sees one bar at a time and retains its own state, so signals
    remain causal while the expensive interpreter start happens only once.
    """

    def __init__(self, socket_path: str, code: str, seed: int = 0) -> None:
        if not Path(socket_path).is_absolute():
            raise PortError(ErrorCode.INVALID_INPUT, "absolute Runner socket path required")
        if not isinstance(code, str) or not code.strip() or len(code.encode("utf-8")) > 1_000_000:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid strategy source")
        if type(seed) is not int or seed < 0:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid strategy seed")
        self._socket_path = socket_path
        self._code = code
        self._seed = seed

    def signals_for_bars(
        self, bars: tuple[Bar, ...], parameters: dict[str, object]
    ) -> tuple[tuple[StrategySignal, ...], ...]:
        if (
            not isinstance(bars, tuple)
            or not 1 <= len(bars) <= MAX_BATCH_BARS
            or not isinstance(parameters, dict)
            or any(not isinstance(bar, Bar) or bar.quality != "valid" for bar in bars)
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid strategy batch input")
        first = bars[0]
        identities = [(bar.timestamp, bar.instrument_id) for bar in bars]
        if (
            identities != sorted(identities)
            or len(set(identities)) != len(identities)
            or any(
                (bar.workspace_id, bar.dataset_id, bar.granularity)
                != (first.workspace_id, first.dataset_id, first.granularity)
                for bar in bars
            )
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "strategy bars must be one ordered dataset")
        payload = {
            "code": self._code,
            "bars": [bar.model_dump(mode="json") for bar in bars],
            "parameters": parameters,
            "seed": self._seed,
        }
        try:
            encoded = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()
        except (ValueError, TypeError, RecursionError):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid strategy parameters") from None
        if len(encoded) > 1_000_000:
            raise PortError(ErrorCode.INVALID_INPUT, "strategy batch input exceeds Runner limit")
        carrier: dict[str, str] = {}
        TraceContextTextMapPropagator().inject(carrier)
        headers = {"traceparent": carrier["traceparent"]} if "traceparent" in carrier else {}
        try:
            with httpx.Client(
                transport=httpx.HTTPTransport(uds=self._socket_path),
                base_url="http://runner",
                timeout=30,
            ) as client:
                response = client.post(
                    "/execute", json={"code": _BATCH_HARNESS, "input": payload}, headers=headers
                )
                response.raise_for_status()
                result = response.json()
        except httpx.TimeoutException:
            raise PortError(ErrorCode.TIMEOUT, "strategy Runner timed out") from None
        except (httpx.HTTPError, ValueError):
            raise PortError(ErrorCode.UNAVAILABLE, "strategy Runner unavailable") from None
        if not isinstance(result, dict) or result.get("status") != "succeeded":
            raise PortError(ErrorCode.REJECTED, "strategy execution failed")
        output = result.get("output")
        if not isinstance(output, str) or len(output.encode()) > 65536:
            raise PortError(ErrorCode.UNAVAILABLE, "invalid strategy Runner result")
        markers = [
            line[len(_BATCH_MARKER) :]
            for line in output.splitlines()
            if line.startswith(_BATCH_MARKER)
        ]
        try:
            batches = json.loads(markers[-1]) if markers else None
            if not isinstance(batches, list) or len(batches) != len(bars):
                raise ValueError
            instruments = {bar.instrument_id for bar in bars}
            parsed: list[tuple[StrategySignal, ...]] = []
            total = 0
            for index, batch in enumerate(batches):
                if (
                    not isinstance(batch, dict)
                    or batch.get("index") != index
                    or not isinstance(batch.get("signals"), list)
                    or len(batch["signals"]) > MAX_SIGNALS
                ):
                    raise ValueError
                total += len(batch["signals"])
                if total > MAX_SIGNALS * 20:
                    raise ValueError
                signals = tuple(_signal(value) for value in batch["signals"])
                if any(signal.instrument_id not in instruments for signal in signals):
                    raise PortError(
                        ErrorCode.REJECTED, "strategy proposes an unavailable instrument"
                    )
                parsed.append(signals)
            return tuple(parsed)
        except (ValueError, TypeError, KeyError, PortError, ArithmeticError):
            raise PortError(ErrorCode.REJECTED, "invalid strategy order proposals") from None
