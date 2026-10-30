import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from qtrade_application.strategies import StrategyService
from qtrade_domain.authorization import WorkspaceAccess
from qtrade_domain.identity import Role, Workspace
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_mock.ports import MockObjectStore
from qtrade_mock.strategies import MockStrategyStore


def _access(role: Role) -> WorkspaceAccess:
    workspace = Workspace(uuid4(), "Personal", "personal", None, False, False, datetime.now(UTC))
    return WorkspaceAccess(workspace, uuid4(), role)


def _schema(maximum: int = 100) -> str:
    return json.dumps(
        {
            "type": "object",
            "properties": {
                "lookback": {"type": "integer", "minimum": 1, "maximum": maximum, "default": 20}
            },
            "required": ["lookback"],
        }
    )


def test_strategy_versions_freeze_code_schema_and_dependencies_and_support_diff():
    access = _access(Role.ANALYST)
    service = StrategyService(MockStrategyStore(), MockObjectStore())
    strategy, first = service.create(
        access,
        name="Moving average",
        description="A simple research strategy",
        code="def signal(close):\n    return close[-1]\n",
        parameter_schema_json=_schema(),
        dependencies_json='{"numpy":"==2.1.0"}',
    )
    second = service.revise(
        access,
        strategy.strategy_id,
        code="def signal(close):\n    return close[-2]\n",
        parameter_schema_json=_schema(200),
        dependencies_json='{"numpy":"==2.2.0"}',
    )
    assert second.version == 2
    assert [item.version for item in service.versions(access, strategy.strategy_id)] == [1, 2]
    assert (
        "-    return close[-1]"
        in service.diff(access, strategy.strategy_id, 1, 2).code_unified_diff
    )
    diff = service.diff(access, strategy.strategy_id, 1, 2)
    assert diff.parameter_schema_changed and diff.dependencies_changed
    assert service.read_code(access, strategy.strategy_id, 1).endswith("close[-1]\n")
    assert service.read_dependencies(access, strategy.strategy_id, 1) == '{"numpy":"==2.1.0"}'
    assert service.validate_parameters(access, strategy.strategy_id, 1, {}) == {"lookback": 20}
    assert service.validate_parameters(access, strategy.strategy_id, 1, {"lookback": 50}) == {
        "lookback": 50
    }
    with pytest.raises(PortError) as invalid:
        service.validate_parameters(access, strategy.strategy_id, 1, {"lookback": 101})
    assert invalid.value.code == ErrorCode.INVALID_INPUT
    with pytest.raises(PortError) as forbidden:
        service.validate_parameters(access, strategy.strategy_id, 1, {"lookback": 20, "typo": 1})
    assert forbidden.value.code == ErrorCode.INVALID_INPUT


def test_strategy_write_permission_schema_validation_and_workspace_scope():
    viewer = _access(Role.VIEWER)
    service = StrategyService(MockStrategyStore(), MockObjectStore())
    with pytest.raises(PortError) as denied:
        service.create(viewer, name="No", code="pass", parameter_schema_json=_schema())
    assert denied.value.code == ErrorCode.REJECTED
    access = _access(Role.ADMIN)
    with pytest.raises(PortError) as malformed:
        service.create(access, name="Bad", code="pass", parameter_schema_json='{"type":"array"}')
    assert malformed.value.code == ErrorCode.INVALID_INPUT
    strategy, _ = service.create(
        access, name="Visible", code="pass", parameter_schema_json=_schema()
    )
    assert service.list(_access(Role.ADMIN)) == ()
    with pytest.raises(PortError) as missing:
        service.get(_access(Role.ADMIN), strategy.strategy_id)
    assert missing.value.code == ErrorCode.NOT_FOUND
