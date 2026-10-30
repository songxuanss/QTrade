"""Offline research commands, isolated from supplier download HTTP routes."""

import json
from collections.abc import Callable, Mapping
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, Response
from pydantic import BeforeValidator

from qtrade_data_contracts.research_replay import (
    ResearchReplay,
    ResearchReplayLimits,
    ResearchReplayRequest,
    ResearchReplaySubmission,
)
from qtrade_data_core.ports import DataFailure
from qtrade_data_core.research_replay import ResearchReplayService


def replay_limits(config: Mapping[str, str]) -> ResearchReplayLimits:
    """Deployment may tighten bounds; request payloads cannot override them."""
    values = {}
    for key in ("pages", "rows", "bytes"):
        value = config.get(f"COLLECTOR_RESEARCH_REPLAY_MAX_{key.upper()}")
        if value is not None:
            if not value.isascii() or not value.isdecimal() or len(value) > 10:
                raise ValueError("invalid research replay bounds")
            values[f"max_{key}"] = int(value)
    try:
        return ResearchReplayLimits.model_validate(values)
    except ValueError:
        raise ValueError("invalid research replay bounds") from None


def command_wire(value: object) -> ResearchReplayRequest:
    if isinstance(value, ResearchReplayRequest):
        return value
    return ResearchReplayRequest.model_validate_json(json.dumps(value))


def submission_wire(value: object) -> ResearchReplaySubmission:
    if isinstance(value, ResearchReplaySubmission):
        return value
    return ResearchReplaySubmission.model_validate_json(json.dumps(value))


CommandBody = Annotated[ResearchReplayRequest, BeforeValidator(command_wire)]
SubmissionBody = Annotated[ResearchReplaySubmission, BeforeValidator(submission_wire)]


def register_replay_routes(app: FastAPI, subject: Callable[..., object]) -> None:
    def service() -> ResearchReplayService:
        return app.state.research_replays  # type: ignore[no-any-return]

    @app.post("/v2/research/replays/preview")
    def preview(
        body: CommandBody,
        response: Response,
        identity: Annotated[tuple[UUID, UUID], Depends(subject)],
    ) -> ResearchReplay:
        if (body.actor_id, body.scope_id) != identity:
            raise DataFailure("forbidden")
        response.headers["Cache-Control"] = "no-store"
        return service().preview(body)

    @app.post("/v2/research/replays")
    def submit(
        body: SubmissionBody,
        response: Response,
        identity: Annotated[tuple[UUID, UUID], Depends(subject)],
    ) -> dict[str, Any]:
        if (body.actor_id, body.scope_id) != identity:
            raise DataFailure("forbidden")
        response.headers["Cache-Control"] = "no-store"
        return service().submit(body.command(), body.control_sha256)

    @app.get("/v2/research/replays/{request_id}")
    def intent(
        request_id: UUID,
        response: Response,
        identity: Annotated[tuple[UUID, UUID], Depends(subject)],
    ) -> ResearchReplay:
        response.headers["Cache-Control"] = "no-store"
        return service().read(*identity, request_id)
