"""Safe audit facts, independent of HTTP and persistence."""

import json
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from ipaddress import ip_address
from typing import Protocol
from uuid import UUID, uuid4

from qtrade_domain.authorization import Permission
from qtrade_domain.identity import Role
from qtrade_domain.ports import ErrorCode


@dataclass(frozen=True)
class AuditDetails:
    target_id: UUID | None = None
    target_workspace_id: UUID | None = None
    session_digest: str | None = None
    permission: Permission | None = None
    reason: ErrorCode | None = None
    role_before: Role | None = None
    role_after: Role | None = None
    active_before: bool | None = None
    active_after: bool | None = None

    def __post_init__(self) -> None:
        if any(
            value is not None and not isinstance(value, UUID)
            for value in (self.target_id, self.target_workspace_id)
        ):
            raise ValueError("invalid audit target")
        if self.session_digest is not None and not re.fullmatch(
            r"[0-9a-f]{64}", self.session_digest
        ):
            raise ValueError("invalid audit session reference")
        for value, kind in (
            (self.permission, Permission),
            (self.reason, ErrorCode),
            (self.role_before, Role),
            (self.role_after, Role),
        ):
            if value is not None and not isinstance(value, kind):
                raise ValueError("invalid audit classification")
        if any(
            value is not None and type(value) is not bool
            for value in (self.active_before, self.active_after)
        ):
            raise ValueError("invalid audit membership state")

    def canonical_json(self) -> str:
        return json.dumps(
            {
                key: str(value) if isinstance(value, UUID) else value
                for key, value in asdict(self).items()
                if value is not None
            },
            sort_keys=True,
            separators=(",", ":"),
        )


@dataclass(frozen=True)
class AuditRecord:
    workspace_id: UUID | None
    actor_type: str
    actor_id: UUID | None
    action: str
    result: str
    occurred_at: datetime
    source: str
    trace_id: UUID
    source_ip: str | None = None
    details: AuditDetails = field(default_factory=AuditDetails)

    def __post_init__(self) -> None:
        if (
            self.actor_type not in {"user", "system", "anonymous"}
            or (self.actor_type == "user") != (self.actor_id is not None)
            or self.result not in {"success", "denied", "failure"}
            or not 1 <= len(self.action) <= 256
            or not 1 <= len(self.source) <= 256
            or self.occurred_at.tzinfo is None
        ):
            raise ValueError("invalid audit record")
        if not isinstance(self.details, AuditDetails):
            raise ValueError("invalid audit details")
        if self.source_ip is not None:
            ip_address(self.source_ip)


class AuditStorePort(Protocol):
    def append(self, record: AuditRecord) -> int: ...


@dataclass(frozen=True)
class AuditContext:
    source: str
    trace_id: UUID = field(default_factory=uuid4)
    source_ip: str | None = None

    def __post_init__(self) -> None:
        if not 1 <= len(self.source) <= 256 or not isinstance(self.trace_id, UUID):
            raise ValueError("invalid audit context")
        if self.source_ip is not None:
            ip_address(self.source_ip)


def contextualize(record: AuditRecord, context: AuditContext | None) -> AuditRecord:
    if context is None:
        return record
    return replace(
        record, source=context.source, trace_id=context.trace_id, source_ip=context.source_ip
    )
