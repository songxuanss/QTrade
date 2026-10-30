from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from qtrade_application.batches import RawBatchService
from qtrade_contracts import DataBatch
from qtrade_domain.authorization import WorkspaceAccess
from qtrade_domain.batches import BatchInput
from qtrade_domain.identity import Role, Workspace
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.raw_records import RawRecord
from qtrade_mock.batches import MockBatchRepository
from qtrade_mock.ports import MockObjectStore
from qtrade_parquet.raw_records import ParquetRawRecordCodec


@pytest.fixture
def data():
    workspace = Workspace(
        uuid4(), "Raw batches", "organization", None, False, False, datetime.now(UTC)
    )
    access = WorkspaceAccess(workspace, uuid4(), Role.ADMIN)
    draft = BatchInput(
        workspace.workspace_id,
        uuid4(),
        "test-provider",
        "test-license",
        uuid4(),
        "TEST:ABC",
        "1m",
        datetime.now(UTC),
        (RawRecord(0, b'{"price":10.000000000000000001}', "application/json"),),
    )
    repository, objects = MockBatchRepository(), MockObjectStore()
    service = RawBatchService(repository, objects, ParquetRawRecordCodec())
    return access, draft, repository, objects, service


def test_publication_preserves_bytes_and_retry_uses_original_stored_codec_bytes(data):
    access, draft, repository, objects, service = data
    published = service.publish(access, draft)
    assert published.record_count == 1 and published.source == draft.source
    assert service.read(access, draft.batch_id).records == draft.records
    assert objects.get(
        draft.workspace_id, published.object_key, expected_sha256=published.content_sha256
    ).startswith(b"PAR1")
    assert service.publish(access, draft) == published

    class UpgradedWriter(ParquetRawRecordCodec):
        def encode(self, records):
            raise AssertionError("existing batches must not be re-encoded")

    assert (
        RawBatchService(repository, objects, UpgradedWriter()).publish(access, draft) == published
    )
    for changed in (
        replace(draft, source="other-source"),
        replace(draft, records=(RawRecord(0, b"different"),)),
    ):
        with pytest.raises(PortError) as conflict:
            service.publish(access, changed)
        assert conflict.value.code == ErrorCode.CONFLICT
    assert service.read(access, draft.batch_id).metadata == published


def test_role_and_workspace_rejection_happens_before_writing_objects(data):
    access, draft, _, objects, service = data
    with pytest.raises(PortError) as denied:
        service.publish(replace(access, role=Role.VIEWER), draft)
    assert denied.value.code == ErrorCode.REJECTED
    with pytest.raises(PortError) as cross:
        service.publish(access, replace(draft, workspace_id=uuid4()))
    assert cross.value.code == ErrorCode.NOT_FOUND
    assert objects._objects == {}
    published = service.publish(access, draft)
    assert service.read(replace(access, role=Role.VIEWER), published.batch_id).metadata == published
    with pytest.raises(PortError) as missing:
        service.read(
            replace(access, workspace=replace(access.workspace, workspace_id=uuid4())),
            published.batch_id,
        )
    assert missing.value.code == ErrorCode.NOT_FOUND


def test_failed_metadata_publication_can_retry_without_overwriting_raw_object(data):
    access, draft, _, objects, _ = data

    class FailOnce(MockBatchRepository):
        failed = False

        def put(self, actor_id, batch):
            if not self.failed:
                self.failed = True
                raise PortError(ErrorCode.UNAVAILABLE, "injected metadata failure")
            super().put(actor_id, batch)

    repository = FailOnce()
    service = RawBatchService(repository, objects, ParquetRawRecordCodec())
    with pytest.raises(PortError):
        service.publish(access, draft)
    with pytest.raises(PortError) as unpublished:
        repository.get(draft.workspace_id, draft.batch_id)
    assert unpublished.value.code == ErrorCode.NOT_FOUND
    before = dict(objects._objects)
    published = service.publish(access, draft)
    assert dict(objects._objects) == before
    assert service.read(access, draft.batch_id).metadata == published


def test_concurrent_publication_returns_one_immutable_metadata_record(data):
    access, draft, _, _, service = data
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = tuple(pool.map(lambda _: service.publish(access, draft), range(8)))
    assert all(result == results[0] for result in results)
    assert service.read(access, draft.batch_id).records == draft.records


def test_checksum_and_repository_scope_validation_fail_closed(data):
    access, draft, repository, objects, service = data
    published = service.publish(access, draft)

    class CorruptObjectStore(MockObjectStore):
        def get(self, workspace_id, key, *, expected_sha256):
            return b"private-corrupt-payload"

    with pytest.raises(PortError) as corruption:
        RawBatchService(repository, CorruptObjectStore(), ParquetRawRecordCodec()).read(
            access, draft.batch_id
        )
    assert corruption.value.code == ErrorCode.CHECKSUM_MISMATCH
    assert "private-corrupt" not in str(corruption.value)

    class WrongScope(MockBatchRepository):
        def get(self, workspace_id, batch_id) -> DataBatch:
            return published.model_copy(update={"workspace_id": uuid4()})

    with pytest.raises(PortError) as scope:
        RawBatchService(WrongScope(), objects, ParquetRawRecordCodec()).read(access, draft.batch_id)
    assert scope.value.code == ErrorCode.UNAVAILABLE


def test_collection_timestamp_normalizes_offsets_and_distinguishes_dst_folds(data):
    from zoneinfo import ZoneInfo

    access, draft, _, _, service = data
    ambiguous = datetime(2026, 11, 1, 1, 30, tzinfo=ZoneInfo("America/New_York"), fold=0)
    draft = replace(draft, collected_at=ambiguous)
    published = service.publish(access, draft)
    assert published.collected_at == ambiguous.astimezone(UTC)
    assert (
        service.publish(access, replace(draft, collected_at=ambiguous.astimezone(UTC))) == published
    )
    with pytest.raises(PortError) as conflict:
        service.publish(access, replace(draft, collected_at=ambiguous.replace(fold=1)))
    assert conflict.value.code == ErrorCode.CONFLICT


def test_object_visibility_failure_never_publishes_metadata(data):
    access, draft, repository, _, _ = data

    class UnreadableObjectStore(MockObjectStore):
        def get(self, workspace_id, key, *, expected_sha256):
            raise PortError(ErrorCode.NOT_FOUND, "injected visibility failure")

    service = RawBatchService(repository, UnreadableObjectStore(), ParquetRawRecordCodec())
    with pytest.raises(PortError) as unavailable:
        service.publish(access, draft)
    assert unavailable.value.code == ErrorCode.UNAVAILABLE
    with pytest.raises(PortError) as unpublished:
        repository.get(draft.workspace_id, draft.batch_id)
    assert unpublished.value.code == ErrorCode.NOT_FOUND
