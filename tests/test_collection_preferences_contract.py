"""Collection draft schema v2 stays readable for v1 and rejects invalid cards."""

import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from qtrade_contracts.collection_preferences import (
    CollectionDimensionDraft,
    CollectionPreferences,
)


def _v1() -> dict[str, object]:
    return {
        "schema_version": 1,
        "dataset_kind": "bars",
        "source": "mairui.pro.bars",
        "granularity": "5m",
        "first": "2025-09-01",
        "last": "2025-09-05",
        "instruments": "601288.SH",
        "fields": ["pe_ttm"],
        "mode": "missing",
    }


def test_v1_draft_remains_readable() -> None:
    draft = CollectionPreferences.model_validate_json(json.dumps(_v1()))
    assert draft.schema_version == 1
    assert draft.dimensions == ()


def test_v2_round_trips_dimensions_in_order() -> None:
    first, second = uuid4(), uuid4()
    draft = CollectionPreferences.model_validate_json(
        json.dumps(
            {
                **_v1(),
                "schema_version": 2,
                "dimensions": [
                    {
                        "dimension_id": str(first),
                        "dataset_kind": "bars",
                        "source": "mairui.history.bars",
                        "granularity": "1d",
                        "first": "2025-12-01",
                        "last": "2025-12-03",
                        "instruments": "601288.SH",
                        "snapshot_ids": [],
                        "fields": [],
                        "mode": "all",
                    },
                    {
                        "dimension_id": str(second),
                        "dataset_kind": "factors",
                        "source": "mairui.factor.history",
                        "granularity": "1d",
                        "instruments": "600519.SH",
                        "fields": ["pe_ttm"],
                        "mode": "missing",
                    },
                ],
            }
        )
    )
    assert draft.schema_version == 2
    assert [item.dimension_id for item in draft.dimensions] == [first, second]
    restored = CollectionPreferences.model_validate_json(
        draft.model_dump_json()
    )
    assert restored == draft
    assert isinstance(draft.dimensions[0], CollectionDimensionDraft)


def test_v2_rejects_duplicate_and_unknown_cards() -> None:
    duplicate = {
        **_v1(),
        "schema_version": 2,
        "dimensions": [
            {"dimension_id": "11111111-1111-4111-8111-111111111111"},
            {"dimension_id": "11111111-1111-4111-8111-111111111111"},
        ],
    }
    with pytest.raises(ValidationError):
        CollectionPreferences.model_validate_json(json.dumps(duplicate))
    unknown = {
        **_v1(),
        "schema_version": 2,
        "dimensions": [
            {
                "dimension_id": "11111111-1111-4111-8111-111111111111",
                "unexpected": True,
            }
        ],
    }
    with pytest.raises(ValidationError):
        CollectionPreferences.model_validate_json(json.dumps(unknown))
    with pytest.raises(ValidationError):
        CollectionPreferences.model_validate_json(
            json.dumps({**_v1(), "schema_version": 3})
        )
