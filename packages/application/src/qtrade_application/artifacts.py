"""Immutable manifest use cases; ObjectStorePort owns conditional create semantics."""

from hashlib import sha256
from uuid import UUID

from qtrade_contracts import ArtifactManifest
from qtrade_domain.artifacts import ManifestInputs, build_manifest, manifest_bytes
from qtrade_domain.ports import ErrorCode, ObjectRef, ObjectStorePort, PortError


class ManifestService:
    def __init__(self, store: ObjectStorePort):
        self._store = store

    @staticmethod
    def _key(run_id: UUID) -> str:
        return f"runs/{run_id}/manifest.v1.json"

    def create(self, inputs: ManifestInputs) -> tuple[ArtifactManifest, ObjectRef]:
        manifest = build_manifest(inputs)
        content = manifest_bytes(manifest)
        ref = self._store.put(
            manifest.workspace_id,
            self._key(manifest.run_id),
            content,
            content_type="application/json",
            expected_sha256=sha256(content).hexdigest(),
        )
        return manifest, ref

    def get(self, workspace_id: UUID, run_id: UUID) -> ArtifactManifest:
        key = self._key(run_id)
        ref = self._store.stat(workspace_id, key)
        content = self._store.get(workspace_id, key, expected_sha256=ref.content_sha256)
        try:
            manifest = ArtifactManifest.model_validate_json(content)
        except ValueError:
            raise PortError(ErrorCode.CHECKSUM_MISMATCH, "invalid stored manifest") from None
        if manifest.workspace_id != workspace_id or manifest.run_id != run_id:
            raise PortError(ErrorCode.CHECKSUM_MISMATCH, "stored manifest identity mismatch")
        return manifest
