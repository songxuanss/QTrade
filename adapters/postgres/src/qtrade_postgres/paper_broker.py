"""Durable Paper settlement: balances, receipt and audit commit together.

The trusted Worker supplies an immutable-dataset price source. This adapter is
not a runtime starter, a live broker, or a source of market prices. A durable
risk decision and prepared execution journal are prerequisites for settlement.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from qtrade_contracts import OrderCommand
from qtrade_domain.audit_port import AuditDetails, AuditRecord
from qtrade_domain.paper_matching import settle_paper_order
from qtrade_domain.ports import (
    BrokerOrder,
    BrokerPosition,
    BrokerSnapshot,
    ErrorCode,
    PortError,
)
from qtrade_postgres.audit import append_in_transaction
from qtrade_postgres.broker_codec import decode_broker_order, encode_broker_order
from qtrade_postgres.connection import PostgresStore
from qtrade_postgres.trading_lock import lock_workspace_trading


class PostgresPaperBroker(PostgresStore):
    def __init__(
        self,
        database_url: str,
        price_source: Callable[[OrderCommand], Decimal],
        *,
        schema: str = "qtrade",
    ):
        super().__init__(database_url, schema=schema)
        self._price_source = price_source

    def create_account(self, workspace_id: UUID, account_id: UUID, initial_cash: Decimal) -> None:
        if (
            not isinstance(initial_cash, Decimal)
            or not initial_cash.is_finite()
            or not Decimal(0) < initial_cash <= Decimal("1000000000")
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid Paper initial cash")
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO paper_account(workspace_id,account_id,initial_cash,cash,currency,"
                "created_at) VALUES(%s,%s,%s,%s,'CNY',%s) ON CONFLICT DO NOTHING",
                (workspace_id, account_id, initial_cash, initial_cash, datetime.now(UTC)),
            )
            row = conn.execute(
                "SELECT initial_cash FROM paper_account WHERE workspace_id=%s AND account_id=%s",
                (workspace_id, account_id),
            ).fetchone()
            if row is None or row[0] != initial_cash:
                raise PortError(ErrorCode.CONFLICT, "Paper account configuration is immutable")

    def submit(self, command: OrderCommand) -> BrokerOrder:
        if command.decision.outcome != "allow":
            raise PortError(ErrorCode.REJECTED, "Paper Broker requires an allowed risk decision")
        intent = command.intent
        with self._connection() as conn:
            lock_workspace_trading(conn, command.workspace_id)
            # One account lock serializes concurrent intents and duplicate retries.
            account = conn.execute(
                "SELECT cash FROM paper_account WHERE workspace_id=%s AND account_id=%s FOR UPDATE",
                (command.workspace_id, intent.account_id),
            ).fetchone()
            if account is None:
                raise PortError(ErrorCode.NOT_FOUND, "Paper account unavailable")
            cached = conn.execute(
                "SELECT command,receipt FROM paper_broker_receipt WHERE workspace_id=%s "
                "AND idempotency_key=%s",
                (command.workspace_id, command.idempotency_key),
            ).fetchone()
            if cached:
                if cached[0] != command.model_dump(mode="json"):
                    raise PortError(ErrorCode.CONFLICT, "Paper idempotency key reused")
                return decode_broker_order(cached[1])
            runtime = conn.execute(
                "SELECT r.mode,r.state,r.account_id,r.strategy_version_id FROM execution_runtime r "
                "JOIN strategy_version v ON v.strategy_version_id=r.strategy_version_id "
                "AND v.workspace_id=r.workspace_id "
                "WHERE r.workspace_id=%s AND r.runtime_id=%s FOR SHARE OF r",
                (command.workspace_id, intent.runtime_id),
            ).fetchone()
            if runtime != ("paper", "running", intent.account_id, intent.strategy_version_id):
                raise PortError(ErrorCode.REJECTED, "Paper runtime is not running")
            decision = conn.execute(
                "SELECT payload FROM risk_decision WHERE workspace_id=%s AND intent_id=%s "
                "AND decision_id=%s AND outcome='allow'",
                (command.workspace_id, intent.intent_id, command.decision.decision_id),
            ).fetchone()
            journal = conn.execute(
                "SELECT command FROM order_execution WHERE workspace_id=%s AND intent_id=%s "
                "AND order_payload->>'status'='submitted'",
                (command.workspace_id, intent.intent_id),
            ).fetchone()
            if (
                decision is None
                or decision[0] != command.decision.model_dump(mode="json")
                or journal is None
                or journal[0] != command.model_dump(mode="json")
            ):
                raise PortError(ErrorCode.REJECTED, "Paper command lacks durable authorization")
            switches = conn.execute(
                "SELECT active FROM kill_switch_state WHERE workspace_id=%s "
                "AND scope_id=ANY(%s::uuid[]) FOR SHARE",
                (command.workspace_id, [UUID(int=0), intent.account_id]),
            ).fetchall()
            if any(row[0] for row in switches):
                raise PortError(ErrorCode.REJECTED, "Kill Switch blocks Paper settlement")
            current = conn.execute(
                "SELECT quantity,average_price FROM paper_position WHERE workspace_id=%s "
                "AND account_id=%s AND instrument_id=%s",
                (command.workspace_id, intent.account_id, intent.instrument_id),
            ).fetchone()
            position = BrokerPosition(
                intent.instrument_id,
                Decimal(str(current[0])) if current else Decimal(0),
                Decimal(str(current[1])) if current else Decimal(0),
            )
            try:
                market = self._price_source(command)
            except Exception:
                raise PortError(ErrorCode.UNAVAILABLE, "Paper price unavailable") from None
            settlement = settle_paper_order(
                command, market=market, cash=Decimal(str(account[0])), position=position
            )
            now = datetime.now(UTC)
            if settlement.order.status == "filled":
                conn.execute(
                    "UPDATE paper_account SET cash=%s WHERE workspace_id=%s AND account_id=%s",
                    (settlement.cash, command.workspace_id, intent.account_id),
                )
                if settlement.position.quantity:
                    conn.execute(
                        "INSERT INTO paper_position(workspace_id,account_id,instrument_id,quantity,"
                        "average_price) VALUES(%s,%s,%s,%s,%s) ON CONFLICT "
                        "(workspace_id,account_id,instrument_id) DO UPDATE "
                        "SET quantity=EXCLUDED.quantity,average_price=EXCLUDED.average_price",
                        (
                            command.workspace_id,
                            intent.account_id,
                            intent.instrument_id,
                            settlement.position.quantity,
                            settlement.position.average_price,
                        ),
                    )
                else:
                    conn.execute(
                        "DELETE FROM paper_position WHERE workspace_id=%s AND account_id=%s "
                        "AND instrument_id=%s",
                        (command.workspace_id, intent.account_id, intent.instrument_id),
                    )
            conn.execute(
                "INSERT INTO paper_broker_receipt(workspace_id,account_id,idempotency_key,"
                "intent_id,command,receipt,market_price,occurred_at) "
                "VALUES(%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s)",
                (
                    command.workspace_id,
                    intent.account_id,
                    command.idempotency_key,
                    intent.intent_id,
                    command.model_dump_json(),
                    encode_broker_order(settlement.order),
                    market,
                    now,
                ),
            )
            append_in_transaction(
                conn,
                AuditRecord(
                    command.workspace_id,
                    "system",
                    None,
                    "paper.order." + settlement.order.status,
                    "success" if settlement.order.status == "filled" else "denied",
                    now,
                    "worker.paper.broker",
                    intent.trace_id,
                    details=AuditDetails(
                        target_id=intent.intent_id, target_workspace_id=command.workspace_id
                    ),
                ),
            )
            return settlement.order

    def cancel(self, workspace_id: UUID, account_id: UUID, broker_order_id: str) -> BrokerOrder:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT receipt FROM paper_broker_receipt WHERE workspace_id=%s AND account_id=%s "
                "AND receipt->>'broker_order_id'=%s",
                (workspace_id, account_id, broker_order_id),
            ).fetchone()
        if row is None:
            raise PortError(ErrorCode.NOT_FOUND, "Paper order unavailable")
        order = decode_broker_order(row[0])
        if order.status == "filled":
            raise PortError(ErrorCode.CONFLICT, "filled Paper order cannot be cancelled")
        return order

    def snapshot(self, workspace_id: UUID, account_id: UUID) -> BrokerSnapshot:
        with self._connection() as conn:
            # Snapshot must not mix balances before and after a concurrent settlement.
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            account = conn.execute(
                "SELECT cash,currency FROM paper_account WHERE workspace_id=%s AND account_id=%s",
                (workspace_id, account_id),
            ).fetchone()
            if account is None:
                raise PortError(ErrorCode.NOT_FOUND, "Paper account unavailable")
            positions = conn.execute(
                "SELECT instrument_id,quantity,average_price FROM paper_position "
                "WHERE workspace_id=%s AND account_id=%s ORDER BY instrument_id",
                (workspace_id, account_id),
            ).fetchall()
            orders = conn.execute(
                "SELECT receipt FROM paper_broker_receipt WHERE workspace_id=%s AND account_id=%s "
                "ORDER BY occurred_at,idempotency_key",
                (workspace_id, account_id),
            ).fetchall()
            return BrokerSnapshot(
                workspace_id,
                account_id,
                datetime.now(UTC),
                str(account[1]),
                Decimal(str(account[0])),
                tuple(
                    BrokerPosition(str(r[0]), Decimal(str(r[1])), Decimal(str(r[2])))
                    for r in positions
                ),
                tuple(decode_broker_order(r[0]) for r in orders),
            )
