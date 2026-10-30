"""Canonical broker receipts for the order journal and durable Paper ledger."""

from pydantic import TypeAdapter, ValidationError

from qtrade_domain.ports import BrokerOrder, ErrorCode, PortError

_ORDERS = TypeAdapter(BrokerOrder)


def encode_broker_order(order: BrokerOrder) -> str:
    return _ORDERS.dump_json(order).decode("utf-8")


def decode_broker_order(value: object) -> BrokerOrder:
    try:
        return _ORDERS.validate_python(value)
    except ValidationError:
        raise PortError(ErrorCode.UNAVAILABLE, "invalid stored broker receipt") from None
