from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from qtrade_application.live_launch import LiveLaunchRequest, LiveLaunchService
from qtrade_domain.authorization import WorkspaceAccess
from qtrade_domain.identity import Role, Workspace
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.strategies import StrategyVersionApproval


class _Runtime:
    def __init__(self):
        self.starts = []

    def start(self, *args):
        self.starts.append(args)
        return uuid4()


class _Evidence:
    def __init__(self):
        self.account_ok = True
        self.gate_clear = True
        self.rollback_ok = True
        self.approval_record = None

    def account_authorized(self, _workspace_id, _account_id):
        return self.account_ok

    def account_clear_for_live(self, _workspace_id, _account_id):
        return self.gate_clear

    def rollback_plan_verified(self, _workspace_id, _account_id, _version_id):
        return self.rollback_ok

    def approval(self, _workspace_id, _strategy_id, _version):
        return self.approval_record


def _setup():
    now = datetime.now(UTC)
    workspace_id, author_id, approver_id = uuid4(), uuid4(), uuid4()
    access = WorkspaceAccess(
        Workspace(workspace_id, "Personal", "personal", None, False, False, now),
        uuid4(),
        Role.ADMIN,
    )
    strategy_id, version_id, account_id = uuid4(), uuid4(), uuid4()
    evidence = _Evidence()
    evidence.approval_record = StrategyVersionApproval(
        uuid4(),
        workspace_id,
        strategy_id,
        version_id,
        4,
        approver_id,
        now - timedelta(minutes=1),
        uuid4(),
        25,
        "risk review passed",
    )
    request = LiveLaunchRequest(
        workspace_id,
        account_id,
        strategy_id,
        4,
        version_id,
        author_id,
        "START LIVE",
    )
    runtime = _Runtime()
    return now, access, request, evidence, runtime


@pytest.mark.parametrize(
    "condition",
    ["feature", "account", "gate", "approval", "confirmation", "rollback"],
)
def test_live_launch_fails_closed_before_runtime_start(condition):
    now, access, request, evidence, runtime = _setup()
    enabled = condition != "feature"
    if condition == "account":
        evidence.account_ok = False
    elif condition == "gate":
        evidence.gate_clear = False
    elif condition == "approval":
        evidence.approval_record = None
    elif condition == "confirmation":
        request = LiveLaunchRequest(
            request.workspace_id,
            request.account_id,
            request.strategy_id,
            request.strategy_version,
            request.strategy_version_id,
            request.strategy_author_id,
            "yes",
        )
    elif condition == "rollback":
        evidence.rollback_ok = False
    service = LiveLaunchService(runtime, evidence, feature_enabled=enabled)

    with pytest.raises(PortError) as error:
        service.launch(access, request, now=now)

    assert error.value.code == ErrorCode.REJECTED
    assert runtime.starts == []


def test_live_launch_starts_only_after_all_independent_gates_pass():
    now, access, request, evidence, runtime = _setup()
    service = LiveLaunchService(runtime, evidence, feature_enabled=True)

    runtime_id = service.launch(access, request, now=now)

    assert runtime_id
    assert len(runtime.starts) == 1
    assert runtime.starts[0][4] == request.strategy_version_id
