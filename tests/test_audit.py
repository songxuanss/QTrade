from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from qtrade_domain.audit_port import AuditRecord
from qtrade_mock.audit import MockAuditStore


def record():
    return AuditRecord(
        uuid4(),
        "user",
        uuid4(),
        "auth.login",
        "success",
        datetime.now(UTC),
        "api",
        uuid4(),
        "127.0.0.1",
    )


def test_audit_records_required_context_without_accepting_secret_payloads():
    store = MockAuditStore()
    first = record()
    assert store.append(first) == 1
    assert store.append(replace(first, action="auth.logout")) == 2
    assert store.records[0] == first
    assert not hasattr(first, "tokens")


@pytest.mark.parametrize(
    "changes",
    [
        {"actor_type": "owner"},
        {"actor_id": None},
        {"actor_type": "anonymous"},
        {"result": "unknown"},
        {"action": ""},
        {"source": ""},
        {"occurred_at": datetime.now()},
        {"source_ip": "forged-ip"},
    ],
)
def test_invalid_audit_context_is_rejected(changes):
    with pytest.raises(ValueError):
        replace(record(), **changes)


def test_audit_details_are_typed_and_preserve_false_without_secret_payloads():
    import json

    from qtrade_domain.audit_port import AuditDetails
    from qtrade_domain.identity import Role

    target = uuid4()
    details = AuditDetails(
        target_id=target,
        role_before=Role.ADMIN,
        role_after=Role.VIEWER,
        active_before=True,
        active_after=False,
    )
    assert json.loads(details.canonical_json()) == {
        "target_id": str(target),
        "role_before": "admin",
        "role_after": "viewer",
        "active_before": True,
        "active_after": False,
    }
    with pytest.raises(TypeError):
        AuditDetails(access_token="never-store-a-token")


@pytest.mark.parametrize(
    "changes",
    [
        {"target_id": "raw-secret"},
        {"permission": "raw-secret"},
        {"reason": "raw-secret"},
        {"session_digest": "raw-cookie"},
        {"role_after": "owner"},
        {"active_after": "true"},
    ],
)
def test_arbitrary_audit_details_are_rejected(changes):
    from qtrade_domain.audit_port import AuditDetails

    with pytest.raises(ValueError):
        AuditDetails(**changes)
