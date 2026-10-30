"""Versioned historical trade-tick aggregation; source volume is shares, not lots.

Provider contract: https://www.mairui.club/hsdata (historical zbjy).
Only observed minutes are emitted; gaps and non-trading periods are not filled.
"""

import json
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from qtrade_contracts import Bar
from qtrade_contracts.models import BarGranularity, Granularity
from qtrade_domain.batches import ArchivedBatch
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_mairui.ticks import MaiRuiAShareTickProvider


class MaiRuiMinuteBars:
    version = "mairui.trade-tick-to-1m.v1"

    def output_granularity(self, source: Granularity) -> BarGranularity:
        if source != "tick":
            raise PortError(ErrorCode.INVALID_INPUT, "minute aggregation requires trade ticks")
        return "1m"

    def transform(self, dataset_id: UUID, batches: tuple[ArchivedBatch, ...]) -> tuple[Bar, ...]:
        groups: dict[
            tuple[str, datetime], list[tuple[datetime, int, Decimal, Decimal, ArchivedBatch]]
        ] = defaultdict(list)
        # MaiRui returns descending ticks. Reverse equal-time ties too, preserving archive order.
        index = 0
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
                minute = timestamp.replace(second=0, microsecond=0)
                groups[(batch.metadata.instrument_id, minute)].append(
                    (timestamp, -index, price, volume, batch)
                )
                index += 1
        bars = []
        for (instrument, minute), ticks in sorted(groups.items()):
            ticks.sort(key=lambda item: (item[0], item[1]))
            source = ticks[0][4].metadata
            prices = [tick[2] for tick in ticks]
            bars.append(
                Bar(
                    schema_version=1,
                    workspace_id=source.workspace_id,
                    instrument_id=instrument,
                    specification_version=1,
                    timestamp=minute,
                    granularity="1m",
                    open=prices[0],
                    high=max(prices),
                    low=min(prices),
                    close=prices[-1],
                    volume=sum((tick[3] for tick in ticks), Decimal(0)),
                    value=sum((tick[2] * tick[3] for tick in ticks), Decimal(0)),
                    batch_id=source.batch_id,
                    dataset_id=dataset_id,
                    quality="valid",
                )
            )
        if not bars:
            raise PortError(ErrorCode.INVALID_INPUT, "download contains no observed trade minutes")
        return tuple(bars)
