"""Persistent fail-closed trading switches and one-time recovery approvals."""

from datetime import datetime
from typing import cast
from uuid import UUID

from qtrade_domain.kill_switch import (
    KillSwitch,
    KillSwitchStore,
    RecoveryRequest,
    RecoveryRequestStore,
)
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_postgres.connection import PostgresStore
from qtrade_postgres.trading_lock import lock_workspace_trading

_GLOBAL_SCOPE = UUID(int=0)


class PostgresKillSwitchStore(PostgresStore, KillSwitchStore, RecoveryRequestStore):
    def get_state(self, workspace_id: UUID, account_id: UUID | None = None) -> KillSwitch | None:
        scope_id = account_id or _GLOBAL_SCOPE
        with self._connection() as conn:
            row = conn.execute(
                "SELECT workspace_id,account_id,active,reason,changed_by,changed_at,revision "
                "FROM kill_switch_state WHERE workspace_id=%s AND scope_id=%s",
                (workspace_id, scope_id),
            ).fetchone()
        return _decode_state(row) if row else None

    def is_active(self, workspace_id: UUID, account_id: UUID | None = None) -> bool:
        scopes = [_GLOBAL_SCOPE] if account_id is None else [_GLOBAL_SCOPE, account_id]
        with self._connection() as conn:
            row = conn.execute(
                "SELECT COALESCE(bool_or(active),false) FROM kill_switch_state "
                "WHERE workspace_id=%s AND scope_id=ANY(%s::uuid[]) AND active",
                (workspace_id, scopes),
            ).fetchone()
        return bool(row[0]) if row else False

    def set_state(
        self,
        state: KillSwitch,
        *,
        expected_revision: int | None,
    ) -> KillSwitch:
        scope_id = state.account_id or _GLOBAL_SCOPE
        actor = None if state.changed_by == _GLOBAL_SCOPE else state.changed_by
        with self._connection() as conn:
            lock_workspace_trading(conn, state.workspace_id)
            if expected_revision is None:
                row = conn.execute(
                    "INSERT INTO kill_switch_state(workspace_id,scope_id,account_id,active,reason,"
                    "changed_by,changed_at,revision) VALUES(%s,%s,%s,%s,%s,%s,%s,1) "
                    "ON CONFLICT DO NOTHING RETURNING workspace_id,account_id,active,reason,"
                    "changed_by,changed_at,revision",
                    (
                        state.workspace_id,
                        scope_id,
                        state.account_id,
                        state.active,
                        state.reason,
                        actor,
                        state.changed_at,
                    ),
                ).fetchone()
            else:
                row = conn.execute(
                    "UPDATE kill_switch_state SET active=%s,reason=%s,changed_by=%s,changed_at=%s,"
                    "revision=revision+1 WHERE workspace_id=%s AND scope_id=%s AND revision=%s "
                    "RETURNING workspace_id,account_id,active,reason,changed_by,changed_at,"
                    "revision",
                    (
                        state.active,
                        state.reason,
                        actor,
                        state.changed_at,
                        state.workspace_id,
                        scope_id,
                        expected_revision,
                    ),
                ).fetchone()
        if row is None:
            raise PortError(ErrorCode.CONFLICT, "kill switch changed concurrently")
        return _decode_state(row)

    def approve_recovery(
        self,
        request: RecoveryRequest,
        *,
        approver_id: UUID,
        reason: str,
        now: datetime,
    ) -> KillSwitch:
        if approver_id == request.requested_by:
            raise PortError(ErrorCode.REJECTED, "recovery requires an independent approver")
        with self._connection() as conn:
            lock_workspace_trading(conn, request.workspace_id)
            pending = conn.execute(
                "SELECT requested_by,switch_revision,account_id FROM kill_switch_recovery_request "
                "WHERE workspace_id=%s AND request_id=%s AND approved_by IS NULL FOR UPDATE",
                (request.workspace_id, request.request_id),
            ).fetchone()
            if pending is None or pending != (
                request.requested_by,
                request.switch_revision,
                request.account_id,
            ):
                raise PortError(ErrorCode.CONFLICT, "recovery request changed or already used")
            row = conn.execute(
                "SELECT workspace_id,account_id,active,reason,changed_by,changed_at,revision "
                "FROM kill_switch_state WHERE workspace_id=%s AND scope_id=%s FOR UPDATE",
                (request.workspace_id, request.account_id or _GLOBAL_SCOPE),
            ).fetchone()
            current = _decode_state(row) if row else None
            if current is None or not current.active or current.revision != request.switch_revision:
                raise PortError(ErrorCode.CONFLICT, "recovery request has stale switch revision")
            result = KillSwitch(
                request.workspace_id,
                request.account_id,
                False,
                reason,
                approver_id,
                now,
                current.revision + 1,
            )
            updated = conn.execute(
                "UPDATE kill_switch_state SET active=false,reason=%s,changed_by=%s,changed_at=%s,"
                "revision=revision+1 WHERE workspace_id=%s AND scope_id=%s RETURNING "
                "workspace_id,account_id,active,reason,changed_by,changed_at,revision",
                (
                    result.reason,
                    result.changed_by,
                    result.changed_at,
                    result.workspace_id,
                    result.account_id or _GLOBAL_SCOPE,
                ),
            ).fetchone()
            conn.execute(
                "UPDATE kill_switch_recovery_request SET approved_by=%s,approved_at=%s "
                "WHERE workspace_id=%s AND request_id=%s",
                (approver_id, now, request.workspace_id, request.request_id),
            )
            if updated is None:
                raise PortError(ErrorCode.CONFLICT, "kill switch changed concurrently")
            return _decode_state(updated)

    def create(self, request: RecoveryRequest) -> None:
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO kill_switch_recovery_request(workspace_id,request_id,account_id,"
                "requested_by,requested_at,switch_revision) VALUES(%s,%s,%s,%s,%s,%s)",
                (
                    request.workspace_id,
                    request.request_id,
                    request.account_id,
                    request.requested_by,
                    request.requested_at,
                    request.switch_revision,
                ),
            )

    def get_request(self, request_id: UUID) -> RecoveryRequest | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT request_id,workspace_id,account_id,requested_by,requested_at,"
                "switch_revision "
                "FROM kill_switch_recovery_request WHERE request_id=%s AND approved_by IS NULL",
                (request_id,),
            ).fetchone()
        if row is None:
            return None
        return RecoveryRequest(
            cast(UUID, row[0]),
            cast(UUID, row[1]),
            cast(UUID | None, row[2]),
            cast(UUID, row[3]),
            cast(datetime, row[4]),
            cast(int | None, row[5]),
        )

    def list_pending(self, workspace_id: UUID) -> tuple[RecoveryRequest, ...]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT request_id,workspace_id,account_id,requested_by,requested_at,"
                "switch_revision "
                "FROM kill_switch_recovery_request WHERE workspace_id=%s AND approved_by IS NULL "
                "ORDER BY requested_at DESC,request_id LIMIT 200",
                (workspace_id,),
            ).fetchall()
        return tuple(
            RecoveryRequest(
                cast(UUID, row[0]),
                cast(UUID, row[1]),
                cast(UUID | None, row[2]),
                cast(UUID, row[3]),
                cast(datetime, row[4]),
                cast(int | None, row[5]),
            )
            for row in rows
        )

    def consume(self, request_id: UUID, *, approver_id: UUID, now: datetime) -> RecoveryRequest:
        with self._connection() as conn:
            row = conn.execute(
                "UPDATE kill_switch_recovery_request SET approved_by=%s,approved_at=%s "
                "WHERE request_id=%s AND approved_by IS NULL AND requested_by<>%s "
                "RETURNING request_id,workspace_id,account_id,requested_by,requested_at,"
                "switch_revision",
                (approver_id, now, request_id, approver_id),
            ).fetchone()
        if row is None:
            raise PortError(ErrorCode.CONFLICT, "recovery request already used or invalid")
        return RecoveryRequest(
            cast(UUID, row[0]),
            cast(UUID, row[1]),
            cast(UUID | None, row[2]),
            cast(UUID, row[3]),
            cast(datetime, row[4]),
            cast(int | None, row[5]),
        )


def _decode_state(row: tuple[object, ...]) -> KillSwitch:
    try:
        return KillSwitch(
            cast(UUID, row[0]),
            cast(UUID | None, row[1]),
            bool(row[2]),
            str(row[3]),
            cast(UUID, row[4]) if row[4] is not None else _GLOBAL_SCOPE,
            cast(datetime, row[5]),
            int(cast(int, row[6])),
        )
    except (TypeError, ValueError, PortError):
        raise PortError(ErrorCode.UNAVAILABLE, "invalid stored kill switch state") from None
