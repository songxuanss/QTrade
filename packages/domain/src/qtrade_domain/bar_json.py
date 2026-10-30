"""Explicit internal standard-Bar JSON format, not a third-party provider parser."""

from uuid import UUID

from qtrade_contracts import Bar
from qtrade_contracts.models import BarGranularity, Granularity
from qtrade_domain.batches import ArchivedBatch
from qtrade_domain.ports import ErrorCode, PortError


class StandardBarJsonTransformation:
    version = "qtrade.standard-bar-json.v1"

    def output_granularity(self, source: Granularity) -> BarGranularity:
        if source == "tick":
            raise PortError(ErrorCode.INVALID_INPUT, "standard Bar transformation requires Bars")
        return source

    def transform(self, dataset_id: UUID, batches: tuple[ArchivedBatch, ...]) -> tuple[Bar, ...]:
        bars: list[Bar] = []
        observations: dict[tuple[str, str, object], Bar] = {}
        for batch in batches:
            if batch.metadata.source != "qtrade.standard-bar-json":
                raise PortError(ErrorCode.INVALID_INPUT, "unsupported raw batch format")
            for record in batch.records:
                try:
                    if record.content_type != "application/json":
                        raise ValueError("unsupported format")
                    bar = Bar.model_validate_json(record.payload)
                except (ValueError, TypeError):
                    raise PortError(ErrorCode.INVALID_INPUT, "invalid raw Bar record") from None
                if (
                    bar.workspace_id != batch.metadata.workspace_id
                    or bar.instrument_id != batch.metadata.instrument_id
                    or bar.granularity != batch.metadata.granularity
                ):
                    raise PortError(ErrorCode.INVALID_INPUT, "raw Bar provenance mismatch")
                normalized = bar.model_copy(
                    update={"batch_id": batch.metadata.batch_id, "dataset_id": dataset_id}
                )
                key = (normalized.instrument_id, normalized.granularity, normalized.timestamp)
                previous = observations.get(key)
                if previous is not None:
                    identity_fields = {"batch_id", "dataset_id"}
                    if previous.model_dump(exclude=identity_fields) != normalized.model_dump(
                        exclude=identity_fields
                    ):
                        raise PortError(
                            ErrorCode.INVALID_INPUT, "conflicting overlapping Bar records"
                        )
                    continue
                observations[key] = normalized
                bars.append(normalized)
        return tuple(bars)
