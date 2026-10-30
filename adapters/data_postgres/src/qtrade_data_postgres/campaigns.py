"""Own-schema plans, atomic pending runs and idempotent expanded-work journal."""

import json
from uuid import UUID, uuid5

import psycopg

from qtrade_data_contracts import CampaignPlan, MemberCollectionPlan
from qtrade_data_core.campaigns import Work
from qtrade_data_core.ports import DataFailure
from qtrade_data_postgres.universes import PostgresUniverses


class PostgresCampaigns(PostgresUniverses):
    def save_plan(self, plan: CampaignPlan) -> None:
        with self._connection(plan.scope_id) as conn:
            conn.execute(
                """INSERT INTO qtrade_data.campaign_plan(scope_id,plan_id,created_at,payload)
                VALUES(%s,%s,%s,%s::jsonb)""",
                (plan.scope_id, plan.plan_id, plan.created_at, plan.model_dump_json()),
            )

    def plan(self, scope_id: UUID, plan_id: UUID) -> CampaignPlan:
        with self._connection(scope_id) as conn:
            row = conn.execute(
                "SELECT payload FROM qtrade_data.campaign_plan WHERE scope_id=%s AND plan_id=%s",
                (scope_id, plan_id),
            ).fetchone()
            if row is None:
                raise DataFailure("not_found")
            try:
                result = CampaignPlan.model_validate_json(json.dumps(row[0]))
                if result.scope_id != scope_id or result.plan_id != plan_id:
                    raise ValueError
                return result
            except (TypeError, ValueError):
                raise DataFailure("unavailable") from None

    def find(self, scope_id: UUID, request_id: UUID) -> tuple[UUID, UUID] | None:
        with self._connection(scope_id) as conn:
            row = conn.execute(
                """SELECT campaign_id,plan_id FROM qtrade_data.campaign_run
                WHERE scope_id=%s AND request_id=%s""",
                (scope_id, request_id),
            ).fetchone()
            return (UUID(str(row[0])), UUID(str(row[1]))) if row else None

    def submit(self, plan: CampaignPlan) -> UUID:
        campaign_id = uuid5(plan.selection.request_id, "collection-campaign.v1")
        with self._connection(plan.scope_id) as conn:
            conn.execute(
                """INSERT INTO qtrade_data.campaign_run
                (scope_id,campaign_id,plan_id,request_id,actor_id,created_at)
                VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(scope_id,request_id) DO NOTHING""",
                (
                    plan.scope_id,
                    campaign_id,
                    plan.plan_id,
                    plan.selection.request_id,
                    plan.selection.actor_id,
                    plan.created_at,
                ),
            )
            row = conn.execute(
                "SELECT campaign_id,plan_id FROM qtrade_data.campaign_run WHERE scope_id=%s "
                "AND request_id=%s",
                (plan.scope_id, plan.selection.request_id),
            ).fetchone()
            if row is None or row[0] != campaign_id or row[1] != plan.plan_id:
                raise DataFailure("conflict")
        return campaign_id

    def run(self, scope_id: UUID, campaign_id: UUID, member_offset: int = 0) -> dict[str, object]:
        if type(member_offset) is not int or not 0 <= member_offset <= 10000:
            raise DataFailure("invalid_input")
        with self._connection(scope_id) as conn:
            row = conn.execute(
                """SELECT plan_id,request_id,state,version,updated_at
                FROM qtrade_data.campaign_run WHERE scope_id=%s AND campaign_id=%s""",
                (scope_id, campaign_id),
            ).fetchone()
            if row is None:
                raise DataFailure("not_found")
            totals = conn.execute(
                """SELECT instrument_id,count(*),
                count(*) FILTER(WHERE result->>'state'='viewable'),
                count(*) FILTER(WHERE result->>'state' IN ('viewable','collected')),
                count(*) FILTER(WHERE result->>'state'='no_observation'),
                count(*) FILTER(WHERE result->>'state'='failed')
                FROM qtrade_data.campaign_work WHERE scope_id=%s AND campaign_id=%s
                GROUP BY instrument_id""",
                (scope_id, campaign_id),
            ).fetchall()
            plan = self.plan(scope_id, UUID(str(row[0])))
            page = plan.members[member_offset : member_offset + 30]
            work = conn.execute(
                """SELECT ordinal,instrument_id,task_id,result,start_at,end_at FROM (
                SELECT *,row_number() OVER(PARTITION BY instrument_id ORDER BY ordinal) AS rank
                FROM qtrade_data.campaign_work WHERE scope_id=%s AND campaign_id=%s
                AND instrument_id=ANY(%s)) AS members WHERE rank<=100 ORDER BY ordinal""",
                (scope_id, campaign_id, [m.instrument.instrument_id for m in page]),
            ).fetchall()
        counts = {str(r[0]): tuple(int(str(v)) for v in r[1:]) for r in totals}
        per_member: dict[str, list[dict[str, object]]] = {}
        for ordinal, instrument, task_id, result, start, end in work:
            if not isinstance(result, dict):
                raise DataFailure("unavailable")
            per_member.setdefault(str(instrument), []).append(
                {
                    **result,
                    "ordinal": ordinal,
                    "task_id": str(task_id),
                    "start": str(result.get("start", start)),
                    "end": str(result.get("end", end)),
                }
            )

        def member_state(member: MemberCollectionPlan) -> str:
            expanded, viewable, collected, noobs, failed = counts.get(
                member.instrument.instrument_id, (0, 0, 0, 0, 0)
            )
            complete = expanded == member.planned_parts and expanded > 0
            return (
                "viewable"
                if complete and viewable == expanded
                else "failed"
                if failed
                else "no_observation"
                if complete and noobs == expanded
                else "collected"
                if complete and collected == expanded
                else "running"
                if expanded
                else "pending"
            )

        details = []
        for member in page:
            count = counts.get(member.instrument.instrument_id, (0, 0, 0, 0, 0))[0]
            state = member_state(member)
            details.append(
                {
                    "instrument_id": member.instrument.instrument_id,
                    "name": member.instrument.name,
                    "affiliations": member.affiliations,
                    "planned_parts": member.planned_parts,
                    "expanded_parts": count,
                    "state": state,
                    "viewable": state == "viewable",
                    "parts": per_member.get(member.instrument.instrument_id, []),
                    "parts_truncated": count > 100,
                }
            )
        return {
            "schema_version": 1,
            "scope_id": str(scope_id),
            "campaign_id": str(campaign_id),
            "plan_id": str(row[0]),
            "request_id": str(row[1]),
            "state": row[2],
            "version": row[3],
            "updated_at": str(row[4]),
            "granularity": plan.selection.granularity,
            "start": plan.selection.start.isoformat(),
            "end": plan.selection.end.isoformat(),
            "mode": plan.selection.mode,
            "member_count": len(plan.members),
            "planned_parts": plan.planned_parts,
            "expanded_parts": sum(c[0] for c in counts.values()),
            "collected_parts": sum(c[2] for c in counts.values()),
            "no_observation_parts": sum(c[3] for c in counts.values()),
            "failed_parts": sum(c[4] for c in counts.values()),
            "viewable_members": sum(member_state(m) == "viewable" for m in plan.members),
            "no_observation_members": sum(
                member_state(m) == "no_observation" for m in plan.members
            ),
            "member_offset": member_offset,
            "member_page_size": 30,
            "members": details,
        }

    def runs(self, scope_id: UUID, limit: int) -> list[dict[str, object]]:
        with self._connection(scope_id) as conn:
            rows = conn.execute(
                "SELECT campaign_id FROM qtrade_data.campaign_run WHERE scope_id=%s ORDER BY "
                "created_at DESC,campaign_id DESC LIMIT %s",
                (scope_id, limit),
            ).fetchall()
        return [self.run(scope_id, UUID(str(row[0]))) for row in rows]

    def ready(self, limit: int) -> tuple[tuple[UUID, UUID], ...]:
        # Worker service inventory only; public callers always enter through scoped access.
        try:
            with psycopg.connect(self._database_url) as conn:
                rows = conn.execute(
                    """SELECT scope_id,campaign_id FROM qtrade_data.campaign_run AS run
                    WHERE state IN ('queued','running') OR (state='cancelled' AND EXISTS(
                    SELECT 1 FROM qtrade_data.campaign_work AS work
                    WHERE work.scope_id=run.scope_id AND work.campaign_id=run.campaign_id
                    AND work.result->>'state'
                    NOT IN ('viewable','collected','no_observation','failed')))
                    ORDER BY updated_at LIMIT %s""",
                    (limit,),
                ).fetchall()
                return tuple((UUID(str(r[0])), UUID(str(r[1]))) for r in rows)
        except psycopg.Error:
            raise DataFailure("unavailable") from None

    def expanded(self, scope_id: UUID, campaign_id: UUID) -> int:
        with self._connection(scope_id) as conn:
            row = conn.execute(
                "SELECT count(*) FROM qtrade_data.campaign_work WHERE scope_id=%s AND "
                "campaign_id=%s",
                (scope_id, campaign_id),
            ).fetchone()
            return int(str(row[0])) if row else 0

    def record(self, scope_id: UUID, campaign_id: UUID, work: Work) -> None:
        with self._connection(scope_id) as conn:
            conn.execute(
                """INSERT INTO qtrade_data.campaign_work
                (scope_id,campaign_id,ordinal,instrument_id,task_id,start_at,end_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(scope_id,campaign_id,ordinal) DO NOTHING""",
                (
                    scope_id,
                    campaign_id,
                    work.ordinal,
                    work.instrument.instrument_id,
                    work.task_id,
                    work.start,
                    work.end,
                ),
            )

    def active(self, scope_id: UUID, campaign_id: UUID, limit: int) -> tuple[tuple[int, UUID], ...]:
        with self._connection(scope_id) as conn:
            rows = conn.execute(
                """SELECT ordinal,task_id FROM qtrade_data.campaign_work
                WHERE scope_id=%s AND campaign_id=%s
                AND result->>'state' NOT IN ('viewable','collected','no_observation','failed')
                ORDER BY checked_at,ordinal LIMIT %s""",
                (scope_id, campaign_id, limit),
            ).fetchall()
            return tuple((int(str(r[0])), UUID(str(r[1]))) for r in rows)

    def result(
        self, scope_id: UUID, campaign_id: UUID, ordinal: int, result: dict[str, object]
    ) -> None:
        with self._connection(scope_id) as conn:
            conn.execute(
                "UPDATE qtrade_data.campaign_work SET result=%s::jsonb,checked_at=now() WHERE "
                "scope_id=%s AND campaign_id=%s AND ordinal=%s",
                (json.dumps(result), scope_id, campaign_id, ordinal),
            )

    def finish(self, scope_id: UUID, campaign_id: UUID) -> None:
        run = self.run(scope_id, campaign_id)
        planned = int(str(run["planned_parts"]))
        expanded = int(str(run["expanded_parts"]))
        terminal = (
            int(str(run["collected_parts"]))
            + int(str(run["failed_parts"]))
            + int(str(run["no_observation_parts"]))
        )
        state = "running"
        if planned == expanded == terminal:
            state = (
                "completed"
                if int(str(run["viewable_members"])) == int(str(run["member_count"]))
                else "failed"
                if terminal and int(str(run["failed_parts"])) == terminal
                else "partial"
            )
        with self._connection(scope_id) as conn:
            conn.execute(
                "UPDATE qtrade_data.campaign_run SET "
                "state=%s,version=version+1,updated_at=now() WHERE scope_id=%s AND "
                "campaign_id=%s AND state<>'cancelled' AND version=%s",
                (state, scope_id, campaign_id, run["version"]),
            )

    def control(self, scope_id: UUID, campaign_id: UUID, action: str) -> tuple[UUID, ...]:
        with self._connection(scope_id) as conn:
            if action == "failed":
                rows = conn.execute(
                    "SELECT task_id FROM qtrade_data.campaign_work WHERE scope_id=%s AND "
                    "campaign_id=%s AND result->>'state'='failed'",
                    (scope_id, campaign_id),
                ).fetchall()
                return tuple(UUID(str(r[0])) for r in rows)
            if action == "retry":
                conn.execute(
                    """UPDATE qtrade_data.campaign_work SET result=%s::jsonb
                    WHERE scope_id=%s AND campaign_id=%s AND result->>'state'='failed'""",
                    (json.dumps({"state": "queued"}), scope_id, campaign_id),
                )
            if action not in ("retry", "cancel"):
                raise DataFailure("invalid_input")
            conn.execute(
                "UPDATE qtrade_data.campaign_run SET "
                "state=%s,version=version+1,updated_at=now() WHERE scope_id=%s AND "
                "campaign_id=%s",
                ("cancelled" if action == "cancel" else "running", scope_id, campaign_id),
            )
            return ()
