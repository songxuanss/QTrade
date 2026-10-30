"""Safe research rejection fields cannot imply approved observations."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from qtrade_data_contracts.research_processing import ResearchProcessingFailure


def failure():
    return ResearchProcessingFailure(
        failure_id=uuid4(),
        scope_id=uuid4(),
        request_id=uuid4(),
        batch_id=uuid4(),
        plan_sha256="a" * 64,
        raw_sha256="b" * 64,
        raw_content_sha256="c" * 64,
        source="fixture.research",
        kind="financial_statement",
        mapping_version="fixture.v1",
        checker_version="research-normalization-check.v1",
        stage="normalization",
        error_code="unverified",
        status="blocked",
        failed_at=datetime.now(UTC),
    )


@pytest.mark.parametrize(
    "change",
    [
        {"normalized_record_count": 0},
        {"status": "passed"},
        {"schema_version": True},
        {"schema_version": 1},
        {"error_code": "private error"},
        {"message": "private error"},
        {"failed_at": "2026-10-14T12:00:00"},
        {"raw_sha256": "bad"},
    ],
)
def test_research_failure_keeps_unknown_and_safe_fields(change):
    value = failure()
    assert ResearchProcessingFailure.model_validate_json(value.model_dump_json()) == value
    with pytest.raises(ValidationError):
        ResearchProcessingFailure.model_validate_json(
            json.dumps(value.model_dump(mode="json") | change)
        )
