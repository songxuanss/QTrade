"""Authenticated UI-facing adapter for portable stock-pool collection campaigns."""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from qtrade_api.security import ApiSecurity
from qtrade_data_contracts import CollectionSelection
from qtrade_data_core.campaigns import CollectionGateway
from qtrade_data_core.ports import DataFailure
from qtrade_domain.authorization import Permission, WorkspaceAccess


class PreviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_ids: list[UUID] = Field(min_length=1, max_length=100)
    instrument_ids: list[str] = Field(default_factory=list, max_length=10000)
    request_id: UUID = Field(default_factory=uuid4)
    granularity: Literal["tick", "1m", "5m", "15m", "1h", "1d"] = "1d"
    start: datetime
    end: datetime
    mode: Literal["missing", "all"] = "missing"


class SubmitBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: UUID
    coverage_version: str = Field(pattern="^[a-f0-9]{64}$")


def campaign_router(service: CollectionGateway | None, security: ApiSecurity) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/workspaces/{workspace_id}/data/campaigns", tags=["collection-campaigns"]
    )

    def configured() -> CollectionGateway:
        if service is None:
            raise HTTPException(503, detail={"code": "unavailable"})
        return service

    def public(error: DataFailure) -> HTTPException:
        return HTTPException(
            {
                "invalid_input": 400,
                "not_found": 404,
                "forbidden": 403,
                "conflict": 409,
                "unsupported": 400,
            }.get(error.code, 503),
            detail={"code": error.code},
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/preview")
    def preview(
        body: PreviewBody,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.DATA_MANAGE, mutate=True))
        ],
    ) -> dict[str, object]:
        if body.start.utcoffset() is None or body.end.utcoffset() is None or body.start >= body.end:
            raise HTTPException(400, detail={"code": "invalid_input"})
        try:
            selection = CollectionSelection(
                schema_version=1,
                scope_id=access.workspace.workspace_id,
                actor_id=access.user_id,
                request_id=body.request_id,
                correlation_id=uuid4(),
                snapshot_ids=tuple(body.snapshot_ids),
                instrument_ids=tuple(body.instrument_ids),
                source="mairui.a-share.tick"
                if body.granularity == "tick"
                else "mairui.a-share.bar",
                authorization_id="mairui-default",
                granularity=body.granularity,
                start=body.start.astimezone(UTC),
                end=body.end.astimezone(UTC),
                mode=body.mode,
                transformation_version="raw-tick.v1"
                if body.granularity == "tick"
                else "mairui.unadjusted-bars.v1",
            )
            return configured().preview(selection).model_dump(mode="json")
        except ValueError:
            raise HTTPException(400, detail={"code": "invalid_input"}) from None
        except DataFailure as error:
            raise public(error) from None

    @router.post("", status_code=201)
    def submit(
        body: SubmitBody,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.DATA_MANAGE, mutate=True))
        ],
    ) -> dict[str, str]:
        try:
            identity = configured().submit(
                access.user_id, access.workspace.workspace_id, body.plan_id, body.coverage_version
            )
            return {"campaign_id": str(identity)}
        except DataFailure as error:
            raise public(error) from None

    @router.get("")
    def runs(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> list[dict[str, object]]:
        try:
            return configured().runs(access.user_id, access.workspace.workspace_id)
        except DataFailure as error:
            raise public(error) from None

    @router.get("/{campaign_id}")
    def run(
        campaign_id: UUID,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
        member_offset: Annotated[int, Query(ge=0, le=10000)] = 0,
    ) -> dict[str, object]:
        try:
            return configured().run(
                access.user_id, access.workspace.workspace_id, campaign_id, member_offset
            )
        except DataFailure as error:
            raise public(error) from None

    @router.post("/{campaign_id}/{action}")
    def control(
        campaign_id: UUID,
        action: Literal["retry", "cancel"],
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.DATA_MANAGE, mutate=True))
        ],
    ) -> dict[str, bool]:
        try:
            configured().control(access.user_id, access.workspace.workspace_id, campaign_id, action)
            return {"accepted": True}
        except DataFailure as error:
            raise public(error) from None

    return router
