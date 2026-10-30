# Market data lineage

`DataLineageService.for_bar` is the application query for one normalized Bar. It verifies the
requested dataset/report, queries the exact instrument/granularity/timestamp from the market
store, confirms the Bar's batch is a source batch of that dataset, reads and verifies the
immutable raw archive, and resolves the persisted collection task referenced by the batch.
The returned `BarLineage` contains the Bar, immutable dataset metadata (including transform
version and report hash), DataBatch source/license/archive metadata, completed collection
request/checkpoints, and matching Data Quality Issues with their evidence.

Every hop is workspace scoped and starts with READ permission. Mismatched source/task identity,
batch provenance, or missing upstream records fail closed as unavailable; callers never receive
an apparently complete lineage assembled from inconsistent metadata. The service composes
existing dataset, raw-batch, market-query, task, and quality boundaries instead of duplicating
their storage logic. It deliberately returns batch hashes/counts rather than copying raw provider
payloads into a lineage response.
