from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from qtrade_application.universe_memberships import (
    MembershipSubmission,
    UniverseMembershipService,
)
from qtrade_domain.authorization import WorkspaceAccess
from qtrade_domain.identity import Role, Workspace
from qtrade_domain.ports import ErrorCode, PortError

START = datetime(2025, 1, 1, tzinfo=UTC)


class Store:
    def __init__(self) -> None:
        self.rows = []

    def create(self, membership):
        self.rows.append(membership)

    def overlaps(self, workspace_id, instrument_id, start, end):
        return any(
            item.instrument_id == instrument_id
            and item.effective_from < end
            and item.effective_to > start
            for item in self.rows
            if item.workspace_id == workspace_id
        )

    def list(self, workspace_id, instrument_id=None, limit=200):
        rows = [item for item in self.rows if item.workspace_id == workspace_id]
        if instrument_id is not None:
            rows = [item for item in rows if item.instrument_id == instrument_id]
        return tuple(rows[:limit])

    def for_instruments(self, workspace_id, instrument_ids):
        wanted = set(instrument_ids)
        return tuple(
            item
            for item in self.rows
            if item.workspace_id == workspace_id and item.instrument_id in wanted
        )


def access(role: Role = Role.ADMIN) -> WorkspaceAccess:
    workspace_id, actor = uuid4(), uuid4()
    workspace = Workspace(workspace_id, "Research", "team", actor, False, False, datetime.now(UTC))
    return WorkspaceAccess(workspace, actor, role)


def submission(**overrides) -> MembershipSubmission:
    values = dict(
        instrument_id="600519.SH",
        effective_from=START,
        effective_to=START + timedelta(days=365),
        evidence="样本区间来自研究口径定义",
    )
    values.update(overrides)
    return MembershipSubmission(**values)


def test_membership_requires_data_manage_permission():
    service = UniverseMembershipService(Store())
    with pytest.raises(PortError) as denied:
        service.register(access(Role.VIEWER), submission())
    assert denied.value.code == ErrorCode.REJECTED


def test_membership_intervals_cannot_overlap_and_get_stable_versions():
    store = Store()
    service = UniverseMembershipService(store)
    actor = access()
    first = service.register(actor, submission())
    assert first.source == "workspace.definition"
    assert len(first.content_version) == 64
    assert first.membership_id in {item.membership_id for item in store.list(first.workspace_id)}

    with pytest.raises(PortError) as overlap:
        service.register(actor, submission(effective_from=START + timedelta(days=30)))
    assert overlap.value.code == ErrorCode.CONFLICT

    later = service.register(
        actor,
        submission(
            effective_from=START + timedelta(days=366),
            effective_to=START + timedelta(days=366, hours=12),
        ),
    )
    assert later.membership_id != first.membership_id


def test_membership_version_tracks_evidence_changes():
    service = UniverseMembershipService(Store())
    first = service.create(access(), submission())
    second = service.create(access(), submission(evidence="另一份口径说明"))
    assert first.content_version != second.content_version


def test_membership_instrument_must_use_a_market_identity():
    service = UniverseMembershipService(Store())
    with pytest.raises(PortError) as invalid:
        service.create(access(), submission(instrument_id="AAPL"))
    assert invalid.value.code == ErrorCode.INVALID_INPUT
