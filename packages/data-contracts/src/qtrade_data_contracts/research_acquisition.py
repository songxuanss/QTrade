"""Safe acquisition metadata; HTTP status is not publication or data validity.

Decoded response bytes remain private archive objects. No URL, header, provider
exception, credential, body or inferred standard-record count crosses this seam.
"""

from datetime import date
from typing import Annotated, Literal, Self
from uuid import UUID, uuid5

from pydantic import AwareDatetime, Field, model_validator

from .collection import check_instrument
from .models import Digest, Text
from .research import ResearchV2
from .research_transport import ResearchKind

MAX_RESPONSE_BYTES = 64 * 1024 * 1024


def response_identity(acquisition_id: UUID, index: int, body_sha256: str) -> UUID:
    return uuid5(acquisition_id, f"research-response:{index}:{body_sha256}")


class ResearchResponseReceipt(ResearchV2):
    scope_id: UUID
    request_id: UUID
    receipt_id: UUID
    acquisition_id: UUID
    response_index: Annotated[int, Field(ge=0, lt=10)]
    actor_id: UUID
    plan_sha256: Digest
    source: Text
    kind: ResearchKind
    mapping_version: Text
    instrument_id: Text
    start: date
    end: date
    cursor: Text | None
    operation: Literal["research.data"]
    http_status: Annotated[int, Field(ge=100, le=599)] | None
    complete: bool
    body_bytes: Annotated[int, Field(ge=0, le=MAX_RESPONSE_BYTES)]
    body_sha256: Digest
    captured_at: AwareDatetime

    @model_validator(mode="after")
    def coherent_response(self) -> Self:
        check_instrument(self.instrument_id)
        if self.start >= self.end or (self.complete and self.http_status is None):
            raise ValueError("invalid acquisition interval or response status")
        if self.receipt_id != response_identity(
            self.acquisition_id, self.response_index, self.body_sha256
        ):
            raise ValueError("response receipt identity mismatch")
        return self


class ResearchResponsePage(ResearchV2):
    scope_id: UUID
    request_id: UUID
    receipts: Annotated[tuple[ResearchResponseReceipt, ...], Field(max_length=100)]
    next_id: UUID | None

    @model_validator(mode="after")
    def scoped_page(self) -> Self:
        keys = [(r.captured_at, str(r.receipt_id)) for r in self.receipts]
        if keys != sorted(set(keys), reverse=True) or len(
            {r.receipt_id for r in self.receipts}
        ) != len(self.receipts):
            raise ValueError("response receipts must be unique and ordered")
        if any(
            (r.scope_id, r.request_id) != (self.scope_id, self.request_id) for r in self.receipts
        ):
            raise ValueError("response page scope/request mismatch")
        if self.next_id is not None and (
            not self.receipts or self.next_id != self.receipts[-1].receipt_id
        ):
            raise ValueError("response cursor must identify the final returned receipt")
        return self
