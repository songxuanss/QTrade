"""Research contract evidence is controlled fixture, not a live supplier/PIT claim."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError
from test_independent_collection import bar

from qtrade_data_contracts.collection import CollectionPublication, digest
from qtrade_data_contracts.research import (
    AdjustmentFactor,
    CorporateAction,
    FinancialMetric,
    FinancialStatement,
    KnowledgeTime,
    MembershipInterval,
    SecurityIdentityChange,
    UnitEvidence,
)


def common():
    return dict(
        scope_id=UUID(int=1),
        source="controlled.research",
        instrument_id="600519.SH",
        collected_at=datetime(2026, 10, 12, tzinfo=UTC),
        mapping_version="controlled.v1",
    )


def metric(value="0", **updates):
    return FinancialMetric(
        **(
            dict(
                field_id="yysr",
                name="营业收入",
                original_value=value,
                original_unit=UnitEvidence(),
                standard_value=None,
                standard_unit=UnitEvidence(),
                normalization="unresolved",
                missing_reason="not_reported" if value is None else None,
            )
            | updates
        )
    )


def statement(**updates):
    return FinancialStatement(
        **(
            common()
            | dict(
                statement="income",
                report_period=date(2015, 6, 30),
                disclosure_date=date(2015, 8, 28),
                disclosure_precision="day",
                period_semantics="unknown",
                metrics=(metric("8364520508.61"),),
            )
            | updates
        )
    )


def test_financial_revisions_and_recollection_are_distinct():
    original = statement()
    revision = statement(disclosure_date=date(2016, 8, 27), metrics=(metric("8364520508.62"),))
    assert original.report_period == revision.report_period
    assert original.content_version != revision.content_version
    recollected = statement(collected_at=original.collected_at + timedelta(days=1))
    assert original.content_version == recollected.content_version
    assert original.knowledge.known_at is None
    assert FinancialStatement.model_validate_json(original.model_dump_json()) == original
    assert original.metrics[0].original_value == Decimal("8364520508.61")
    assert original.metrics[0].standard_value is None


def test_unknown_and_zero_do_not_conflate():
    assert metric().original_value == Decimal(0)
    missing = metric(None)
    assert missing.original_value is None and missing.missing_reason == "not_reported"
    with pytest.raises(ValidationError):
        metric(None, missing_reason=None)
    with pytest.raises(ValidationError):
        metric("0", missing_reason="provider_null")


@pytest.mark.parametrize("value", [0.1, True, "NaN", "1e10"])
def test_decimal_precision_has_no_float_or_nan_coercion(value):
    with pytest.raises(ValidationError):
        metric(value)


def test_unit_conversion_requires_evidence_and_version():
    native = UnitEvidence(unit="CNY_10000", basis="documented", evidence_reference="fixture:units")
    standard = UnitEvidence(
        unit="CNY", basis="local_definition", evidence_reference="fixture:conversion"
    )
    converted = metric(
        "1.23",
        original_unit=native,
        standard_value="12300",
        standard_unit=standard,
        normalization="converted",
        conversion_version="scale.v1",
    )
    assert converted.standard_value == Decimal("12300")
    for updates in (
        dict(standard_value="0"),
        dict(normalization="identity", standard_value="0"),
        dict(
            original_unit=native,
            standard_unit=standard,
            standard_value="0",
            normalization="converted",
        ),
    ):
        with pytest.raises(ValidationError):
            metric(**updates)
    with pytest.raises(ValidationError):
        UnitEvidence(unit="CNY")


def test_disclosure_date_does_not_manufacture_knowledge():
    record = statement()
    assert record.disclosure_precision == "day" and record.knowledge.status == "unknown"
    known = KnowledgeTime(
        status="evidenced",
        known_at=datetime(2015, 8, 28, 10, tzinfo=UTC),
        evidence_reference="fixture:publication-ledger",
    )
    assert statement(knowledge=known).knowledge.known_at == known.known_at
    for updates in (
        dict(status="evidenced"),
        dict(known_at=record.collected_at),
        dict(status="evidenced", known_at=record.collected_at),
        dict(
            status="evidenced",
            known_at=record.collected_at.replace(tzinfo=None),
            evidence_reference="fixture",
        ),
    ):
        with pytest.raises(ValidationError):
            KnowledgeTime(**updates)


@pytest.mark.parametrize(
    "update",
    [
        {"schema_version": 1},
        {"schema_version": "2"},
        {"schema_version": True},
        {"instrument_id": "00700"},
        {"arbitrary": {}},
        {"disclosure_precision": "unknown"},
        {"period_semantics": "single_period"},
        {"metrics": ()},
        {"metrics": (metric(), metric())},
    ],
)
def test_financial_rejects_unsupported_or_ambiguous_structure(update):
    with pytest.raises(ValidationError):
        statement(**update)


def test_member_intervals_are_not_snapshot_times():
    record = MembershipInterval(
        **common(),
        universe_id="index:hs300",
        valid_from=date(2015, 6, 1),
        valid_until=date(2015, 12, 1),
        interval_evidence="fixture:membership",
    )
    assert MembershipInterval.model_validate_json(record.model_dump_json()) == record
    with pytest.raises(ValidationError):
        MembershipInterval(**(record.model_dump() | {"valid_until": record.valid_from}))


def test_identity_change_requires_previous_code():
    record = SecurityIdentityChange(
        **common(),
        security_id="fixture-security-1",
        change="code",
        previous_instrument_id="600005.SH",
        effective_date=date(2017, 2, 1),
        name="受控证券",
        change_evidence="fixture:identity",
    )
    assert SecurityIdentityChange.model_validate_json(record.model_dump_json()) == record
    with pytest.raises(ValidationError):
        SecurityIdentityChange(**(record.model_dump() | {"previous_instrument_id": None}))


def event(**updates):
    return CorporateAction(
        **(
            common()
            | dict(
                source_event_id="fixture-event-1",
                action="distribution",
                announcement_date=date(2026, 6, 22),
                ex_date=date(2026, 6, 26),
                record_date=date(2026, 6, 25),
                status="implemented",
                cash="280.242",
                cash_currency="CNY",
                cash_tax_basis="before_tax",
                basis_shares="10",
                bonus_shares="0",
                transfer_shares="0",
                terms_evidence="fixture:terms",
            )
            | updates
        )
    )


def test_event_identity_and_content_revision_are_separate():
    first, revision = event(), event(cash="280.243")
    assert first.event_identity == revision.event_identity
    assert first.content_version != revision.content_version
    assert first.payment_date is None and first.listing_date is None
    assert CorporateAction.model_validate_json(first.model_dump_json()) == first
    with pytest.raises(ValidationError):
        event(cash_currency=None)
    with pytest.raises(ValidationError):
        event(basis_shares="0")
    with pytest.raises(ValidationError):
        event(split_ratio="2")


def test_adjustment_factor_requires_base_and_algorithm():
    record = AdjustmentFactor(
        **common(),
        observation_date=date(2025, 9, 1),
        adjustment="forward",
        factor="0.975",
        base_date=date(2026, 10, 9),
        base_value="1",
        algorithm_version="fixture.v1",
        convention_evidence="fixture:adjustment",
    )
    assert AdjustmentFactor.model_validate_json(record.model_dump_json()) == record
    for update in ({"base_date": None}, {"algorithm_version": ""}, {"factor": "-1"}):
        with pytest.raises(ValidationError):
            AdjustmentFactor(**(record.model_dump() | update))


def test_v1_bar_serialization_frozen():
    assert (
        digest(bar().model_dump(mode="json"))
        == "73d9fef477ac288668cfb553ee255c8c02737b99020e31b64362427516bdc49e"
    )


def test_v1_manifest_remains_readable_with_frozen_digest():
    manifest = CollectionPublication.model_validate_json(
        (Path(__file__).parent / "fixtures/research-v1-manifest.json").read_bytes()
    )
    assert manifest.schema_version == 1
    assert (
        digest(manifest.model_dump(mode="json"))
        == "303a526ec2fdad4462cb74843bb9ca64fa0eb510db45b2645412599a3ff99b56"
    )


@pytest.mark.parametrize("action,ratio", [("split", "2"), ("consolidation", "0.5")])
def test_split_and_consolidation_terms_are_typed(action, ratio):
    record = CorporateAction(
        **common(),
        source_event_id="split-fixture",
        action=action,
        status="implemented",
        basis_shares="1",
        split_ratio=ratio,
        terms_evidence="fixture:split",
    )
    assert CorporateAction.model_validate_json(record.model_dump_json()) == record
    with pytest.raises(ValidationError):
        CorporateAction(**(record.model_dump() | {"cash": "1", "cash_currency": "CNY"}))
    with pytest.raises(ValidationError):
        CorporateAction(**(record.model_dump() | {"split_ratio": "1"}))


def test_rights_terms_have_price_and_do_not_mix_cash_distribution():
    record = CorporateAction(
        **common(),
        source_event_id="rights-fixture",
        action="rights",
        status="approved",
        basis_shares="10",
        rights_shares="3",
        rights_price="5.50",
        terms_evidence="fixture:rights",
    )
    assert CorporateAction.model_validate_json(record.model_dump_json()) == record
    for update in ({"rights_price": None}, {"bonus_shares": "1"}):
        with pytest.raises(ValidationError):
            CorporateAction(**(record.model_dump() | update))


def test_research_discriminator_rejects_unknown_category_and_version():
    import json

    from pydantic import TypeAdapter

    from qtrade_data_contracts.research import ResearchObservation

    adapter = TypeAdapter(ResearchObservation)
    assert adapter.validate_json(statement().model_dump_json()) == statement()
    for update in ({"kind": "bars"}, {"schema_version": 3}):
        with pytest.raises(ValidationError):
            adapter.validate_json(json.dumps(statement().model_dump(mode="json") | update))
