"""Validate the client seam without evaluating untrusted source in pytest."""

import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from qtrade_contracts import Bar
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_runner_adapter.strategy_program import IsolatedStrategyProgram


@pytest.fixture
def bar():
    return Bar.model_validate_json(
        (Path(__file__).parent / "fixtures/contracts/Bar.json").read_text()
    )


@pytest.fixture
def response(monkeypatch):
    values = {"status": "succeeded", "output": "QTRADE_SIGNALS_V1 []"}
    calls = []

    class Client:
        def __init__(self, **options):
            calls.append(options)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, path, *, json, headers):
            calls.append((path, json, headers))
            if isinstance(values.get("failure"), Exception):
                raise values["failure"]
            return httpx.Response(
                200, json=values, request=httpx.Request("POST", "http://runner/execute")
            )

    monkeypatch.setattr("qtrade_runner_adapter.strategy_program.httpx.Client", Client)
    return values, calls


def proposal(bar):
    return {
        "instrument_id": bar.instrument_id,
        "side": "buy",
        "quantity": "100",
        "order_type": "market",
        "limit_price": None,
        "stop_price": None,
        "time_in_force": "day",
    }


def test_isolated_strategy_uses_socket_and_parses_only_valid_proposals(bar, response):
    values, calls = response
    values["output"] = "User output\nQTRADE_SIGNALS_V1 " + json.dumps([proposal(bar)])
    signals = IsolatedStrategyProgram().evaluate("source", (bar,), {"window": 5})
    assert signals[0].quantity == 100
    assert calls[-1][0] == "/execute"
    assert calls[-1][1]["input"]["history"][0]["close"] == str(bar.close)
    assert calls[-1][1]["input"]["parameters"] == {"window": 5}
    assert "exec(compile" in calls[-1][1]["code"]  # Executed by the remote Runner, not client.


@pytest.mark.parametrize(
    "change",
    [
        {"quantity": "NaN"},
        {"quantity": "1e9000000"},
        {"quantity": "1" + "0" * 29},
        {"quantity": True},
        {"quantity": "0"},
        {"side": "hack"},
        {"order_type": "limit"},
        {"instrument_id": "UNKNOWN"},
        {"command_id": "forged"},
    ],
)
def test_isolated_strategy_rejects_malformed_and_forged_proposals(bar, response, change):
    values, _ = response
    values["output"] = "QTRADE_SIGNALS_V1 " + json.dumps([{**proposal(bar), **change}])
    with pytest.raises(PortError) as error:
        IsolatedStrategyProgram().evaluate("source", (bar,), {})
    assert error.value.code == ErrorCode.REJECTED


@pytest.mark.parametrize("status", ["failed", "timed_out", "output_limit", "resource_limit"])
def test_isolated_strategy_failure_never_exposes_program_output(bar, response, status):
    values, _ = response
    values.update(status=status, output="private strategy parameter sentinel")
    with pytest.raises(PortError) as error:
        IsolatedStrategyProgram().evaluate("source", (bar,), {})
    assert "sentinel" not in str(error.value)
    assert error.value.code == ErrorCode.REJECTED


def test_isolated_strategy_rejects_history_reordering_duplicates_and_suspect_data(bar, response):
    from datetime import timedelta

    values, calls = response
    later = bar.model_copy(update={"timestamp": bar.timestamp + timedelta(minutes=1)})
    other = bar.model_copy(update={"dataset_id": uuid4()})
    for history in (
        (later, bar),
        (bar, bar),
        (bar, other),
        (bar.model_copy(update={"quality": "suspect"}),),
    ):
        with pytest.raises(PortError):
            IsolatedStrategyProgram().evaluate("source", history, {})
    assert calls == []


@pytest.mark.parametrize("output", ["no marker", "QTRADE_SIGNALS_V1 null", "QTRADE_SIGNALS_V1 bad"])
def test_isolated_strategy_rejects_invalid_runner_output(bar, response, output):
    values, _ = response
    values["output"] = output
    with pytest.raises(PortError):
        IsolatedStrategyProgram().evaluate("source", (bar,), {})


def test_isolated_strategy_maps_transport_errors_without_diagnostics(bar, response):
    values, _ = response
    values["failure"] = httpx.ConnectError("private socket diagnostics sentinel")
    with pytest.raises(PortError) as error:
        IsolatedStrategyProgram().evaluate("source", (bar,), {})
    assert error.value.code == ErrorCode.UNAVAILABLE
    assert "sentinel" not in str(error.value)


def test_strategy_socket_propagates_only_trace_identity(bar, response):
    from opentelemetry.context import attach, detach
    from opentelemetry.trace import (
        NonRecordingSpan,
        SpanContext,
        TraceFlags,
        TraceState,
        set_span_in_context,
    )

    _, calls = response
    span = NonRecordingSpan(
        SpanContext(
            trace_id=int("a" * 32, 16),
            span_id=int("b" * 16, 16),
            is_remote=False,
            trace_flags=TraceFlags(1),
            trace_state=TraceState([("private", "private-state-sentinel")]),
        )
    )
    token = attach(set_span_in_context(span))
    try:
        assert IsolatedStrategyProgram().evaluate("source", (bar,), {}) == ()
    finally:
        detach(token)
    headers = calls[-1][2]
    assert headers == {"traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-01"}
    assert "sentinel" not in str(headers)
