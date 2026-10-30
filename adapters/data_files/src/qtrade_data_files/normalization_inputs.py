"""Private bounded conversion checkpoints, separate from successful publications."""

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from itertools import islice
from pathlib import Path
from uuid import UUID, uuid5

from pydantic import AwareDatetime, TypeAdapter

from qtrade_data_contracts.collection import CollectionRawCapture, digest
from qtrade_data_contracts.normalization_input import (
    CollectionNormalizationInput,
    NormalizationPayload,
)
from qtrade_data_core.collection import CollectionPart
from qtrade_data_core.ports import DataFailure

DATETIME_WIRE = TypeAdapter(AwareDatetime)
MAX_INPUT_BYTES = 128 * 1024 * 1024
MAX_SOURCE_BYTES = 256 * 1024 * 1024


class FileNormalizationInputs:
    def __init__(self, root: Path, write: Callable[[Path, bytes], None]):
        self.root, self.write = root, write

    def _directory(self, part: CollectionPart) -> Path:
        return self.root / str(part.request.scope_id) / str(part.part_id) / "normalization-inputs"

    @staticmethod
    def _bound(value: CollectionNormalizationInput, part: CollectionPart) -> None:
        if (
            value.request != part.request
            or value.job_id != part.job_id
            or value.part_id != part.part_id
            or value.instrument_id != part.instrument_id
            or value.field != part.field
            or value.start != part.start
            or value.end != part.end
            or value.mapping_version != part.mapping_version
        ):
            raise DataFailure("unverified")

    def read(
        self, part: CollectionPart, input_id: UUID, maximum: int = MAX_INPUT_BYTES
    ) -> CollectionNormalizationInput:
        path = self._directory(part) / f"input-{input_id}.json"
        try:
            with path.open("rb") as stream:
                body = stream.read(min(maximum, MAX_INPUT_BYTES) + 1)
            if len(body) > min(maximum, MAX_INPUT_BYTES):
                raise DataFailure("invalid_input")
            value = CollectionNormalizationInput.model_validate_json(body)
            if value.input_id != input_id:
                raise DataFailure("unverified")
            self._bound(value, part)
            return value
        except FileNotFoundError:
            raise DataFailure("not_found") from None
        except (OSError, ValueError):
            raise DataFailure("unverified") from None

    def inputs(self, part: CollectionPart) -> tuple[CollectionNormalizationInput, ...]:
        paths = list(islice(self._directory(part).glob("input-*.json"), 101))
        if len(paths) > 100:
            raise DataFailure("invalid_input")
        try:
            if sum(p.stat().st_size for p in paths) > MAX_INPUT_BYTES:
                raise DataFailure("invalid_input")
            values = tuple(self.read(part, UUID(p.stem.removeprefix("input-"))) for p in paths)
        except (OSError, ValueError):
            raise DataFailure("unverified") from None
        return tuple(sorted(values, key=lambda v: (v.prepared_at, str(v.input_id))))

    def _receipts(
        self,
        part: CollectionPart,
        captures: tuple[CollectionRawCapture, ...],
        maximum: int = MAX_SOURCE_BYTES,
    ) -> int:
        if not captures or len(captures) > 4096:
            raise DataFailure("unverified")
        history = (
            {"factor_history"}
            if part.request.dataset_kind == "factors"
            else {"history", "cached_history"}
        )
        if not any(c.kind in history for c in captures):
            raise DataFailure("unverified")
        directory = self._directory(part).parent / "source-captures"
        total = 0
        try:
            for receipt in captures:
                if (
                    receipt.scope_id,
                    receipt.part_id,
                    receipt.job_id,
                    receipt.actor_id,
                    receipt.source,
                    receipt.mapping_version,
                    receipt.attempt,
                    receipt.lease_token,
                ) != (
                    part.request.scope_id,
                    part.part_id,
                    part.job_id,
                    part.request.actor_id,
                    part.request.source,
                    part.mapping_version,
                    part.attempt,
                    part.lease_token,
                ):
                    raise DataFailure("unverified")
                identity = uuid5(
                    part.part_id,
                    f"{receipt.kind}:{receipt.lease_token}:{receipt.attempt}:{receipt.content_sha256}",
                )
                with (directory / f"receipt-{receipt.capture_id}.json").open("rb") as stream:
                    metadata = stream.read(64 * 1024 + 1)
                if len(metadata) > 64 * 1024:
                    raise DataFailure("invalid_input")
                total += len(metadata)
                if total > min(maximum, MAX_SOURCE_BYTES):
                    raise DataFailure("invalid_input")
                stored = CollectionRawCapture.model_validate_json(metadata)
                if identity != receipt.capture_id or stored != receipt:
                    raise DataFailure("unverified")
                # Hash incrementally: exact HTTP envelopes and decoded history
                # may coexist. Never allocate an unbounded second body copy.
                checksum = sha256()
                with (directory / f"payload-{receipt.content_sha256}.json").open("rb") as stream:
                    while block := stream.read(1024 * 1024):
                        total += len(block)
                        if total > min(maximum, MAX_SOURCE_BYTES):
                            raise DataFailure("invalid_input")
                        checksum.update(block)
                if checksum.hexdigest() != receipt.content_sha256:
                    raise DataFailure("unverified")
        except (OSError, ValueError):
            raise DataFailure("unverified") from None
        return total

    def evidence(
        self,
        part: CollectionPart,
        input_id: UUID,
        maximum: int,
        max_receipts: int,
    ) -> tuple[CollectionNormalizationInput, tuple[CollectionRawCapture, ...], int]:
        """Revalidate only the frozen input's receipts, never unrelated retries.

        A completed parent's current lease is cleared. Historical receipts must
        instead agree with one another about their original attempt/lease.
        """
        value = self.read(part, input_id, maximum)
        if len(value.raw_capture_ids) > max_receipts:
            raise DataFailure("invalid_input")
        receipts = []
        directory = self._directory(part).parent / "source-captures"
        try:
            for capture_id in value.raw_capture_ids:
                with (directory / f"receipt-{capture_id}.json").open("rb") as stream:
                    data = stream.read(64 * 1024 + 1)
                if len(data) > 64 * 1024:
                    raise DataFailure("invalid_input")
                receipt = CollectionRawCapture.model_validate_json(data)
                if receipt.capture_id != capture_id or receipt.captured_at > value.prepared_at:
                    raise DataFailure("unverified")
                receipts.append(receipt)
            size = (self._directory(part) / f"input-{input_id}.json").stat().st_size
        except (OSError, ValueError):
            raise DataFailure("unverified") from None
        if size > maximum or not receipts:
            raise DataFailure("invalid_input")
        historical = replace(part, attempt=receipts[0].attempt, lease_token=receipts[0].lease_token)
        size += self._receipts(historical, tuple(receipts), maximum - size)
        return value, tuple(receipts), size

    def capture(
        self,
        part: CollectionPart,
        payload: NormalizationPayload,
        captures: tuple[CollectionRawCapture, ...],
    ) -> CollectionNormalizationInput:
        self._receipts(part, captures)
        body = dict(
            schema_version=2,
            format_version="collection-normalization-input.v1",
            request=part.request.model_dump(mode="json"),
            job_id=str(part.job_id),
            part_id=str(part.part_id),
            instrument_id=part.instrument_id,
            field=part.field,
            start=DATETIME_WIRE.dump_python(part.start, mode="json"),
            end=DATETIME_WIRE.dump_python(part.end, mode="json"),
            mapping_version=part.mapping_version,
            instrument_name=part.instrument_name,
            raw_capture_ids=sorted({str(c.capture_id) for c in captures}),
            payload=payload.model_dump(mode="json"),
        )
        identity = uuid5(part.part_id, digest(body))
        path = self._directory(part) / f"input-{identity}.json"
        if path.exists():
            existing = self.read(part, identity)
            if (
                existing.model_dump(
                    mode="json", exclude={"input_id", "input_sha256", "prepared_at"}
                )
                != body
            ):
                raise DataFailure("conflict")
            return existing
        body |= {
            "input_id": str(identity),
            "prepared_at": DATETIME_WIRE.dump_python(datetime.now(UTC), mode="json"),
        }
        value = CollectionNormalizationInput.model_validate_json(
            json.dumps(body | {"input_sha256": digest(body)})
        )
        encoded = value.model_dump_json().encode()
        if len(encoded) > MAX_INPUT_BYTES:
            raise DataFailure("invalid_input")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.write(path, encoded)
        except DataFailure as error:
            if error.code != "conflict":
                raise
            # Concurrent identical capture keeps the first preparation time.
            existing = self.read(part, identity)
            if existing.model_dump(exclude={"prepared_at", "input_sha256"}) != value.model_dump(
                exclude={"prepared_at", "input_sha256"}
            ):
                raise
            return existing
        return value
