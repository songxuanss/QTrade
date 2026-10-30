"""Internal storage/provider seams and explicit public data failure categories."""

from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from qtrade_data_contracts import UniverseContent, UniverseDefinition, UniverseSnapshot
from qtrade_data_contracts.models import ErrorCode

Action = Literal["read", "manage"]


class DataFailure(Exception):
    def __init__(self, code: ErrorCode):
        self.code = code
        super().__init__(code)


class DataAuthorizer(Protocol):
    def require(self, actor_id: UUID, scope_id: UUID, action: Action) -> None: ...


class ReferenceProvider(Protocol):
    def definitions(self) -> tuple[UniverseDefinition, ...]: ...

    def members(self, scope_id: UUID, definition: UniverseDefinition) -> UniverseContent: ...

    def custom(
        self, scope_id: UUID, name: str, identifiers: tuple[str, ...]
    ) -> UniverseContent: ...


class UniverseRepository(Protocol):
    def append(self, actor_id: UUID, content: UniverseContent) -> UniverseSnapshot: ...

    def get(self, scope_id: UUID, snapshot_id: UUID) -> UniverseSnapshot: ...

    def list(
        self,
        scope_id: UUID,
        *,
        limit: int,
        before: tuple[datetime, UUID] | None = None,
    ) -> tuple[UniverseSnapshot, ...]: ...
