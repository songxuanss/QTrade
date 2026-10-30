"""Optional prepared-conversion seam; acquisition and offline execution share it."""

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from qtrade_data_contracts.collection import CollectionRawCapture
from qtrade_data_contracts.normalization_input import (
    CollectionNormalizationInput,
    NormalizationPayload,
)
from qtrade_data_core.collection import CollectionPart, CollectionResult, RawCapture

PreparedCapture = Callable[[NormalizationPayload], None]


@runtime_checkable
class PreparedCollectionProvider(Protocol):
    def fetch_prepared(
        self, part: CollectionPart, *, capture: RawCapture, prepared: PreparedCapture
    ) -> CollectionResult: ...
    def normalize(self, part: CollectionPart, payload: NormalizationPayload) -> CollectionResult:
        """Local conversion only: no HTTP, catalog refresh or cache lookup."""
        ...


@runtime_checkable
class PreparedCollectionArchive(Protocol):
    def capture_prepared(
        self,
        part: CollectionPart,
        payload: NormalizationPayload,
        captures: tuple[CollectionRawCapture, ...],
    ) -> CollectionNormalizationInput: ...
