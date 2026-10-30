"""Export additive acquisition metadata, without rewriting frozen v2 manifests."""

import json
from pathlib import Path

from qtrade_data_contracts.research_acquisition import ResearchResponsePage, ResearchResponseReceipt

output = Path(__file__).resolve().parents[1] / "packages/data-contracts/schemas/v2"
output.mkdir(parents=True, exist_ok=True)
for model in (ResearchResponsePage, ResearchResponseReceipt):
    schema = model.model_json_schema(mode="serialization")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"urn:qtrade:data:v2:{model.__name__}"
    (output / f"{model.__name__}.schema.json").write_text(
        json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )
print("Exported 2 v2 safe research response metadata schemas")
