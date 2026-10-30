"""Derive identities from scoped manifests; only managers can request a new check."""

import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from qtrade_api.security import ApiSecurity
from qtrade_data_contracts.collection import CollectionPublication
from qtrade_data_contracts.freshness import FreshnessCheckRequest, FreshnessKey, FreshnessResult
from qtrade_data_contracts.freshness_health import FreshnessHealthPage, FreshnessHealthQuery
from qtrade_data_contracts.freshness_watch import (
    FreshnessWatch,
    FreshnessWatchControl,
    FreshnessWatchCreate,
    watch_identity,
)
from qtrade_domain.authorization import Permission, WorkspaceAccess


def register_freshness_bridge(
    router: APIRouter, security: ApiSecurity, remote: Callable[..., Any]
) -> None:
    max_age = int(os.environ.get("QTRADE_FRESHNESS_SNAPSHOT_MAX_AGE_SECONDS", "900"))
    if not 1 <= max_age <= 86400:
        raise ValueError("freshness snapshot age must be within 1–86400 seconds")

    def selection(publication_id: UUID, access: WorkspaceAccess) -> FreshnessKey:
        value = remote("GET", f"/v1/publications/{publication_id}", access)
        try:
            publication = CollectionPublication.model_validate_json(json.dumps(value))
        except ValueError:
            raise HTTPException(503, detail={"code": "unavailable"}) from None
        if (
            publication.scope_id != access.workspace.workspace_id
            or publication.publication_id != publication_id
        ):
            raise HTTPException(503, detail={"code": "unavailable"})
        if (
            publication.dataset_kind != "bars"
            or publication.granularity != "1d"
            or publication.evidence.get("adjustment") != "none"
        ):
            raise HTTPException(400, detail={"code": "unsupported"})
        return FreshnessKey(
            scope_id=publication.scope_id,
            source=publication.source,
            instrument_id=publication.instrument_id,
            dataset_kind="bars",
            period="1d",
            adjustment="unadjusted",
            date_axis="observation_date",
            mapping_version=publication.mapping_version,
        )

    def view(
        value: Any, key: FreshnessKey, response: Response, access: WorkspaceAccess
    ) -> dict[str, Any]:
        try:
            snapshot = FreshnessResult.model_validate_json(json.dumps(value))
        except ValueError:
            raise HTTPException(503, detail={"code": "unavailable"}) from None
        if snapshot.key != key:
            raise HTTPException(503, detail={"code": "unavailable"})
        watch_issue = None
        try:
            raw_watch = remote("GET", f"/v1/freshness/watches/{watch_identity(key)}", access)
        except HTTPException as error:
            if error.status_code != 404:
                raise
        else:
            try:
                watch = FreshnessWatch.model_validate_json(json.dumps(raw_watch))
            except ValueError:
                raise HTTPException(503, detail={"code": "unavailable"}) from None
            if watch.key != key:
                raise HTTPException(503, detail={"code": "unavailable"})
            # A failed later automatic check must override an older healthy
            # snapshot, even while that snapshot's age TTL has not elapsed.
            if watch.last_attempt_at is not None and watch.last_attempt_at >= snapshot.checked_at:
                watch_issue = watch.last_error_code
        now = datetime.now(UTC)
        age = (now - snapshot.checked_at).total_seconds()
        # This is the latest check for the logical dataset, not a live probe or
        # an assessment limited to the particular publication used to locate it.
        response.headers["Cache-Control"] = "no-store"
        return {
            "snapshot": snapshot.model_dump(mode="json"),
            "read_at": now.isoformat(),
            "age_seconds": max(0, int(age)),
            "max_age_seconds": max_age,
            "stale": age > max_age,
            "clock_skew": age < 0,
            "watch_issue": watch_issue,
        }

    @router.get("/publications/{publication_id}/freshness")
    def latest(
        publication_id: UUID,
        response: Response,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> dict[str, Any]:
        key = selection(publication_id, access)
        value = remote("POST", "/v1/freshness/latest", access, body=key.model_dump(mode="json"))
        return view(value, key, response, access)

    @router.post("/publications/{publication_id}/freshness/check")
    def check(
        publication_id: UUID,
        response: Response,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.DATA_MANAGE, mutate=True))
        ],
    ) -> dict[str, Any]:
        key = selection(publication_id, access)
        # A command carries only a publication reference. Calendar, source
        # observations and checkpoint clocks are verified inside the collector.
        body = FreshnessCheckRequest(publication_id=publication_id)
        value = remote("POST", "/v1/freshness/check", access, body=body.model_dump(mode="json"))
        return view(value, key, response, access)

    def watch_value(
        value: Any,
        access: WorkspaceAccess,
        response: Response,
        *,
        key: FreshnessKey | None = None,
        watch_id: UUID | None = None,
    ) -> dict[str, Any]:
        try:
            watch = FreshnessWatch.model_validate_json(json.dumps(value))
        except ValueError:
            raise HTTPException(503, detail={"code": "unavailable"}) from None
        if (
            watch.scope_id != access.workspace.workspace_id
            or (key is not None and watch.key != key)
            or (watch_id is not None and watch.watch_id != watch_id)
        ):
            raise HTTPException(503, detail={"code": "unavailable"})
        response.headers["Cache-Control"] = "no-store"
        return watch.model_dump(mode="json")

    @router.get("/publications/{publication_id}/freshness/watch")
    def get_watch(
        publication_id: UUID,
        response: Response,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> dict[str, Any]:
        key = selection(publication_id, access)
        value = remote("GET", f"/v1/freshness/watches/by-publication/{publication_id}", access)
        return watch_value(value, access, response, key=key)

    @router.post("/publications/{publication_id}/freshness/watch")
    def create_watch(
        publication_id: UUID,
        response: Response,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.DATA_MANAGE, mutate=True))
        ],
    ) -> dict[str, Any]:
        key = selection(publication_id, access)
        body = FreshnessWatchCreate(publication_id=publication_id)
        value = remote("POST", "/v1/freshness/watches", access, body=body.model_dump(mode="json"))
        return watch_value(value, access, response, key=key)

    @router.post("/freshness/watches/{watch_id}/control")
    def control_watch(
        watch_id: UUID,
        body: FreshnessWatchControl,
        response: Response,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.DATA_MANAGE, mutate=True))
        ],
    ) -> dict[str, Any]:
        value = remote(
            "POST",
            f"/v1/freshness/watches/{watch_id}/control",
            access,
            body=body.model_dump(mode="json"),
        )
        payload = watch_value(value, access, response, watch_id=watch_id)
        if (
            payload["revision"] != body.revision + 1
            or payload["enabled"] != body.enabled
            or (body.enabled and payload["actor_id"] != str(access.user_id))
        ):
            raise HTTPException(503, detail={"code": "unavailable"})
        return payload

    @router.get("/freshness/health")
    def health(
        response: Response,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        after_key: Annotated[str | None, Query(pattern=r"^[a-f0-9]{64}$")] = None,
    ) -> FreshnessHealthPage:
        body = FreshnessHealthQuery(
            scope_id=access.workspace.workspace_id, limit=limit, after_key=after_key
        )
        value = remote("POST", "/v1/freshness/health", access, body=body.model_dump(mode="json"))
        try:
            page = FreshnessHealthPage.model_validate_json(json.dumps(value))
        except ValueError:
            raise HTTPException(503, detail={"code": "unavailable"}) from None
        if (
            page.scope_id != access.workspace.workspace_id
            or len(page.entries) > limit
            or (after_key is not None and any(row.key_sha256 <= after_key for row in page.entries))
            or (page.next_key is not None and len(page.entries) != limit)
        ):
            raise HTTPException(503, detail={"code": "unavailable"})
        response.headers["Cache-Control"] = "no-store"
        return page
