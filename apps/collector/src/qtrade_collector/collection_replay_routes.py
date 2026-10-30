"""Read-only metadata and explicit offline commands; no private RAW endpoint."""

import json
from collections.abc import Callable, Mapping
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, Response
from pydantic import BeforeValidator

from qtrade_data_contracts.collection_replay import (
    CollectionReplay,
    CollectionReplayInputs,
    CollectionReplayLimits,
    CollectionReplayRequest,
    CollectionReplaySubmission,
)
from qtrade_data_core.collection_replay import CollectionReplayService
from qtrade_data_core.ports import DataFailure


def limits(config: Mapping[str, str]) -> CollectionReplayLimits:
    values = {}
    for key in ("rows", "bytes", "receipts"):
        value = config.get(f"COLLECTOR_NORMALIZATION_REPLAY_MAX_{key.upper()}")
        if value is not None:
            if not value.isascii() or not value.isdecimal() or len(value) > 10:
                raise ValueError("invalid normalization replay bounds")
            values["max_" + key] = int(value)
    try:
        return CollectionReplayLimits.model_validate(values)
    except ValueError:
        raise ValueError("invalid normalization replay bounds") from None


def command_wire(value: object) -> CollectionReplayRequest:
    if isinstance(value, CollectionReplayRequest):
        return value
    return CollectionReplayRequest.model_validate_json(json.dumps(value))


def submission_wire(value: object) -> CollectionReplaySubmission:
    if isinstance(value, CollectionReplaySubmission):
        return value
    return CollectionReplaySubmission.model_validate_json(json.dumps(value))


CommandBody = Annotated[CollectionReplayRequest, BeforeValidator(command_wire)]
SubmitBody = Annotated[CollectionReplaySubmission, BeforeValidator(submission_wire)]


def register_collection_replay_routes(app: FastAPI, subject: Callable[..., object]) -> None:
    def service() -> CollectionReplayService:
        return app.state.collection_replays  # type: ignore[no-any-return]

    @app.get("/v2/collection/jobs/{job_id}/parts/{part_id}/normalization-inputs")
    def inputs(
        job_id: UUID,
        part_id: UUID,
        response: Response,
        identity: Annotated[tuple[UUID, UUID], Depends(subject)],
    ) -> CollectionReplayInputs:
        response.headers["Cache-Control"] = "no-store"
        return service().catalog(*identity, job_id, part_id)

    @app.post("/v2/collection/replays/preview")
    def preview(
        body: CommandBody,
        response: Response,
        identity: Annotated[tuple[UUID, UUID], Depends(subject)],
    ) -> CollectionReplay:
        if (body.actor_id, body.scope_id) != identity:
            raise DataFailure("forbidden")
        response.headers["Cache-Control"] = "no-store"
        return service().preview(body)

    @app.post("/v2/collection/replays")
    def submit(
        body: SubmitBody,
        response: Response,
        identity: Annotated[tuple[UUID, UUID], Depends(subject)],
    ) -> dict[str, Any]:
        if (body.actor_id, body.scope_id) != identity:
            raise DataFailure("forbidden")
        response.headers["Cache-Control"] = "no-store"
        return service().submit(body.command(), body.control_sha256)

    @app.get("/v2/collection/replays/{request_id}")
    def intent(
        request_id: UUID,
        response: Response,
        identity: Annotated[tuple[UUID, UUID], Depends(subject)],
    ) -> CollectionReplay:
        response.headers["Cache-Control"] = "no-store"
        return service().read(*identity, request_id)
