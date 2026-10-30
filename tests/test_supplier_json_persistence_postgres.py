"""Controlled malformed HTTP; real queue, private files and PG, never live readiness."""

import json
from base64 import b64decode
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from test_collection_replay_postgres import setup
from test_research_evidence_postgres import database as isolated_database

from qtrade_data_files.archive import FileCollectionArchive
from qtrade_data_postgres.collection_replay import PostgresCollectionReplays

database = isolated_database


@pytest.mark.parametrize("factors", [False, True])
@pytest.mark.parametrize(
    "body",
    [
        b'{"code":403,"code":200,"data":[]}',
        b'[{"value":1,"\\u0076alue":2}]',
        b'[{"value":NaN}]',
        b'{"code":false,"data":[]}',
    ],
)
def test_worker_retains_rejected_http_without_publishing_or_changing_old_results(
    database, tmp_path, factors, body
):
    req, parent, provider, archive, repo, _, service, _, _, _ = setup(
        database, tmp_path, factors=factors
    )
    original = repo.publication(req.scope_id, parent.part_id)
    frozen = {p: p.read_bytes() for p in archive.root.rglob("*") if p.is_file()}
    rejected = req.model_copy(update={"request_id": uuid4()})
    plan = service.preview(rejected)
    service.submit(rejected, plan.coverage_version)
    # The successful parent primed the full-year bar cache. Use a fresh owned
    # cache so this run must exercise the HTTP decoder, not reuse good history.
    provider.cache = tmp_path / "rejected-response-cache"
    provider.cache.mkdir(mode=0o700)
    calls = []

    def respond(_):
        calls.append(True)
        return httpx.Response(200, content=body)

    provider.client = httpx.Client(transport=httpx.MockTransport(respond))
    try:
        part = service.run_once()
        assert part.job_id == rejected.request_id
        assert len(calls) == 1
        restarted = PostgresCollectionReplays(database)
        failed = restarted.job(req.scope_id, rejected.request_id)
        assert failed["parts"][0]["error_code"] == "unavailable"
        assert failed["parts"][0]["publication"] is None
        reopened = FileCollectionArchive(archive.root)
        (receipt,) = reopened.source_captures(part, datetime.now(UTC))
        assert receipt.kind == "http_response"
        path = reopened.root / str(req.scope_id) / str(part.part_id) / "source-captures"
        envelope = json.loads((path / f"payload-{receipt.content_sha256}.json").read_bytes())
        assert envelope["complete"] and envelope["http_status"] == 200
        assert b64decode(envelope["body_base64"]) == body
        assert reopened.recover(part) is None
        assert reopened.normalization_inputs(part) == ()
        assert reopened.qualities(part) == ()
        assert restarted.publication(req.scope_id, parent.part_id) == original
        assert all(p.read_bytes() == value for p, value in frozen.items())
        assert all(p.stat().st_mode & 0o077 == 0 for p in path.iterdir() if p.is_file())
    finally:
        # Transient decode failures have a bounded retry policy. Retire only this
        # owned test run so a later test cannot claim its delayed retry.
        repo.control(req.scope_id, rejected.request_id, "cancel")
