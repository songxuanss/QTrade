"""Failure evidence cannot invent observations or carry unstructured secrets."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from qtrade_data_contracts.processing_failure import CollectionProcessingFailure


def failure():
    return CollectionProcessingFailure(
        schema_version=1,
        failure_id=uuid4(),
        scope_id=uuid4(),
        part_id=uuid4(),
        job_id=uuid4(),
        attempt=1,
        checker_version="collection-normalization-check.v1",
        mapping_version="fixture.v1",
        stage="normalization",
        error_code="unverified",
        status="blocked",
        raw_capture_ids=(uuid4(),),
        failed_at=datetime.now(UTC),
    )


@pytest.mark.parametrize(
    "change",
    [
        {"normalized_record_count": 0},
        {"status": "passed"},
        {"attempt": 0},
        {"error_code": "private diagnostic"},
        {"message": "private diagnostic"},
        {"schema_version": True},
        {"schema_version": 2},
        {"raw_capture_ids": []},
    ],
)
def test_rejection_record_is_strict_and_preserves_unknown_quantity(change):
    value = failure()
    assert CollectionProcessingFailure.model_validate_json(value.model_dump_json()) == value
    with pytest.raises(ValidationError):
        CollectionProcessingFailure.model_validate_json(
            json.dumps(value.model_dump(mode="json") | change)
        )


def test_duplicate_receipts_and_naive_time_rejected():
    value = failure()
    body = value.model_dump(mode="json")
    for change in (
        {"raw_capture_ids": body["raw_capture_ids"] * 2},
        {"failed_at": "2026-10-14T12:00:00"},
    ):
        with pytest.raises(ValidationError):
            CollectionProcessingFailure.model_validate_json(json.dumps(body | change))
