"""Scoped append-only research metadata and archive lineage; no publication shortcut."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

import psycopg

from qtrade_data_contracts.research_archive import ResearchArtifactManifest, ResearchRawManifest
from qtrade_data_contracts.research_transport import ResearchPlan
from qtrade_data_core.ports import DataFailure


class PostgresResearchEvidence:
    def __init__(self, database_url: str):
        self.url = database_url

    @contextmanager
    def _connection(self) -> Iterator[psycopg.Connection[Any]]:
        try:
            with psycopg.connect(self.url) as conn:
                yield conn
        except psycopg.Error:
            raise DataFailure("unavailable") from None

    @staticmethod
    def _plan(conn: psycopg.Connection[Any], scope: UUID, request: UUID) -> ResearchPlan:
        row = conn.execute(
            "SELECT plan FROM qtrade_data.research_run "
            "WHERE scope_id=%s AND request_id=%s FOR UPDATE",
            (scope, request),
        ).fetchone()
        if row is None:
            raise DataFailure("not_found")
        return ResearchPlan.model_validate_json(json.dumps(row[0]))

    def register(self, plan: ResearchPlan) -> None:
        with self._connection() as conn:
            self._register(conn, plan)

    def _register(self, conn: psycopg.Connection[Any], plan: ResearchPlan) -> None:
        """Allow queue/lineage adapters to freeze a run in their own transaction."""
        conn.execute(
            """INSERT INTO qtrade_data.research_run
            (scope_id,request_id,actor_id,plan_sha256,plan)
            VALUES(%s,%s,%s,%s,%s::jsonb) ON CONFLICT(scope_id,request_id) DO NOTHING""",
            (
                plan.request.scope_id,
                plan.request.request_id,
                plan.request.actor_id,
                plan.plan_sha256,
                plan.model_dump_json(),
            ),
        )
        if self._plan(conn, plan.request.scope_id, plan.request.request_id) != plan:
            raise DataFailure("conflict")

    def append_raw(self, manifest: ResearchRawManifest) -> None:
        with self._connection() as conn:
            plan = self._plan(conn, manifest.scope_id, manifest.request_id)
            if (
                manifest.plan_sha256 != plan.plan_sha256
                or manifest.source != plan.capability.source
                or manifest.kind != plan.capability.kind
                or not any(
                    p.instrument_id == manifest.instrument_id
                    and p.start == manifest.start
                    and p.end == manifest.end
                    for p in plan.partitions
                )
            ):
                raise DataFailure("conflict")
            conn.execute(
                """INSERT INTO qtrade_data.research_raw_batch(scope_id,batch_id,request_id,manifest)
                VALUES(%s,%s,%s,%s::jsonb) ON CONFLICT(scope_id,batch_id) DO NOTHING""",
                (
                    manifest.scope_id,
                    manifest.batch_id,
                    manifest.request_id,
                    manifest.model_dump_json(),
                ),
            )
            row = conn.execute(
                "SELECT manifest FROM qtrade_data.research_raw_batch "
                "WHERE scope_id=%s AND batch_id=%s",
                (manifest.scope_id, manifest.batch_id),
            ).fetchone()
            if (
                row is None
                or ResearchRawManifest.model_validate_json(json.dumps(row[0])) != manifest
            ):
                raise DataFailure("conflict")
            conn.execute(
                """UPDATE qtrade_data.research_run SET stage='raw_persisted',updated_at=now()
                WHERE scope_id=%s AND request_id=%s AND stage='registered'""",
                (manifest.scope_id, manifest.request_id),
            )

    def append_artifact(self, manifest: ResearchArtifactManifest) -> None:
        with self._connection() as conn:
            plan = self._plan(conn, manifest.scope_id, manifest.request_id)
            if manifest.plan_sha256 != plan.plan_sha256 or manifest.capability != plan.capability:
                raise DataFailure("conflict")
            for batch in manifest.raw_batches:
                row = conn.execute(
                    "SELECT manifest FROM qtrade_data.research_raw_batch "
                    "WHERE scope_id=%s AND batch_id=%s",
                    (batch.scope_id, batch.batch_id),
                ).fetchone()
                if (
                    row is None
                    or ResearchRawManifest.model_validate_json(json.dumps(row[0])) != batch
                ):
                    raise DataFailure("conflict")
            conn.execute(
                """INSERT INTO qtrade_data.research_artifact
                (scope_id,artifact_id,request_id,manifest)
                VALUES(%s,%s,%s,%s::jsonb) ON CONFLICT(scope_id,artifact_id) DO NOTHING""",
                (
                    manifest.scope_id,
                    manifest.artifact_id,
                    manifest.request_id,
                    manifest.model_dump_json(),
                ),
            )
            row = conn.execute(
                "SELECT manifest FROM qtrade_data.research_artifact "
                "WHERE scope_id=%s AND artifact_id=%s",
                (manifest.scope_id, manifest.artifact_id),
            ).fetchone()
            if (
                row is None
                or ResearchArtifactManifest.model_validate_json(json.dumps(row[0])) != manifest
            ):
                raise DataFailure("conflict")
            for ordinal, batch in enumerate(manifest.raw_batches):
                conn.execute(
                    """INSERT INTO qtrade_data.research_artifact_raw
                    (scope_id,artifact_id,batch_id,ordinal)
                    VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                    (manifest.scope_id, manifest.artifact_id, batch.batch_id, ordinal),
                )
            conn.execute(
                """UPDATE qtrade_data.research_run SET stage='standard_persisted',updated_at=now()
                WHERE scope_id=%s AND request_id=%s""",
                (manifest.scope_id, manifest.request_id),
            )

    def raw_manifest(self, scope: UUID, batch_id: UUID) -> ResearchRawManifest:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT manifest FROM qtrade_data.research_raw_batch "
                "WHERE scope_id=%s AND batch_id=%s",
                (scope, batch_id),
            ).fetchone()
            if row is None:
                raise DataFailure("not_found")
            return ResearchRawManifest.model_validate_json(json.dumps(row[0]))

    def artifact_manifest(self, scope: UUID, artifact_id: UUID) -> ResearchArtifactManifest:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT manifest FROM qtrade_data.research_artifact "
                "WHERE scope_id=%s AND artifact_id=%s",
                (scope, artifact_id),
            ).fetchone()
            if row is None:
                raise DataFailure("not_found")
            return ResearchArtifactManifest.model_validate_json(json.dumps(row[0]))

    def stage(self, scope: UUID, request_id: UUID) -> str:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT stage FROM qtrade_data.research_run WHERE scope_id=%s AND request_id=%s",
                (scope, request_id),
            ).fetchone()
            if row is None:
                raise DataFailure("not_found")
            return str(row[0])
