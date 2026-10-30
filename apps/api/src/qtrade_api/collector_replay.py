"""Session-derived offline commands; QTrade never reads collector tables/files."""

import json
from collections.abc import Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict

from qtrade_api.security import ApiSecurity
from qtrade_data_contracts.models import Digest
from qtrade_data_contracts.research_replay import ResearchReplay, ResearchReplayRequest
from qtrade_domain.authorization import Permission, WorkspaceAccess


class ReplaySelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    parent_request_id: UUID


class ReplaySubmission(ReplaySelection):
    control_sha256: Digest


def register_replay_bridge(
    router: APIRouter, security: ApiSecurity, remote: Callable[..., Any]
) -> None:
    def command(body: ReplaySelection, access: WorkspaceAccess) -> ResearchReplayRequest:
        try:
            return ResearchReplayRequest(
                actor_id=access.user_id,
                scope_id=access.workspace.workspace_id,
                request_id=body.request_id,
                parent_request_id=body.parent_request_id,
            )
        except ValueError:
            raise HTTPException(400, detail={"code": "invalid_input"}) from None

    def verified(value: Any, scope: UUID, request: UUID) -> ResearchReplay:
        # A malformed or mis-scoped collector response is unavailable, never a
        # reason to expose another tenant's parent/input metadata to the UI.
        try:
            result = ResearchReplay.model_validate_json(json.dumps(value))
        except ValueError:
            raise HTTPException(503, detail={"code": "unavailable"}) from None
        if (result.command.scope_id, result.command.request_id) != (scope, request):
            raise HTTPException(503, detail={"code": "unavailable"})
        return result

    @router.post("/research/replays/preview")
    def preview(
        body: ReplaySelection,
        response: Response,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.DATA_MANAGE, mutate=True))
        ],
    ) -> ResearchReplay:
        draft = command(body, access)
        value = remote(
            "POST", "/v2/research/replays/preview", access, draft.model_dump(mode="json")
        )
        result = verified(value, draft.scope_id, draft.request_id)
        if result.command != draft:
            raise HTTPException(503, detail={"code": "unavailable"})
        response.headers["Cache-Control"] = "no-store"
        return result

    @router.post("/research/replays", status_code=201)
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
            "/v2/research/replays",
            access,
            draft.model_dump(mode="json") | {"control_sha256": body.control_sha256},
        )

    @router.get("/research/replays/{request_id}")
    def intent(
        request_id: UUID,
        response: Response,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> ResearchReplay:
        value = remote("GET", f"/v2/research/replays/{request_id}", access)
        result = verified(value, access.workspace.workspace_id, request_id)
        response.headers["Cache-Control"] = "no-store"
        return result
