"""Authenticated UI bridge to the separately deployed data collector."""

import hmac
import json
from datetime import date, datetime
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictBool

from qtrade_api.collector_collection_replay import register_collection_replay_bridge
from qtrade_api.collector_freshness import register_freshness_bridge
from qtrade_api.collector_replay import register_replay_bridge
from qtrade_api.collector_research_queries import register_research_query_bridge
from qtrade_api.collector_schedules import register_schedule_bridge
from qtrade_api.security import ApiSecurity, public_error
from qtrade_application.workspaces import WorkspaceService
from qtrade_contracts.collection_preferences import CollectionPreferences
from qtrade_data_contracts.collection import CollectionJobRequest, CollectionWireRequest
from qtrade_data_contracts.research_acquisition import ResearchResponsePage
from qtrade_data_contracts.research_planning import ResearchSubmission
from qtrade_data_contracts.research_transport import (
    DateAxis,
    ResearchKind,
    ResearchPlanRequest,
    ResearchQuery,
)
from qtrade_data_contracts.source_profiles import SourceProfileCatalog
from qtrade_data_core.ports import DataFailure, UniverseRepository
from qtrade_data_http.client import CollectorClient
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.ports import PortError


class ImportStatus(Protocol):
    def state(self, scope: UUID, publication: UUID) -> dict[str, object]: ...
    def names(self, scope: UUID) -> dict[str, str]: ...


class PreferencesStore(Protocol):
    def get(self, actor: UUID, scope: UUID) -> CollectionPreferences | None: ...
    def put(self, actor: UUID, scope: UUID, draft: CollectionPreferences) -> None: ...


def preferences_wire(value: object) -> CollectionPreferences:
    if isinstance(value, CollectionPreferences):
        return value
    return CollectionPreferences.model_validate_json(json.dumps(value))


PreferencesBody = Annotated[CollectionPreferences, BeforeValidator(preferences_wire)]


class PreviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    dataset_kind: Literal["bars", "factors"]
    instrument_ids: list[str] = Field(default_factory=list, max_length=10000)
    snapshot_ids: list[UUID] = Field(default_factory=list, max_length=100)
    granularity: Literal["1m", "5m", "15m", "30m", "1h", "1d"]
    start: datetime
    end: datetime
    fields: list[str] = Field(default_factory=list, max_length=200)
    mode: Literal["missing", "all"] = "missing"
    request_id: UUID = Field(default_factory=uuid4)


class SubmitBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: CollectionWireRequest
    coverage_version: str
    confirmed: StrictBool = False


class ResearchSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    kind: ResearchKind
    instrument_ids: list[str] = Field(min_length=1, max_length=10000)
    date_axis: DateAxis
    start: date
    end: date
    mode: Literal["missing", "all", "review"] = "missing"
    request_id: UUID = Field(default_factory=uuid4)


def research_plan_wire(value: object) -> ResearchPlanRequest:
    if isinstance(value, ResearchPlanRequest):
        return value
    return ResearchPlanRequest.model_validate_json(json.dumps(value))


def research_query_wire(value: object) -> ResearchQuery:
    if isinstance(value, ResearchQuery):
        return value
    return ResearchQuery.model_validate_json(json.dumps(value))


ResearchPlanBody = Annotated[ResearchPlanRequest, BeforeValidator(research_plan_wire)]
ResearchQueryBody = Annotated[ResearchQuery, BeforeValidator(research_query_wire)]


def research_submission_wire(value: object) -> ResearchSubmission:
    if isinstance(value, ResearchSubmission):
        return value
    return ResearchSubmission.model_validate_json(json.dumps(value))


ResearchSubmissionBody = Annotated[ResearchSubmission, BeforeValidator(research_submission_wire)]


