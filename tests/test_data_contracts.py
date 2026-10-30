"""Portable wire validation protects snapshots and real completion semantics."""

import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from qtrade_data_contracts import (
    CONTRACTS,
    BarPage,
    CampaignPlan,
    CampaignProgress,
    CatalogQuery,
    CollectionRange,
    CollectionSelection,
    DataBar,
    DataError,
    MemberCollectionPlan,
    PublicationManifest,
    ReferenceIdentity,
    SourceCapability,
    UniverseContent,
    UniverseMember,
    UniverseSnapshot,
)

SCOPE = UUID("3ce6159a-e706-46c9-b52d-96fc62f90236")
START = datetime(2025, 9, 1, tzinfo=timezone(timedelta(hours=8)))
ROOT = Path(__file__).resolve().parents[1]


def examples():
    base = {"schema_version": 1, "scope_id": SCOPE}
    identity = ReferenceIdentity(
        instrument_id="601288.SH", instrument_kind="stock", name="农业银行", exchange="SH"
    )
    member = UniverseMember(instrument=identity, affiliations=("自定义",))
    content = UniverseContent(
        **base,
        universe_id="custom:bank",
        version=1,
        name="我的股票池",
        category="custom",
        source="user_selection",
        observed_at=START,
        membership_basis="current_snapshot",
        market_scope=("SH",),
        classification_version="user-selection.v1",
        members=(member,),
    )
    snapshot = UniverseSnapshot.freeze(uuid4(), content)
    selection = CollectionSelection(
        **base,
        request_id=uuid4(),
        actor_id=uuid4(),
        correlation_id=uuid4(),
        snapshot_ids=(snapshot.snapshot_id,),
        source="mairui.a-share.bar",
        authorization_id="mairui-default",
        granularity="1d",
        start=START,
        end=START + timedelta(days=10),
        transformation_version="mairui.unadjusted-bars.v1",
    )
    plan = CampaignPlan(
        **base,
        plan_id=uuid4(),
        selection=selection,
        coverage_version="0" * 64,
        created_at=START,
        expires_at=START + timedelta(minutes=15),
        planned_parts=2,
        members=(
            MemberCollectionPlan(
                instrument=identity,
                affiliations=(),
                existing_ranges=(),
                submit_ranges=(CollectionRange(start=selection.start, end=selection.end),),
                planned_parts=2,
            ),
        ),
    )
    bar = DataBar(
        **base,
        dataset_id=uuid4(),
        instrument_id=identity.instrument_id,
        granularity="1d",
        timestamp=START,
        open="6.500000000000001",
        high="7",
        low="6",
        close="6.7",
        volume="100000",
        batch_ids=(uuid4(),),
        quality="unverified",
    )
    return {
        UniverseSnapshot: snapshot,
        CollectionSelection: selection,
        CampaignPlan: plan,
        CampaignProgress: CampaignProgress(
            **base,
            campaign_id=uuid4(),
            plan_id=plan.plan_id,
            version=1,
            state="running",
            planned_parts=2,
            expanded_parts=1,
            collected_parts=1,
            failed_parts=0,
            member_count=1,
            viewable_members=0,
            no_observation_members=0,
            updated_at=START,
        ),
        SourceCapability: SourceCapability(
            **base,
            source=selection.source,
            authorization_id=selection.authorization_id,
            instrument_kinds=("stock",),
            granularities=("1d",),
            verification="unverified",
            observed_at=START,
        ),
        CatalogQuery: CatalogQuery(
            **base,
            instrument_id=identity.instrument_id,
            granularity="1d",
            start=START,
            end=selection.end,
        ),
        DataBar: bar,
        BarPage: BarPage(**base, bars=(bar,)),
        PublicationManifest: PublicationManifest(
            **base,
            dataset_id=bar.dataset_id,
            dataset_version=1,
            source=selection.source,
            instrument_ids=(identity.instrument_id,),
            granularity="1d",
            start=START,
            end=selection.end,
            record_count=1,
            batch_ids=bar.batch_ids,
            content_sha256="a" * 64,
            transformation_version=selection.transformation_version,
            published_at=START,
            quality="unverified",
        ),
        DataError: DataError(**base, code="forbidden", correlation_id=uuid4()),
    }


