"""Export new offline contracts without regenerating frozen plan/publication schemas."""

import json
from pathlib import Path

from qtrade_data_contracts.research_replay import (
    ResearchReplay,
    ResearchReplayLimits,
    ResearchReplayRequest,
    ResearchReplaySubmission,
)

root = Path(__file__).resolve().parents[1] / "packages/data-contracts/schemas/v2"
for model in (
    ResearchReplayLimits,
    ResearchReplayRequest,
    ResearchReplay,
    ResearchReplaySubmission,
):
    schema = model.model_json_schema(mode="serialization")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"urn:qtrade:data:v2:{model.__name__}"
    (root / f"{model.__name__}.schema.json").write_text(
        json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
