"""Workspace-scoped Data Quality Issue persistence and lifecycle transitions."""

import json
from dataclasses import replace
from datetime import datetime
from hashlib import sha256
from typing import cast
from uuid import UUID

import psycopg
from psycopg import sql

from qtrade_domain.authorization import Permission
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.quality import IssueCode, IssueStatus, QualityIssue, Severity, claim, close
from qtrade_postgres.connection import PostgresStore
from qtrade_postgres.workspaces import PostgresWorkspaceStore


class PostgresQualityIssueStore(PostgresStore):
    @staticmethod
    def _decode(row: tuple[object, ...]) -> QualityIssue:
        try:
            evidence = row[10]
            payload = evidence if isinstance(evidence, dict) else json.loads(str(evidence))
            if not isinstance(payload, dict):
                raise ValueError("invalid evidence")
            return QualityIssue(
                issue_id=UUID(str(row[0])),
                workspace_id=UUID(str(row[1])),
                dataset_id=UUID(str(row[2])),
                code=cast(IssueCode, str(row[3])),
                severity=cast(Severity, str(row[4])),
                instrument_id=str(row[5]),
                granularity=str(row[6]),
                starts_at=cast(datetime, row[7]),
                ends_at=cast(datetime, row[8]),
                batch_ids=tuple(UUID(str(item)) for item in cast(list[object], row[9])),
                evidence=json.dumps(payload, sort_keys=True, separators=(",", ":")),
                detected_at=cast(datetime, row[11]),
                status=cast(IssueStatus, str(row[12])),
                claimed_by=UUID(str(row[13])) if row[13] else None,
                closed_by=UUID(str(row[14])) if row[14] else None,
                closed_at=cast(datetime | None, row[15]),
            )
        except (TypeError, ValueError, KeyError, PortError):
            raise PortError(ErrorCode.UNAVAILABLE, "invalid stored data quality issue") from None

    @staticmethod
    def _authorize(
        conn: psycopg.Connection[tuple[object, ...]], actor_id: UUID, workspace_id: UUID
    ) -> None:
        conn.execute(
            "SELECT workspace_id FROM workspace WHERE workspace_id=%s FOR UPDATE", (workspace_id,)
        )
        access = PostgresWorkspaceStore._access(conn, actor_id, workspace_id)
        if access is None:
            raise PortError(ErrorCode.NOT_FOUND, "workspace resource unavailable")
        access.require(Permission.DATA_MANAGE)

    def record_scan(self, actor_id: UUID, issues: tuple[QualityIssue, ...]) -> None:
        if not issues:
            return
        workspaces = {issue.workspace_id for issue in issues}
        if len(workspaces) != 1:
            raise PortError(ErrorCode.INVALID_INPUT, "quality scan spans workspaces")
        workspace_id = next(iter(workspaces))
        with self._connection() as conn:
            self._authorize(conn, actor_id, workspace_id)
            for issue in issues:
                conn.execute(
                    """INSERT INTO data_quality_issue(issue_id,workspace_id,dataset_id,issue_code,
                    severity,instrument_id,granularity,starts_at,ends_at,batch_ids,evidence,
                    evidence_sha256,detected_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::uuid[],%s::jsonb,%s,%s)
                    ON CONFLICT(issue_id) DO NOTHING""",
                    (
                        issue.issue_id,
                        issue.workspace_id,
                        issue.dataset_id,
                        issue.code,
                        issue.severity,
                        issue.instrument_id,
                        issue.granularity,
                        issue.starts_at,
                        issue.ends_at,
                        [str(item) for item in issue.batch_ids],
                        issue.evidence,
                        sha256(issue.evidence.encode()).hexdigest(),
                        issue.detected_at,
                    ),
                )
                row = conn.execute(
                    """SELECT issue_id,workspace_id,dataset_id,issue_code,severity,instrument_id,
                    granularity,starts_at,ends_at,batch_ids,evidence,detected_at,status,claimed_by,
                    closed_by,closed_at FROM data_quality_issue WHERE issue_id=%s""",
                    (issue.issue_id,),
                ).fetchone()
                if (
                    row is None
                    or replace(
                        self._decode(row),
                        status="open",
                        claimed_by=None,
                        closed_by=None,
                        closed_at=None,
                    )
                    != issue
                ):
                    raise PortError(ErrorCode.CONFLICT, "quality issue identity reused")

    def list_issues(
        self, workspace_id: UUID, dataset_id: UUID | None = None, limit: int = 100
    ) -> tuple[QualityIssue, ...]:
        if type(limit) is not int or not 1 <= limit <= 200:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid quality issue page size")
        with self._connection() as conn:
            filter_sql = sql.SQL(" AND dataset_id=%s" if dataset_id is not None else "")
            parameters = (
                (workspace_id, dataset_id, limit)
                if dataset_id is not None
                else (workspace_id, limit)
            )
            rows = conn.execute(
                sql.SQL("""
                SELECT issue_id,workspace_id,dataset_id,issue_code,severity,instrument_id,
                granularity,starts_at,ends_at,batch_ids,evidence,detected_at,status,claimed_by,
                closed_by,closed_at FROM data_quality_issue WHERE workspace_id=%s{}
                ORDER BY starts_at DESC,issue_id LIMIT %s""").format(filter_sql),
                parameters,
            ).fetchall()
        return tuple(self._decode(row) for row in rows)

    def _transition(
        self,
        actor_id: UUID,
        workspace_id: UUID,
        issue_id: UUID,
        now: datetime,
        *,
        to_status: str,
    ) -> QualityIssue:
        with self._connection() as conn:
            self._authorize(conn, actor_id, workspace_id)
            row = conn.execute(
                """SELECT issue_id,workspace_id,dataset_id,issue_code,severity,instrument_id,
                granularity,starts_at,ends_at,batch_ids,evidence,detected_at,status,claimed_by,
                closed_by,closed_at FROM data_quality_issue
                WHERE workspace_id=%s AND issue_id=%s FOR UPDATE""",
                (workspace_id, issue_id),
            ).fetchone()
            if row is None:
                raise PortError(ErrorCode.NOT_FOUND, "quality issue unavailable")
            current = self._decode(row)
            updated = (
                claim(current, actor_id)
                if to_status == "claimed"
                else close(current, actor_id, now)
            )
            cursor = conn.execute(
                """UPDATE data_quality_issue SET status=%s,claimed_by=%s,closed_by=%s,closed_at=%s
                WHERE workspace_id=%s AND issue_id=%s""",
                (
                    updated.status,
                    updated.claimed_by,
                    updated.closed_by,
                    updated.closed_at,
                    workspace_id,
                    issue_id,
                ),
            )
            if cursor.rowcount != 1:
                raise PortError(ErrorCode.CONFLICT, "quality issue transition lost")
            return updated

    def claim_issue(
        self, actor_id: UUID, workspace_id: UUID, issue_id: UUID, now: datetime
    ) -> QualityIssue:
        return self._transition(actor_id, workspace_id, issue_id, now, to_status="claimed")

    def close_issue(
        self, actor_id: UUID, workspace_id: UUID, issue_id: UUID, now: datetime
    ) -> QualityIssue:
        return self._transition(actor_id, workspace_id, issue_id, now, to_status="closed")
