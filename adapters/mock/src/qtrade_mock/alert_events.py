"""Idempotent in-memory adapter for normalized alert events."""

from dataclasses import replace
from datetime import datetime
from threading import RLock
from uuid import UUID

from qtrade_domain.alerts import AlertAction, AlertDisposition, AlertEvent, AlertStatus
from qtrade_domain.ports import ErrorCode, PortError


class MockAlertEventStore:
    def __init__(self) -> None:
        self._events: dict[tuple[UUID, str, str], AlertEvent] = {}
        self._history: dict[UUID, list[AlertDisposition]] = {}
        self._lock = RLock()

    def record(self, event: AlertEvent) -> AlertEvent:
        key = (event.workspace_id, event.source.value, event.source_event_id)
        with self._lock:
            existing = self._events.get(key)
            if existing is not None:
                if existing == event:
                    return existing
                # A retry may carry a new event UUID; compare immutable source facts.
                if (
                    existing.event_type,
                    existing.severity,
                    existing.title,
                    existing.message,
                    existing.resource_id,
                    existing.occurred_at,
                    existing.context_snapshot_json,
                ) != (
                    event.event_type,
                    event.severity,
                    event.title,
                    event.message,
                    event.resource_id,
                    event.occurred_at,
                    event.context_snapshot_json,
                ):
                    raise PortError(ErrorCode.CONFLICT, "alert source event identity reused")
                return existing
            self._events[key] = event
            return event

    def list(self, workspace_id: UUID, limit: int) -> tuple[AlertEvent, ...]:
        if type(limit) is not int or not 1 <= limit <= 200:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid alert event limit")
        with self._lock:
            rows = [event for event in self._events.values() if event.workspace_id == workspace_id]
            return tuple(
                sorted(
                    rows, key=lambda event: (event.occurred_at, str(event.event_id)), reverse=True
                )[:limit]
            )

    def get(self, workspace_id: UUID, event_id: UUID) -> AlertEvent | None:
        with self._lock:
            return next(
                (
                    e
                    for e in self._events.values()
                    if e.workspace_id == workspace_id and e.event_id == event_id
                ),
                None,
            )

    def history(self, workspace_id: UUID, event_id: UUID) -> tuple[AlertDisposition, ...]:
        if self.get(workspace_id, event_id) is None:
            return ()
        with self._lock:
            return tuple(self._history.get(event_id, ()))

    def handle(
        self,
        actor_id: UUID,
        workspace_id: UUID,
        event_id: UUID,
        action: AlertAction,
        occurred_at: datetime,
        note: str | None = None,
        silenced_until: datetime | None = None,
    ) -> AlertEvent:
        with self._lock:
            key = next(
                (
                    k
                    for k, e in self._events.items()
                    if e.workspace_id == workspace_id and e.event_id == event_id
                ),
                None,
            )
            if key is None:
                raise PortError(ErrorCode.NOT_FOUND, "alert event unavailable")
            event = self._events[key]
            if event.status == AlertStatus.CLOSED and action != "note":
                raise PortError(ErrorCode.CONFLICT, "closed alert cannot be changed")
            if action == "claim":
                if event.assignee_id not in (None, actor_id):
                    raise PortError(ErrorCode.CONFLICT, "alert already claimed")
                changed = event.assignee_id != actor_id
                updated = replace(event, assignee_id=actor_id, updated_at=occurred_at)
            elif action == "acknowledge":
                if event.status == AlertStatus.CLOSED:
                    raise PortError(ErrorCode.CONFLICT, "closed alert cannot be acknowledged")
                changed = event.status != AlertStatus.ACKNOWLEDGED
                updated = replace(event, status=AlertStatus.ACKNOWLEDGED, updated_at=occurred_at)
            elif action == "silence":
                changed = event.silenced_until != silenced_until
                updated = replace(event, silenced_until=silenced_until, updated_at=occurred_at)
            elif action == "close":
                changed = event.status != AlertStatus.CLOSED
                updated = replace(event, status=AlertStatus.CLOSED, updated_at=occurred_at)
            else:
                changed = True
                updated = replace(event, updated_at=occurred_at)
            self._events[key] = updated
            if changed:
                disposition = AlertDisposition(
                    event_id, workspace_id, actor_id, action, note, silenced_until, occurred_at
                )
                self._history.setdefault(event_id, []).append(disposition)
            return updated
