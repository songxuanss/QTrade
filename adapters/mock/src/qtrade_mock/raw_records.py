"""Test-only raw codec; deliberately does not claim to produce Parquet."""

import base64
import json

from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.raw_records import RawRecord, validate_records

PREFIX = b"QTRADE-MOCK-RAW1\n"


class MockRawRecordCodec:
    def encode(self, records: tuple[RawRecord, ...]) -> bytes:
        validate_records(records)
        return (
            PREFIX
            + json.dumps(
                [
                    [
                        record.sequence,
                        base64.b64encode(record.payload).decode(),
                        record.content_type,
                    ]
                    for record in records
                ],
                separators=(",", ":"),
            ).encode()
        )

    def decode(self, content: bytes, *, expected_count: int) -> tuple[RawRecord, ...]:
        try:
            if (
                not isinstance(content, bytes)
                or not content.startswith(PREFIX)
                or type(expected_count) is not int
                or expected_count < 0
            ):
                raise ValueError("invalid mock raw envelope")
            rows = json.loads(content[len(PREFIX) :])
            if not isinstance(rows, list) or len(rows) != expected_count:
                raise ValueError("invalid mock raw count")
            records = []
            for row in rows:
                if not isinstance(row, list) or len(row) != 3:
                    raise ValueError("invalid mock raw row")
                records.append(RawRecord(row[0], base64.b64decode(row[1], validate=True), row[2]))
            result = tuple(records)
            validate_records(result)
            return result
        except (ValueError, TypeError, UnicodeError):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid mock raw envelope") from None
