"""Export only new freshness schemas; existing immutable contracts stay untouched."""

import json
from pathlib import Path

from qtrade_data_contracts.freshness import (
    FreshnessCheckpoint,
    FreshnessCheckRequest,
    FreshnessEvidence,
    FreshnessExpectation,
    FreshnessHistoryQuery,
    FreshnessKey,
    FreshnessResult,
)
from qtrade_data_contracts.freshness_delivery import (
    FreshnessNoticeAck,
    FreshnessNoticeClaim,
    FreshnessNoticeLease,
)
from qtrade_data_contracts.freshness_health import (
    FreshnessHealthEntry,
    FreshnessHealthPage,
    FreshnessHealthQuery,
)
from qtrade_data_contracts.freshness_notice import FreshnessNotice
from qtrade_data_contracts.freshness_watch import (
    FreshnessWatch,
    FreshnessWatchControl,
    FreshnessWatchCreate,
)

output = Path(__file__).resolve().parents[1] / "packages/data-contracts/schemas/v1"
for model in (
    FreshnessHistoryQuery,
    FreshnessKey,
    FreshnessExpectation,
    FreshnessCheckRequest,
    FreshnessCheckpoint,
    FreshnessEvidence,
    FreshnessResult,
    FreshnessNotice,
    FreshnessNoticeClaim,
    FreshnessNoticeLease,
    FreshnessNoticeAck,
    FreshnessWatch,
    FreshnessWatchCreate,
    FreshnessWatchControl,
    FreshnessHealthEntry,
    FreshnessHealthPage,
    FreshnessHealthQuery,
):
    schema = model.model_json_schema(mode="serialization")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"urn:qtrade:data:v1:{model.__name__}"
    (output / f"{model.__name__}.schema.json").write_text(
        json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )
print("Exported 17 freshness schemas; no existing manifests rewritten")
