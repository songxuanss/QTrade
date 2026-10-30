"""Publish verified raw objects before immutable metadata, and recover safe retries."""

from hashlib import sha256
from uuid import UUID

from qtrade_contracts import DataBatch
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.batches import ArchivedBatch, BatchInput, BatchRepositoryPort
from qtrade_domain.ports import ErrorCode, ObjectRef, ObjectStorePort, PortError
from qtrade_domain.raw_records import RawRecordCodecPort, validate_records


class RawBatchService:
    def __init__(
        self, repository: BatchRepositoryPort, objects: ObjectStorePort, codec: RawRecordCodecPort
    ):
        self._repository, self._objects, self._codec = repository, objects, codec

    def _metadata(self, workspace_id: UUID, batch_id: UUID) -> DataBatch:
        result = self._repository.get(workspace_id, batch_id)
        if (
            not isinstance(result, DataBatch)
            or result.workspace_id != workspace_id
            or result.batch_id != batch_id
        ):
            raise PortError(ErrorCode.UNAVAILABLE, "batch repository returned invalid scope")
        return result

    def _read(self, metadata: DataBatch) -> ArchivedBatch:
        try:
            content = self._objects.get(
                metadata.workspace_id,
                metadata.object_key,
                expected_sha256=metadata.content_sha256,
            )
        except PortError as error:
            if error.code == ErrorCode.NOT_FOUND:
                raise PortError(ErrorCode.UNAVAILABLE, "raw batch object unavailable") from None
            raise
        if not isinstance(content, bytes) or sha256(content).hexdigest() != metadata.content_sha256:
            raise PortError(ErrorCode.CHECKSUM_MISMATCH, "raw batch checksum mismatch")
        try:
            records = self._codec.decode(content, expected_count=metadata.record_count)
            validate_records(records)
            return ArchivedBatch(metadata, records)
        except PortError as error:
            if error.code == ErrorCode.INVALID_INPUT:
                raise PortError(ErrorCode.CHECKSUM_MISMATCH, "invalid stored raw batch") from None
            raise

    def _retry(self, draft: BatchInput, existing: DataBatch) -> DataBatch:
        expected = draft.metadata(
            object_key=existing.object_key, content_sha256=existing.content_sha256
        )
        if expected != existing or self._read(existing).records != draft.records:
            raise PortError(ErrorCode.CONFLICT, "batch identity reused with different content")
        return existing

    def publish(self, access: WorkspaceAccess, draft: BatchInput) -> DataBatch:
        access.require(Permission.DATA_MANAGE, object_workspace_id=draft.workspace_id)
        try:
            existing = self._metadata(draft.workspace_id, draft.batch_id)
        except PortError as error:
            if error.code != ErrorCode.NOT_FOUND:
                raise
        else:
            return self._retry(draft, existing)
        content = self._codec.encode(draft.records)
        if not isinstance(content, bytes):
            raise PortError(ErrorCode.UNAVAILABLE, "raw codec returned invalid content")
        try:
            decoded = self._codec.decode(content, expected_count=len(draft.records))
            validate_records(decoded)
        except PortError:
            raise PortError(ErrorCode.UNAVAILABLE, "raw codec returned invalid batch") from None
        if decoded != draft.records:
            raise PortError(ErrorCode.UNAVAILABLE, "raw codec changed collection records")
        digest = sha256(content).hexdigest()
        key = f"batches/{draft.batch_id}/{digest}.parquet"
        ref = self._objects.put(
            draft.workspace_id,
            key,
            content,
            content_type="application/vnd.apache.parquet",
            expected_sha256=digest,
        )
        if (
            not isinstance(ref, ObjectRef)
            or ref.workspace_id != draft.workspace_id
            or ref.key != key
            or ref.content_sha256 != digest
            or ref.size != len(content)
            or ref.content_type != "application/vnd.apache.parquet"
        ):
            raise PortError(ErrorCode.UNAVAILABLE, "object store returned invalid batch reference")
        # Publish only after the object is readable with its declared checksum.
        try:
            verified = self._objects.get(draft.workspace_id, key, expected_sha256=digest)
        except PortError as visibility_error:
            if visibility_error.code == ErrorCode.NOT_FOUND:
                raise PortError(
                    ErrorCode.UNAVAILABLE, "raw batch publication unavailable"
                ) from None
            raise
        if verified != content:
            raise PortError(ErrorCode.CHECKSUM_MISMATCH, "raw batch publication checksum mismatch")
        metadata = draft.metadata(object_key=key, content_sha256=digest)
        try:
            self._repository.put(access.user_id, metadata)
        except PortError as publication_error:
            if publication_error.code == ErrorCode.CONFLICT:
                return self._retry(draft, self._metadata(draft.workspace_id, draft.batch_id))
            raise
        return metadata

    def read(self, access: WorkspaceAccess, batch_id: UUID) -> ArchivedBatch:
        access.require(Permission.READ)
        return self._read(self._metadata(access.workspace.workspace_id, batch_id))

    def list(self, access: WorkspaceAccess, limit: int = 100) -> tuple[DataBatch, ...]:
        access.require(Permission.READ)
        result = self._repository.list(access.workspace.workspace_id, limit)
        if any(
            not isinstance(item, DataBatch) or item.workspace_id != access.workspace.workspace_id
            for item in result
        ):
            raise PortError(ErrorCode.UNAVAILABLE, "batch repository returned invalid scope")
        return result
