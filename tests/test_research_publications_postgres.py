"""Actual isolated PG release/query chain; source and PIT ledger are fixtures."""

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import psycopg
import pytest
from test_research_contracts import metric, statement
from test_research_evidence import NOW, evidence
from test_research_interface import ControlledSource, request

from qtrade_data_contracts.research import KnowledgeTime
from qtrade_data_contracts.research_transport import ResearchQuery
from qtrade_data_core.ports import DataFailure
from qtrade_data_core.research import ResearchRawPage, ResearchService
from qtrade_data_core.research_release import ResearchReleaseService
from qtrade_data_files.research import FileResearchArchive
from qtrade_data_http.client import StaticScopeAuthorizer
from qtrade_data_postgres.research_publications import (
    PostgresResearchPublications,
    PostgresResearchReader,
)

pytest_plugins = ("test_research_evidence_postgres",)


def release_setup(database, tmp_path, record_update=None):
    archive, plan, _, raw, record = evidence(tmp_path)
    if record_update:
        record = record.model_copy(update=record_update)
    artifact = archive.standardize(plan, (raw,), (record,), NOW)
    repo = PostgresResearchPublications(database)
    repo.register(plan)
    repo.append_raw(raw)
    repo.append_artifact(artifact)
    auth = StaticScopeAuthorizer({f"{plan.request.actor_id}:{plan.request.scope_id}": "manage"})
    return ResearchReleaseService(auth, archive, repo), repo, archive, plan, artifact


def test_block_repair_release_and_frozen_versions(database, tmp_path):
    service, repo, archive, plan, blocked = release_setup(
        database, tmp_path, {"report_period": date(2014, 6, 30)}
    )
    decision = service.review(plan.request.actor_id, plan.request.scope_id, blocked.artifact_id)
    assert decision.status == "blocked"
    with pytest.raises(DataFailure, match="unverified"):
        service.publish(plan.request.actor_id, plan.request.scope_id, blocked.artifact_id)
    with psycopg.connect(database) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM qtrade_data.research_publication WHERE scope_id=%s",
                (plan.request.scope_id,),
            ).fetchone()[0]
            == 0
        )
    fixed = statement(
        scope_id=plan.request.scope_id,
        mapping_version=plan.capability.mapping_version,
        metrics=(metric("100"),),
        collected_at=NOW,
    )
    artifact = archive.standardize(plan, blocked.raw_batches, (fixed,), NOW)
    repo.append_artifact(artifact)
    published = service.publish(plan.request.actor_id, plan.request.scope_id, artifact.artifact_id)
    assert published.quality.status == "warning"
    assert repo.artifact_manifest(plan.request.scope_id, blocked.artifact_id) == blocked
    assert (
        service.publish(plan.request.actor_id, plan.request.scope_id, artifact.artifact_id)
        == published
    )
    reader = PostgresResearchReader(
        PostgresResearchPublications(database), FileResearchArchive(tmp_path)
    )
    query = ResearchQuery(
        actor_id=plan.request.actor_id,
        scope_id=plan.request.scope_id,
        publication_id=published.publication_id,
        content_version=published.content_version,
        kind="financial_statement",
    )
    page = ResearchService(service.auth, (), reader).records(query)
    assert page.records == (fixed,)
    with pytest.raises(DataFailure, match="unsupported"):
        ResearchService(service.auth, (), reader).records(query.model_copy(update={"as_of": NOW}))


def test_as_of_selects_revision_and_excludes_unknown_knowledge(database, tmp_path):
    req = request(scope_id=uuid4(), request_id=uuid4())
    provider = ControlledSource()
    provider.capability = provider.capability.model_copy(
        update={"supports_as_of": True, "knowledge_evidence": "fixture:complete-ledger"}
    )
    auth = StaticScopeAuthorizer({f"{req.actor_id}:{req.scope_id}": "manage"})
    plan = ResearchService(auth, (provider,)).preview(req)
    archive = FileResearchArchive(tmp_path)
    raw = archive.capture(
        plan,
        plan.partitions[0],
        ResearchRawPage(({"fixture": "revision-ledger"},), None, None),
        NOW,
    )

    def version(day, amount, disclosure):
        return statement(
            scope_id=req.scope_id,
            mapping_version=plan.capability.mapping_version,
            metrics=(metric(amount),),
            disclosure_date=disclosure,
            knowledge=KnowledgeTime(
                status="evidenced", known_at=day, evidence_reference="fixture:ledger"
            ),
        )

    early = version(datetime(2015, 8, 28, tzinfo=UTC), "100", date(2015, 8, 28))
    later = version(datetime(2016, 8, 27, tzinfo=UTC), "101", date(2016, 8, 27))
    unknown = statement(
        scope_id=req.scope_id,
        mapping_version=plan.capability.mapping_version,
        report_period=date(2015, 3, 31),
        metrics=(metric("50"),),
    )
    artifact = archive.standardize(plan, (raw,), (early, later, unknown), NOW)
    repo = PostgresResearchPublications(database)
    repo.register(plan)
    repo.append_raw(raw)
    repo.append_artifact(artifact)
    manifest = ResearchReleaseService(auth, archive, repo).publish(
        req.actor_id, req.scope_id, artifact.artifact_id
    )
    service = ResearchService(auth, (), PostgresResearchReader(repo, archive))
    base = dict(
        actor_id=req.actor_id,
        scope_id=req.scope_id,
        publication_id=manifest.publication_id,
        content_version=manifest.content_version,
        kind="financial_statement",
    )
    assert service.records(
        ResearchQuery(**base, as_of=datetime(2015, 9, 1, tzinfo=UTC))
    ).records == (early,)
    assert service.records(
        ResearchQuery(**base, as_of=datetime(2016, 9, 1, tzinfo=UTC))
    ).records == (later,)
    page = service.records(ResearchQuery(**base, limit=1))
    assert page.next_cursor
    assert service.records(ResearchQuery(**base, cursor=page.next_cursor, limit=1)).records == (
        later,
    )
    with pytest.raises(DataFailure, match="invalid_input"):
        service.records(ResearchQuery(**base, cursor=page.next_cursor, as_of=NOW))
    with pytest.raises(DataFailure, match="conflict"):
        service.records(ResearchQuery(**(base | {"content_version": "b" * 64})))


