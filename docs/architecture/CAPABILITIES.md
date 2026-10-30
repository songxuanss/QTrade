# Adapter capability selection

`CapabilityDescriptor` is an immutable, versioned domain declaration. It contains
the adapter ID, kind, supported markets, intervals, order types, trading sessions,
live capability, and health classification. Credentials and SDK objects are not
part of this public declaration.

`CapabilityRegistry` binds declarations to actual port implementations. It rejects
duplicate IDs and kind/port mismatches. `require(id, request)` is the selection
interface: unsupported features fail with `unsupported`, while unknown, degraded,
or unavailable health fails with `unavailable`. Selection returns the bound
adapter only after these checks. Health replacement preserves the declaration
and adapter; it does not mutate previously returned snapshots.

Business consumers must select through this interface before invoking an adapter.
Capability checks complement workspace permissions and RiskEngine decisions;
they never grant authorization or approve an order. Trading consumers remain
responsible for the business-state and risk requirements in their feature tasks.

Workspace-scoped discovery and availability endpoints use the shared session/RBAC
guard. Settings renders only declarations received through the authenticated BFF,
shows unsupported fields and live trading as unavailable, and distinguishes an
empty registry from an API failure. Port implementations are never serialized.

The mock declarations cover all six existing ports. They advertise only simulation
data and order recording, with live disabled. Job-only engine/research doubles
remain health-unknown; they are not represented as executable simulators. External
adapters are registered only when actually configured. The production composition
currently has no configured engine/data/broker, so its discovery list is empty.

Tests exercise six actual port bindings, selection-to-market-data round trips,
kind mismatches, duplicate registration, unsupported features, health recovery,
workspace scope, and desktop/mobile settings accessibility and visual regression.
