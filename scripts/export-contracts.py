"""Export stable JSON Schema 2020-12 wire contracts (run via uv run)."""

import json
from pathlib import Path

from qtrade_contracts import CONTRACTS

output = Path(__file__).resolve().parents[1] / "packages/contracts/schemas/v1"
output.mkdir(parents=True, exist_ok=True)
for model in CONTRACTS:
    schema = model.model_json_schema(mode="serialization")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"urn:qtrade:contracts:v1:{model.__name__}"
    (output / f"{model.__name__}.schema.json").write_text(
        json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )
print(f"Exported {len(CONTRACTS)} v1 schemas")
