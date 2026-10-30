# Raw batch publication boundary

`BatchInput` groups an immutable record tuple with source, license reference, ingestion task,
instrument, granularity and collection time. Its metadata uses the public DataBatch v1
contract; collection timestamps normalize to UTC so different DST folds remain distinct.
`ArchivedBatch` combines committed metadata and exact original records.

`RawBatchService.publish` requires DATA_MANAGE and the requested workspace. It first checks
for a committed batch. An exact retry verifies the original stored object and returns that
metadata; it does not re-encode an old batch with an upgraded writer. Reusing the batch ID
with changed records or collection metadata conflicts.

New publication validates the codec round trip, writes a content-addressed, create-only
object, verifies its reference and readable bytes, then publishes immutable metadata through
`BatchRepositoryPort`. Concurrent exact publishers converge on one metadata record. Storage
and metadata are separate services: if metadata publication fails, an unreferenced immutable
object may remain. Retrying reuses that object; readers see only committed metadata. This is
a recoverable publication workflow, not a distributed transaction. Candidate objects must not
be deleted while publication may be in progress.

`read` requires READ and verifies repository scope, the declared object hash, Parquet envelope,
record count and original ordering. Missing objects behind committed metadata are unavailable;
corrupt hashes or envelopes return checksum errors without echoing provider bytes.

The application depends on repository, object-store and codec ports. SQL, S3 and PyArrow remain
in adapters. The mock metadata repository is immutable and concurrency-safe. Tests use actual
Parquet encoding with mock metadata/object storage to check byte preservation, authorization,
concurrent publication, retries across writer upgrades, metadata-failure recovery, checksum/scope
failures, object visibility failure and DST-fold identity.

PostgreSQL migration 0005 stores immutable workspace-scoped batch metadata and publisher
identity. Publication locks the workspace and rechecks current membership inside the write
transaction; a previously captured admin access object cannot bypass a later role change.
Exact metadata retries converge, while changed metadata conflicts. Database triggers reject
updates, deletion and truncation. Reads validate both the contract and requested scope.

The combined integration test uses real PyArrow Parquet, MinIO and PostgreSQL. Eight concurrent
publishers converge on one object and one metadata row; a reconstructed service reads the
original precision-sensitive JSON and CSV bytes. Changed records conflict, and privileged
object corruption produces a checksum failure. Its dedicated bucket is removed afterwards.
The PostgreSQL self-test runs this case when the local object-store service is available.

`DatasetReplayService` interprets verified archives through explicitly registered, versioned
`BarTransformationPort` implementations. A stable replay request chooses a fresh dataset ID;
its fingerprint includes the ordered batch set, transformation version, range, UTC timestamps
and optional parent. Changed requests cannot reuse that identity. Parent replay retains the
same source batches and increments the lineage version. Separate branches can share a version
number; the immutable dataset UUID identifies the exact snapshot, including backtest references.

Replay checks Bar contracts, target workspace/dataset, source batch/instrument/granularity,
duplicate identities and the exclusive range end. A content-addressed report records original
batch metadata/hashes, output counts, quality flags and exactly those checks. It does not claim
calendar/gap/outlier analysis, which remains the later quality-engine task. The report becomes
readable first, then an immutable market snapshot is written, then dataset metadata is committed.
Failure leaves reusable unpublished candidates. Committed retries verify the report and return
the original metadata even after an old transformation implementation has been removed.

`StandardBarJsonTransformation` supports only the explicitly identified internal
`qtrade.standard-bar-json` format and validates each source Bar before assigning the new dataset
ID. It is not an external-provider parser. Provider-specific semantics belong behind the same
versioned Port. Tests inject revised rules and prove old raw bytes, Bars, metadata and a historical
BacktestRun reference remain unchanged; they also exercise concurrency, recovery, scope/role
checks, unknown rules, invalid outputs and corrupt reports.

PostgreSQL migration 0006 persists dataset receipts and ordered source links. Source foreign
keys, parent scope/version checks, immutable triggers and a deferred completeness constraint
prevent partial provenance from committing. Source links can be inserted only in the dataset's
creation transaction; late appends fail. The repository rechecks live membership while holding
the workspace lock, validates stored contracts/scope/source order, and accepts only exact retries.

Actual PostgreSQL acceptance covers concurrency, immutable SQL guards, lineage, role changes,
missing-source rollback and rejection of direct incomplete SQL publication. Actual Timescale
acceptance combines PostgreSQL metadata, PyArrow archives and market snapshots: revised rules
create fresh versions while preserving old Bars/raw bytes/backtest references, and failed metadata
publication recovers by reusing the existing immutable market snapshot. That replay case uses
mock object storage; the separate raw-publication test uses actual MinIO/PyArrow/PostgreSQL.
Raw batches and datasets are persisted through reusable services; collector/API/UI orchestration
belongs to the following ingestion and data-workbench tasks.