def test_sql_gate_cannot_publish_blocked_quality(database, tmp_path):
    service, repo, _, plan, artifact = release_setup(
        database, tmp_path, {"report_period": date(2014, 6, 30)}
    )
    quality = service.review(plan.request.actor_id, plan.request.scope_id, artifact.artifact_id)
    with psycopg.connect(database, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute(
                """INSERT INTO qtrade_data.research_publication
                (scope_id,publication_id,artifact_id,quality_id,manifest)
                VALUES(%s,%s,%s,%s,'{}'::jsonb)""",
                (plan.request.scope_id, uuid4(), artifact.artifact_id, quality.quality_id),
            )
    assert repo.raw_manifest(plan.request.scope_id, artifact.raw_batches[0].batch_id)


def test_release_role_and_scope_restrictions(database, tmp_path):
    service, _, _, plan, artifact = release_setup(database, tmp_path)
    with pytest.raises(DataFailure, match="forbidden"):
        service.publish(UUID(int=999), plan.request.scope_id, artifact.artifact_id)
    with pytest.raises(DataFailure, match="forbidden"):
        service.publish(plan.request.actor_id, UUID(int=999), artifact.artifact_id)


def test_public_http_manifest_and_query_use_real_isolated_persistence(
    database, tmp_path, monkeypatch
):
    import json

    from fastapi.testclient import TestClient

    service, repo, archive, plan, artifact = release_setup(database, tmp_path)
    publication = service.publish(
        plan.request.actor_id, plan.request.scope_id, artifact.artifact_id
    )
    monkeypatch.setenv("COLLECTOR_SERVICE_TOKEN", "x" * 32)
    monkeypatch.setenv("DATA_COLLECTION_DATABASE_URL", str(database))
    monkeypatch.setenv("COLLECTOR_ARCHIVE_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "COLLECTOR_SCOPE_GRANTS_JSON",
        json.dumps({f"{plan.request.actor_id}:{plan.request.scope_id}": "manage"}),
    )
    monkeypatch.delenv("COLLECTOR_AUTHORIZER_URL", raising=False)
    from qtrade_collector.main import create_app

    app = create_app()
    app.state.research_service = ResearchService(
        service.auth, (), PostgresResearchReader(repo, archive)
    )
    client = TestClient(app)
    headers = {
        "Authorization": "Bearer " + "x" * 32,
        "X-Data-Actor": str(plan.request.actor_id),
        "X-Data-Scope": str(plan.request.scope_id),
    }
    response = client.get(
        f"/v2/research/publications/{publication.publication_id}", headers=headers
    )
    assert (
        response.status_code == 200
        and response.json()["content_version"] == publication.content_version
    )
    body = ResearchQuery(
        actor_id=plan.request.actor_id,
        scope_id=plan.request.scope_id,
        publication_id=publication.publication_id,
        content_version=publication.content_version,
        kind="financial_statement",
    ).model_dump(mode="json")
    query = client.post("/v2/research/query", headers=headers, json=body)
    assert query.status_code == 200 and len(query.json()["records"]) == 1
    assert query.json()["records"][0]["metrics"][0]["original_value"] == "100"
    assert (
        client.post(
            "/v2/research/query", headers=headers, json=body | {"as_of": NOW.isoformat()}
        ).status_code
        == 400
    )
    assert (
        client.get(
            f"/v2/research/publications/{publication.publication_id}",
            headers=headers | {"X-Data-Scope": str(uuid4())},
        ).status_code
        == 403
    )


def test_sql_missing_json_fields_cannot_bypass_gate_with_null_checks(database, tmp_path):
    _, repo, _, plan, _ = release_setup(database, tmp_path)
    artifact_id, quality_id = uuid4(), uuid4()
    with psycopg.connect(database, autocommit=True) as conn:
        conn.execute(
            """INSERT INTO qtrade_data.research_artifact
            (scope_id,artifact_id,request_id,manifest) VALUES(%s,%s,%s,'{}'::jsonb)""",
            (plan.request.scope_id, artifact_id, plan.request.request_id),
        )
        conn.execute(
            """INSERT INTO qtrade_data.research_quality
            (scope_id,quality_id,artifact_id,result) VALUES(%s,%s,%s,'{}'::jsonb)""",
            (plan.request.scope_id, quality_id, artifact_id),
        )
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute(
                """INSERT INTO qtrade_data.research_publication
                (scope_id,publication_id,artifact_id,quality_id,manifest)
                VALUES(%s,%s,%s,%s,'{"quality":{},"artifact":{}}'::jsonb)""",
                (plan.request.scope_id, uuid4(), artifact_id, quality_id),
            )
