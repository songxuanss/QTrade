"""Tenant scope, export limits and CSV interpretation are business boundaries."""

import csv
import io
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from qtrade_application.audit_query import AuditQueryService
from qtrade_domain.audit_port import AuditContext
from qtrade_domain.audit_query import AuditQuery
from qtrade_domain.authorization import WorkspaceAccess
from qtrade_domain.identity import Role, Workspace
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_mock.audit import MockAuditStore
from qtrade_postgres.audit_query import _safe_row


class Queries:
    def __init__(self, rows):
        self.rows = rows

    def query(self, access, query, limit):
        return tuple(
            row for row in self.rows if query.before is None or row["event_id"] < query.before
        )[:limit]


def setup():
    now = datetime.now(UTC)
    access = WorkspaceAccess(
        Workspace(uuid4(), "Audit", "personal", None, False, False, now), uuid4(), Role.ADMIN
    )
    rows = [
        {
            "event_id": i,
            "workspace_id": str(access.workspace.workspace_id),
            "action": "audit.test",
            "details": {},
            "actor_id": str(access.user_id),
        }
        for i in (3, 2, 1)
    ]
    audit = MockAuditStore()
    return access, rows, audit, AuditQuery(now - timedelta(days=1), now)


def test_pagination_export_columns_audit_and_roles():
    access, rows, audit, query = setup()
    service = AuditQueryService(Queries(rows), audit)
    page = service.search(access, query, 2)
    assert [row["event_id"] for row in page["events"]] == [3, 2]
    assert page["next_before"] == 2
    assert service.search(access, replace(query, before=2), 2)["events"] == [rows[-1]]
    content = service.export(access, query, AuditContext("api.audit.test"))
    parsed = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
    assert len(parsed) == 3 and all(
        row["workspace_id"] == str(access.workspace.workspace_id) for row in parsed
    )
    assert audit.records[-1].action == "audit.export"
    for role in (Role.ANALYST, Role.TRADER, Role.VIEWER):
        for operation in (
            lambda role=role: service.search(replace(access, role=role), query),
            lambda role=role: service.export(
                replace(access, role=role), query, AuditContext("test")
            ),
        ):
            with pytest.raises(PortError) as error:
                operation()
            assert error.value.code == ErrorCode.REJECTED


@pytest.mark.parametrize(
    "value", ["=1+1", " +cmd", "-2", "@formula", "\tvalue", "\rvalue", "\nvalue"]
)
def test_csv_formula_cells_are_escaped(value):
    assert AuditQueryService._cell(value) == "'" + value


def test_oversize_export_invalid_range_and_cross_scope_port_are_rejected():
    access, rows, audit, query = setup()
    many = [dict(rows[0], event_id=i) for i in range(6000)]
    service = AuditQueryService(Queries(many), audit)
    with pytest.raises(PortError) as error:
        service.export(access, query, AuditContext("test"))
    assert error.value.code == ErrorCode.INVALID_INPUT and not audit.records
    with pytest.raises(PortError):
        service.export(access, replace(query, before=2), AuditContext("test"))
    with pytest.raises(PortError):
        AuditQuery(query.start, query.start + timedelta(days=32))
    with pytest.raises(PortError):
        AuditQuery(query.start.replace(tzinfo=None), query.end)
    with pytest.raises(PortError) as scope:
        AuditQueryService(Queries([dict(rows[0], workspace_id=str(uuid4()))]), audit).search(
            access, query
        )
    assert scope.value.code == ErrorCode.UNAVAILABLE


def test_stored_projection_discards_opaque_fields_session_digest_and_masks_ip():
    now = datetime.now(UTC)
    target = uuid4()
    row = (
        1,
        uuid4(),
        "user",
        uuid4(),
        "audit.test",
        "success",
        now,
        "api.test",
        "203.0.113.8",
        uuid4(),
        {
            "target_id": str(target),
            "session_digest": "a" * 64,
            "secret": "private-test-key",
            "provider_payload": {"password": "never"},
        },
    )
    safe = _safe_row(row)
    assert safe["details"] == {"target_id": str(target)}
    assert safe["source_network"] == "203.0.113.0/24"
    assert "private-test-key" not in str(safe) and "session_digest" not in str(safe)
