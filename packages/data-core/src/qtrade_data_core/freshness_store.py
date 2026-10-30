"""Authorized snapshot operations; infrastructure cannot define freshness policy."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from qtrade_data_contracts.freshness import FreshnessKey, FreshnessResult
from qtrade_data_core.ports import DataAuthorizer, DataFailure


class FreshnessRepository(Protocol):
    def append(self, result: FreshnessResult) -> FreshnessResult: ...
    def latest(self, key: FreshnessKey) -> FreshnessResult: ...
    def history(
        self, key: FreshnessKey, *, limit: int, before: datetime | None = None
    ) -> tuple[FreshnessResult, ...]: ...


class FreshnessSnapshots:
    def __init__(self, auth: DataAuthorizer, repository: FreshnessRepository):
        self.auth, self.repository = auth, repository

    def record(self, actor: UUID, result: FreshnessResult) -> FreshnessResult:
        """Archive an exact observation; live producers use FreshnessRefresh.commit."""
        self.auth.require(actor, result.scope_id, "manage")
        # Frozen Pydantic objects can still be forged with model_copy; validate
        # at the port boundary before a writer can persist an alleged decision.
        try:
            validated = FreshnessResult.model_validate_json(result.model_dump_json())
        except ValueError:
            raise DataFailure("invalid_input") from None
        stored = self.repository.append(validated)
        if stored != validated:
            raise DataFailure("unavailable")
        return stored

    def latest(self, actor: UUID, key: FreshnessKey) -> FreshnessResult:
        self.auth.require(actor, key.scope_id, "read")
        result = self.repository.latest(key)
        require_snapshot_key(key, result)
        return result

    def history(
        self, actor: UUID, key: FreshnessKey, *, limit: int = 50, before: datetime | None = None
    ) -> tuple[FreshnessResult, ...]:
        self.auth.require(actor, key.scope_id, "read")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise DataFailure("invalid_input")
        if before is not None and before.utcoffset() is None:
            raise DataFailure("invalid_input")
        results = self.repository.history(key, limit=limit, before=before)
        if len(results) > limit:
            raise DataFailure("unavailable")
        for result in results:
            require_snapshot_key(key, result)
        times = [result.checked_at for result in results]
        if times != sorted(set(times), reverse=True) or any(
            before is not None and value >= before for value in times
        ):
            raise DataFailure("unavailable")
        return results


def require_snapshot_key(key: FreshnessKey, result: FreshnessResult) -> None:
    """Repository responses remain untrusted scoped transport, including model_copy values."""
    if result.key != key or result.scope_id != key.scope_id:
        raise DataFailure("unavailable")
    try:
        FreshnessResult.model_validate_json(result.model_dump_json())
    except ValueError:
        raise DataFailure("unavailable") from None
