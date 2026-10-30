"""Versioned Parquet envelope preserves original bytes and source ordering."""

from typing import cast

import pyarrow as pa
import pyarrow.parquet as pq

from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.raw_records import RawRecord, validate_records

SCHEMA = pa.schema(
    [
        pa.field("sequence", pa.int64(), nullable=False),
        pa.field("payload", pa.binary(), nullable=False),
        pa.field("content_type", pa.string(), nullable=False),
    ],
    metadata={b"qtrade.raw_records.schema_version": b"1"},
)


class ParquetRawRecordCodec:
    def encode(self, records: tuple[RawRecord, ...]) -> bytes:
        validate_records(records)
        try:
            table = pa.Table.from_arrays(
                [
                    pa.array([record.sequence for record in records], type=pa.int64()),
                    pa.array([record.payload for record in records], type=pa.binary()),
                    pa.array([record.content_type for record in records], type=pa.string()),
                ],
                schema=SCHEMA,
            )
            output = pa.BufferOutputStream()
            pq.write_table(
                table,
                output,
                compression="zstd",
                version="2.6",
                use_dictionary=False,
                write_statistics=False,
            )
            return cast(bytes, output.getvalue().to_pybytes())
        except (pa.ArrowException, ValueError, TypeError, OSError):
            raise PortError(ErrorCode.INVALID_INPUT, "raw records cannot be encoded") from None

    def decode(self, content: bytes, *, expected_count: int) -> tuple[RawRecord, ...]:
        if not isinstance(content, bytes) or type(expected_count) is not int or expected_count < 0:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid raw-record input")
        try:
            file = pq.ParquetFile(pa.BufferReader(content))
            if file.metadata.num_rows != expected_count or not file.schema_arrow.equals(
                SCHEMA, check_metadata=True
            ):
                raise ValueError("invalid raw-record schema or count")
            records = tuple(
                RawRecord(row["sequence"], row["payload"], row["content_type"])
                for row in file.read().to_pylist()
            )
            validate_records(records)
            return records
        except (pa.ArrowException, ValueError, TypeError, OSError, KeyError):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid raw-record Parquet") from None
