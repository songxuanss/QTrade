"""Atomic immutable Parquet blobs and checksummed manifests behind the archive seam."""

import json
import os
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import pyarrow as pa
import pyarrow.parquet as pq

from qtrade_data_contracts.collection import (
    CollectedBar,
    CollectionPublication,
    CollectionRawCapture,
    FactorPoint,
    RawPayloadKind,
    digest,
)
from qtrade_data_contracts.collection_quality import CollectionQualityResult
from qtrade_data_contracts.normalization_input import (
    CollectionNormalizationInput,
    NormalizationPayload,
)
from qtrade_data_contracts.processing_failure import CollectionProcessingFailure
from qtrade_data_core.collection import CollectionPart, CollectionResult
from qtrade_data_core.ports import DataFailure


class FileCollectionArchive:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _directory(self, scope: object, publication_id: object) -> Path:
        directory = self.root / str(scope) / str(publication_id)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        return directory

    @staticmethod
    def _write(path: Path, content: bytes) -> None:
        if path.exists():
            if path.read_bytes() != content:
                raise DataFailure("conflict")
            return
        temporary = path.with_name(path.name + "." + str(uuid4()) + ".tmp")
        try:
            with temporary.open("xb") as stream:
                temporary.chmod(0o600)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                if path.read_bytes() != content:
                    raise DataFailure("conflict") from None
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _parquet(rows: list[dict[str, object]]) -> bytes:
        table = pa.table(
            {
                "payload": [
                    json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                    for row in rows
                ]
            }
        )
        sink = BytesIO()
        pq.write_table(table, sink, compression="zstd")
        return sink.getvalue()

    def recover(self, part: CollectionPart) -> CollectionPublication | None:
        path = self._directory(part.request.scope_id, part.part_id) / "manifest.json"
        if not path.exists():
            return None
        try:
            publication = CollectionPublication.model_validate_json(path.read_bytes())
            if (
                publication.part_id != part.part_id
                or publication.scope_id != part.request.scope_id
                or publication.mapping_version != part.mapping_version
            ):
                raise ValueError
            self.records(publication)
            return publication
        except (ValueError, OSError):
            raise DataFailure("unavailable") from None

    def capture_raw(
        self, part: CollectionPart, kind: RawPayloadKind, payload: object
    ) -> CollectionRawCapture:
        """Retain decoded source input before normalization, including empty/bad shapes.

        Receipts are separated from v1 manifests. No receipt creates a publication
        or counts as successful coverage. Retry leases have distinct receipt IDs.
        """
        body = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
        ).encode()
        content_hash = sha256(body).hexdigest()
        capture_id = uuid5(part.part_id, f"{kind}:{part.lease_token}:{part.attempt}:{content_hash}")
        directory = self._directory(part.request.scope_id, part.part_id) / "source-captures"
        directory.mkdir(exist_ok=True, mode=0o700)
        content_path = directory / f"payload-{content_hash}.json"
        receipt_path = directory / f"receipt-{capture_id}.json"
        self._write(content_path, body)
        rows = (
            payload
            if isinstance(payload, list)
            else payload.get("data")
            if isinstance(payload, dict)
            else None
        )
        receipt = CollectionRawCapture(
            schema_version=1,
            capture_id=capture_id,
            scope_id=part.request.scope_id,
            part_id=part.part_id,
            job_id=part.job_id,
            actor_id=part.request.actor_id,
            source=part.request.source,
            mapping_version=part.mapping_version,
            kind=kind,
            attempt=part.attempt,
            lease_token=part.lease_token,
            content_sha256=content_hash,
            captured_at=datetime.now(UTC),
            response_rows=len(rows) if isinstance(rows, list) else None,
        )
        if receipt_path.exists():
            existing = CollectionRawCapture.model_validate_json(receipt_path.read_bytes())
            if existing.model_dump(exclude={"captured_at"}) != receipt.model_dump(
                exclude={"captured_at"}
            ):
                raise DataFailure("conflict")
            return existing
        self._write(receipt_path, receipt.model_dump_json().encode())
        return receipt

    def capture_prepared(
        self,
        part: CollectionPart,
        payload: NormalizationPayload,
        captures: tuple[CollectionRawCapture, ...],
    ) -> CollectionNormalizationInput:
        from .normalization_inputs import FileNormalizationInputs

        return FileNormalizationInputs(self.root, self._write).capture(part, payload, captures)

    def normalization_inputs(
        self, part: CollectionPart
    ) -> tuple[CollectionNormalizationInput, ...]:
        from .normalization_inputs import FileNormalizationInputs

        return FileNormalizationInputs(self.root, self._write).inputs(part)

    def normalization_input(
        self, part: CollectionPart, input_id: UUID
    ) -> CollectionNormalizationInput:
        from .normalization_inputs import FileNormalizationInputs

        return FileNormalizationInputs(self.root, self._write).read(part, input_id)

    def normalization_evidence(
        self,
        part: CollectionPart,
        input_id: UUID,
        maximum: int,
        max_receipts: int,
    ) -> tuple[CollectionNormalizationInput, tuple[CollectionRawCapture, ...], int]:
        from .normalization_inputs import FileNormalizationInputs

        return FileNormalizationInputs(self.root, self._write).evidence(
            part, input_id, maximum, max_receipts
        )

    def source_row_proofs(
        self, part: CollectionPart, before: datetime
    ) -> tuple[tuple[CollectionRawCapture, frozenset[str]], ...]:
        """Verify bounded acquisition evidence without exposing supplier payloads.

        Only receipts preceding the referenced decision participate; a later
        retry cannot retroactively prove an older publication's acquisition.
        """
        directory = self.root / str(part.request.scope_id) / str(part.part_id) / "source-captures"
        paths = list(directory.glob("receipt-*.json"))
        if len(paths) > 100:
            raise DataFailure("unavailable")
        result = []
        total = 0
        try:
            for path in paths:
                receipt = CollectionRawCapture.model_validate_json(path.read_bytes())
                if receipt.captured_at > before:
                    continue
                if (
                    receipt.scope_id,
                    receipt.part_id,
                    receipt.job_id,
                    receipt.actor_id,
                    receipt.source,
                    receipt.mapping_version,
                ) != (
                    part.request.scope_id,
                    part.part_id,
                    part.job_id,
                    part.request.actor_id,
                    part.request.source,
                    part.mapping_version,
                ):
                    raise ValueError("capture identity mismatch")
                identity = uuid5(
                    part.part_id,
                    f"{receipt.kind}:{receipt.lease_token}:{receipt.attempt}:{receipt.content_sha256}",
                )
                if identity != receipt.capture_id or path.name != f"receipt-{identity}.json":
                    raise ValueError("capture identity mismatch")
                body_path = directory / f"payload-{receipt.content_sha256}.json"
                total += body_path.stat().st_size
                if total > 64 * 1024 * 1024:
                    raise DataFailure("unavailable")
                body = body_path.read_bytes()
                if sha256(body).hexdigest() != receipt.content_sha256:
                    raise ValueError("capture checksum mismatch")
                payload = json.loads(body)
                rows = (
                    payload
                    if isinstance(payload, list)
                    else payload.get("data")
                    if isinstance(payload, dict)
                    else None
                )
                if (len(rows) if isinstance(rows, list) else None) != receipt.response_rows:
                    raise ValueError("capture quantity mismatch")
                source_rows = rows.get("list") if isinstance(rows, dict) else rows
                row_hashes = (
                    frozenset(digest(row) for row in source_rows if isinstance(row, dict))
                    if isinstance(source_rows, list)
                    else frozenset()
                )
                result.append((receipt, row_hashes))
            return tuple(
                sorted(result, key=lambda value: (value[0].captured_at, str(value[0].capture_id)))
            )
        except (OSError, ValueError, TypeError):
            raise DataFailure("unverified") from None

    def source_captures(
        self, part: CollectionPart, before: datetime
    ) -> tuple[CollectionRawCapture, ...]:
        return tuple(receipt for receipt, _ in self.source_row_proofs(part, before))

    def raw_records(self, publication: CollectionPublication) -> tuple[dict[str, object], ...]:
        """Reopen exact immutable raw rows for independently reproducible checks."""
        path = (
            self.root
            / str(publication.scope_id)
            / str(publication.publication_id)
            / f"raw-{publication.raw_sha256}.parquet"
        )
        try:
            blob = path.read_bytes()
            if sha256(blob).hexdigest() != publication.raw_sha256:
                raise ValueError("raw checksum mismatch")
            values = tuple(
                json.loads(value) for value in pq.read_table(BytesIO(blob))["payload"].to_pylist()
            )
            if len(values) != publication.raw_record_count or any(
                not isinstance(value, dict) for value in values
            ):
                raise ValueError("raw rows mismatch")
            return values
        except (OSError, ValueError, TypeError, KeyError, pa.ArrowException):
            raise DataFailure("unverified") from None

    def record_processing_failure(
        self, part: CollectionPart, failure: CollectionProcessingFailure
    ) -> None:
        if (
            failure.scope_id,
            failure.part_id,
            failure.job_id,
            failure.attempt,
            failure.mapping_version,
        ) != (part.request.scope_id, part.part_id, part.job_id, part.attempt, part.mapping_version):
            raise DataFailure("forbidden")
        directory = self._directory(part.request.scope_id, part.part_id)
        # References must belong to this exact persisted attempt, not another
        # workspace or an unrelated task. No raw body is exposed by the check.
        try:
            for capture_id in failure.raw_capture_ids:
                receipt = CollectionRawCapture.model_validate_json(
                    (directory / "source-captures" / f"receipt-{capture_id}.json").read_bytes()
                )
                if (
                    receipt.capture_id != capture_id
                    or receipt.actor_id != part.request.actor_id
                    or receipt.source != part.request.source
                    or not (
                        directory / "source-captures" / f"payload-{receipt.content_sha256}.json"
                    ).is_file()
                ):
                    raise DataFailure("unavailable")
                if (
                    receipt.scope_id,
                    receipt.part_id,
                    receipt.job_id,
                    receipt.attempt,
                    receipt.mapping_version,
                ) != (
                    failure.scope_id,
                    failure.part_id,
                    failure.job_id,
                    failure.attempt,
                    failure.mapping_version,
                ):
                    raise DataFailure("forbidden")
        except (OSError, ValueError):
            raise DataFailure("unavailable") from None
        path = directory / f"processing-failure-{failure.failure_id}.json"
        if path.exists():
            existing = CollectionProcessingFailure.model_validate_json(path.read_bytes())
            if existing.model_dump(exclude={"failed_at"}) != failure.model_dump(
                exclude={"failed_at"}
            ):
                raise DataFailure("conflict")
            return
        self._write(path, failure.model_dump_json().encode())

    def processing_failures(self, part: CollectionPart) -> tuple[CollectionProcessingFailure, ...]:
        directory = self.root / str(part.request.scope_id) / str(part.part_id)
        paths = list(directory.glob("processing-failure-*.json"))
        if len(paths) > 100:
            raise DataFailure("unavailable")
        try:
            failures = tuple(
                CollectionProcessingFailure.model_validate_json(p.read_bytes()) for p in paths
            )
            if any(
                f.scope_id != part.request.scope_id or f.part_id != part.part_id for f in failures
            ):
                raise ValueError
            return tuple(sorted(failures, key=lambda f: (f.failed_at, str(f.failure_id))))
        except (OSError, ValueError):
            raise DataFailure("unavailable") from None

    def record_quality(self, part: CollectionPart, result: CollectionQualityResult) -> None:
        """Retain the first check of a decision without rewriting frozen manifests."""
        if result.scope_id != part.request.scope_id or result.part_id != part.part_id:
            raise DataFailure("forbidden")
        path = (
            self._directory(part.request.scope_id, part.part_id)
            / f"quality-{result.quality_id}.json"
        )
        if path.exists():
            existing = CollectionQualityResult.model_validate_json(path.read_bytes())
            if existing.model_dump(exclude={"checked_at"}) != result.model_dump(
                exclude={"checked_at"}
            ):
                raise DataFailure("conflict")
            return
        self._write(path, result.model_dump_json().encode())

    def qualities(self, part: CollectionPart) -> tuple[CollectionQualityResult, ...]:
        """Absent evidence stays absent; a read never creates folders or invents approval."""
        directory = self.root / str(part.request.scope_id) / str(part.part_id)
        paths = list(directory.glob("quality-*.json"))
        if len(paths) > 100:
            raise DataFailure("unavailable")
        try:
            results = tuple(
                CollectionQualityResult.model_validate_json(path.read_bytes()) for path in paths
            )
            if any(
                item.scope_id != part.request.scope_id or item.part_id != part.part_id
                for item in results
            ):
                raise ValueError
            return tuple(sorted(results, key=lambda item: (item.checked_at, str(item.quality_id))))
        except (OSError, ValueError):
            raise DataFailure("unavailable") from None

    def publish(self, part: CollectionPart, result: CollectionResult) -> CollectionPublication:
        existing = self.recover(part)
        if existing:
            return existing
        if not result.raw or not result.records:
            raise DataFailure("invalid_input")
        directory = self._directory(part.request.scope_id, part.part_id)
        raw = self._parquet(list(result.raw))
        records = [row.model_dump(mode="json") for row in result.records]
        normalized = self._parquet(records)
        raw_hash, records_hash = sha256(raw).hexdigest(), digest(records)
        self._write(directory / f"raw-{raw_hash}.parquet", raw)
        self._write(directory / f"records-{records_hash}.parquet", normalized)
        publication = CollectionPublication(
            schema_version=1,
            scope_id=part.request.scope_id,
            publication_id=part.part_id,
            job_id=part.job_id,
            part_id=part.part_id,
            actor_id=part.request.actor_id,
            correlation_id=part.request.correlation_id,
            source=part.request.source,
            dataset_kind=part.request.dataset_kind,
            instrument_id=part.instrument_id,
            granularity=part.request.granularity,
            instrument_name=part.instrument_name,
            field=part.field,
            start=part.start,
            end=part.end,
            mapping_version=part.mapping_version,
            raw_sha256=raw_hash,
            records_sha256=records_hash,
            raw_record_count=len(result.raw),
            record_count=len(records),
            published_at=datetime.now(UTC),
            units=result.units,
            source_time_convention=result.source_time_convention,
            evidence=result.evidence,
            limitations=result.limitations,
        )
        self._write(directory / "manifest.json", publication.model_dump_json().encode())
        return publication

    def records(self, publication: CollectionPublication) -> tuple[CollectedBar | FactorPoint, ...]:
        directory = self._directory(publication.scope_id, publication.publication_id)
        try:
            raw = (directory / f"raw-{publication.raw_sha256}.parquet").read_bytes()
            if sha256(raw).hexdigest() != publication.raw_sha256:
                raise ValueError
            if pq.read_table(BytesIO(raw)).num_rows != publication.raw_record_count:
                raise ValueError
            content = (directory / f"records-{publication.records_sha256}.parquet").read_bytes()
            payloads = pq.read_table(BytesIO(content))["payload"].to_pylist()
            values = [json.loads(value) for value in payloads]
            if (
                digest(values) != publication.records_sha256
                or len(values) != publication.record_count
            ):
                raise ValueError
            model = CollectedBar if publication.dataset_kind == "bars" else FactorPoint
            return tuple(model.model_validate_json(json.dumps(value)) for value in values)
        except (ValueError, OSError, pa.ArrowException):
            raise DataFailure("unavailable") from None
