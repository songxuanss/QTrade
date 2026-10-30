"""Export additive offline v2 contracts; never regenerate frozen v1 schemas."""

import json
from pathlib import Path

from qtrade_data_contracts.collection_replay import (
    CollectionInputReference,
    CollectionReplay,
    CollectionReplayInputs,
    CollectionReplayLimits,
    CollectionReplayParent,
    CollectionReplayQueueRequest,
    CollectionReplayRequest,
    CollectionReplaySource,
    CollectionReplaySubmission,
)

output = Path(__file__).resolve().parents[1] / "packages/data-contracts/schemas/v2"
output.mkdir(parents=True, exist_ok=True)
for model in (
    CollectionInputReference,
    CollectionReplay,
    CollectionReplayInputs,
    CollectionReplayLimits,
    CollectionReplayParent,
    CollectionReplayQueueRequest,
    CollectionReplayRequest,
    CollectionReplaySource,
    CollectionReplaySubmission,
):
    schema = model.model_json_schema(mode="serialization")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"urn:qtrade:data:v2:{model.__name__}"
    (output / f"{model.__name__}.schema.json").write_text(
        json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
