"""Content-stable artifact identity, independent of persistence and provider SDKs."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID, uuid5

from qtrade_contracts import ArtifactManifest, MarketDataset, StrategyVersion
from qtrade_contracts.models import canonical_object
from qtrade_domain.ports import ErrorCode, PortError

MANIFEST_NAMESPACE = UUID("69631b10-301e-43c4-bc09-f0588caa50cb")


@dataclass(frozen=True)
class ManifestInputs:
    run_id: UUID
    kind: str
    created_by: UUID
    created_at: datetime
    strategy: StrategyVersion
    dataset: MarketDataset
    parameters_json: str
    engine_name: str
    engine_version: str
    adapter_version: str
    environment_image_digest: str
    random_seed: int
    nondeterministic_sources: tuple[str, ...] = ()
    execution_config_json: str | None = None


def manifest_bytes(manifest: ArtifactManifest) -> bytes:
    """Canonical UTF-8 representation used for both hashing and storage."""
    return json.dumps(
        manifest.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def build_manifest(inputs: ManifestInputs) -> ArtifactManifest:
    if inputs.strategy.workspace_id != inputs.dataset.workspace_id:
        raise PortError(ErrorCode.INVALID_INPUT, "manifest inputs belong to different workspaces")
    if inputs.kind == "backtest" and inputs.execution_config_json is None:
        raise PortError(ErrorCode.INVALID_INPUT, "backtest manifest requires frozen run settings")
    if inputs.created_at.utcoffset() is None:
        raise PortError(ErrorCode.INVALID_INPUT, "manifest creation time requires timezone")
    try:
        parameters = canonical_object(inputs.parameters_json)
        execution_config = (
            canonical_object(inputs.execution_config_json)
            if inputs.execution_config_json is not None
            else None
        )
        provisional = ArtifactManifest.model_validate(
            {
                "schema_version": 1,
                "workspace_id": inputs.strategy.workspace_id,
                "manifest_id": UUID(int=0),
                "run_id": inputs.run_id,
                "kind": inputs.kind,
                "created_by": inputs.created_by,
                "created_at": inputs.created_at.astimezone(UTC),
                "strategy_version_id": inputs.strategy.strategy_version_id,
                "code_sha256": inputs.strategy.code_sha256,
                "dataset_id": inputs.dataset.dataset_id,
                "dataset_sha256": inputs.dataset.content_sha256,
                "parameters_json": parameters,
                "parameters_sha256": sha256(parameters.encode()).hexdigest(),
                "engine_name": inputs.engine_name,
                "engine_version": inputs.engine_version,
                "adapter_version": inputs.adapter_version,
                "environment_image_digest": inputs.environment_image_digest,
                "random_seed": inputs.random_seed,
                "nondeterministic_sources": inputs.nondeterministic_sources,
                "execution_config_json": execution_config,
                "execution_config_sha256": (
                    sha256(execution_config.encode()).hexdigest()
                    if execution_config is not None
                    else None
                ),
            }
        )
        identity = uuid5(MANIFEST_NAMESPACE, sha256(manifest_bytes(provisional)).hexdigest())
        return ArtifactManifest.model_validate(
            {**provisional.model_dump(), "manifest_id": identity}
        )
    except ValueError:
        raise PortError(ErrorCode.INVALID_INPUT, "invalid manifest inputs") from None
