"""Create and read workspace universe membership intervals with evidence."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal, Protocol
from uuid import UUID, uuid4

from qtrade_contracts import UniverseMembership
from qtrade_data_contracts.collection import check_instrument
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.ports import ErrorCode, PortError


@dataclass(frozen=True, slots=True)
class MembershipSubmission:
    instrument_id: str
    effective_from: datetime
    effective_to: datetime
    evidence: str
    source: Literal["workspace.definition", "supplier.membership"] = "workspace.definition"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.instrument_id, str)
            or not 1 <= len(self.instrument_id) <= 128
            or self.effective_from.utcoffset() is None
            or self.effective_to.utcoffset() is None
            or self.effective_from >= self.effective_to
            or not isinstance(self.evidence, str)
            or not 1 <= len(self.evidence) <= 500
            or self.source not in {"workspace.definition", "supplier.membership"}
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid membership request")


class MembershipStorePort(Protocol):
    def create(self, membership: UniverseMembership) -> None: ...

    def overlaps(
        self, workspace_id: UUID, instrument_id: str, start: datetime, end: datetime
    ) -> bool: ...

    def list(
        self,
        workspace_id: UUID,
        instrument_id: str | None = None,
        limit: int = 200,
    ) -> tuple[UniverseMembership, ...]: ...

    def for_instruments(
        self, workspace_id: UUID, instrument_ids: tuple[str, ...]
    ) -> tuple[UniverseMembership, ...]: ...


class UniverseMembershipService:
    def __init__(self, store: MembershipStorePort) -> None:
        self._store = store

    def create(
        self, access: WorkspaceAccess, submission: MembershipSubmission
    ) -> UniverseMembership:
        access.require(Permission.DATA_MANAGE)
        try:
            check_instrument(submission.instrument_id)
        except ValueError:
            raise PortError(
                ErrorCode.INVALID_INPUT,
                "membership instrument must use an explicit market identity",
            ) from None
        workspace_id = access.workspace.workspace_id
        if self._store.overlaps(
            workspace_id,
            submission.instrument_id,
            submission.effective_from,
            submission.effective_to,
        ):
            raise PortError(ErrorCode.CONFLICT, "membership interval overlaps an existing interval")
        created_at = datetime.now(UTC)
        return UniverseMembership(
            schema_version=1,
            workspace_id=workspace_id,
            membership_id=uuid4(),
            instrument_id=submission.instrument_id,
            effective_from=submission.effective_from,
            effective_to=submission.effective_to,
            source=submission.source,
            evidence=submission.evidence,
            content_version=self._version(submission),
            created_by=access.user_id,
            created_at=created_at,
        )

    @staticmethod
    def _version(submission: MembershipSubmission) -> str:
        return sha256(
            json.dumps(
                {
                    "instrument_id": submission.instrument_id,
                    "effective_from": submission.effective_from.astimezone(UTC).isoformat(),
                    "effective_to": submission.effective_to.astimezone(UTC).isoformat(),
                    "source": submission.source,
                    "evidence": submission.evidence,
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest()

    def persist(self, membership: UniverseMembership) -> UniverseMembership:
        self._store.create(membership)
        return membership

    def register(
        self, access: WorkspaceAccess, submission: MembershipSubmission
    ) -> UniverseMembership:
        return self.persist(self.create(access, submission))

    def list(
        self, access: WorkspaceAccess, instrument_id: str | None = None, limit: int = 200
    ) -> tuple[UniverseMembership, ...]:
        access.require(Permission.READ)
        return self._store.list(access.workspace.workspace_id, instrument_id, limit)

    def resolve(
        self, workspace_id: UUID, instrument_ids: tuple[str, ...]
    ) -> tuple[UniverseMembership, ...]:
        return self._store.for_instruments(workspace_id, instrument_ids)
