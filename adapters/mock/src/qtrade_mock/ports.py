"""Deterministic port doubles, explicitly not a market or backtest simulator."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from qtrade_contracts import ArtifactManifest, BacktestRun, Bar, OrderCommand
from qtrade_contracts.models import BarGranularity
from qtrade_domain.market_data import dataset_bars
from qtrade_domain.objects import validate_object
from qtrade_domain.ports import (
    BarPage,
    BarQuery,
    BrokerConnectionStatus,
    BrokerEvent,
    BrokerEventType,
    BrokerOrder,
    BrokerSnapshot,
    ComputeJob,
    DeliveryReceipt,
    ErrorCode,
    Notification,
    ObjectRef,
    PortError,
    ResearchRequest,
)


class MockObjectStore:
    def __init__(self) -> None:
        self._objects: dict[tuple[UUID, str], tuple[ObjectRef, bytes]] = {}

    def put(
        self,
        workspace_id: UUID,
        key: str,
        content: bytes,
        *,
        content_type: str,
        expected_sha256: str,
    ) -> ObjectRef:
        validate_object(key, content, expected_sha256)
        ref = ObjectRef(workspace_id, key, expected_sha256, len(content), content_type)
        previous = self._objects.get((workspace_id, key))
        if previous is not None and previous != (ref, content):
            raise PortError(ErrorCode.CONFLICT, "immutable object already exists")
        self._objects[(workspace_id, key)] = (ref, content)
        return ref

    def stat(self, workspace_id: UUID, key: str) -> ObjectRef:
        try:
            return self._objects[(workspace_id, key)][0]
        except KeyError:
            raise PortError(ErrorCode.NOT_FOUND, "object not found") from None

    def get(self, workspace_id: UUID, key: str, *, expected_sha256: str) -> bytes:
        validate_object(key, None, expected_sha256)
        self.stat(workspace_id, key)
        content = self._objects[(workspace_id, key)][1]
        validate_object(key, content, expected_sha256)
        return content


class MockMarketData:
    def __init__(self) -> None:
        self._datasets: dict[tuple[UUID, UUID], tuple[Bar, ...]] = {}

    def write_bars(self, workspace_id: UUID, dataset_id: UUID, bars: tuple[Bar, ...]) -> None:
        ordered = dataset_bars(workspace_id, dataset_id, bars)
        key = (workspace_id, dataset_id)
        if key in self._datasets and self._datasets[key] != ordered:
            raise PortError(ErrorCode.CONFLICT, "immutable dataset already exists")
        self._datasets[key] = ordered

    def query_bars(self, query: BarQuery) -> BarPage:
        key = (query.workspace_id, query.dataset_id)
        if key not in self._datasets:
            raise PortError(ErrorCode.NOT_FOUND, "dataset not found")
        bars = tuple(
            b
            for b in self._datasets[key]
            if b.instrument_id == query.instrument_id
            and b.granularity == query.granularity
            and query.start <= b.timestamp < query.end
        )
        try:
            offset = int(query.cursor) if query.cursor is not None else 0
        except ValueError:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid cursor") from None
        if offset < 0 or offset > len(bars):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid cursor")
        end = offset + query.limit
        return BarPage(bars[offset:end], str(end) if end < len(bars) else None)

    def instruments_for_dataset(
        self, workspace_id: UUID, dataset_id: UUID
    ) -> tuple[tuple[str, BarGranularity], ...]:
        try:
            bars = self._datasets[(workspace_id, dataset_id)]
        except KeyError:
            raise PortError(ErrorCode.NOT_FOUND, "dataset not found") from None
        return tuple(sorted({(bar.instrument_id, bar.granularity) for bar in bars}))


class MockCompute:
    """Jobs only advance via explicit complete(), making cancellation/failure testable."""

    def __init__(self) -> None:
        self._jobs: dict[tuple[UUID, UUID], ComputeJob] = {}
        self._inputs: dict[tuple[UUID, UUID], str] = {}

    def _submit(self, manifest: ArtifactManifest, identity: str) -> ComputeJob:
        key = (manifest.workspace_id, manifest.run_id)
        if key in self._inputs and self._inputs[key] != identity:
            raise PortError(ErrorCode.CONFLICT, "job ID reused with different inputs")
        self._inputs[key] = identity
        self._jobs.setdefault(key, ComputeJob(*key, "queued", 0))
        return self._jobs[key]

    def clear(self) -> None:
        self._jobs.clear()
        self._inputs.clear()

    def status(self, workspace_id: UUID, job_id: UUID) -> ComputeJob:
        try:
            return self._jobs[(workspace_id, job_id)]
        except KeyError:
            raise PortError(ErrorCode.NOT_FOUND, "job not found") from None

    def cancel(self, workspace_id: UUID, job_id: UUID) -> ComputeJob:
        job = self.status(workspace_id, job_id)
        if job.status in {"completed", "failed"}:
            raise PortError(ErrorCode.CONFLICT, "job already terminal")
        job = replace(job, status="cancelled")
        self._jobs[(workspace_id, job_id)] = job
        return job

    def advance(self, workspace_id: UUID, job_id: UUID, progress: int) -> ComputeJob:
        job = self.status(workspace_id, job_id)
        if (
            job.status not in {"queued", "running"}
            or type(progress) is not int
            or not job.progress <= progress < 100
        ):
            raise PortError(ErrorCode.CONFLICT, "invalid job progress")
        job = replace(job, status="running", progress=progress)
        self._jobs[(workspace_id, job_id)] = job
        return job

    def fail(self, workspace_id: UUID, job_id: UUID, code: ErrorCode) -> ComputeJob:
        job = self.status(workspace_id, job_id)
        if job.status not in {"queued", "running"}:
            raise PortError(ErrorCode.CONFLICT, "job already terminal")
        job = replace(job, status="failed", error_code=code)
        self._jobs[(workspace_id, job_id)] = job
        return job

    def complete(self, workspace_id: UUID, job_id: UUID, result: ObjectRef) -> ComputeJob:
        job = self.status(workspace_id, job_id)
        if job.status not in {"queued", "running"} or result.workspace_id != workspace_id:
            raise PortError(ErrorCode.CONFLICT, "invalid completion")
        job = replace(job, status="completed", progress=100, result=result)
        self._jobs[(workspace_id, job_id)] = job
        return job


class MockTradingEngine(MockCompute):
    def submit(self, run: BacktestRun, manifest: ArtifactManifest) -> ComputeJob:
        if (
            run.workspace_id != manifest.workspace_id
            or run.run_id != manifest.run_id
            or run.manifest_id != manifest.manifest_id
            or run.dataset_id != manifest.dataset_id
            or run.strategy_version_id != manifest.strategy_version_id
            or manifest.kind != "backtest"
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "run/manifest mismatch")
        return self._submit(manifest, run.model_dump_json() + manifest.model_dump_json())


class MockResearch(MockCompute):
    def submit(self, request: ResearchRequest) -> ComputeJob:
        if request.manifest.kind != "research" or any(
            ref.workspace_id != request.manifest.workspace_id for ref in request.input_artifacts
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "research input mismatch")
        return self._submit(request.manifest, request.manifest.model_dump_json() + repr(request))


class MockBroker:
    def __init__(self) -> None:
        self._commands: dict[UUID, OrderCommand] = {}
        self._orders: dict[UUID, BrokerOrder] = {}
        self._connections: dict[tuple[UUID, UUID], BrokerConnectionStatus] = {}
        self._events: dict[tuple[UUID, UUID], list[BrokerEvent]] = {}
        self._failures: dict[tuple[UUID, UUID], ErrorCode] = {}

    def set_connected(
        self,
        workspace_id: UUID,
        account_id: UUID,
        connected: bool,
        *,
        error_code: str | None = None,
    ) -> None:
        scope = (workspace_id, account_id)
        self._connections[scope] = BrokerConnectionStatus(connected, datetime.now(UTC), error_code)
        self._emit(
            workspace_id,
            account_id,
            None,
            "connection_changed",
            error_code=error_code,
        )

    def fail_next_submission(self, workspace_id: UUID, account_id: UUID, code: ErrorCode) -> None:
        if code not in {ErrorCode.TIMEOUT, ErrorCode.UNAVAILABLE, ErrorCode.RATE_LIMITED}:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid mock broker failure code")
        self._failures[(workspace_id, account_id)] = code

    def connection_status(self, workspace_id: UUID, account_id: UUID) -> BrokerConnectionStatus:
        return self._connections.get(
            (workspace_id, account_id), BrokerConnectionStatus(True, datetime.now(UTC))
        )

    def events(
        self, workspace_id: UUID, account_id: UUID, *, after_sequence: int = 0, limit: int = 100
    ) -> tuple[BrokerEvent, ...]:
        if (
            type(after_sequence) is not int
            or after_sequence < 0
            or type(limit) is not int
            or not 1 <= limit <= 1000
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid broker event cursor")
        return tuple(
            event
            for event in self._events.get((workspace_id, account_id), ())
            if event.sequence > after_sequence
        )[:limit]

    def submit(self, command: OrderCommand) -> BrokerOrder:
        scope = (command.workspace_id, command.intent.account_id)
        connection = self.connection_status(*scope)
        if not connection.connected:
            raise PortError(ErrorCode.UNAVAILABLE, "mock broker is disconnected")
        failure = self._failures.pop(scope, None)
        if failure is not None:
            raise PortError(failure, "mock broker submission failed")
        key = command.idempotency_key
        if key in self._commands and self._commands[key] != command:
            raise PortError(ErrorCode.CONFLICT, "idempotency key reused")
        if key not in self._commands:
            self._commands[key] = command
            self._orders[key] = BrokerOrder(
                command.workspace_id,
                command.intent.account_id,
                command.command_id,
                str(key),
                "submitted",
                command.intent.quantity,
                Decimal(0),
            )
            self._emit(*scope, str(key), "submitted")
        return self._orders[key]

    def cancel(self, workspace_id: UUID, account_id: UUID, broker_order_id: str) -> BrokerOrder:
        for key, order in self._orders.items():
            if (order.workspace_id, order.account_id, order.broker_order_id) == (
                workspace_id,
                account_id,
                broker_order_id,
            ):
                if order.status in {"filled", "cancelled", "rejected"}:
                    if order.status == "cancelled":
                        return order
                    raise PortError(ErrorCode.CONFLICT, "terminal broker order cannot be cancelled")
                order = replace(order, status="cancelled")
                self._orders[key] = order
                self._emit(workspace_id, account_id, broker_order_id, "cancelled")
                return order
        raise PortError(ErrorCode.NOT_FOUND, "order not found")

    def fill(
        self,
        idempotency_key: UUID,
        cumulative_quantity: Decimal,
        average_price: Decimal,
    ) -> BrokerOrder:
        order = self._orders.get(idempotency_key)
        if order is None:
            raise PortError(ErrorCode.NOT_FOUND, "broker order not found")
        if (
            not isinstance(cumulative_quantity, Decimal)
            or not cumulative_quantity.is_finite()
            or cumulative_quantity <= order.filled_quantity
            or cumulative_quantity > order.quantity
            or not isinstance(average_price, Decimal)
            or not average_price.is_finite()
            or average_price <= 0
            or order.status not in {"submitted", "partially_filled"}
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid mock broker fill")
        status: Literal["filled", "partially_filled"] = (
            "filled" if cumulative_quantity == order.quantity else "partially_filled"
        )
        updated = replace(
            order,
            status=status,
            filled_quantity=cumulative_quantity,
            average_fill_price=average_price,
        )
        self._orders[idempotency_key] = updated
        self._emit(
            order.workspace_id,
            order.account_id,
            order.broker_order_id,
            status,
            filled_quantity=cumulative_quantity,
            average_fill_price=average_price,
        )
        return updated

    def reject(self, idempotency_key: UUID, code: str) -> BrokerOrder:
        order = self._orders.get(idempotency_key)
        if order is None:
            raise PortError(ErrorCode.NOT_FOUND, "broker order not found")
        if order.status not in {"submitted", "partially_filled"} or not code:
            raise PortError(ErrorCode.CONFLICT, "broker order cannot be rejected")
        updated = replace(order, status="rejected", rejection_code=code)
        self._orders[idempotency_key] = updated
        self._emit(
            order.workspace_id, order.account_id, order.broker_order_id, "rejected", error_code=code
        )
        return updated

    def _emit(
        self,
        workspace_id: UUID,
        account_id: UUID,
        broker_order_id: str | None,
        event_type: BrokerEventType,
        *,
        filled_quantity: Decimal = Decimal(0),
        average_fill_price: Decimal | None = None,
        error_code: str | None = None,
    ) -> None:
        events = self._events.setdefault((workspace_id, account_id), [])
        events.append(
            BrokerEvent(
                workspace_id,
                account_id,
                len(events) + 1,
                uuid4(),
                broker_order_id,
                event_type,
                datetime.now(UTC),
                filled_quantity,
                average_fill_price,
                error_code,
            )
        )

    def snapshot(self, workspace_id: UUID, account_id: UUID) -> BrokerSnapshot:
        return BrokerSnapshot(
            workspace_id,
            account_id,
            datetime(2026, 1, 1, tzinfo=UTC),
            "USD",
            Decimal(0),
            (),
            tuple(
                order
                for order in self._orders.values()
                if order.workspace_id == workspace_id and order.account_id == account_id
            ),
        )


class MockNotification:
    def __init__(self) -> None:
        self._sent: dict[tuple[UUID, UUID], Notification] = {}

    def send(self, notification: Notification) -> DeliveryReceipt:
        key = (notification.workspace_id, notification.notification_id)
        if key in self._sent and self._sent[key] != notification:
            raise PortError(ErrorCode.CONFLICT, "notification ID reused")
        self._sent[key] = notification
        return DeliveryReceipt(
            notification.notification_id, str(notification.notification_id), "delivered"
        )
