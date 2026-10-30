"""Safe operation context shared by orchestration and adapter logging."""

from contextvars import ContextVar

collection_context: ContextVar[dict[str, str | int] | None] = ContextVar(
    "collection_context", default=None
)
