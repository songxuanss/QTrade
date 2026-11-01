"""Versioned trade-tick passthrough bars; one Bar per observed provider print."""

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from qtrade_contracts import Bar
from qtrade_contracts.models import BarGranularity, Granularity
from qtrade_domain.batches import ArchivedBatch
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_mairui.ticks import MaiRuiAShareTickProvider


class MaiRuiTradeTicks:
    version = "mairui.trade-tick.v1"

    def output_granularity(self, source: Granularity) -> BarGranularity:
        if source != "tick":
            raise PortError(ErrorCode.INVALID_INPUT, "tick passthrough requires trade ticks")
        return "tick"

    def transform(self, dataset_id: UUID, batches: tuple[ArchivedBatch, ...]) -> tuple[Bar, ...]:
        observed: dict[tuple[str, datetime], Bar] = {}
        for batch in batches:
            if (
                batch.metadata.source != MaiRuiAShareTickProvider.source
                or batch.metadata.granularity != "tick"
            ):
                raise PortError(ErrorCode.INVALID_INPUT, "unsupported trade-tick provenance")
            for record in batch.records:
                try:
                    row = json.loads(record.payload)
                    timestamp = MaiRuiAShareTickProvider._timestamp(row)
                    price, volume = Decimal(str(row["p"])), Decimal(str(row["v"]))
                    if not price.is_finite() or price <= 0 or not volume.is_finite() or volume < 0:
                        raise ValueError
                except (KeyError, TypeError, ValueError, InvalidOperation):
                    raise PortError(ErrorCode.INVALID_INPUT, "invalid trade tick") from None
                observed[(batch.metadata.instrument_id, timestamp)] = Bar(
                    schema_version=1,
                    workspace_id=batch.metadata.workspace_id,
                    instrument_id=batch.metadata.instrument_id,
                    specification_version=1,
                    timestamp=timestamp,
                    granularity="tick",
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    volume=volume,
                    value=price * volume,
                    batch_id=batch.metadata.batch_id,
                    dataset_id=dataset_id,
                    quality="valid",
                )
        bars = tuple(observed[key] for key in sorted(observed, key=lambda item: (item[0], item[1])))
        if not bars:
            raise PortError(ErrorCode.INVALID_INPUT, "download contains no observed trade ticks")
        return bars
