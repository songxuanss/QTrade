"""Session-derived schedule identities and frozen pool references at the bridge."""

import json
from collections.abc import Callable
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from qtrade_api.security import ApiSecurity
from qtrade_data_contracts.schedules import CollectionScheduleControl, CollectionScheduleDraft
from qtrade_data_core.ports import DataFailure, UniverseRepository
from qtrade_domain.authorization import Permission, WorkspaceAccess


class ScheduleSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schedule_id: UUID
    name: str = Field(min_length=1, max_length=256)
    instrument_ids: list[str] = Field(default_factory=list, max_length=10000)
    snapshot_id: UUID | None = None
    source: str = "mairui.history.bars"
    cadence: Literal["daily", "weekly", "quarterly"] = "daily"
    timezone: str = "Asia/Shanghai"
    local_time: str
    weekday: StrictInt = Field(default=4, ge=0, le=6)
    lookback_days: StrictInt = Field(default=366, ge=1, le=3660)
    catchup_days: StrictInt = Field(default=7, ge=1, le=31)
    approved_parts: StrictInt = Field(default=100, ge=1, le=1000000)
    approved_credits: StrictInt = Field(default=500, ge=1, le=1000000000)
    automation_confirmed: StrictBool = False


def register_schedule_bridge(
    router: APIRouter,
    security: ApiSecurity,
    remote: Callable[..., Any],
    universes: UniverseRepository | None,
) -> None:
    @router.get("/schedules")
    def listing(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
        offset: Annotated[int, Query(ge=0, le=1000000)] = 0,
    ) -> Any:
        return remote("GET", "/v1/schedules", access, params={"limit": 100, "offset": offset})

    @router.post("/schedules", status_code=201)
    def create(
        body: ScheduleSelection,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.DATA_MANAGE, mutate=True))
        ],
    ) -> Any:
        identifiers = sorted(set(body.instrument_ids))
        reference: dict[str, object] = {}
        if not identifiers and body.snapshot_id is not None:
            if universes is None:
                raise HTTPException(503, detail={"code": "unavailable"})
            try:
                snapshot = universes.get(access.workspace.workspace_id, body.snapshot_id)
            except DataFailure as error:
                code = {"not_found": 404, "forbidden": 403}.get(error.code, 503)
                raise HTTPException(code, detail={"code": error.code}) from None
            identifiers = sorted({m.instrument.instrument_id for m in snapshot.members})
            reference = {
                "membership_snapshot_id": str(snapshot.snapshot_id),
                "membership_content_sha256": snapshot.content_sha256,
            }
        try:
            values = body.model_dump(
                mode="json", exclude={"instrument_ids", "snapshot_id", "source"}
            )
            draft = CollectionScheduleDraft.model_validate_json(
                json.dumps(
                    {
                        **values,
                        "schema_version": 1,
                        "scope_id": str(access.workspace.workspace_id),
                        "actor_id": str(access.user_id),
                        "selection": {
                            "kind": "market",
                            "source": body.source,
                            "dataset_kind": "bars",
                            "granularity": "1d",
                            "instrument_ids": identifiers,
                            **reference,
                        },
                    }
                )
            )
        except ValueError:
            raise HTTPException(400, detail={"code": "invalid_input"}) from None
        return remote("POST", "/v1/schedules", access, draft.model_dump(mode="json"))

    @router.post("/schedules/{schedule}/{action}")
    def control(
        schedule: UUID,
        action: Literal["enable", "disable"],
        body: CollectionScheduleControl,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.DATA_MANAGE, mutate=True))
        ],
    ) -> Any:
        return remote("POST", f"/v1/schedules/{schedule}/{action}", access, body.model_dump())
