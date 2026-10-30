"""Raw-before-normalization research evidence and immutable standard artifacts."""

import json
from datetime import datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid5

import pyarrow.parquet as pq
from pydantic import TypeAdapter

from qtrade_data_contracts.collection import digest
from qtrade_data_contracts.research import ResearchObservation
from qtrade_data_contracts.research_acquisition import (
    MAX_RESPONSE_BYTES,
    ResearchResponseReceipt,
    response_identity,
)
from qtrade_data_contracts.research_archive import ResearchArtifactManifest, ResearchRawManifest
from qtrade_data_contracts.research_processing import ResearchProcessingFailure
from qtrade_data_contracts.research_transport import ResearchPartition, ResearchPlan
from qtrade_data_core.ports import DataFailure
from qtrade_data_core.research import ResearchRawPage
from qtrade_data_core.research_acquisition import ResearchSourceResponse
from qtrade_data_files.archive import FileCollectionArchive

RECORDS = TypeAdapter(tuple[ResearchObservation, ...])


class FileResearchArchive:
    def __init__(self, root: Path):
        self.root = root.resolve() / "research-v2"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _directory(self, scope_id: UUID, identifier: UUID) -> Path:
        path = self.root / str(scope_id) / str(identifier)
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        return path

    def capture_response(
        self,
        plan: ResearchPlan,
        partition: ResearchPartition,
        cursor: str | None,
        acquisition: UUID,
        index: int,
        response: ResearchSourceResponse,
        at: datetime,
    ) -> ResearchResponseReceipt:
        """Retain exact decoded HTTP bytes before status or JSON interpretation.

        Private objects are immutable and fsynced before metadata can be indexed.
        No supplier URL/header or exception text is added to either envelope.
        """
        if partition not in plan.partitions:
            raise DataFailure("conflict")
        if type(response.body) is not bytes or len(response.body) > MAX_RESPONSE_BYTES:
            raise DataFailure("unverified")
        body_hash = sha256(response.body).hexdigest()
        try:
            receipt = ResearchResponseReceipt(
                scope_id=plan.request.scope_id,
                request_id=plan.request.request_id,
                receipt_id=response_identity(acquisition, index, body_hash),
                acquisition_id=acquisition,
                response_index=index,
                actor_id=plan.request.actor_id,
                plan_sha256=plan.plan_sha256,
                source=plan.capability.source,
                kind=plan.capability.kind,
                mapping_version=plan.capability.mapping_version,
                instrument_id=partition.instrument_id,
                start=partition.start,
                end=partition.end,
                cursor=cursor,
                operation=response.operation,
                http_status=response.status,
                complete=response.complete,
                body_bytes=len(response.body),
                body_sha256=body_hash,
                captured_at=at,
            )
            directory = self._directory(receipt.scope_id, receipt.request_id) / "responses"
            directory.mkdir(exist_ok=True, mode=0o700)
            path = directory / f"{receipt.receipt_id}.json"
            body = directory / f"{receipt.receipt_id}.bin"
            FileCollectionArchive._write(body, response.body)
            if path.exists():
                existing = ResearchResponseReceipt.model_validate_json(path.read_bytes())
                if existing.model_dump(exclude={"captured_at"}) != receipt.model_dump(
                    exclude={"captured_at"}
                ):
                    raise DataFailure("conflict")
                return existing
            FileCollectionArchive._write(path, receipt.model_dump_json().encode())
            return receipt
        except (OSError, ValueError):
            raise DataFailure("unavailable") from None

    def response_body(self, receipt: ResearchResponseReceipt) -> bytes:
        """Internal verifier/reprocessing read, never exposed by a browser route."""
        receipt = ResearchResponseReceipt.model_validate_json(receipt.model_dump_json())
        path = (
            self.root
            / str(receipt.scope_id)
            / str(receipt.request_id)
            / "responses"
            / f"{receipt.receipt_id}.bin"
        )
        try:
            if path.stat().st_size != receipt.body_bytes:
                raise ValueError
            body = path.read_bytes()
            if sha256(body).hexdigest() != receipt.body_sha256:
                raise ValueError
            return body
        except (OSError, ValueError):
            raise DataFailure("unverified") from None

    def record_processing_failure(
        self, plan: ResearchPlan, raw: ResearchRawManifest, failure: ResearchProcessingFailure
    ) -> None:
        if (
            failure.scope_id,
            failure.request_id,
            failure.plan_sha256,
            failure.batch_id,
            failure.raw_sha256,
            failure.raw_content_sha256,
            failure.source,
            failure.kind,
            failure.mapping_version,
        ) != (
            plan.request.scope_id,
            plan.request.request_id,
            plan.plan_sha256,
            raw.batch_id,
            raw.raw_sha256,
            raw.raw_content_sha256,
            plan.capability.source,
            plan.capability.kind,
            plan.capability.mapping_version,
        ):
            raise DataFailure("conflict")
        if (raw.scope_id, raw.request_id, raw.plan_sha256) != (
            plan.request.scope_id,
            plan.request.request_id,
            plan.plan_sha256,
        ):
            raise DataFailure("conflict")
        if (
            raw.source != plan.capability.source
            or raw.kind != plan.capability.kind
            or not any(
                p.instrument_id == raw.instrument_id and p.start == raw.start and p.end == raw.end
                for p in plan.partitions
            )
        ):
            raise DataFailure("conflict")
        # Verify the actual durable input, not only its supplied manifest. The
        # failure record carries hashes and identities, never supplier payload.
        self.raw(raw)
        path = (
            self._directory(plan.request.scope_id, plan.request.request_id)
            / f"processing-failure-{failure.failure_id}.json"
        )
        if path.exists():
            existing = ResearchProcessingFailure.model_validate_json(path.read_bytes())
            if existing.model_dump(exclude={"failed_at"}) != failure.model_dump(
                exclude={"failed_at"}
            ):
                raise DataFailure("conflict")
            return
        FileCollectionArchive._write(path, failure.model_dump_json().encode())

    def processing_failures(self, plan: ResearchPlan) -> tuple[ResearchProcessingFailure, ...]:
        directory = self.root / str(plan.request.scope_id) / str(plan.request.request_id)
        paths = list(directory.glob("processing-failure-*.json"))
        if len(paths) > 100:
            raise DataFailure("unavailable")
        try:
            values = tuple(
                ResearchProcessingFailure.model_validate_json(p.read_bytes()) for p in paths
            )
            if any(
                (f.scope_id, f.request_id, f.plan_sha256, f.source, f.kind)
                != (
                    plan.request.scope_id,
                    plan.request.request_id,
                    plan.plan_sha256,
                    plan.capability.source,
                    plan.capability.kind,
                )
                for f in values
            ):
                raise ValueError
            return tuple(sorted(values, key=lambda f: (f.failed_at, str(f.failure_id))))
        except (OSError, ValueError):
            raise DataFailure("unavailable") from None

    def capture(
        self,
        plan: ResearchPlan,
        partition: ResearchPartition,
        page: ResearchRawPage,
        collected_at: datetime,
    ) -> ResearchRawManifest:
        if partition not in plan.partitions:
            raise DataFailure("conflict")
        try:
            content = digest(list(page.rows))
        except (TypeError, ValueError):
            raise DataFailure("invalid_input") from None
        batch_id = uuid5(
            plan.request.request_id,
            digest(
                {
                    "partition": partition.model_dump(mode="json"),
                    "cursor": page.cursor,
                    "next_cursor": page.next_cursor,
                    "raw": content,
                }
            ),
        )
        directory = self._directory(plan.request.scope_id, batch_id)
        manifest_path = directory / "raw-manifest.json"
        if manifest_path.exists():
            try:
                manifest = ResearchRawManifest.model_validate_json(manifest_path.read_bytes())
            except (ValueError, OSError):
                raise DataFailure("unverified") from None
            if (
                manifest.plan_sha256 != plan.plan_sha256
                or manifest.raw_content_sha256 != content
                or manifest.scope_id != plan.request.scope_id
                or manifest.request_id != plan.request.request_id
                or manifest.batch_id != batch_id
                or manifest.source != plan.capability.source
                or manifest.kind != plan.capability.kind
                or manifest.instrument_id != partition.instrument_id
                or manifest.start != partition.start
                or manifest.end != partition.end
                or manifest.cursor != page.cursor
                or manifest.next_cursor != page.next_cursor
                or manifest.raw_record_count != len(page.rows)
            ):
                raise DataFailure("conflict")
            self.raw(manifest)
            return manifest
        blob = FileCollectionArchive._parquet(list(page.rows))
        manifest = ResearchRawManifest(
            scope_id=plan.request.scope_id,
            request_id=plan.request.request_id,
            batch_id=batch_id,
            plan_sha256=plan.plan_sha256,
            source=plan.capability.source,
            kind=plan.capability.kind,
            instrument_id=partition.instrument_id,
            start=partition.start,
            end=partition.end,
            cursor=page.cursor,
            next_cursor=page.next_cursor,
            collected_at=collected_at,
            raw_sha256=sha256(blob).hexdigest(),
            raw_content_sha256=content,
            raw_record_count=len(page.rows),
        )
        FileCollectionArchive._write(directory / f"raw-{manifest.raw_sha256}.parquet", blob)
        try:
            FileCollectionArchive._write(manifest_path, manifest.model_dump_json().encode())
        except DataFailure as error:
            if error.code != "conflict" or not manifest_path.exists():
                raise
            # A competing writer may have frozen an earlier collection time.
            # Re-enter the validated recovery path; never overwrite its manifest.
            return self.capture(plan, partition, page, collected_at)
        return manifest

    def raw(self, manifest: ResearchRawManifest) -> tuple[dict[str, object], ...]:
        path = (
            self._directory(manifest.scope_id, manifest.batch_id)
            / f"raw-{manifest.raw_sha256}.parquet"
        )
        try:
            blob = path.read_bytes()
            if sha256(blob).hexdigest() != manifest.raw_sha256:
                raise ValueError("raw byte checksum")
            rows = tuple(
                json.loads(value) for value in pq.read_table(BytesIO(blob))["payload"].to_pylist()
            )
            if (
                len(rows) != manifest.raw_record_count
                or digest(list(rows)) != manifest.raw_content_sha256
            ):
                raise ValueError("raw content checksum/count")
            return rows
        except (ValueError, OSError, KeyError):
            raise DataFailure("unverified") from None

    def replay_raw(
        self, manifest: ResearchRawManifest, maximum: int
    ) -> tuple[tuple[dict[str, object], ...], int]:
        """Bound both stored bytes and Parquet's uncompressed input before decode.

        Return the larger cost for a shared run budget. Ordinary archived reads
        keep their old interface; only explicit offline work uses this limit.
        """
        if type(maximum) is not int or maximum < 1:
            raise DataFailure("invalid_input")
        path = (
            self.root
            / str(manifest.scope_id)
            / str(manifest.batch_id)
            / f"raw-{manifest.raw_sha256}.parquet"
        )
        try:
            with path.open("rb") as source:
                blob = source.read(maximum + 1)
            if len(blob) > maximum:
                raise DataFailure("invalid_input")
            if sha256(blob).hexdigest() != manifest.raw_sha256:
                raise DataFailure("unverified")
            parquet = pq.ParquetFile(BytesIO(blob))
            uncompressed = sum(
                parquet.metadata.row_group(i).total_byte_size
                for i in range(parquet.metadata.num_row_groups)
            )
            cost = max(len(blob), uncompressed)
            if cost > maximum:
                raise DataFailure("invalid_input")
            rows = tuple(json.loads(v) for v in parquet.read()["payload"].to_pylist())
            if (
                len(rows) != manifest.raw_record_count
                or digest(list(rows)) != manifest.raw_content_sha256
            ):
                raise DataFailure("unverified")
            return rows, cost
        except (OSError, ValueError, KeyError):
            raise DataFailure("unverified") from None

    def standardize(
        self,
        plan: ResearchPlan,
        batches: tuple[ResearchRawManifest, ...],
        records: tuple[ResearchObservation, ...],
        created_at: datetime,
    ) -> ResearchArtifactManifest:
        if not records or not batches or not any(b.raw_record_count for b in batches):
            raise DataFailure("invalid_input")
        if any(
            b.scope_id != plan.request.scope_id
            or b.request_id != plan.request.request_id
            or b.plan_sha256 != plan.plan_sha256
            or b.source != plan.capability.source
            or b.kind != plan.capability.kind
            for b in batches
        ):
            raise DataFailure("conflict")
        if any(
            r.scope_id != plan.request.scope_id
            or r.source != plan.capability.source
            or r.kind != plan.capability.kind
            or r.instrument_id not in plan.request.instrument_ids
            or r.mapping_version != plan.capability.mapping_version
            for r in records
        ):
            raise DataFailure("unverified")
        for batch in batches:
            self.raw(batch)
        values = [r.model_dump(mode="json") for r in records]
        record_hash = digest(values)
        semantic_hash = digest(sorted(r.content_version for r in records))
        artifact_id = uuid5(
            plan.request.request_id,
            digest([plan.plan_sha256, [str(b.batch_id) for b in batches], record_hash]),
        )
        directory = self._directory(plan.request.scope_id, artifact_id)
        path = directory / "artifact-manifest.json"
        if path.exists():
            try:
                manifest = ResearchArtifactManifest.model_validate_json(path.read_bytes())
            except (ValueError, OSError):
                raise DataFailure("unverified") from None
            if (
                manifest.records_sha256 != record_hash
                or manifest.plan_sha256 != plan.plan_sha256
                or manifest.scope_id != plan.request.scope_id
                or manifest.request_id != plan.request.request_id
                or manifest.artifact_id != artifact_id
                or manifest.capability != plan.capability
                or manifest.raw_batches != batches
                or manifest.record_count != len(records)
                or manifest.semantic_sha256 != semantic_hash
            ):
                raise DataFailure("conflict")
            self.records(manifest)
            return manifest
        blob = FileCollectionArchive._parquet(values)
        manifest = ResearchArtifactManifest(
            scope_id=plan.request.scope_id,
            request_id=plan.request.request_id,
            artifact_id=artifact_id,
            plan_sha256=plan.plan_sha256,
            capability=plan.capability,
            mapping_version=plan.capability.mapping_version,
            raw_batches=batches,
            records_sha256=record_hash,
            records_file_sha256=sha256(blob).hexdigest(),
            semantic_sha256=semantic_hash,
            record_count=len(records),
            created_at=created_at,
        )
        FileCollectionArchive._write(directory / f"records-{record_hash}.parquet", blob)
        try:
            FileCollectionArchive._write(path, manifest.model_dump_json().encode())
        except DataFailure as error:
            if error.code != "conflict" or not path.exists():
                raise
            return self.standardize(plan, batches, records, created_at)
        return manifest

    def records(self, manifest: ResearchArtifactManifest) -> tuple[ResearchObservation, ...]:
        try:
            path = (
                self._directory(manifest.scope_id, manifest.artifact_id)
                / f"records-{manifest.records_sha256}.parquet"
            )
            blob = path.read_bytes()
            if sha256(blob).hexdigest() != manifest.records_file_sha256:
                raise ValueError("standard file checksum")
            values = [json.loads(v) for v in pq.read_table(BytesIO(blob))["payload"].to_pylist()]
            if len(values) != manifest.record_count or digest(values) != manifest.records_sha256:
                raise ValueError("standard content checksum/count")
            records = RECORDS.validate_json(json.dumps(values))
            if digest(sorted(r.content_version for r in records)) != manifest.semantic_sha256:
                raise ValueError("semantic checksum")
            for batch in manifest.raw_batches:
                self.raw(batch)
            return records
        except (ValueError, OSError, KeyError):
            raise DataFailure("unverified") from None
