"""Browser identity adapter for the independently owned universe catalog."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from qtrade_api.security import ApiSecurity
from qtrade_data_contracts.source_profiles import SourceProfileCatalog
from qtrade_data_core.ports import DataFailure
from qtrade_data_core.source_profiles import SourceProfileDirectory
from qtrade_data_core.universes import UniverseCatalog
from qtrade_domain.authorization import Permission, WorkspaceAccess


class ResolveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    universe_id: str = Field(min_length=1, max_length=256)


class CustomBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    instrument_ids: list[str] = Field(min_length=1, max_length=10000)


def universe_router(
    service: UniverseCatalog | None,
    security: ApiSecurity,
    source_profiles: SourceProfileDirectory | None = None,
) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/workspaces/{workspace_id}/data/universes", tags=["universes"]
    )

    def configured() -> UniverseCatalog:
        if service is None:
            raise HTTPException(503, detail={"code": "unavailable"})
        return service

    def public(error: DataFailure) -> HTTPException:
        status = {"invalid_input": 400, "forbidden": 403, "not_found": 404, "conflict": 409}.get(
            error.code, 503
        )
        return HTTPException(
            status, detail={"code": error.code}, headers={"Cache-Control": "no-store"}
        )

    @router.get("/source-profiles")
    def profiles(
        response: Response,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> SourceProfileCatalog:
        if source_profiles is None:
            raise HTTPException(503, detail={"code": "unavailable"})
        try:
            value = source_profiles.read(access.user_id, access.workspace.workspace_id)
        except DataFailure as error:
            raise public(error) from None
        response.headers["Cache-Control"] = "no-store"
        return value

    @router.get("/definitions")
    def definitions(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
        search: Annotated[str, Query(max_length=100)] = "",
        category: Literal["industry", "theme", "index_members"] | None = None,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> dict[str, object]:
        try:
            items, total = configured().definitions(
                access.user_id,
                access.workspace.workspace_id,
                search=search,
                category=category,
                offset=offset,
                limit=limit,
            )
            return {
                "items": [item.model_dump(mode="json") for item in items],
                "total": total,
                "offset": offset,
                "limit": limit,
            }
        except DataFailure as error:
            raise public(error) from None

    @router.post("/resolve", status_code=201)
    def resolve(
        body: ResolveBody,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.DATA_MANAGE, mutate=True))
        ],
    ) -> dict[str, object]:
        try:
            return (
                configured()
                .resolve(access.user_id, access.workspace.workspace_id, body.universe_id)
                .model_dump(mode="json")
            )
        except DataFailure as error:
            raise public(error) from None

    @router.post("/custom", status_code=201)
    def custom(
        body: CustomBody,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.DATA_MANAGE, mutate=True))
        ],
    ) -> dict[str, object]:
        try:
            return (
                configured()
                .custom(
                    access.user_id,
                    access.workspace.workspace_id,
                    body.name,
                    tuple(body.instrument_ids),
                )
                .model_dump(mode="json")
            )
        except DataFailure as error:
            raise public(error) from None

    @router.get("/snapshots")
    def snapshots(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        before_time: datetime | None = None,
        before_id: UUID | None = None,
    ) -> list[dict[str, object]]:
        if (before_time is None) != (before_id is None):
            raise HTTPException(400, "both pagination fields required")
        before = (
            (before_time, before_id) if before_time is not None and before_id is not None else None
        )
        try:
            return [
                item.model_dump(mode="json")
                for item in configured().snapshots(
                    access.user_id, access.workspace.workspace_id, limit=limit, before=before
                )
            ]
        except DataFailure as error:
            raise public(error) from None

    @router.get("/snapshots/{snapshot_id}")
    def snapshot(
        snapshot_id: UUID,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> dict[str, object]:
        try:
            return (
                configured()
                .snapshot(access.user_id, access.workspace.workspace_id, snapshot_id)
                .model_dump(mode="json")
            )
        except DataFailure as error:
            raise public(error) from None

    return router
