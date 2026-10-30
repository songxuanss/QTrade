"""Calendar-aware stage attribution; controlled evidence is not live readiness."""

import json
from datetime import UTC, date, datetime, time, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from qtrade_data_contracts.freshness import (
    FreshnessCheckpoint,
    FreshnessEvidence,
    FreshnessExpectation,
    FreshnessKey,
    FreshnessResult,
)
from qtrade_data_core.default_range import natural_year_range
from qtrade_data_core.freshness import daily_expectation, evaluate
from qtrade_data_core.ports import DataFailure

NOW = datetime(2026, 10, 14, 10, tzinfo=UTC)
DAY = date(2026, 10, 14)
RELEASE = datetime(2026, 10, 14, 9, 10, tzinfo=UTC)


def key(**changes):
    return FreshnessKey(
        **(
            dict(
                scope_id=uuid4(),
                source="controlled.daily",
                instrument_id="601288.SH",
                dataset_kind="bars",
                period="1d",
                adjustment="unadjusted",
                date_axis="observation_date",
                mapping_version="controlled.v1",
            )
            | changes
        )
    )


def point(day=DAY):
    return FreshnessCheckpoint(complete_through=day, observed_at=NOW, evidence_sha256="a" * 64)


def expected(**changes):
    return FreshnessExpectation(
        **(
            dict(
                complete_through=DAY,
                available_at=RELEASE,
                timezone="Asia/Shanghai",
                rule_version="controlled.daily.v1",
                basis="fixture calendar",
            )
            | changes
        )
    )


def evidence(k, **changes):
    return FreshnessEvidence(**(dict(key=k, source_health="available") | changes))


@pytest.mark.parametrize(
    "stage,values",
    [
        ("source", {"source": point(DAY - timedelta(days=1))}),
        ("collection", {"source": point()}),
        ("collection", {"source": point(), "collected": point(DAY - timedelta(days=1))}),
        ("publication", {"source": point(), "collected": point()}),
        (
            "publication",
            {"source": point(), "collected": point(), "published": point(DAY - timedelta(days=1))},
        ),
    ],
)
def test_lag_attributes_measured_stage_without_changing_expectation(stage, values):
    k = key()
    report = evaluate(k, expected(), evidence(k, **values), NOW)
    assert report.status == "late" and report.stage == stage
    assert report.overdue_seconds == 3000
    assert report.expectation.complete_through == DAY
    assert (
        report.action
        == {
            "source": "check_source",
            "collection": "collect_missing",
            "publication": "check_publication",
        }[stage]
    )
    assert FreshnessResult.model_validate_json(report.model_dump_json()) == report


def test_published_complete_period_current_with_explicit_unknown_source_observation():
    k = key()
    report = evaluate(k, expected(), evidence(k, source_health="unknown", published=point()), NOW)
    assert report.status == "current" and report.overdue_seconds == 0
    assert report.evidence.source_health == "unknown" and report.evidence.source is None


@pytest.mark.parametrize("published", [None, point()])
def test_source_outage_not_hidden_by_cache(published):
    k = key()
    report = evaluate(
        k, expected(), evidence(k, source_health="unavailable", published=published), NOW
    )
    assert report.status == "unavailable" and report.stage == "source"
    assert report.action == "check_source" and report.overdue_seconds == 0


def test_unknown_source_cannot_be_blame_for_local_collection():
    k = key()
    report = evaluate(k, expected(), evidence(k), NOW)
    assert report.status == "unknown" and report.reason == "source_unknown"
    assert report.overdue_seconds == 0


@pytest.mark.parametrize(
    "kind,axis,field,period",
    [
        ("factors", "observation_date", "roe_ttm", "1d"),
        ("financial_statement", "report_period", None, None),
        ("corporate_action", "event_date", None, None),
    ],
)
def test_sparse_and_unknown_frequency_never_inherit_daily_price_rule(kind, axis, field, period):
    k = key(dataset_kind=kind, date_axis=axis, field=field, period=period, adjustment=None)
    report = evaluate(k, None, evidence(k, published=point()), NOW)
    assert report.status == "unknown" and report.reason == "rule_unknown"
    with pytest.raises(DataFailure, match="unsupported"):
        daily_expectation(
            k,
            natural_year_range(
                NOW, (DAY,), timezone="Asia/Shanghai", release_time=time(17, 10), basis="fixture"
            ),
        )


@pytest.mark.parametrize(
    "seconds,status,overdue", [(3599, "within_grace", 0), (3600, "late", 0), (3601, "late", 1)]
)
def test_grace_boundary_keeps_recovery_stage(seconds, status, overdue):
    k = key()
    at = RELEASE + timedelta(seconds=seconds)
    raw = point().model_copy(update={"observed_at": RELEASE})
    report = evaluate(k, expected(grace_seconds=3600), evidence(k, source=raw), at)
    assert (report.status, report.overdue_seconds, report.stage) == (status, overdue, "collection")


