# Original record Parquet codec

`ParquetRawRecordCodec` implements the domain `RawRecordCodecPort`. PyArrow stays in this
Adapter and is prohibited in API/domain/application imports by the architecture check.
The dependency is resolved and locked as PyArrow 25.0.1.

Schema version 1 has nonnullable `sequence:int64`, `payload:binary`, and `content_type:string`
columns plus `qtrade.raw_records.schema_version=1` metadata. Sequence is the zero-based,
contiguous collection position. Original provider bytes are preserved exactly; JSON numeric
text, CSV line endings, malformed records and arbitrary binary payloads are not interpreted.
Semantic validation and normalization belong to the versioned transformation/quality pipeline.

The writer uses Parquet 2.6 with Zstandard compression and fixed writer settings. The reader
checks the schema/version and declared row count before materializing records, then checks
sequence and raw-value types. No provider values are echoed in codec error messages.
An empty source batch encodes as a valid zero-row file.

The mock codec has an explicit test-only format and shares the round-trip/order contract;
actual format acceptance also opens the produced file independently with PyArrow. Tests cover
exact binary/UTF-8/CSV/high-precision numeric bytes, deterministic same-release output,
unknown schema/version, truncated files, invalid sequences and count mismatches.

Immutable object publication, DataBatch metadata, read-time hashes and replay to a fresh
MarketDataset are the remaining task 4.5 orchestration/persistence work. Existing batches
will reference the original stored bytes and hash rather than relying on re-encoding with
future codec versions.
