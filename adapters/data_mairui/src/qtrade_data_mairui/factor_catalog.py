"""Bounded persistent metadata snapshots with short, I/O-free state locks."""

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

from pydantic import TypeAdapter

from qtrade_data_contracts.collection import FactorDefinition, digest
from qtrade_data_contracts.factor_catalog import CatalogStatus, FactorCatalogSnapshot
from qtrade_data_contracts.models import ErrorCode

DEFINITIONS = TypeAdapter(tuple[FactorDefinition, ...])
MAX_BYTES = 4_000_000


class FactorCatalogCache:
    """Snapshots are provider metadata, never proof of current subscription rights.

    Readers only copy immutable state. The caller owns refresh single-flight and
    provider pacing; a network request or disk commit never holds the state lock.
    """

    def __init__(self, root: Path):
        self.root = root
        self.path = root / "factor-catalog-current.v1.json"
        self._guard = Lock()
        self._definitions: tuple[FactorDefinition, ...] = ()
        self._fetched_at: datetime | None = None
        self._attempt: datetime | None = None
        self._error: ErrorCode | None = None
        self._refreshing = False
        self._load()

    def _load(self) -> None:
        try:
            if self.path.exists():
                if self.path.stat().st_size > MAX_BYTES:
                    raise ValueError
                saved = FactorCatalogSnapshot.model_validate_json(self.path.read_bytes())
                if saved.status not in {"fresh", "stale"}:
                    raise ValueError
                self._definitions, self._fetched_at = saved.definitions, saved.fetched_at
                self._attempt = saved.last_attempt_at
                return
            # Older immutable catalog files have verified content hashes but no
            # retrieval timestamp. Import at most 16 candidates as explicitly
            # stale evidence; file mtime must not become a provider timestamp.
            paths = sorted(self.root.glob("factor-catalog-*.json"), reverse=True)[:16]
            for path in paths:
                if path.stat().st_size > MAX_BYTES:
                    continue
                try:
                    definitions = DEFINITIONS.validate_json(path.read_bytes())
                    if not definitions or len(definitions) > 1000:
                        continue
                    sha = digest([item.model_dump(mode="json") for item in definitions])
                    if path.name != f"factor-catalog-{sha}.json":
                        continue
                    self._definitions = definitions
                    self._snapshot(True, datetime.now(UTC))  # Validate identities too.
                    return
                except ValueError:
                    self._definitions = ()
        except (OSError, ValueError):
            self._definitions, self._fetched_at = (), None
            self._error = "unverified"

    def _snapshot(self, configured: bool, now: datetime) -> FactorCatalogSnapshot:
        definitions = self._definitions if configured else ()
        age = (now - self._fetched_at).total_seconds() if self._fetched_at else None
        status: CatalogStatus = (
            "unconfigured"
            if not configured
            else "fresh"
            if definitions and age is not None and 0 <= age < 900 and not self._error
            else "stale"
            if definitions
            else "loading"
            if self._refreshing
            else "unavailable"
        )
        return FactorCatalogSnapshot(
            schema_version=1,
            source="mairui.factor.history",
            status=status,
            definitions=definitions,
            content_sha256=digest([d.model_dump(mode="json") for d in definitions])
            if definitions
            else None,
            fetched_at=self._fetched_at if configured else None,
            last_attempt_at=self._attempt if configured else None,
            refreshing=self._refreshing if configured else False,
            error_code=self._error if configured else None,
            permission_evidence="not_established_by_catalog",
        )

    def snapshot(self, configured: bool, now: datetime | None = None) -> FactorCatalogSnapshot:
        with self._guard:
            return self._snapshot(configured, now or datetime.now(UTC))

    def begin(self) -> None:
        with self._guard:
            self._attempt, self._refreshing = datetime.now(UTC), True

    def failed(self, code: ErrorCode) -> None:
        with self._guard:
            self._error, self._refreshing = code, False

    def save(self, definitions: tuple[FactorDefinition, ...]) -> None:
        now = datetime.now(UTC)
        saved = FactorCatalogSnapshot(
            schema_version=1,
            source="mairui.factor.history",
            status="fresh",
            definitions=definitions,
            content_sha256=digest([d.model_dump(mode="json") for d in definitions]),
            fetched_at=now,
            last_attempt_at=now,
            refreshing=False,
            error_code=None,
            permission_evidence="not_established_by_catalog",
        )
        encoded = saved.model_dump_json()
        if len(encoded.encode()) > MAX_BYTES:
            raise ValueError("catalog snapshot exceeds bounded storage")
        # Readers see either the previous complete snapshot or the new complete
        # one. A failed disk commit retains prior state; no half-written pointer.
        handle, name = tempfile.mkstemp(prefix=".factor-catalog-", dir=self.root)
        try:
            with os.fdopen(handle, "w") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(name).unlink(missing_ok=True)
        with self._guard:
            self._definitions, self._fetched_at = definitions, now
            self._error, self._refreshing = None, False
