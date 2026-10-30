# Realtime market-data supervision

`RealtimeMarketDataService` owns connection supervision while `RealtimeProviderPort` hides vendor
WebSocket/client details. Providers translate heartbeats and ticks into `ProviderPulse` and
`MarketTick`; the application measures event lag, rejects repeated or older sequence/timestamp
observations from the health counters, and advances the connection state. No SDK type or provider
error text crosses into the domain.

Health is keyed by workspace, source, and instrument. State exposes healthy/degraded/disconnected/
reconnecting, last heartbeat/event/sequence, latest and maximum lag, out-of-order count, reconnect
count, next retry time, and a safe error code. Reconnection uses a persisted backoff deadline;
stale or future heartbeats fail closed. `RealtimePolicy` contains explicit timeout and lag limits.
PostgreSQL stores snapshots for later authorized API/workbench reads; the mock adapter scripts
heartbeats, delays, event ordering, outages, and recovery.

The API exposes workspace-scoped `GET /api/v1/workspaces/{workspace_id}/market-streams` and
`GET /api/v1/workspaces/{workspace_id}/market-streams/{source}/{instrument_id}` endpoints. Both
require a readable workspace session and return only safe state and error codes.

The health monitor does not persist tick history or broadcast to browser clients. Those belong to
the later market-watch/WebSocket consumers, which can subscribe to this status seam without
depending on provider adapters.
