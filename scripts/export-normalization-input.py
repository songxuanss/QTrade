"""Export the additive checkpoint; never regenerate frozen v1 artifact schemas."""

import json
from pathlib import Path

from qtrade_data_contracts.normalization_input import CollectionNormalizationInput

root = Path(__file__).resolve().parents[1] / "packages/data-contracts/schemas/v2"
schema = CollectionNormalizationInput.model_json_schema(mode="serialization")
schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
schema["$id"] = "urn:qtrade:data:v2:CollectionNormalizationInput"
(root / "CollectionNormalizationInput.schema.json").write_text(
    json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
)
