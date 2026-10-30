"""Export standalone collection schemas without importing trading contracts."""

import json
from pathlib import Path

from qtrade_data_contracts import CONTRACTS
from qtrade_data_contracts.collection import (
    CollectedBar,
    CollectionJobRequest,
    CollectionPreview,
    CollectionPublication,
    CollectionRecordPage,
    FactorDefinition,
    FactorPoint,
    InstrumentIdentity,
    SourceDataset,
)
from qtrade_data_contracts.planning import CollectionCostEstimate, CollectionPlanGuard
from qtrade_data_contracts.processing_failure import CollectionProcessingFailure
from qtrade_data_contracts.schedule_dispatch import ScheduleDispatchDecision
from qtrade_data_contracts.schedules import (
    CollectionScheduleControl,
    CollectionScheduleDraft,
    CollectionScheduleRecord,
)
from qtrade_data_contracts.source_profiles import (
    CapabilityFact,
    SourceProfile,
    SourceProfileCatalog,
)
from qtrade_data_contracts.usage import UsageSnapshot

CONTRACTS = (
    CapabilityFact,
    SourceProfile,
    SourceProfileCatalog,
    *CONTRACTS,
    CollectedBar,
    CollectionJobRequest,
    CollectionPreview,
    CollectionPublication,
    CollectionRecordPage,
    FactorDefinition,
    FactorPoint,
    SourceDataset,
    InstrumentIdentity,
    UsageSnapshot,
    CollectionCostEstimate,
    CollectionPlanGuard,
    CollectionProcessingFailure,
    CollectionScheduleDraft,
    CollectionScheduleRecord,
    CollectionScheduleControl,
    ScheduleDispatchDecision,
)

output = Path(__file__).resolve().parents[1] / "packages/data-contracts/schemas/v1"
output.mkdir(parents=True, exist_ok=True)
for model in CONTRACTS:
    schema = model.model_json_schema(mode="serialization")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"urn:qtrade:data:v1:{model.__name__}"
    (output / f"{model.__name__}.schema.json").write_text(
        json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )
print(f"Exported {len(CONTRACTS)} standalone v1 data schemas")
