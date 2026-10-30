"""Shared immutable dataset validation and canonical persistence representation."""

import json
from datetime import UTC
from hashlib import sha256
from uuid import UUID

from qtrade_contracts import Bar
from qtrade_domain.ports import ErrorCode, PortError


def dataset_bars(workspace_id: UUID, dataset_id: UUID, bars: tuple[Bar, ...]) -> tuple[Bar, ...]:
    if (
        not isinstance(workspace_id, UUID)
        or not isinstance(dataset_id, UUID)
        or not isinstance(bars, tuple)
        or any(
            not isinstance(bar, Bar)
            or bar.workspace_id != workspace_id
            or bar.dataset_id != dataset_id
            for bar in bars
        )
    ):
        raise PortError(ErrorCode.INVALID_INPUT, "invalid dataset scope or bars")
    identities = [(bar.instrument_id, bar.granularity, bar.timestamp) for bar in bars]
    if len(identities) != len(set(identities)):
        raise PortError(ErrorCode.INVALID_INPUT, "duplicate bar")
    return tuple(sorted(bars, key=lambda bar: (bar.timestamp, bar.instrument_id, bar.granularity)))


def bar_payload(bar: Bar) -> str:
    payload = bar.model_dump(mode="json")
    payload["timestamp"] = bar.timestamp.astimezone(UTC).isoformat()
    for field in ("open", "high", "low", "close", "volume", "value"):
        value = payload[field]
        if isinstance(value, str) and "." in value:
            payload[field] = value.rstrip("0").rstrip(".")
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def dataset_digest(bars: tuple[Bar, ...]) -> str:
    digest = sha256()
    for bar in bars:
        digest.update(bar_payload(bar).encode())
        digest.update(b"\n")
    return digest.hexdigest()
