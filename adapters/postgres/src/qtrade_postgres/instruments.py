"""Atomic master-data imports, immutable specifications, and conflict preservation."""

import json
from datetime import datetime
from hashlib import sha256
from typing import cast
from uuid import UUID, uuid4

from psycopg import sql

from qtrade_contracts import Instrument
from qtrade_domain.authorization import Permission
from qtrade_domain.instruments import (
    InstrumentImport,
    InstrumentPage,
    InstrumentQuery,
    SpecificationConflict,
    validate_import,
)
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_postgres.connection import PostgresStore
from qtrade_postgres.workspaces import PostgresWorkspaceStore


def serialized(item: Instrument) -> str:
    payload = item.model_dump(mode="json")
    for field in ("tick_size", "lot_size", "contract_multiplier"):
        value = payload[field]
        payload[field] = value.rstrip("0").rstrip(".") if "." in value else value
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def instrument(payload: object) -> Instrument:
    try:
        return Instrument.model_validate_json(json.dumps(payload))
    except (ValueError, TypeError):
        raise PortError(ErrorCode.UNAVAILABLE, "invalid stored instrument specification") from None


class PostgresInstrumentStore(PostgresStore):
    def import_instruments(
        self,
        actor_id: UUID,
        workspace_id: UUID,
        instruments: tuple[Instrument, ...],
    ) -> tuple[InstrumentImport, ...]:
        validate_import(workspace_id, instruments)
        with self._connection() as conn:
            conn.execute(
                "SELECT workspace_id FROM workspace WHERE workspace_id=%s FOR UPDATE",
                (workspace_id,),
            )
            access = PostgresWorkspaceStore._access(conn, actor_id, workspace_id)
            if access is None:
                raise PortError(ErrorCode.NOT_FOUND, "workspace resource unavailable")
            access.require(Permission.DATA_MANAGE)
            results = []
            for item in instruments:
                row = conn.execute(
                    """SELECT s.payload FROM instrument_current c JOIN instrument_specification s
                    USING(workspace_id,instrument_id,specification_version)
                    WHERE c.workspace_id=%s AND c.instrument_id=%s""",
                    (workspace_id, item.instrument_id),
                ).fetchone()
                payload = serialized(item)
                if row is None:
                    conn.execute(
                        """INSERT INTO instrument_specification(workspace_id,instrument_id,
                        specification_version,payload,name_search,imported_by)
                        VALUES (%s,%s,%s,%s::jsonb,%s,%s)""",
                        (
                            workspace_id,
                            item.instrument_id,
                            item.specification_version,
                            payload,
                            item.name.casefold(),
                            actor_id,
                        ),
                    )
                    conn.execute(
                        "INSERT INTO instrument_current VALUES (%s,%s,%s)",
                        (workspace_id, item.instrument_id, item.specification_version),
                    )
                    results.append(InstrumentImport(item.instrument_id, "created"))
                elif instrument(row[0]) == item:
                    results.append(InstrumentImport(item.instrument_id, "unchanged"))
                else:
                    existing = instrument(row[0])
                    issue = conn.execute(
                        """INSERT INTO instrument_specification_conflict(issue_id,workspace_id,
                        instrument_id,existing_version,proposed,proposal_sha256)
                        VALUES (%s,%s,%s,%s,%s::jsonb,%s)
                        ON CONFLICT(workspace_id,instrument_id,proposal_sha256)
                        DO UPDATE SET proposal_sha256=EXCLUDED.proposal_sha256
                        RETURNING issue_id""",
                        (
                            uuid4(),
                            workspace_id,
                            item.instrument_id,
                            existing.specification_version,
                            payload,
                            sha256(payload.encode()).hexdigest(),
                        ),
                    ).fetchone()
                    if issue is None:
                        raise PortError(ErrorCode.UNAVAILABLE, "instrument conflict unavailable")
                    results.append(
                        InstrumentImport(item.instrument_id, "conflict", UUID(str(issue[0])))
                    )
            return tuple(results)

    def query(self, query: InstrumentQuery) -> InstrumentPage:
        conditions: list[sql.Composable] = [sql.SQL("c.workspace_id=%s")]
        parameters: list[object] = [query.workspace_id]
        for key, value in (
            ("instrument_id", query.instrument_id),
            ("exchange", query.exchange),
            ("market", query.market),
        ):
            if value is not None:
                conditions.append(sql.SQL("s.payload->>{}=%s").format(sql.Literal(key)))
                parameters.append(value)
        if query.name is not None:
            conditions.append(sql.SQL("strpos(s.name_search,%s)>0"))
            parameters.append(query.name.casefold())
        if query.enabled is not None:
            conditions.append(sql.SQL("(s.payload->>'enabled')::boolean=%s"))
            parameters.append(query.enabled)
        if query.after is not None:
            conditions.append(sql.SQL('c.instrument_id COLLATE "C">%s'))
            parameters.append(query.after)
        parameters.append(query.limit + 1)
        with self._connection() as conn:
            rows = conn.execute(
                sql.SQL(
                    "SELECT s.payload FROM instrument_current c JOIN instrument_specification s "
                    "USING(workspace_id,instrument_id,specification_version) WHERE {} "
                    'ORDER BY c.instrument_id COLLATE "C" LIMIT %s'
                ).format(sql.SQL(" AND ").join(conditions)),
                parameters,
            ).fetchall()
        items = tuple(instrument(row[0]) for row in rows)
        page = items[: query.limit]
        return InstrumentPage(page, page[-1].instrument_id if len(items) > query.limit else None)

    def conflicts(self, workspace_id: UUID) -> tuple[SpecificationConflict, ...]:
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT i.issue_id,i.instrument_id,s.payload,i.proposed,i.detected_at
                FROM instrument_specification_conflict i JOIN instrument_specification s
                ON (s.workspace_id,s.instrument_id,s.specification_version)=
                   (i.workspace_id,i.instrument_id,i.existing_version)
                WHERE i.workspace_id=%s ORDER BY i.detected_at,i.issue_id""",
                (workspace_id,),
            ).fetchall()
        return tuple(
            SpecificationConflict(
                UUID(str(row[0])),
                workspace_id,
                str(row[1]),
                instrument(row[2]),
                instrument(row[3]),
                cast(datetime, row[4]),
            )
            for row in rows
        )
