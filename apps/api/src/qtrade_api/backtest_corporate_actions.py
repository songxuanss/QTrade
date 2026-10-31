"""Load published corporate actions for a frozen backtest dataset range."""

import json
from collections.abc import Iterator
from datetime import datetime

from qtrade_data_contracts.research import CorporateAction
from qtrade_data_contracts.research_publication import ResearchPublicationManifest
from qtrade_data_contracts.research_transport import ResearchQuery, ResearchRecordPage
from qtrade_data_core.ports import DataFailure
from qtrade_data_http.client import CollectorClient
from qtrade_domain.authorization import WorkspaceAccess
from qtrade_domain.ports import ErrorCode, PortError


class CollectorCorporateActions:
    """Read immutable published events through the collector's public contract."""

    def __init__(self, client: CollectorClient) -> None:
        self._client = client

    def load(
        self,
        access: WorkspaceAccess,
        instrument_ids: tuple[str, ...],
        start: datetime,
        end: datetime,
    ) -> tuple[CorporateAction, ...]:
        if start.utcoffset() is None or end.utcoffset() is None or start >= end:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid corporate-action range")
        try:
            jobs = self._client.request(
                "GET",
                "/v2/research/jobs",
                actor_id=access.user_id,
                scope_id=access.workspace.workspace_id,
            )
            if not isinstance(jobs, list):
                raise DataFailure("unavailable")
            wanted = set(instrument_ids)
            seen: dict[str, CorporateAction] = {}
            for job in jobs:
                if not isinstance(job, dict):
                    continue
                request = job.get("request")
                publication_id = job.get("publication_id")
                if (
                    not isinstance(request, dict)
                    or request.get("kind") != "corporate_action"
                    or not isinstance(publication_id, str)
                    or job.get("state") != "completed"
                ):
                    continue
                job_ids = {
                    value for value in request.get("instrument_ids", ()) if isinstance(value, str)
                }
                if not job_ids & wanted:
                    continue
                manifest_value = self._client.request(
                    "GET",
                    f"/v2/research/publications/{publication_id}",
                    actor_id=access.user_id,
                    scope_id=access.workspace.workspace_id,
                )
                manifest = ResearchPublicationManifest.model_validate_json(
                    json.dumps(manifest_value)
                )
                if (
                    manifest.scope_id != access.workspace.workspace_id
                    or manifest.artifact.capability.kind != "corporate_action"
                ):
                    raise DataFailure("conflict")
                for page in self._pages(access, manifest):
                    for record in page.records:
                        if not isinstance(record, CorporateAction):
                            raise DataFailure("conflict")
                        if (
                            record.instrument_id not in wanted
                            or record.status != "implemented"
                            or record.ex_date is None
                            or not start.date() <= record.ex_date < end.date()
                        ):
                            continue
                        previous = seen.get(record.event_identity)
                        if (
                            previous is not None
                            and previous.content_version != record.content_version
                        ):
                            raise DataFailure("conflict")
                        seen[record.event_identity] = record
            return tuple(
                sorted(seen.values(), key=lambda item: (item.ex_date, item.event_identity))
            )
        except DataFailure as error:
            code = {
                "timeout": ErrorCode.TIMEOUT,
                "rate_limited": ErrorCode.RATE_LIMITED,
                "forbidden": ErrorCode.UNAUTHORIZED,
                "not_found": ErrorCode.NOT_FOUND,
                "invalid_input": ErrorCode.INVALID_INPUT,
            }.get(error.code, ErrorCode.UNAVAILABLE)
            raise PortError(code, "corporate action publication unavailable") from None
        except (ValueError, TypeError, KeyError):
            raise PortError(ErrorCode.UNAVAILABLE, "invalid corporate action publication") from None

    def _pages(
        self, access: WorkspaceAccess, manifest: ResearchPublicationManifest
    ) -> Iterator[ResearchRecordPage]:
        cursor = None
        for _ in range(100):
            query = ResearchQuery(
                actor_id=access.user_id,
                scope_id=access.workspace.workspace_id,
                publication_id=manifest.publication_id,
                content_version=manifest.content_version,
                kind="corporate_action",
                cursor=cursor,
                limit=1000,
            )
            value = self._client.request(
                "POST",
                "/v2/research/query",
                actor_id=access.user_id,
                scope_id=access.workspace.workspace_id,
                body=query.model_dump(mode="json"),
            )
            page = ResearchRecordPage.model_validate_json(json.dumps(value))
            if (
                page.scope_id != query.scope_id
                or page.publication_id != query.publication_id
                or page.content_version != query.content_version
                or page.kind != "corporate_action"
            ):
                raise DataFailure("conflict")
            yield page
            if page.next_cursor is None:
                return
            cursor = page.next_cursor
        raise DataFailure("unavailable")
