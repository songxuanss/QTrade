import time

import pytest

from qtrade_runner.sandbox import StrategySandbox


@pytest.fixture
def sandbox():
    return StrategySandbox()


def test_runner_returns_bounded_result_and_stdin_input(sandbox):
    result = sandbox.execute(
        "import sys\nprint('received:' + sys.stdin.read())\n", {"run_id": "abc"}
    )
    assert result.status == "succeeded"
    assert 'received:{"run_id":"abc"}' in result.output
    assert result.elapsed_ms < 2_000


def test_wall_clock_timeout_terminates_sleeping_strategy(sandbox):
    started = time.monotonic()
    result = sandbox.execute("import time\ntime.sleep(30)\n")
    assert result.status == "timed_out"
    assert time.monotonic() - started < 8


def test_output_limit_terminates_noisy_strategy(sandbox):
    result = sandbox.execute("print('x' * 2_000_000)\n")
    assert result.status == "output_limit"
    assert "1 MiB" in result.output


def test_cpu_limit_terminates_busy_loop(sandbox):
    result = sandbox.execute("while True:\n    pass\n")
    assert result.status == "resource_limit"
    assert result.elapsed_ms < 5_000


def test_memory_limit_is_applied_to_child(sandbox):
    result = sandbox.execute("payload = bytearray(512 * 1024 * 1024)\n")
    assert result.status == "failed"
    assert "MemoryError" in result.output


def test_child_cannot_write_to_container_root(sandbox):
    result = sandbox.execute(
        "from pathlib import Path\nPath('/etc/qtrade-runner-write-test').write_text('x')\n"
    )
    assert result.status == "failed"
    assert "PermissionError" in result.output or "Read-only file system" in result.output