def test_holiday_and_pre_release_expectation_uses_calendar_not_local_latest():
    k = key()
    # Authoritative fixture calendar excludes a weekday holiday, October 1.
    days = (date(2026, 9, 30), date(2026, 10, 9))
    for at in [datetime(2026, 10, 4, 10, tzinfo=UTC), datetime(2026, 10, 9, 8, tzinfo=UTC)]:
        window = natural_year_range(
            at,
            days,
            timezone="Asia/Shanghai",
            release_time=time(17, 10),
            basis="fixture holiday/release",
        )
        exp = daily_expectation(k, window)
        assert exp.complete_through == date(2026, 9, 30)
        pub = FreshnessCheckpoint(
            complete_through=date(2026, 9, 30), observed_at=at, evidence_sha256="b" * 64
        )
        assert evaluate(k, exp, evidence(k, published=pub), at).status == "current"
    after = natural_year_range(
        datetime(2026, 10, 9, 10, tzinfo=UTC),
        days,
        timezone="Asia/Shanghai",
        release_time=time(17, 10),
        basis="fixture",
    )
    assert daily_expectation(k, after).complete_through == date(2026, 10, 9)


@pytest.mark.parametrize(
    "change",
    [
        {"scope_id": uuid4()},
        {"source": "other"},
        {"instrument_id": "600519.SH"},
        {"mapping_version": "other.v2"},
    ],
)
def test_foreign_or_different_semantics_evidence_rejected(change):
    k = key()
    other = k.model_copy(update=change)
    with pytest.raises(DataFailure, match="conflict"):
        evaluate(k, expected(), evidence(other), NOW)


@pytest.mark.parametrize(
    "change",
    [
        {"period": None},
        {"field": "pe_ttm"},
        {"date_axis": "report_period"},
        {"instrument_id": "bad"},
    ],
)
def test_invalid_daily_identity_rejected(change):
    with pytest.raises(ValidationError):
        key(**change)


@pytest.mark.parametrize(
    "change",
    [
        {"grace_seconds": True},
        {"grace_seconds": -1},
        {"grace_seconds": 604801},
        {"timezone": "Unknown/Zone"},
        {"complete_through": DAY + timedelta(days=1)},
    ],
)
def test_invalid_release_rule_rejected(change):
    with pytest.raises(ValidationError):
        expected(**change)


@pytest.mark.parametrize("case", ["checkpoint", "expectation", "naive"])
def test_untrusted_future_or_naive_clock_rejected(case):
    k = key()
    with pytest.raises((DataFailure, ValueError)):
        evaluate(
            k,
            expected(available_at=NOW + timedelta(seconds=1))
            if case == "expectation"
            else expected(),
            evidence(
                k, source=point().model_copy(update={"observed_at": NOW + timedelta(seconds=1)})
            )
            if case == "checkpoint"
            else evidence(k),
            NOW.replace(tzinfo=None) if case == "naive" else NOW,
        )


@pytest.mark.parametrize(
    "change",
    [
        {"scope_id": uuid4()},
        {"status": "current"},
        {"stage": "publication"},
        {"action": "none"},
        {"overdue_seconds": -1},
        {"schema_version": True},
    ],
)
def test_wire_cannot_misrepresent_scoped_state(change):
    k = key()
    report = evaluate(k, expected(), evidence(k, source=point()), NOW)
    body = report.model_dump(mode="json") | change

    with pytest.raises(ValidationError):
        FreshnessResult.model_validate_json(json.dumps(body, default=str))


def test_old_adjustment_evidence_cannot_claim_current_raw_prices():
    k = key()
    other = k.model_copy(update={"adjustment": "forward"})
    with pytest.raises(DataFailure, match="conflict"):
        evaluate(k, expected(), evidence(other, published=point()), NOW)


@pytest.mark.parametrize("mutation", ["missing_publication", "grace_clock", "missing_source"])
def test_wire_state_requires_matching_checkpoint_and_clock(mutation):
    k = key()
    report = evaluate(k, expected(), evidence(k, source=point()), NOW)
    body = report.model_dump(mode="json")
    if mutation == "missing_publication":
        body.update(
            status="current",
            stage="none",
            reason="published_current",
            action="none",
            overdue_seconds=0,
        )
    elif mutation == "grace_clock":
        body.update(status="within_grace", overdue_seconds=0)
    else:
        body["evidence"]["source"] = None
    with pytest.raises(ValidationError):
        FreshnessResult.model_validate_json(json.dumps(body))


def test_future_data_date_cannot_forge_current_checkpoint():
    k = key()
    with pytest.raises(DataFailure, match="unverified"):
        evaluate(k, expected(), evidence(k, published=point(DAY + timedelta(days=1))), NOW)


def test_exported_result_schema_accepts_actual_decision_and_rejects_unknown_version():
    from pathlib import Path

    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import ValidationError as SchemaValidationError

    path = (
        Path(__file__).resolve().parents[1]
        / "packages/data-contracts/schemas/v1/FreshnessResult.schema.json"
    )
    schema = json.loads(path.read_text())
    Draft202012Validator.check_schema(schema)
    k = key()
    body = evaluate(k, expected(), evidence(k, published=point()), NOW).model_dump(mode="json")
    validator = Draft202012Validator(schema)
    validator.validate(body)
    with pytest.raises(SchemaValidationError):
        validator.validate(body | {"schema_version": 2})
    with pytest.raises(SchemaValidationError):
        validator.validate(body | {"provider_url": "forbidden extra field"})
