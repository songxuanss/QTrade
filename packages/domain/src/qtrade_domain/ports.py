"""Provider-independent interfaces. Adapters translate their SDK types at this seam.

Calls are synchronous; application workers own scheduling, deadlines and retries.
Workspace scope is explicit on every resource operation. No provider credentials or
SDK exceptions are exposed to callers.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Protocol, runtime_checkable
from uuid import UUID

from qtrade_contracts import ArtifactManifest, BacktestRun, Bar, OrderCommand
from qtrade_contracts.models import BarGranularity


class ErrorCode(StrEnum):
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    INVALID_INPUT = "invalid_input"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    UNAUTHORIZED = "unauthorized"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    REJECTED = "rejected"


class PortError(Exception):
    """Stable, safe error code; message must never include SDK secrets/responses."""

    def __init__(self, code: ErrorCode, message: str, *, retry_after: float | None = None):
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        return self.code in {
            ErrorCode.UNAVAILABLE,
            ErrorCode.TIMEOUT,
            ErrorCode.RATE_LIMITED,
        }


@dataclass(frozen=True)
class ObjectRef:
    workspace_id: UUID
    key: str
    content_sha256: str
    size: int
    content_type: str


@runtime_checkable
class ObjectStorePort(Protocol):
    def put(
        self,
        workspace_id: UUID,
        key: str,
        content: bytes,
        *,
        content_type: str,
        expected_sha256: str,
    ) -> ObjectRef:
        """Create only. Identical retry returns existing ref; overwrite conflicts."""
        ...

    def get(self, workspace_id: UUID, key: str, *, expected_sha256: str) -> bytes: ...
    def stat(self, workspace_id: UUID, key: str) -> ObjectRef: ...


@dataclass(frozen=True)
class BarQuery:
    workspace_id: UUID
    dataset_id: UUID
    instrument_id: str
    granularity: BarGranularity
    start: datetime
    end: datetime
    limit: int = 1000
    cursor: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.workspace_id, UUID)
            or not isinstance(self.dataset_id, UUID)
            or not isinstance(self.instrument_id, str)
            or not 1 <= len(self.instrument_id) <= 256
            or self.granularity not in ("tick", "1m", "5m", "15m", "30m", "1h", "1d")
            or not isinstance(self.start, datetime)
            or not isinstance(self.end, datetime)
            or self.start.utcoffset() is None
            or self.end.utcoffset() is None
            or type(self.limit) is not int
            or (
                self.cursor is not None
                and (not isinstance(self.cursor, str) or not 1 <= len(self.cursor) <= 512)
            )
            or self.start >= self.end
            or not 1 <= self.limit <= 10000
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid bar query range or limit")


@dataclass(frozen=True)
class BarPage:
    bars: tuple[Bar, ...]
    next_cursor: str | None


@runtime_checkable
class MarketDataPort(Protocol):
    def query_bars(self, query: BarQuery) -> BarPage: ...
    def write_bars(self, workspace_id: UUID, dataset_id: UUID, bars: tuple[Bar, ...]) -> None:
        """Dataset creation is immutable; an identical retry is idempotent."""
        ...


@dataclass(frozen=True)
class ComputeJob:
    workspace_id: UUID
    job_id: UUID
    status: Literal["queued", "running", "cancelled", "failed", "completed"]
    progress: int
    result: ObjectRef | None = None
    error_code: ErrorCode | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.workspace_id, UUID)
            or not isinstance(self.job_id, UUID)
            or self.status not in {"queued", "running", "cancelled", "failed", "completed"}
            or type(self.progress) is not int
            or not 0 <= self.progress <= 100
            or (self.result is not None and self.result.workspace_id != self.workspace_id)
            or (self.status == "completed" and (self.progress != 100 or self.result is None))
            or (self.status == "failed" and self.error_code is None)
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid compute job")


@runtime_checkable
class TradingEnginePort(Protocol):
    def submit(self, run: BacktestRun, manifest: ArtifactManifest) -> ComputeJob: ...
    def status(self, workspace_id: UUID, job_id: UUID) -> ComputeJob: ...
    def cancel(self, workspace_id: UUID, job_id: UUID) -> ComputeJob: ...


BrokerOrderStatus = Literal["submitted", "partially_filled", "filled", "cancelled", "rejected"]


@dataclass(frozen=True)
class BrokerOrder:
    workspace_id: UUID
    account_id: UUID
    command_id: UUID
    broker_order_id: str
    status: BrokerOrderStatus
    quantity: Decimal
    filled_quantity: Decimal
    rejection_code: str | None = None
    average_fill_price: Decimal | None = None
    commission: Decimal = Decimal(0)


@dataclass(frozen=True)
class BrokerPosition:
    instrument_id: str
    quantity: Decimal
    average_price: Decimal


@dataclass(frozen=True)
class BrokerSnapshot:
    workspace_id: UUID
    account_id: UUID
    as_of: datetime
    currency: str
    cash: Decimal
    positions: tuple[BrokerPosition, ...]
    orders: tuple[BrokerOrder, ...]


@dataclass(frozen=True)
class BrokerConnectionStatus:
    connected: bool
    observed_at: datetime
    last_error_code: str | None = None


BrokerEventType = Literal[
    "submitted", "partially_filled", "filled", "cancelled", "rejected", "connection_changed"
]


@dataclass(frozen=True)
class BrokerEvent:
    workspace_id: UUID
    account_id: UUID
    sequence: int
    event_id: UUID
    broker_order_id: str | None
    event_type: BrokerEventType
    occurred_at: datetime
    filled_quantity: Decimal = Decimal(0)
    average_fill_price: Decimal | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.workspace_id, UUID)
            or not isinstance(self.account_id, UUID)
            or type(self.sequence) is not int
            or self.sequence < 1
            or not isinstance(self.event_id, UUID)
            or self.event_type
            not in {
                "submitted",
                "partially_filled",
                "filled",
                "cancelled",
                "rejected",
                "connection_changed",
            }
            or not isinstance(self.occurred_at, datetime)
            or self.occurred_at.tzinfo is None
            or not isinstance(self.filled_quantity, Decimal)
            or not self.filled_quantity.is_finite()
            or self.filled_quantity < 0
            or (
                self.average_fill_price is not None
                and (
                    not isinstance(self.average_fill_price, Decimal)
                    or not self.average_fill_price.is_finite()
                    or self.average_fill_price <= 0
                )
            )
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid broker event")


@runtime_checkable
class BrokerMonitoringPort(Protocol):
    def connection_status(self, workspace_id: UUID, account_id: UUID) -> BrokerConnectionStatus: ...

    def events(
        self, workspace_id: UUID, account_id: UUID, *, after_sequence: int = 0, limit: int = 100
    ) -> tuple[BrokerEvent, ...]: ...


@runtime_checkable
class BrokerPort(Protocol):
    def submit(self, command: OrderCommand) -> BrokerOrder: ...
    def cancel(self, workspace_id: UUID, account_id: UUID, broker_order_id: str) -> BrokerOrder: ...
    def snapshot(self, workspace_id: UUID, account_id: UUID) -> BrokerSnapshot: ...


@dataclass(frozen=True)
class ResearchRequest:
    manifest: ArtifactManifest
    operation: str
    input_artifacts: tuple[ObjectRef, ...]


@runtime_checkable
class ResearchPort(Protocol):
    def submit(self, request: ResearchRequest) -> ComputeJob: ...
    def status(self, workspace_id: UUID, job_id: UUID) -> ComputeJob: ...
    def cancel(self, workspace_id: UUID, job_id: UUID) -> ComputeJob: ...


@dataclass(frozen=True)
class Notification:
    workspace_id: UUID
    notification_id: UUID
    channel: "NotificationChannel"
    destination_id: str
    subject: str
    body: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.workspace_id, UUID)
            or not isinstance(self.notification_id, UUID)
            or self.channel not in {"in_app", "email", "webhook"}
            or not isinstance(self.destination_id, str)
            or not 1 <= len(self.destination_id) <= 320
            or not isinstance(self.subject, str)
            or not 1 <= len(self.subject.strip()) <= 200
            or any(char in self.destination_id + self.subject for char in "\r\n")
            or not isinstance(self.body, str)
            or not 1 <= len(self.body.strip()) <= 8192
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid notification")


@dataclass(frozen=True)
class DeliveryReceipt:
    notification_id: UUID
    provider_reference: str
    status: Literal["delivered", "accepted"]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.notification_id, UUID)
            or not isinstance(self.provider_reference, str)
            or not 1 <= len(self.provider_reference) <= 256
            or self.status not in {"delivered", "accepted"}
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid notification receipt")


NotificationChannel = Literal["in_app", "email", "webhook"]


@runtime_checkable
class NotificationPort(Protocol):
    def send(self, notification: Notification) -> DeliveryReceipt:
        """Deduplicate on workspace/notification ID; safe receipt, no credentials."""
        ...


class NotificationInboxPort(Protocol):
    def record(self, notification: Notification, now: datetime) -> DeliveryReceipt: ...
