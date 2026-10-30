"""Real isolated PG metadata/lineage tests with controlled supplier records."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict
from test_research_evidence import NOW, evidence

from qtrade_data_core.ports import DataFailure
from qtrade_data_postgres.migrate import apply
from qtrade_data_postgres.research import PostgresResearchEvidence


class PrivateDatabaseUrl(str):
    def __repr__(self):
        return "<private isolated test database>"


@pytest.fixture
def database():
    url = os.getenv("QTRADE_TEST_RESEARCH_DATABASE_URL")
    if not url:
        pytest.skip("isolated research PostgreSQL not configured")
    assert conninfo_to_dict(url)["dbname"].startswith("research_test_"), (
        "Use a dedicated disposable research database"
    )
    url = PrivateDatabaseUrl(url)
    apply(url)
    return url


def test_raw_and_standard_metadata_restart_idempotency(database, tmp_path):
    archive, plan, _, raw, record = evidence(tmp_path)
    repository = PostgresResearchEvidence(database)
    repository.register(plan)
    assert repository.stage(raw.scope_id, raw.request_id) == "registered"
    repository.append_raw(raw)
    assert repository.stage(raw.scope_id, raw.request_id) == "raw_persisted"
    restarted = PostgresResearchEvidence(database)
    assert restarted.raw_manifest(raw.scope_id, raw.batch_id) == raw
    assert archive.raw(restarted.raw_manifest(raw.scope_id, raw.batch_id))[0]["amount"] == "100"
    artifact = archive.standardize(plan, (raw,), (record,), NOW)
    restarted.append_artifact(artifact)
    restarted.append_artifact(artifact)
    restarted.append_raw(raw)
    assert restarted.stage(raw.scope_id, raw.request_id) == "standard_persisted"
    assert restarted.artifact_manifest(raw.scope_id, artifact.artifact_id) == artifact
    assert archive.records(restarted.artifact_manifest(raw.scope_id, artifact.artifact_id)) == (
        record,
    )
    with psycopg.connect(database) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM qtrade_data.research_artifact_raw "
                "WHERE scope_id=%s AND artifact_id=%s",
                (artifact.scope_id, artifact.artifact_id),
            ).fetchone()[0]
            == 1
        )


def test_cross_scope_and_manifest_replacement_refused(database, tmp_path):
    _, plan, _, raw, _ = evidence(tmp_path)
    repository = PostgresResearchEvidence(database)
    repository.register(plan)
    repository.append_raw(raw)
    with pytest.raises(DataFailure, match="not_found"):
        repository.raw_manifest(UUID(int=999), raw.batch_id)
    with pytest.raises(DataFailure, match="conflict"):
        repository.append_raw(raw.model_copy(update={"raw_record_count": 99}))
    assert repository.raw_manifest(raw.scope_id, raw.batch_id) == raw


def test_artifact_requires_persisted_raw_lineage(database, tmp_path):
    archive, plan, _, raw, record = evidence(tmp_path)
    repository = PostgresResearchEvidence(database)
    repository.register(plan)
    artifact = archive.standardize(plan, (raw,), (record,), NOW)
    with pytest.raises(DataFailure, match="conflict"):
        repository.append_artifact(artifact)
    repository.append_raw(raw)
    repository.append_artifact(artifact)


def test_sql_cannot_mutate_evidence_or_frozen_plan(database, tmp_path):
    archive, plan, _, raw, record = evidence(tmp_path)
    repository = PostgresResearchEvidence(database)
    repository.register(plan)
    repository.append_raw(raw)
    artifact = archive.standardize(plan, (raw,), (record,), NOW)
    repository.append_artifact(artifact)
    with psycopg.connect(database, autocommit=True) as conn:
        for sql, args in (
            (
                "UPDATE qtrade_data.research_raw_batch SET manifest='{}'::jsonb "
                "WHERE scope_id=%s AND batch_id=%s",
                (raw.scope_id, raw.batch_id),
            ),
            (
                "DELETE FROM qtrade_data.research_artifact WHERE scope_id=%s AND artifact_id=%s",
                (artifact.scope_id, artifact.artifact_id),
            ),
            (
                "UPDATE qtrade_data.research_run SET plan='{}'::jsonb "
                "WHERE scope_id=%s AND request_id=%s",
                (raw.scope_id, raw.request_id),
            ),
        ):
            with pytest.raises(psycopg.errors.RaiseException):
                conn.execute(sql, args)
    assert repository.raw_manifest(raw.scope_id, raw.batch_id) == raw


def test_concurrent_append_is_idempotent_and_migrations_locked(database, tmp_path):
    _, plan, _, raw, _ = evidence(tmp_path)

    def append(_):
        repo = PostgresResearchEvidence(database)
        repo.register(plan)
        repo.append_raw(raw)

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(append, range(3)))
    assert apply(database) == ()
    with psycopg.connect(database) as conn:
        row = conn.execute(
            "SELECT manifest FROM qtrade_data.research_raw_batch WHERE scope_id=%s AND batch_id=%s",
            (raw.scope_id, raw.batch_id),
        ).fetchone()
        assert json.loads(raw.model_dump_json()) == row[0]
