"""Export new execution-control contracts without rewriting frozen v2 schemas."""

import json
from pathlib import Path

from qtrade_data_contracts.research_planning import ResearchPlanPreview, ResearchSubmission
from qtrade_data_contracts.research_processing import ResearchProcessingFailure

output = Path(__file__).resolve().parents[1] / "packages/data-contracts/schemas/v2"
output.mkdir(parents=True, exist_ok=True)
for model in (ResearchPlanPreview, ResearchSubmission, ResearchProcessingFailure):
    schema = model.model_json_schema(mode="serialization")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"urn:qtrade:data:v2:{model.__name__}"
    (output / f"{model.__name__}.schema.json").write_text(
        json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )
print("Exported 3 v2 research execution-control/check schemas")
