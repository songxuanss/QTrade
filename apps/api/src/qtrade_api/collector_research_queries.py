"""Research reads use the current viewer, independently of the collecting actor."""

import json
from collections.abc import Callable
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt

from qtrade_api.security import ApiSecurity
from qtrade_data_contracts.models import Digest, Text
from qtrade_data_contracts.research_transport import ResearchKind, ResearchQuery, ResearchRecordPage
from qtrade_domain.authorization import Permission, WorkspaceAccess


class ResearchReadSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[2] = 2
    publication_id: UUID
    content_version: Digest
    kind: ResearchKind
    cursor: Text | None = None
    limit: Annotated[StrictInt, Field(ge=1, le=1000)] = 100
    as_of: AwareDatetime | None = None


def register_research_query_bridge(
    router: APIRouter, security: ApiSecurity, remote: Callable[..., Any]
) -> None:
    @router.post("/research/records")
    def records(
        body: ResearchReadSelection,
        response: Response,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> ResearchRecordPage:
        # The job creator is provenance, not the user issuing this read. Never
        # impersonate that creator to make a different viewer's query succeed.
        query = ResearchQuery.model_validate_json(
            json.dumps(
                body.model_dump(mode="json")
                | {
                    "actor_id": str(access.user_id),
                    "scope_id": str(access.workspace.workspace_id),
                }
            )
        )
        value = remote("POST", "/v2/research/query", access, query.model_dump(mode="json"))
        try:
            page = ResearchRecordPage.model_validate_json(json.dumps(value))
        except ValueError:
            raise HTTPException(503, detail={"code": "unavailable"}) from None
        if (page.scope_id, page.publication_id, page.content_version, page.kind, page.cursor) != (
            query.scope_id,
            query.publication_id,
            query.content_version,
            query.kind,
            query.cursor,
        ) or len(page.records) > query.limit:
            raise HTTPException(503, detail={"code": "unavailable"})
        response.headers["Cache-Control"] = "no-store"
        return page
