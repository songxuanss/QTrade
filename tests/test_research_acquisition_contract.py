"""Receipt metadata cannot carry payloads or fake parsed-record success."""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from test_research_evidence import NOW, evidence

from qtrade_data_contracts.research_acquisition import ResearchResponseReceipt
from qtrade_data_core.ports import DataFailure
from qtrade_data_core.research import ResearchRawPage
from qtrade_data_core.research_acquisition import (
    ResearchAcquisitionEvidence,
    ResearchSourceResponse,
)
from qtrade_data_core.research_jobs import ResearchJobsService
from qtrade_data_http.client import StaticScopeAuthorizer


def test_public_metadata_rejects_payload_url_and_false_counts(tmp_path):
    archive, plan, *_ = evidence(tmp_path)
    receipt = archive.capture_response(
        plan,
        plan.partitions[0],
        None,
        uuid4(),
        0,
        ResearchSourceResponse("research.data", 200, b'{"private":"fixture"}', True),
        NOW,
    )
    assert archive.response_body(receipt) == b'{"private":"fixture"}'
    for extra in (
        {"body": "private"},
        {"url": "https://private.invalid"},
        {"normalized_record_count": 0},
        {"http_status": True},
        {"body_bytes": True},
    ):
        with pytest.raises(ValidationError):
            ResearchResponseReceipt.model_validate_json(
                json.dumps(receipt.model_dump(mode="json") | extra)
            )


def test_reader_reauthorizes_before_repository():
    actor, scope, job = uuid4(), uuid4(), uuid4()
    auth = StaticScopeAuthorizer({})

    def forbidden(*args):
        raise AssertionError("unauthorized read reached repository")

    service = ResearchJobsService(auth, None, SimpleNamespace(job=forbidden, plan=forbidden))
    with pytest.raises(DataFailure, match="forbidden"):
        service.response_receipts(actor, scope, job)


def test_source_envelope_is_private_and_receipt_has_no_quality_status(tmp_path):
    archive, plan, *_ = evidence(tmp_path)
    receipt = archive.capture_response(
        plan,
        plan.partitions[0],
        None,
        uuid4(),
        0,
        ResearchSourceResponse("research.data", 403, b"fixture denial", True),
        NOW,
    )
    data = receipt.model_dump(mode="json")
    assert "status" not in data and "record_count" not in data
    assert "body" not in data and "body_base64" not in data
    assert data["http_status"] == 403 and data["complete"] is True


def test_capture_window_rejects_retained_callback_and_missing_response(tmp_path):
    archive, plan, *_ = evidence(tmp_path)
    saved = []
    capture = ResearchAcquisitionEvidence(
        archive, SimpleNamespace(append=lambda *args: saved.append(args))
    )

    class Provider:
        def fetch_with_capture(self, request, partition, cursor, sink):
            self.sink = sink
            sink(ResearchSourceResponse("research.data", 200, b"[]", True))
            return ResearchRawPage((), None, None)

    provider = Provider()
    capture.fetch(plan, provider, plan.partitions[0], None, lambda: NOW)
    assert len(saved) == 1
    with pytest.raises(DataFailure, match="conflict"):
        provider.sink(ResearchSourceResponse("research.data", 200, b"[]", True))
    missing = SimpleNamespace(fetch_with_capture=lambda *args: ResearchRawPage((), None, None))
    with pytest.raises(DataFailure, match="unverified"):
        capture.fetch(plan, missing, plan.partitions[0], None, lambda: NOW)


def test_provider_cannot_swallow_a_failed_receipt_write(tmp_path):
    archive, plan, *_ = evidence(tmp_path)

    class Index:
        count = 0

        def append(self, *args):
            self.count += 1
            if self.count == 2:
                raise DataFailure("unavailable")

    class Provider:
        def fetch_with_capture(self, request, partition, cursor, sink):
            sink(ResearchSourceResponse("research.data", 200, b"[]", True))
            try:
                sink(ResearchSourceResponse("research.data", 200, b"[]", True))
            except DataFailure:
                pass
            return ResearchRawPage((), None, None)

    with pytest.raises(DataFailure, match="unavailable"):
        ResearchAcquisitionEvidence(archive, Index()).fetch(
            plan, Provider(), plan.partitions[0], None, lambda: NOW
        )
