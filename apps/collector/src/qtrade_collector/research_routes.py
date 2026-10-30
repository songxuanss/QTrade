"""Versioned research HTTP surface; identity comes from the collector composition."""

import json
from collections.abc import Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, Query, Response
from pydantic import BeforeValidator

from qtrade_data_contracts.research_acquisition import ResearchResponsePage
from qtrade_data_contracts.research_planning import ResearchSubmission
from qtrade_data_contracts.research_transport import ResearchPlanRequest, ResearchQuery
from qtrade_data_core.ports import DataFailure
from qtrade_data_core.research import ResearchService


def plan_wire(value: object) -> ResearchPlanRequest:
    if isinstance(value, ResearchPlanRequest):
        return value
    return ResearchPlanRequest.model_validate_json(json.dumps(value))


def query_wire(value: object) -> ResearchQuery:
    if isinstance(value, ResearchQuery):
        return value
    return ResearchQuery.model_validate_json(json.dumps(value))


PlanBody = Annotated[ResearchPlanRequest, BeforeValidator(plan_wire)]
QueryBody = Annotated[ResearchQuery, BeforeValidator(query_wire)]


def submission_wire(value: object) -> ResearchSubmission:
    if isinstance(value, ResearchSubmission):
        return value
    return ResearchSubmission.model_validate_json(json.dumps(value))


SubmissionBody = Annotated[ResearchSubmission, BeforeValidator(submission_wire)]


def register_research_routes(app: FastAPI, subject: Callable[..., object]) -> None:
    def service() -> ResearchService:
        return app.state.research_service  # type: ignore[no-any-return]

    @app.get("/v2/research/catalog")
    def catalog(identity: Annotated[tuple[UUID, UUID], Depends(subject)]) -> list[dict[str, Any]]:
        return [c.model_dump(mode="json") for c in service().catalog(*identity)]

    @app.post("/v2/research/preview")
    def preview(
        body: PlanBody, identity: Annotated[tuple[UUID, UUID], Depends(subject)]
    ) -> dict[str, Any]:
        if (body.actor_id, body.scope_id) != identity:
            raise DataFailure("forbidden")
        return service().preview(body).model_dump(mode="json")

    @app.post("/v2/research/query")
    def records(
        body: QueryBody, identity: Annotated[tuple[UUID, UUID], Depends(subject)]
    ) -> dict[str, Any]:
        if (body.actor_id, body.scope_id) != identity:
            raise DataFailure("forbidden")
        return service().records(body).model_dump(mode="json")

    @app.post("/v2/research/plan-preview")
    def controlled_preview(
        body: PlanBody, identity: Annotated[tuple[UUID, UUID], Depends(subject)]
    ) -> dict[str, Any]:
        if (body.actor_id, body.scope_id) != identity:
            raise DataFailure("forbidden")
        return service().controlled_preview(body).model_dump(mode="json")

    @app.get("/v2/research/publications/{publication_id}")
    def manifest(
        publication_id: UUID,
        identity: Annotated[tuple[UUID, UUID], Depends(subject)],
    ) -> dict[str, Any]:
        return service().manifest(*identity, publication_id).model_dump(mode="json")

    @app.post("/v2/research/jobs")
    def submit(
        body: SubmissionBody, identity: Annotated[tuple[UUID, UUID], Depends(subject)]
    ) -> dict[str, Any]:
        if (body.actor_id, body.scope_id) != identity:
            raise DataFailure("forbidden")
        return app.state.research_jobs.submit(  # type: ignore[no-any-return]
            body.selection(), body.control_sha256, body.confirmed
        )

    @app.get("/v2/research/jobs")
    def jobs(identity: Annotated[tuple[UUID, UUID], Depends(subject)]) -> Any:
        return app.state.research_jobs.jobs(*identity)

    @app.get("/v2/research/jobs/{request_id}")
    def job(
        request_id: UUID, identity: Annotated[tuple[UUID, UUID], Depends(subject)]
    ) -> dict[str, Any]:
        return app.state.research_jobs.job(*identity, request_id)  # type: ignore[no-any-return]

    @app.get("/v2/research/jobs/{request_id}/processing-failures")
    def processing_failures(
        request_id: UUID, identity: Annotated[tuple[UUID, UUID], Depends(subject)]
    ) -> list[dict[str, Any]]:
        return [
            f.model_dump(mode="json")
            for f in app.state.research_jobs.processing_failures(*identity, request_id)
        ]

    @app.get("/v2/research/jobs/{request_id}/response-receipts")
    def response_receipts(
        response: Response,
        request_id: UUID,
        identity: Annotated[tuple[UUID, UUID], Depends(subject)],
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        after_id: UUID | None = None,
    ) -> ResearchResponsePage:
        value = app.state.research_jobs.response_receipts(*identity, request_id, limit, after_id)
        response.headers["Cache-Control"] = "no-store"
        return value  # type: ignore[no-any-return]

    @app.post("/v2/research/jobs/{request_id}/retry")
    def retry(
        request_id: UUID, identity: Annotated[tuple[UUID, UUID], Depends(subject)]
    ) -> dict[str, Any]:
        return app.state.research_jobs.retry(*identity, request_id)  # type: ignore[no-any-return]
