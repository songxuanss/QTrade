from pathlib import Path

import pytest

from qtrade_postgres.migrate import MigrationError, apply, discover


def test_migrations_have_stable_order_and_content_hash():
    migrations = discover()
    assert [item.version for item in migrations] == list(range(1, len(migrations) + 1))
    assert migrations[0].name == "identity"
    assert len(migrations[0].checksum) == 64
    assert migrations[13].name == "tick_raw_completion"
    assert migrations[14].name == "alert_escalation_state"
    assert migrations[15].name == "alert_evaluation_queue"
    assert migrations[16].name == "notification_delivery"
    assert migrations[17].name == "alert_rule_notification_targets"
    assert migrations[18].name == "alert_event_context_snapshot"
    assert migrations[19].name == "alert_job_context_snapshot"
    assert migrations[20].name == "alert_disposition_history"
    assert migrations[21].name == "strategies"
    assert migrations[22].name == "strategy_dependency_manifest_optional"
    assert migrations[23].name == "strategy_lifecycle"
    assert migrations[24].name == "backtest_runs"


@pytest.mark.parametrize("files", [[], ["0002_gap.sql"], ["0001_a.sql", "0001_b.sql"], ["bad.sql"]])
def test_invalid_migration_catalog_fails_before_connecting(tmp_path, files):
    for filename in files:
        (tmp_path / filename).write_text("SELECT 1;")
    with pytest.raises(MigrationError):
        discover(tmp_path)


@pytest.mark.parametrize(
    "schema", ["public;drop schema public", "BadName", "../schema", "", "x" * 64]
)
def test_schema_name_rejected_before_database_connection(schema):
    with pytest.raises(MigrationError, match="invalid schema"):
        apply("never connect", schema=schema)


def test_migration_snapshot_is_not_accidentally_edited():
    import hashlib

    path = (
        Path(__file__).parents[1]
        / "adapters/postgres/src/qtrade_postgres/migrations/0001_identity.sql"
    )
    assert (
        hashlib.sha256(path.read_bytes()).hexdigest()
        == "f0a6ed4351f74b81b104c724af1856f59700be87eadfe1dfbeb4a490defa80fa"
    )
