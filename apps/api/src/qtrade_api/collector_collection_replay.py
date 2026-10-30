"""Workspace/session bridge for local reprocessing; no collector storage imports."""

import json
from collections.abc import Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict

from qtrade_api.security import ApiSecurity
from qtrade_data_contracts.collection_replay import (
    CollectionReplay,
    CollectionReplayInputs,
    CollectionReplayRequest,
)
from qtrade_data_contracts.models import Digest
from qtrade_domain.authorization import Permission, WorkspaceAccess


class ReplaySelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    parent_job_id: UUID
    parent_part_id: UUID
    input_id: UUID


class ReplaySubmission(ReplaySelection):
    control_sha256: Digest


def register_collection_replay_bridge(
    router: APIRouter, security: ApiSecurity, remote: Callable[..., Any]
) -> None:
    def command(body: ReplaySelection, access: WorkspaceAccess) -> CollectionReplayRequest:
        try:
            return CollectionReplayRequest(
                actor_id=access.user_id,
                scope_id=access.workspace.workspace_id,
                request_id=body.request_id,
                parent_job_id=body.parent_job_id,
                parent_part_id=body.parent_part_id,
                input_id=body.input_id,
            )
        except ValueError:
            raise HTTPException(400, detail={"code": "invalid_input"}) from None

    def verified(value: Any, scope: UUID, request: UUID) -> CollectionReplay:
        try:
            result = CollectionReplay.model_validate_json(json.dumps(value))
        except ValueError:
            raise HTTPException(503, detail={"code": "unavailable"}) from None
        if (result.command.scope_id, result.command.request_id) != (scope, request):
            raise HTTPException(503, detail={"code": "unavailable"})
        return result

    @router.get("/jobs/{job_id}/parts/{part_id}/normalization-inputs")
    def inputs(
        job_id: UUID,
        part_id: UUID,
        response: Response,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> CollectionReplayInputs:
        data = remote(
            "GET", f"/v2/collection/jobs/{job_id}/parts/{part_id}/normalization-inputs", access
        )
        try:
            value = CollectionReplayInputs.model_validate_json(json.dumps(data))
        except ValueError:
            raise HTTPException(503, detail={"code": "unavailable"}) from None
        if (value.scope_id, value.job_id, value.part_id) != (
            access.workspace.workspace_id,
            job_id,
            part_id,
        ):
            raise HTTPException(503, detail={"code": "unavailable"})
        response.headers["Cache-Control"] = "no-store"
        return value

    @router.post("/replays/preview")
    def preview(
        body: ReplaySelection,
        response: Response,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.DATA_MANAGE, mutate=True))
        ],
    ) -> CollectionReplay:
        draft = command(body, access)
        result = verified(
            remote("POST", "/v2/collection/replays/preview", access, draft.model_dump(mode="json")),
            draft.scope_id,
            draft.request_id,
        )
        if result.command != draft:
            raise HTTPException(503, detail={"code": "unavailable"})
        response.headers["Cache-Control"] = "no-store"
        return result

    @router.post("/replays", status_code=201)
    def submit(
        body: ReplaySubmission,
        response: Response,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.DATA_MANAGE, mutate=True))
        ],
    ) -> Any:
        draft = command(body, access)
        response.headers["Cache-Control"] = "no-store"
        return remote(
            "POST",
            "/v2/collection/replays",
            access,
            draft.model_dump(mode="json") | {"control_sha256": body.control_sha256},
        )

    @router.get("/replays/{request_id}")
    def intent(
        request_id: UUID,
        response: Response,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> CollectionReplay:
        result = verified(
            remote("GET", f"/v2/collection/replays/{request_id}", access),
            access.workspace.workspace_id,
            request_id,
        )
        response.headers["Cache-Control"] = "no-store"
        return result
