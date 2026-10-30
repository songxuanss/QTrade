"""Exact provider record bytes; no normalization, numeric parsing or timestamp repair."""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from qtrade_domain.ports import ErrorCode, PortError


@dataclass(frozen=True)
class RawRecord:
    sequence: int
    payload: bytes
    content_type: str = "application/octet-stream"

    def __post_init__(self) -> None:
        if (
            type(self.sequence) is not int
            or self.sequence < 0
            or not isinstance(self.payload, bytes)
            or not isinstance(self.content_type, str)
            or not 1 <= len(self.content_type) <= 256
            or any(not 32 <= ord(character) < 127 for character in self.content_type)
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid raw record")


def validate_records(records: tuple[RawRecord, ...]) -> None:
    if not isinstance(records, tuple) or any(
        not isinstance(record, RawRecord) or record.sequence != index
        for index, record in enumerate(records)
    ):
        raise PortError(
            ErrorCode.INVALID_INPUT, "raw record sequence must preserve collection order"
        )


@runtime_checkable
class RawRecordCodecPort(Protocol):
    def encode(self, records: tuple[RawRecord, ...]) -> bytes: ...
    def decode(self, content: bytes, *, expected_count: int) -> tuple[RawRecord, ...]: ...
