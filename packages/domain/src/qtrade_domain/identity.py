"""Identity vocabulary. Authentication tokens remain in encrypted infrastructure storage."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class Role(StrEnum):
    VIEWER = "viewer"
    ANALYST = "analyst"
    TRADER = "trader"
    RISK_MANAGER = "risk_manager"
    ADMIN = "admin"


@dataclass(frozen=True)
class User:
    user_id: UUID
    issuer: str
    subject: str
    display_name: str
    email: str | None
    disabled: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class Workspace:
    workspace_id: UUID
    name: str
    kind: str
    owner_id: UUID | None
    live_trading_enabled: bool
    archived: bool
    created_at: datetime


@dataclass(frozen=True)
class Membership:
    workspace_id: UUID
    user_id: UUID
    role: Role
    active: bool
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class Session:
    session_digest: str
    user_id: UUID
    current_workspace_id: UUID | None
    sso_sid: str | None
    csrf_digest: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    revoked_at: datetime | None


@dataclass(frozen=True)
class AuditEvent:
    event_id: int
    workspace_id: UUID | None
    actor_type: str
    actor_id: UUID | None
    action: str
    result: str
    occurred_at: datetime
    source: str
    source_ip: str | None
    trace_id: UUID
    details_json: str