def collector_router(
    client: CollectorClient | None,
    security: ApiSecurity,
    status: ImportStatus | None,
    universes: UniverseRepository | None,
    preferences: PreferencesStore | None = None,
) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/workspaces/{workspace_id}/data/collector", tags=["remote-collector"]
    )

    @router.get("/preferences")
    def get_preferences(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> CollectionPreferences | None:
        if preferences is None:
            raise HTTPException(503, detail={"code": "unavailable"})
        try:
            return preferences.get(access.user_id, access.workspace.workspace_id)
        except PortError as error:
            raise public_error(error) from None

    @router.put("/preferences")
    def put_preferences(
        body: PreferencesBody,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.DATA_MANAGE, mutate=True))
        ],
    ) -> CollectionPreferences:
        if preferences is None:
            raise HTTPException(503, detail={"code": "unavailable"})
        try:
            preferences.put(access.user_id, access.workspace.workspace_id, body)
            return body
        except PortError as error:
            raise public_error(error) from None

    def remote(
        method: str, path: str, access: WorkspaceAccess, body: Any = None, params: Any = None
    ) -> Any:
        if client is None:
            raise HTTPException(503, detail={"code": "unavailable"})
        try:
            return client.request(
                method,
                path,
                actor_id=access.user_id,
                scope_id=access.workspace.workspace_id,
                body=body,
                params=params,
            )
        except DataFailure as error:
            code = {
                "forbidden": 403,
                "unauthorized": 403,
                "invalid_input": 400,
                "unsupported": 400,
                "unverified": 400,
                "not_found": 404,
                "conflict": 409,
                "rate_limited": 429,
            }.get(error.code, 503)
            raise HTTPException(code, detail={"code": error.code}) from None

    register_schedule_bridge(router, security, remote, universes)
    register_freshness_bridge(router, security, remote)
    register_collection_replay_bridge(router, security, remote)
    register_replay_bridge(router, security, remote)
    register_research_query_bridge(router, security, remote)

    def annotated_job(result: dict[str, Any], access: WorkspaceAccess) -> dict[str, Any]:
        for part in result.get("parts", []):
            publication = part.get("publication")
            if publication:
                part["consumer"] = (
                    status.state(access.workspace.workspace_id, UUID(publication["publication_id"]))
                    if status and publication["dataset_kind"] == "bars"
                    else {"state": "browsable", "error_code": None}
                )
        return result

    @router.get("/catalog")
    def catalog(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> Any:
        return remote("GET", "/v1/catalog", access)

    @router.get("/factors")
    def factors(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> Any:
        return remote("GET", "/v1/factors", access)

    @router.get("/source-profiles")
    def source_profiles(
        response: Response,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> SourceProfileCatalog:
        value = remote("GET", "/v1/source-profiles", access)
        try:
            result = SourceProfileCatalog.model_validate_json(json.dumps(value))
        except ValueError:
            raise HTTPException(503, detail={"code": "unavailable"}) from None
        if result.scope_id != access.workspace.workspace_id:
            raise HTTPException(503, detail={"code": "unavailable"})
        response.headers["Cache-Control"] = "no-store"
        return result

    @router.get("/factor-catalog")
    def factor_catalog(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> Any:
        return remote("GET", "/v1/factor-catalog", access)

    @router.get("/usage")
    def usage_snapshot(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> Any:
        return remote("GET", "/v1/usage", access)

    @router.get("/default-range")
    def default_range(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
        source: Annotated[str, Query(min_length=1, max_length=100)],
        granularity: Literal["1m", "5m", "15m", "30m", "1h", "1d"],
    ) -> Any:
        return remote(
            "GET",
            "/v1/default-range",
            access,
            params={"source": source, "granularity": granularity},
        )

    @router.get("/names")
    def names(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
        instrument_id: Annotated[list[str] | None, Query(max_length=200)] = None,
    ) -> dict[str, str]:
        saved = status.names(access.workspace.workspace_id) if status else {}
        if client and instrument_id:
            missing = [value for value in instrument_id if value not in saved]
            if missing:
                try:
                    saved.update(
                        client.request(
                            "GET",
                            "/v1/names",
                            actor_id=access.user_id,
                            scope_id=access.workspace.workspace_id,
                            params=[("instrument_id", value) for value in missing],
                        )
                    )
                except DataFailure:
                    pass
        return saved

    @router.post("/preview")
    def preview(
        body: PreviewBody,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.DATA_MANAGE, mutate=True))
        ],
    ) -> Any:
        identifiers = set(body.instrument_ids)
        try:
            # Industries/indices and explicit companies are combined into one
            # frozen request. Deduplicate so overlapping members are fetched once.
            for snapshot_id in body.snapshot_ids:
                if universes is None:
                    raise DataFailure("unavailable")
                identifiers.update(
                    member.instrument.instrument_id
                    for member in universes.get(access.workspace.workspace_id, snapshot_id).members
                )
            if not identifiers:
                raise DataFailure("invalid_input")
            request = CollectionJobRequest.model_validate_json(
                json.dumps(
                    {
                        "schema_version": 1,
                        "scope_id": str(access.workspace.workspace_id),
                        "actor_id": str(access.user_id),
                        "correlation_id": str(uuid4()),
                        "request_id": str(body.request_id),
                        "source": body.source,
                        "dataset_kind": body.dataset_kind,
                        "instrument_ids": sorted(identifiers),
                        "granularity": body.granularity,
                        "start": body.start.isoformat(),
                        "end": body.end.isoformat(),
                        "fields": body.fields,
                        "mode": body.mode,
                    }
                )
            )
            return remote("POST", "/v1/preview", access, request.model_dump(mode="json"))
        except (ValueError, DataFailure):
            raise HTTPException(400, detail={"code": "invalid_input"}) from None

    @router.post("/jobs", status_code=201)
    def submit(
        body: SubmitBody,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.DATA_MANAGE, mutate=True))
        ],
    ) -> Any:
        if (
            body.request.actor_id != access.user_id
            or body.request.scope_id != access.workspace.workspace_id
        ):
            raise HTTPException(403, detail={"code": "forbidden"})
        return annotated_job(
            remote("POST", "/v1/jobs", access, body.model_dump(mode="json")), access
        )

    @router.get("/jobs")
    def jobs(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> Any:
        return [annotated_job(item, access) for item in remote("GET", "/v1/jobs", access)]

    @router.get("/jobs/{job_id}/parts/{part_id}/processing-failures")
    def processing_failures(
        job_id: UUID,
        part_id: UUID,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> Any:
        return remote("GET", f"/v1/jobs/{job_id}/parts/{part_id}/processing-failures", access)

    @router.get("/jobs/{job_id}/parts/{part_id}/quality")
    def qualities(
        job_id: UUID,
        part_id: UUID,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> Any:
        return remote("GET", f"/v1/jobs/{job_id}/parts/{part_id}/quality", access)

    @router.get("/jobs/{job_id}")
    def job(
        job_id: UUID,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> Any:
        return annotated_job(
            remote("GET", f"/v1/jobs/{job_id}", access, params={"offset": offset}), access
        )

    @router.post("/jobs/{job_id}/{action}")
    def control(
        job_id: UUID,
        action: Literal["retry", "cancel"],
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.DATA_MANAGE, mutate=True))
        ],
    ) -> Any:
        return remote("POST", f"/v1/jobs/{job_id}/{action}", access)

    @router.get("/publications/{publication_id}/records")
    def records(
        publication_id: UUID,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> Any:
        return remote(
            "GET",
            f"/v1/publications/{publication_id}/records",
            access,
            params={"offset": offset, "limit": 100},
        )

    @router.get("/research/catalog")
    def research_catalog(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> Any:
        return remote("GET", "/v2/research/catalog", access)

    @router.post("/research/preview")
    def research_preview(
        body: ResearchSelection,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.DATA_MANAGE, mutate=True))
        ],
    ) -> Any:
        try:
            request = ResearchPlanRequest.model_validate_json(
                json.dumps(
                    body.model_dump(mode="json")
                    | {
                        "schema_version": 2,
                        "actor_id": str(access.user_id),
                        "scope_id": str(access.workspace.workspace_id),
                        "correlation_id": str(uuid4()),
                    }
                )
            )
        except ValueError:
            raise HTTPException(400, detail={"code": "invalid_input"}) from None
        return remote("POST", "/v2/research/plan-preview", access, request.model_dump(mode="json"))

    @router.post("/research/jobs", status_code=201)
    def research_submit(
        body: ResearchSubmissionBody,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.DATA_MANAGE, mutate=True))
        ],
    ) -> Any:
        if body.actor_id != access.user_id or body.scope_id != access.workspace.workspace_id:
            raise HTTPException(403, detail={"code": "forbidden"})
        return remote("POST", "/v2/research/jobs", access, body.model_dump(mode="json"))

    @router.get("/research/jobs")
    def research_jobs(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> Any:
        return remote("GET", "/v2/research/jobs", access)

    @router.get("/research/jobs/{request_id}")
    def research_job(
        request_id: UUID,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> Any:
        return remote("GET", f"/v2/research/jobs/{request_id}", access)

    @router.get("/research/publications/{publication_id}")
    def research_manifest(
        publication_id: UUID,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> Any:
        return remote("GET", f"/v2/research/publications/{publication_id}", access)

    @router.get("/research/jobs/{request_id}/processing-failures")
    def research_processing_failures(
        request_id: UUID,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> Any:
        return remote("GET", f"/v2/research/jobs/{request_id}/processing-failures", access)

    @router.get("/research/jobs/{request_id}/response-receipts")
    def research_response_receipts(
        response: Response,
        request_id: UUID,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        after_id: UUID | None = None,
    ) -> ResearchResponsePage:
        # Scope/actor are session-derived. There is deliberately no body-read or
        # arbitrary archive path route alongside this historical metadata query.
        params = {"limit": str(limit)}
        if after_id is not None:
            params["after_id"] = str(after_id)
        value = remote(
            "GET", f"/v2/research/jobs/{request_id}/response-receipts", access, params=params
        )
        try:
            page = ResearchResponsePage.model_validate_json(json.dumps(value))
        except ValueError:
            raise HTTPException(503, detail={"code": "unavailable"}) from None
        if (
            (page.scope_id, page.request_id) != (access.workspace.workspace_id, request_id)
            or len(page.receipts) > limit
            or (page.next_id is not None and len(page.receipts) != limit)
            or any(r.receipt_id == after_id for r in page.receipts)
        ):
            raise HTTPException(503, detail={"code": "unavailable"})
        response.headers["Cache-Control"] = "no-store"
        return page

    @router.post("/research/jobs/{request_id}/retry")
    def research_retry(
        request_id: UUID,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.DATA_MANAGE, mutate=True))
        ],
    ) -> Any:
        return remote("POST", f"/v2/research/jobs/{request_id}/retry", access)

    @router.post("/research/query")
    def research_query(
        body: ResearchQueryBody,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.DATA_MANAGE, mutate=True))
        ],
    ) -> Any:
        if body.actor_id != access.user_id or body.scope_id != access.workspace.workspace_id:
            raise HTTPException(403, detail={"code": "forbidden"})
        return remote("POST", "/v2/research/query", access, body.model_dump(mode="json"))

    return router


def collector_authorization_router(
    workspaces: WorkspaceService | None, token: str | None
) -> APIRouter:
    auth_router = APIRouter()

    class AuthorizationBody(BaseModel):
        model_config = ConfigDict(extra="forbid")
        actor_id: UUID
        scope_id: UUID
        action: Literal["read", "manage"]

    @auth_router.post("/internal/data/authorize", include_in_schema=False)
    def authorize(
        body: AuthorizationBody, authorization: Annotated[str | None, Header()] = None
    ) -> dict[str, bool]:
        if not token or not hmac.compare_digest(authorization or "", "Bearer " + token):
            raise HTTPException(401, detail={"code": "unauthorized"})
        if workspaces is None:
            raise HTTPException(503, detail={"code": "unavailable"})
        try:
            workspaces.authorize(
                body.actor_id,
                body.scope_id,
                Permission.DATA_MANAGE if body.action == "manage" else Permission.READ,
            )
            return {"allowed": True}
        except PortError as error:
            raise public_error(error) from None

    return auth_router
