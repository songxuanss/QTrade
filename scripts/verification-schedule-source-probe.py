"""Three bounded real source diagnostics; raw receipts, no jobs or publication.

Copy this file into the collector and invoke with actor/scope UUIDs. The guarded
file entry point is required by the same spawned HTTP transport used in service.
Only classifications/counts leave the container, never URLs or raw payloads.
"""

import json
import sys
from datetime import datetime
from uuid import UUID, uuid4

from qtrade_collector.main import create_app
from qtrade_data_contracts.collection import CollectionJobRequest
from qtrade_data_core.collection import CollectionPart
from qtrade_data_core.ports import DataFailure
from qtrade_data_core.usage_execution import usage_scope


def main() -> None:
    scope, actor = UUID(sys.argv[1]), UUID(sys.argv[2])
    service = create_app().state.collection_service
    service.auth.require(actor, scope, "manage")
    results = []
    for start, end in (
        ("2026-10-07T16:00:00+00:00", "2026-10-14T16:00:00+00:00"),
        ("2026-10-12T16:00:00+00:00", "2026-10-13T16:00:00+00:00"),
        ("2025-08-31T16:00:00+00:00", "2025-09-01T16:00:00+00:00"),
    ):
        request = CollectionJobRequest(
            schema_version=1,
            scope_id=scope,
            actor_id=actor,
            request_id=uuid4(),
            correlation_id=uuid4(),
            source="mairui.history.bars",
            dataset_kind="bars",
            instrument_ids=("600036.SH",),
            granularity="1d",
            start=datetime.fromisoformat(start),
            end=datetime.fromisoformat(end),
        )
        part = CollectionPart(
            uuid4(),
            request.request_id,
            request,
            "600036.SH",
            None,
            request.start,
            request.end,
            "mairui.history-bars.v2",
        )
        receipts = []

        def capture(kind: str, payload: object, part=part, receipts=receipts) -> None:
            receipt = service.archive.capture_raw(part, kind, payload)
            receipts.append({"kind": kind, "rows": receipt.response_rows})

        outcome = {"start": start, "end": end}
        try:
            with usage_scope(actor, scope):
                result = service.providers[0].fetch(part, capture=capture)
            outcome["standard_rows"] = len(result.records)
        except DataFailure as error:
            outcome["error_code"] = error.code
        outcome["captures"] = receipts
        results.append(outcome)
    print(json.dumps(results))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("Bounded source diagnostics failed; private details withheld") from None
