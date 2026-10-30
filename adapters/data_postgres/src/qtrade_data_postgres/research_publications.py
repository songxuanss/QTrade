"""Quality-bound publication ledger and frozen version/as-of reader."""

import json
from collections import defaultdict
from uuid import UUID

from qtrade_data_contracts.collection import digest
from qtrade_data_contracts.research import ResearchObservation
from qtrade_data_contracts.research_processing import ResearchProcessingFailure
from qtrade_data_contracts.research_publication import (
    ResearchPublicationManifest,
    ResearchQualityResult,
)
from qtrade_data_contracts.research_transport import ResearchPlan, ResearchQuery, ResearchRecordPage
from qtrade_data_core.ports import DataFailure
from qtrade_data_core.research import ResearchPublicationReference
from qtrade_data_core.research_evidence import ResearchArchivePort
from qtrade_data_core.research_quality import logical_key
from qtrade_data_postgres.research import PostgresResearchEvidence


class PostgresResearchPublications(PostgresResearchEvidence):
    def plan(self, scope: UUID, request_id: UUID) -> ResearchPlan:
        with self._connection() as conn:
            return self._plan(conn, scope, request_id)

    def record_quality(self, result: ResearchQualityResult) -> ResearchQualityResult:
        artifact = self.artifact_manifest(result.scope_id, result.artifact_id)
        if artifact.records_sha256 != result.records_sha256:
            raise DataFailure("conflict")
        with self._connection() as conn:
            conn.execute(
                """INSERT INTO qtrade_data.research_quality(scope_id,quality_id,artifact_id,result)
                VALUES(%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING""",
                (result.scope_id, result.quality_id, result.artifact_id, result.model_dump_json()),
            )
            row = conn.execute(
                "SELECT result FROM qtrade_data.research_quality "
                "WHERE scope_id=%s AND quality_id=%s",
                (result.scope_id, result.quality_id),
            ).fetchone()
            if row is None:
                raise DataFailure("conflict")
            stored = ResearchQualityResult.model_validate_json(json.dumps(row[0]))
            if stored.model_dump(exclude={"checked_at"}) != result.model_dump(
                exclude={"checked_at"}
            ):
                raise DataFailure("conflict")
            return stored

    def publish(self, manifest: ResearchPublicationManifest) -> ResearchPublicationManifest:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT manifest FROM qtrade_data.research_publication "
                "WHERE scope_id=%s AND publication_id=%s",
                (manifest.scope_id, manifest.publication_id),
            ).fetchone()
            if row is not None:
                stored = ResearchPublicationManifest.model_validate_json(json.dumps(row[0]))
                if stored.artifact != manifest.artifact or stored.quality != manifest.quality:
                    raise DataFailure("conflict")
                return stored
            conn.execute(
                """INSERT INTO qtrade_data.research_publication
                (scope_id,publication_id,artifact_id,quality_id,manifest)
                VALUES(%s,%s,%s,%s,%s::jsonb)
                ON CONFLICT DO NOTHING""",
                (
                    manifest.scope_id,
                    manifest.publication_id,
                    manifest.artifact.artifact_id,
                    manifest.quality.quality_id,
                    manifest.model_dump_json(),
                ),
            )
            row = conn.execute(
                "SELECT manifest FROM qtrade_data.research_publication "
                "WHERE scope_id=%s AND publication_id=%s",
                (manifest.scope_id, manifest.publication_id),
            ).fetchone()
            if row is None:
                raise DataFailure("conflict")
            stored = ResearchPublicationManifest.model_validate_json(json.dumps(row[0]))
            if stored.artifact != manifest.artifact or stored.quality != manifest.quality:
                raise DataFailure("conflict")
            return stored

    def manifest(self, scope: UUID, publication_id: UUID) -> ResearchPublicationManifest:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT manifest FROM qtrade_data.research_publication "
                "WHERE scope_id=%s AND publication_id=%s",
                (scope, publication_id),
            ).fetchone()
            if row is None:
                raise DataFailure("not_found")
            return ResearchPublicationManifest.model_validate_json(json.dumps(row[0]))


class PostgresResearchReader:
    def __init__(self, publications: PostgresResearchPublications, archive: ResearchArchivePort):
        self.publications, self.archive = publications, archive

    def processing_failures(
        self, scope: UUID, request: UUID
    ) -> tuple[ResearchProcessingFailure, ...]:
        return self.archive.processing_failures(self.publications.plan(scope, request))

    def manifest(self, scope: UUID, publication_id: UUID) -> ResearchPublicationManifest:
        return self.publications.manifest(scope, publication_id)

    def reference(self, scope_id: UUID, publication_id: UUID) -> ResearchPublicationReference:
        manifest = self.manifest(scope_id, publication_id)
        return ResearchPublicationReference(
            scope_id, publication_id, manifest.content_version, manifest.artifact.capability
        )

    def page(self, query: ResearchQuery) -> ResearchRecordPage:
        manifest = self.manifest(query.scope_id, query.publication_id)
        if manifest.content_version != query.content_version:
            raise DataFailure("conflict")
        if manifest.artifact.capability.kind != query.kind:
            raise DataFailure("unsupported")
        values = self.archive.records(manifest.artifact)
        if query.as_of is not None:
            if not manifest.artifact.capability.supports_as_of:
                raise DataFailure("unsupported")
            versions: dict[tuple[str, ...], list[ResearchObservation]] = defaultdict(list)
            for record in values:
                if (
                    record.knowledge.status == "evidenced"
                    and record.knowledge.known_at is not None
                    and record.knowledge.known_at <= query.as_of
                ):
                    versions[logical_key(record)].append(record)
            selected = []
            for record_versions in versions.values():
                latest = max(
                    r.knowledge.known_at
                    for r in record_versions
                    if r.knowledge.known_at is not None
                )
                candidates = [r for r in record_versions if r.knowledge.known_at == latest]
                if len({r.content_version for r in candidates}) != 1:
                    raise DataFailure("unverified")
                selected.append(candidates[0])
            values = tuple(selected)
        binding = digest(
            [
                str(query.scope_id),
                str(query.publication_id),
                query.content_version,
                query.as_of.isoformat() if query.as_of else None,
                query.kind,
            ]
        )[:32]
        offset = 0
        if query.cursor is not None:
            try:
                left, right = query.cursor.split(".", 1)
                if not left.isdecimal() or right != binding:
                    raise ValueError
                offset = int(left)
            except ValueError:
                raise DataFailure("invalid_input") from None
        if offset > len(values):
            raise DataFailure("invalid_input")
        records = values[offset : offset + query.limit]
        next_offset = offset + len(records)
        return ResearchRecordPage(
            scope_id=query.scope_id,
            publication_id=query.publication_id,
            content_version=query.content_version,
            kind=query.kind,
            cursor=query.cursor,
            next_cursor=f"{next_offset}.{binding}" if next_offset < len(values) else None,
            records=records,
            page_sha256=digest([r.model_dump(mode="json") for r in records]),
        )