@pytest.mark.parametrize("model", CONTRACTS)
def test_schema_roundtrip_and_drift(model):
    example = examples()[model]
    schema = json.loads(
        (ROOT / f"packages/data-contracts/schemas/v1/{model.__name__}.schema.json").read_text()
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(
        example.model_dump(mode="json")
    )
    assert model.model_validate_json(example.model_dump_json()) == example
    generated = model.model_json_schema(mode="serialization")
    generated["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    generated["$id"] = f"urn:qtrade:data:v1:{model.__name__}"
    assert generated == schema


@pytest.mark.parametrize("value", [True, "1", 2, None])
def test_explicit_version_and_no_credential_fields(value):
    body = examples()[CollectionSelection].model_dump(mode="json")
    body["schema_version"] = value
    with pytest.raises(ValidationError):
        CollectionSelection.model_validate_json(json.dumps(body))
    body["schema_version"] = 1
    body["licence"] = "must-not-be-in-public-contract"
    with pytest.raises(ValidationError):
        CollectionSelection.model_validate_json(json.dumps(body))


def test_snapshot_tampering_duplicates_and_immutability():
    snapshot = examples()[UniverseSnapshot]
    body = snapshot.model_dump(mode="json")
    body["members"][0]["instrument"]["name"] = "changed"
    with pytest.raises(ValidationError, match="hash mismatch"):
        UniverseSnapshot.model_validate_json(json.dumps(body))
    with pytest.raises(ValidationError):
        snapshot.version = 2
    with pytest.raises(ValidationError, match="unique"):
        UniverseContent(
            **snapshot.model_dump(exclude={"snapshot_id", "content_sha256", "members"}),
            members=(snapshot.members[0], snapshot.members[0]),
        )


def test_current_members_cannot_claim_historical_interval():
    body = examples()[UniverseSnapshot].model_dump(exclude={"snapshot_id", "content_sha256"})
    body.update(effective_start=START, effective_end=START + timedelta(days=1))
    with pytest.raises(ValidationError, match="historical interval"):
        UniverseContent(**body)


@pytest.mark.parametrize("value", [6.5, True, "NaN", "Infinity", "1e4", "0.0000000000000000001"])
def test_prices_reject_lossy_or_unbounded_values(value):
    body = examples()[DataBar].model_dump()
    body["open"] = value
    with pytest.raises(ValidationError):
        DataBar(**body)


def test_exact_data_and_legacy_ids_survive_json():
    bar = examples()[DataBar]
    assert bar.instrument_id == "601288.SH" and bar.scope_id == SCOPE
    assert bar.timestamp.tzinfo is UTC
    assert DataBar.model_validate_json(bar.model_dump_json()).open == Decimal("6.500000000000001")
    assert '"open":"6.500000000000001"' in bar.model_dump_json()
    with pytest.raises(ValidationError):
        ReferenceIdentity(
            instrument_id="000001.SH", instrument_kind="index", name="上证指数", exchange="SH"
        )
    assert (
        ReferenceIdentity(
            instrument_id="index:000001.SH", instrument_kind="index", name="上证指数", exchange="SH"
        ).instrument_id
        != "000001.SH"
    )


def test_partial_expansion_or_raw_only_cannot_be_complete():
    body = examples()[CampaignProgress].model_dump()
    body["state"] = "completed"
    with pytest.raises(ValidationError, match="completion"):
        CampaignProgress(**body)
    body.update(expanded_parts=2, collected_parts=2)
    with pytest.raises(ValidationError, match="completion"):
        CampaignProgress(**body)
    body["viewable_members"] = 1
    assert CampaignProgress(**body).state == "completed"


def test_plan_detects_scope_and_wrong_parts():
    body = examples()[CampaignPlan].model_dump()
    body["scope_id"] = uuid4()
    with pytest.raises(ValidationError, match="scope"):
        CampaignPlan(**body)
    body["scope_id"] = SCOPE
    body["planned_parts"] = 1
    with pytest.raises(ValidationError, match="parts mismatch"):
        CampaignPlan(**body)


def test_page_scope_and_cursor_bounds():
    models = examples()
    with pytest.raises(ValidationError, match="scope mismatch"):
        BarPage(schema_version=1, scope_id=uuid4(), bars=(models[DataBar],))
    body = models[CatalogQuery].model_dump()
    body["cursor"] = "x" * 2049
    with pytest.raises(ValidationError):
        CatalogQuery(**body)
