import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.raw_records import RawRecord, RawRecordCodecPort, validate_records
from qtrade_mock.raw_records import MockRawRecordCodec
from qtrade_parquet.raw_records import SCHEMA, ParquetRawRecordCodec


@pytest.mark.parametrize("codec", [ParquetRawRecordCodec(), MockRawRecordCodec()])
def test_raw_codec_contract_preserves_exact_payload_and_collection_order(codec):
    records = (
        RawRecord(0, b'{"price":12345678901234567890.000000000000000001}', "application/json"),
        RawRecord(1, b"time,price\r\n2026-10-01,10.000\r\n", "text/csv"),
        RawRecord(2, "unparsed provider message: 中文\n".encode(), "text/plain; charset=utf-8"),
        RawRecord(3, bytes(range(256))),
        RawRecord(4, b'{"invalid-semantic-row":', "application/json"),
    )
    assert isinstance(codec, RawRecordCodecPort)
    content = codec.encode(records)
    assert codec.encode(records) == content
    assert codec.decode(content, expected_count=5) == records
    assert codec.decode(codec.encode(()), expected_count=0) == ()
    with pytest.raises(PortError):
        codec.decode(content, expected_count=4)


def test_actual_file_is_versioned_parquet_with_binary_provider_payloads():
    content = ParquetRawRecordCodec().encode((RawRecord(0, b"original"),))
    assert content[:4] == content[-4:] == b"PAR1"
    file = pq.ParquetFile(pa.BufferReader(content))
    assert file.schema_arrow.equals(SCHEMA, check_metadata=True)
    assert file.metadata.num_rows == 1
    assert file.read().to_pylist() == [
        {"sequence": 0, "payload": b"original", "content_type": "application/octet-stream"}
    ]


@pytest.mark.parametrize("kind", ["truncated", "version", "schema", "sequence"])
def test_corrupt_or_unknown_parquet_is_rejected_without_echoing_data(kind):
    codec = ParquetRawRecordCodec()
    if kind == "truncated":
        content = codec.encode((RawRecord(0, b"private-sentinel"),))[:-7]
    else:
        schema = SCHEMA
        if kind == "version":
            schema = schema.with_metadata({b"qtrade.raw_records.schema_version": b"2"})
        if kind == "schema":
            schema = pa.schema(
                [
                    pa.field("sequence", pa.int64(), nullable=False),
                    pa.field("payload", pa.string(), nullable=False),
                    pa.field("content_type", pa.string(), nullable=False),
                ],
                metadata=SCHEMA.metadata,
            )
        table = pa.Table.from_pylist(
            [
                {
                    "sequence": 2 if kind == "sequence" else 0,
                    "payload": "private-sentinel" if kind == "schema" else b"private-sentinel",
                    "content_type": "application/json",
                }
            ],
            schema=schema,
        )
        output = pa.BufferOutputStream()
        pq.write_table(table, output)
        content = output.getvalue().to_pybytes()
    with pytest.raises(PortError) as failure:
        codec.decode(content, expected_count=1)
    assert failure.value.code == ErrorCode.INVALID_INPUT
    assert "private-sentinel" not in str(failure.value)


@pytest.mark.parametrize(
    "fields",
    [
        {"sequence": True},
        {"sequence": -1},
        {"payload": bytearray(b"x")},
        {"content_type": "text/plain\r\nprivate"},
        {"content_type": ""},
    ],
)
def test_invalid_raw_record_types_are_rejected(fields):
    with pytest.raises(PortError):
        RawRecord(**{"sequence": 0, "payload": b"x", **fields})


def test_record_sequence_is_an_explicit_original_position():
    with pytest.raises(PortError):
        validate_records((RawRecord(1, b"second"), RawRecord(0, b"first")))
    with pytest.raises(PortError):
        validate_records([RawRecord(0, b"first")])
