"""Use archived A-share bars to exercise the real Worker-to-Runner strategy bridge.

This checks execution and proposals, not runtime start, risk, or fills. No user
source executes in this operator process or in the Worker process.
"""

import json
import runpy
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(path / "src")
    for area in ("apps", "packages", "adapters")
    for path in (ROOT / area).iterdir()
    if (path / "src").is_dir()
]

from qtrade_contracts import MarketDataset  # noqa: E402
from qtrade_domain.ports import BarQuery  # noqa: E402
from qtrade_postgres.market_data import TimescaleMarketData  # noqa: E402

ENVIRONMENT = runpy.run_path(str(ROOT / "scripts/verification-deployment.py"))["environment"]

WORKER = r'''
import json, sys
from qtrade_contracts import Bar
from qtrade_domain.ports import PortError
from qtrade_runner_adapter.strategy_program import IsolatedStrategyProgram
from qtrade_application.strategy_replay import StrategyReplayService
payload = json.load(sys.stdin)
bars = tuple(Bar.model_validate_json(json.dumps(value)) for value in payload["bars"])
program = IsolatedStrategyProgram()
from uuid import UUID
from opentelemetry import trace
from qtrade_runtime.observability import ServiceTelemetry
import httpx
post = httpx.Client.post
def observe_post(client, *args, **kwargs):
    response = post(client, *args, **kwargs)
    expected = trace.get_current_span().get_span_context().trace_id
    assert expected and UUID(response.headers['X-QTrade-Trace-ID']).int == expected
    assert set(kwargs['headers']) == {'traceparent'}
    return response
httpx.Client.post = observe_post
telemetry = ServiceTelemetry('strategy-program-verification')
source = """from qtrade import Strategy
class Counter(Strategy):
    def on_start(self):
        self.count = 0
    def on_bar(self, bar):
        self.count += 1
        if self.count == self.parameters['when']:
            self.buy(self.parameters['quantity'])
"""
with telemetry.operation('strategy.program.verify'):
    assert program.evaluate(source, bars[:1], {"when": len(bars), "quantity": 100}) == ()
    first = program.evaluate(source, bars, {"when": len(bars), "quantity": 100})
    second = IsolatedStrategyProgram().evaluate(source, bars, {"when": len(bars), "quantity": 100})
    assert first == second and len(first) == 1
    assert first[0].instrument_id == bars[-1].instrument_id and first[0].quantity == 100
    assert first[0].side == "buy" and first[0].order_type == "market"
    replay = StrategyReplayService(program).replay(source, bars, {"when": 3, "quantity": 100})
    repeated = StrategyReplayService(IsolatedStrategyProgram()).replay(
        source, bars, {"quantity": 100, "when": 3}
    )
    assert replay == repeated and replay.bar_count == 5
    assert len(replay.proposals) == 1 and replay.proposals[0].bar_index == 2
    assert replay.proposals[0].signal.quantity == 100
    prefix = "from qtrade import Strategy\nclass Bad(Strategy):\n def on_bar(self, bar):\n  "
    failures = [prefix + "self.buy(-1)\n",
                prefix + "raise RuntimeError('private parameter sentinel')\n",
                prefix + "while True: pass\n"]
    for source in failures:
        try:
            program.evaluate(source, bars[:1], {})
        except PortError as error:
            assert 'sentinel' not in str(error)
        else:
            raise AssertionError('invalid or unbounded strategy was accepted')
    # A failed resource-limited job must not prevent a subsequent valid job.
    idle = 'from qtrade import Strategy\nclass Idle(Strategy):\n pass\n'
    assert program.evaluate(idle, bars[:1], {}) == ()
print('PASS: shared trace, real bars, causal proposal schedule and safe isolated replay')
'''

API_DENIAL = r'''
import json, sys
from qtrade_contracts import Bar
from qtrade_domain.ports import PortError, ErrorCode
from qtrade_runner_adapter.strategy_program import IsolatedStrategyProgram
payload = json.load(sys.stdin)
bar = Bar.model_validate_json(json.dumps(payload["bars"][0]))
try:
    IsolatedStrategyProgram().evaluate(
        'from qtrade import Strategy\nclass Idle(Strategy):\n pass\n', (bar,), {}
    )
except PortError as error:
    assert error.code == ErrorCode.UNAVAILABLE
else:
    raise AssertionError('API unexpectedly accessed Runner socket')
print('PASS: API container cannot execute strategies through the Worker-only socket')
'''


def main() -> None:
    environment = ENVIRONMENT()
    result = json.loads(
        (ROOT / ".tools/verification-local/review/history-1d-results.json").read_text()
    )
    import psycopg

    # Archived data used by the real SSO deployment resides in its independent database.
    state = json.loads((ROOT / ".tools/sso-local/state.json").read_text())
    with psycopg.connect(state["api_url"]) as conn:
        row = conn.execute(
            "SELECT payload FROM market_dataset WHERE dataset_id=%s", (result["datasetId"],)
        ).fetchone()
        assert row is not None
    dataset = MarketDataset.model_validate_json(json.dumps(row[0]))
    bars = (
        TimescaleMarketData(environment["MARKET_DATABASE_URL"])
        .query_bars(
            BarQuery(
                dataset.workspace_id,
                dataset.dataset_id,
                "600519.SH",
                "1d",
                dataset.start,
                dataset.end,
            )
        )
        .bars
    )
    assert len(bars) == 5
    for service, script in (("worker", WORKER), ("api", API_DENIAL)):
        completed = subprocess.run(
            ["docker", "compose", "exec", "-T", service, "python", "-c", script],
            cwd=ROOT,
            input=json.dumps({"bars": [bar.model_dump(mode="json") for bar in bars]}),
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode:
            # Never print SDK/process diagnostics or untrusted strategy output.
            raise SystemExit(f"strategy bridge verification failed at {service} container")
        print(completed.stdout.strip())


if __name__ == "__main__":
    main()
