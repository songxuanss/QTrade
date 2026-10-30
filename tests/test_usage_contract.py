"""Public usage evidence rejects invented accounting and contradictory states."""

import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from qtrade_data_contracts.usage import UsageSnapshot


def snapshot():
    return dict(
        schema_version=1,
        scope_id=str(uuid4()),
        source="mairui",
        day="2026-10-13",
        timezone="Asia/Shanghai",
        generated_at="2026-10-13T12:00:00Z",
        resets_at="2026-10-13T16:00:00Z",
        source_budget=dict(
            daily_limit=100, credits_used=5, active_reservations=0, concurrency_limit=4
        ),
        scope_budget=dict(
            daily_limit=50, credits_used=5, active_reservations=0, concurrency_limit=2
        ),
        request_count=1,
        success_count=1,
        failure_count=0,
        expired_count=0,
        denial_count=0,
        response_bytes=2,
        accounting="internal_credit_safeguard",
        recent_denials=[],
        recent_requests=[
            dict(
                request_id=str(uuid4()),
                operation="factors.history",
                credits=5,
                reserved_at="2026-10-13T11:00:00Z",
                lease_until="2026-10-13T12:00:00Z",
                state="finished",
                lease_elapsed=True,
                outcome="success",
                error_code=None,
                duration_ms=100,
                response_bytes=2,
            )
        ],
    )


def test_snapshot_roundtrip_and_rejection_evidence():
    body = snapshot()
    accepted = UsageSnapshot.model_validate_json(json.dumps(body))
    assert UsageSnapshot.model_validate_json(accepted.model_dump_json()) == accepted
    body["denial_count"] = 1
    body["recent_denials"] = [
        dict(
            request_id=str(uuid4()),
            operation="bars.history",
            credits=1,
            reason="scope_daily",
            denied_at="2026-10-13T11:30:00Z",
        )
    ]
    assert (
        UsageSnapshot.model_validate_json(json.dumps(body)).recent_denials[0].reason
        == "scope_daily"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", True),
        ("schema_version", 2),
        ("request_count", -1),
        ("request_count", "1"),
        ("accounting", "vendor_invoice"),
        ("timezone", "bad-zone"),
        ("day", "2026-10-12"),
        ("success_count", 2),
        ("resets_at", "2026-10-13T10:00:00Z"),
    ],
)
def test_invalid_snapshot_or_invented_semantics_rejected(field, value):
    body = snapshot() | {field: value}
    with pytest.raises(ValidationError):
        UsageSnapshot.model_validate_json(json.dumps(body))


@pytest.mark.parametrize(
    "field,value",
    [
        ("state", "reserved"),
        ("outcome", "failure"),
        ("lease_elapsed", False),
        ("duration_ms", None),
        ("operation", "https://private/source"),
    ],
)
def test_request_ownership_and_result_must_agree(field, value):
    body = snapshot()
    body["recent_requests"][0][field] = value
    with pytest.raises(ValidationError):
        UsageSnapshot.model_validate_json(json.dumps(body))
