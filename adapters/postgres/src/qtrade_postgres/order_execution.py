"""Transactional order command journal for exactly-once broker recovery."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

from qtrade_contracts import Order, OrderCommand, OrderEvent
from qtrade_domain.order_execution import OrderExecutionRecord
from qtrade_domain.ports import BrokerOrder, BrokerOrderStatus, ErrorCode, PortError
from qtrade_postgres.connection import PostgresStore
from qtrade_postgres.query import column_query


class PostgresOrderExecutionStore(PostgresStore):
    _COLUMNS = "command,order_payload,events,broker_order"

    def get_for_intent(self, workspace_id: UUID, intent_id: UUID) -> OrderExecutionRecord | None:
        with self._connection() as conn:
            row = conn.execute(
                column_query(
                    "SELECT {} FROM order_execution WHERE workspace_id=%s AND intent_id=%s",
                    self._COLUMNS,
                ),
                (workspace_id, intent_id),
            ).fetchone()
        return _decode(row) if row else None

    def prepare(self, record: OrderExecutionRecord) -> OrderExecutionRecord:
        if record.broker_order is not None or record.order.status != "submitted":
            raise PortError(
                ErrorCode.INVALID_INPUT, "order execution must be prepared before dispatch"
            )
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO order_execution(workspace_id,intent_id,idempotency_key,command,"
                "order_payload,events,broker_order,updated_at) "
                "VALUES (%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,NULL,%s) "
                "ON CONFLICT DO NOTHING",
                (
                    record.order.workspace_id,
                    record.order.intent_id,
                    record.command.idempotency_key,
                    record.command.model_dump_json(),
                    record.order.model_dump_json(),
                    _events_json(record.events),
                    datetime.now(UTC),
                ),
            )
            row = conn.execute(
                column_query(
                    "SELECT {} FROM order_execution WHERE workspace_id=%s AND intent_id=%s",
                    self._COLUMNS,
                ),
                (record.order.workspace_id, record.order.intent_id),
            ).fetchone()
        if row is None:
            raise PortError(ErrorCode.UNAVAILABLE, "order execution preparation unavailable")
        stored = _decode(row)
        if stored.command != record.command:
            raise PortError(ErrorCode.CONFLICT, "order intent already has a different command")
        return stored

    def update(self, record: OrderExecutionRecord) -> OrderExecutionRecord:
        workspace_id, intent_id = record.order.workspace_id, record.order.intent_id
        with self._connection() as conn:
            row = conn.execute(
                column_query(
                    "SELECT {} FROM order_execution WHERE workspace_id=%s"
                    " AND intent_id=%s FOR UPDATE",
                    self._COLUMNS,
                ),
                (workspace_id, intent_id),
            ).fetchone()
            if row is None:
                raise PortError(ErrorCode.NOT_FOUND, "order execution unavailable")
            current = _decode(row)
            if record == current:
                return current
            if (
                current.command == record.command
                and current.broker_order == record.broker_order
                and current.order.status == record.order.status
                and current.order.filled_quantity == record.order.filled_quantity
                and current.order.average_fill_price == record.order.average_fill_price
            ):
                return current
            if (
                current.command != record.command
                or record.order.revision != current.order.revision + 1
                or record.events[:-1] != current.events
                or len(record.events) != len(current.events) + 1
            ):
                raise PortError(ErrorCode.CONFLICT, "invalid order execution revision")
            conn.execute(
                "UPDATE order_execution SET order_payload=%s::jsonb,events=%s::jsonb,"
                "broker_order=%s::jsonb,updated_at=%s WHERE workspace_id=%s AND intent_id=%s",
                (
                    record.order.model_dump_json(),
                    _events_json(record.events),
                    _broker_json(record.broker_order),
                    datetime.now(UTC),
                    workspace_id,
                    intent_id,
                ),
            )
        return record


def _events_json(events: tuple[OrderEvent, ...]) -> str:
    return json.dumps(
        [json.loads(event.model_dump_json()) for event in events],
        separators=(",", ":"),
        sort_keys=True,
    )


def _broker_json(order: BrokerOrder | None) -> str | None:
    if order is None:
        return None
    return json.dumps(
        {
            "workspace_id": str(order.workspace_id),
            "account_id": str(order.account_id),
            "command_id": str(order.command_id),
            "broker_order_id": order.broker_order_id,
            "status": order.status,
            "quantity": str(order.quantity),
            "filled_quantity": str(order.filled_quantity),
            "rejection_code": order.rejection_code,
            "average_fill_price": (
                str(order.average_fill_price) if order.average_fill_price is not None else None
            ),
            "commission": str(order.commission),
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _decode(row: tuple[object, ...]) -> OrderExecutionRecord:
    try:
        command = OrderCommand.model_validate_json(json.dumps(row[0]))
        order = Order.model_validate_json(json.dumps(row[1]))
        event_values = row[2]
        if not isinstance(event_values, list):
            raise ValueError
        events = tuple(OrderEvent.model_validate_json(json.dumps(item)) for item in event_values)
        broker = None
        if row[3] is not None:
            value = cast(dict[str, object], row[3])
            broker = BrokerOrder(
                UUID(str(value["workspace_id"])),
                UUID(str(value["account_id"])),
                UUID(str(value["command_id"])),
                str(value["broker_order_id"]),
                cast(BrokerOrderStatus, str(value["status"])),
                Decimal(str(value["quantity"])),
                Decimal(str(value["filled_quantity"])),
                cast(str | None, value["rejection_code"]),
                Decimal(str(value["average_fill_price"]))
                if value["average_fill_price"] is not None
                else None,
                Decimal(str(value["commission"])),
            )
        return OrderExecutionRecord(command, order, events, broker)
    except (KeyError, TypeError, ValueError, PortError):
        raise PortError(ErrorCode.UNAVAILABLE, "invalid stored order execution") from None
