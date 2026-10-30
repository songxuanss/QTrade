"""Authorization and immutable member resolution behind a small catalog interface."""

from datetime import datetime
from uuid import UUID

from qtrade_data_contracts import UniverseDefinition, UniverseSnapshot

from .ports import DataAuthorizer, DataFailure, ReferenceProvider, UniverseRepository


class UniverseCatalog:
    def __init__(
        self,
        authorizer: DataAuthorizer,
        provider: ReferenceProvider,
        repository: UniverseRepository,
    ):
        self._authorizer, self._provider, self._repository = authorizer, provider, repository

    def definitions(
        self,
        actor_id: UUID,
        scope_id: UUID,
        *,
        search: str = "",
        category: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[tuple[UniverseDefinition, ...], int]:
        self._authorizer.require(actor_id, scope_id, "read")
        if (
            len(search) > 100
            or type(offset) is not int
            or offset < 0
            or type(limit) is not int
            or not 1 <= limit <= 100
            or category not in {None, "industry", "theme", "index_members"}
        ):
            raise DataFailure("invalid_input")
        items = tuple(
            sorted(
                (
                    item
                    for item in self._provider.definitions()
                    if (category is None or item.category == category)
                    and search.casefold() in (item.name + item.universe_id).casefold()
                ),
                key=lambda item: (item.name, item.universe_id),
            )
        )
        return items[offset : offset + limit], len(items)

    def resolve(self, actor_id: UUID, scope_id: UUID, universe_id: str) -> UniverseSnapshot:
        self._authorizer.require(actor_id, scope_id, "manage")
        item = next(
            (item for item in self._provider.definitions() if item.universe_id == universe_id), None
        )
        if item is None:
            raise DataFailure("not_found")
        if item.source_status != "available":
            raise DataFailure("unverified")
        content = self._provider.members(scope_id, item)
        if content.scope_id != scope_id or content.universe_id != universe_id:
            raise DataFailure("unavailable")
        self._authorizer.require(actor_id, scope_id, "manage")
        return self._repository.append(actor_id, content)

    def custom(
        self,
        actor_id: UUID,
        scope_id: UUID,
        name: str,
        identifiers: tuple[str, ...],
    ) -> UniverseSnapshot:
        self._authorizer.require(actor_id, scope_id, "manage")
        if not name.strip() or len(name) > 100 or not 1 <= len(identifiers) <= 10000:
            raise DataFailure("invalid_input")
        content = self._provider.custom(scope_id, name.strip(), identifiers)
        if content.scope_id != scope_id:
            raise DataFailure("unavailable")
        self._authorizer.require(actor_id, scope_id, "manage")
        return self._repository.append(actor_id, content)

    def snapshot(self, actor_id: UUID, scope_id: UUID, snapshot_id: UUID) -> UniverseSnapshot:
        self._authorizer.require(actor_id, scope_id, "read")
        result = self._repository.get(scope_id, snapshot_id)
        if result.scope_id != scope_id or result.snapshot_id != snapshot_id:
            raise DataFailure("unavailable")
        return result

    def snapshots(
        self,
        actor_id: UUID,
        scope_id: UUID,
        *,
        limit: int = 20,
        before: tuple[datetime, UUID] | None = None,
    ) -> tuple[UniverseSnapshot, ...]:
        self._authorizer.require(actor_id, scope_id, "read")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise DataFailure("invalid_input")
        items = self._repository.list(scope_id, limit=limit, before=before)
        if len(items) > limit or any(item.scope_id != scope_id for item in items):
            raise DataFailure("unavailable")
        return items
