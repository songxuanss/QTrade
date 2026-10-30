from qtrade_application.strategy_validation import validate_strategy_source


def codes(source: str, dependencies: dict[str, str] | None = None) -> set[str]:
    return {item.code for item in validate_strategy_source(source, dependencies or {}).diagnostics}


def test_syntax_and_pinned_import_dependencies_are_checked():
    assert "syntax.invalid" in codes("def signal(:\n    pass\n")
    assert "dependency.missing" in codes("import numpy as np\n")
    assert not validate_strategy_source("import numpy as np\n", {"numpy": "==2.1.0"}).diagnostics
    assert not validate_strategy_source("def signal():\n    return 1\n", {}).diagnostics
    assert "dependency.unpinned" in codes("pass\n", {"numpy": ">=2"})


def test_local_file_and_network_capabilities_are_rejected():
    local = "from pathlib import Path\nPath('/etc/passwd').read_text()\n"
    remote = "import requests\nrequests.get('https://example.com')\n"
    assert {"capability.filesystem"} <= codes(local)
    assert {"capability.network"} <= codes(remote)


def test_future_indexing_and_negative_shift_are_rejected():
    assert "lookahead.positive_index" in codes("def signal(close):\n    return close[1]\n")
    assert "lookahead.negative_shift" in codes(
        "def signal(series):\n    return series.shift(-1)\n", {"pandas": "==2.2.0"}
    )
    assert not codes("def signal(close):\n    return close[-1]\n")


def test_static_resource_guards_reject_unbounded_work_and_dynamic_access():
    assert "resource.unbounded_loop" in codes("while True:\n    pass\n")
    assert "resource.loop_bound" in codes("for _ in range(limit):\n    pass\n")
    assert "resource.loop_bound" in codes("for _ in range(1000001):\n    pass\n")
    assert "capability.forbidden_call" in codes("eval('1 + 1')\n")
    assert "resource.recursion" in codes("def run():\n    return run()\n")
