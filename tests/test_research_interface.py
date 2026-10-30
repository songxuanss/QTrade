"""Controlled adapter HTTP/port contracts. No supplier live integration claimed."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_research_contracts import statement

from qtrade_data_contracts.collection import digest
from qtrade_data_contracts.planning import CollectionCostEstimate
from qtrade_data_contracts.research import KnowledgeTime
from qtrade_data_contracts.research_transport import (
    ResearchCapability,
    ResearchPartition,
    ResearchPlan,
    ResearchPlanRequest,
    ResearchQuery,
    ResearchRecordPage,
)
from qtrade_data_core.ports import DataFailure
from qtrade_data_core.research import (
    ResearchPublicationReference,
    ResearchRawPage,
    ResearchService,
)
from qtrade_data_http.client import StaticScopeAuthorizer

ACTOR, SCOPE, READER, PUBLICATION = (UUID(int=i) for i in (10, 20, 30, 40))


class ControlledSource:
    def estimate(self, request, parts):
        return CollectionCostEstimate(
            calls_min=0,
            calls_max=2 * parts,
            credits_min=0,
            credits_max=2 * parts,
            accounting="internal_credit_safeguard",
            includes_retries=False,
            basis="fixture: two pages per partition, retries excluded",
        )

    def __init__(self):
        self.capability = ResearchCapability(
            source="controlled.research",
            name="受控财务fixture",
            kind="financial_statement",
            configured=True,
            evidence="fixture",
            markets=("CN",),
            instrument_kinds=("stock",),
            date_axes=("report_period",),
            timezone="Asia/Shanghai",
            mapping_version="fixture.v1",
            history_start=date(2015, 1, 1),
        )
        self.fetch_calls = 0

    def capabilities(self):
        return (self.capability,)

    def partitions(self, request):
        for identifier in request.instrument_ids:
            yield ResearchPartition(instrument_id=identifier, start=request.start, end=request.end)

    def fetch(self, request, partition, cursor):
        self.fetch_calls += 1
        if cursor not in {None, "page-2"}:
            raise DataFailure("invalid_input")
        return ResearchRawPage(
            ({"amount": "0", "fixture": True},), cursor, "page-2" if cursor is None else None
        )

    def normalize(self, request, page, collected_at):
        return (statement(scope_id=request.scope_id, collected_at=collected_at),)


class ControlledReader:
    def __init__(self, capability):
        self.reference_value = ResearchPublicationReference(
            SCOPE, PUBLICATION, "a" * 64, capability
        )
        self.records = (
            statement(scope_id=SCOPE),
            statement(scope_id=SCOPE, disclosure_date=date(2016, 8, 27)),
        )

    def reference(self, scope, publication):
        if (
            scope != self.reference_value.scope_id
            or publication != self.reference_value.publication_id
        ):
            raise DataFailure("not_found")
        return self.reference_value

    def page(self, query):
        offset = 0 if query.cursor is None else 1 if query.cursor == "page-2" else -1
        if offset == -1:
            raise DataFailure("invalid_input")
        values = self.records[offset : offset + query.limit]
        return ResearchRecordPage(
            scope_id=query.scope_id,
            publication_id=query.publication_id,
            content_version=query.content_version,
            kind=query.kind,
            cursor=query.cursor,
            next_cursor="page-2" if offset + len(values) < len(self.records) else None,
            records=values,
            page_sha256=digest([r.model_dump(mode="json") for r in values]),
        )


def request(**updates):
    return ResearchPlanRequest(
        **(
            dict(
                actor_id=ACTOR,
                scope_id=SCOPE,
                request_id=UUID(int=50),
                correlation_id=UUID(int=60),
                source="controlled.research",
                kind="financial_statement",
                instrument_ids=("600519.SH",),
                date_axis="report_period",
                start=date(2015, 1, 1),
                end=date(2016, 1, 1),
            )
            | updates
        )
    )


def query(**updates):
    return ResearchQuery(
        **(
            dict(
                actor_id=ACTOR,
                scope_id=SCOPE,
                publication_id=PUBLICATION,
                content_version="a" * 64,
                kind="financial_statement",
                limit=1,
            )
            | updates
        )
    )


@pytest.fixture
def setup():
    auth = StaticScopeAuthorizer({f"{ACTOR}:{SCOPE}": "manage", f"{READER}:{SCOPE}": "read"})
    provider = ControlledSource()
    reader = ControlledReader(provider.capability)
    return ResearchService(auth, (provider,), reader), provider, reader


def test_discovery_and_plan_are_pure_and_json_round_trip(setup):
    service, provider, _ = setup
    assert service.catalog(ACTOR, SCOPE)[0].evidence == "fixture"
    plan = service.preview(request())
    assert ResearchPlan.model_validate_json(plan.model_dump_json()) == plan
    assert provider.fetch_calls == 0
    assert plan.estimated_calls_min == 1 and plan.estimated_calls_max is None
    assert service.preview(request()).plan_sha256 == plan.plan_sha256
    assert service.preview(request(end=date(2017, 1, 1))).plan_sha256 != plan.plan_sha256


@pytest.mark.parametrize(
    "updates,code",
    [
        ({"actor_id": READER}, "forbidden"),
        ({"scope_id": UUID(int=999)}, "forbidden"),
        ({"start": date(2014, 1, 1)}, "unsupported"),
        ({"date_axis": "event_date"}, "unsupported"),
        ({"kind": "corporate_action"}, "unsupported"),
        ({"source": "unconnected"}, "unsupported"),
        ({"instrument_ids": ("stock:HK:XHKG:00700",)}, "unsupported"),
    ],
)
def test_plan_rejections_do_not_fetch(setup, updates, code):
    service, provider, _ = setup
    with pytest.raises(DataFailure, match=code):
        service.preview(request(**updates))
    assert provider.fetch_calls == 0


def test_disabled_source_is_not_a_fake_usable_entry(setup):
    service, provider, _ = setup
    provider.capability = provider.capability.model_copy(
        update={"configured": False, "unblock_condition": "接通真实适配器"}
    )
    assert not service.catalog(ACTOR, SCOPE)[0].configured
    with pytest.raises(DataFailure, match="unavailable"):
        service.preview(request())


@pytest.mark.parametrize("bad", ["gap", "overlap", "wrong_instrument"])
def test_adapter_partitions_must_cover_exact_selected_range(setup, bad):
    service, provider, _ = setup

    def parts(req):
        if bad == "wrong_instrument":
            yield ResearchPartition(instrument_id="601288.SH", start=req.start, end=req.end)
        else:
            yield ResearchPartition(
                instrument_id="600519.SH", start=req.start, end=date(2015, 6, 1)
            )
            yield ResearchPartition(
                instrument_id="600519.SH",
                start=date(2015, 6, 2) if bad == "gap" else date(2015, 5, 31),
                end=req.end,
            )

    provider.partitions = parts
    with pytest.raises(DataFailure, match="unverified"):
        service.preview(request())


def test_provider_paging_port_and_normalization_are_typed(setup):
    _, provider, _ = setup
    req = request()
    part = next(provider.partitions(req))
    first = provider.fetch(req, part, None)
    second = provider.fetch(req, part, first.next_cursor)
    assert first.next_cursor == "page-2" and second.next_cursor is None
    assert (
        provider.normalize(req, first, datetime(2026, 10, 12, tzinfo=UTC))[0].kind
        == "financial_statement"
    )


def test_frozen_version_pagination_and_unknown_knowledge(setup):
    service, _, _ = setup
    first = service.records(query())
    second = service.records(query(cursor=first.next_cursor))
    assert first.next_cursor == "page-2" and second.next_cursor is None
    assert first.records[0].disclosure_date != second.records[0].disclosure_date
    assert ResearchRecordPage.model_validate_json(first.model_dump_json()) == first
    with pytest.raises(DataFailure, match="unsupported"):
        service.records(query(as_of=datetime(2015, 9, 1, tzinfo=UTC)))
    for updates, code in (
        ({"scope_id": UUID(int=999)}, "forbidden"),
        ({"content_version": "b" * 64}, "conflict"),
        ({"kind": "corporate_action"}, "unsupported"),
        ({"cursor": "other-version-page"}, "invalid_input"),
    ):
        with pytest.raises(DataFailure, match=code):
            service.records(query(**updates))


def test_as_of_defense_rejects_adapter_future_or_unknown_records(setup):
    service, provider, reader = setup
    cap = provider.capability.model_copy(
        update={"supports_as_of": True, "knowledge_evidence": "fixture:ledger"}
    )
    reader.reference_value = replace(reader.reference_value, capability=cap)
    as_of = datetime(2015, 9, 1, tzinfo=UTC)
    with pytest.raises(DataFailure, match="unverified"):
        service.records(query(as_of=as_of))
    reader.records = (
        statement(
            scope_id=SCOPE,
            knowledge=KnowledgeTime(
                status="evidenced", known_at=as_of, evidence_reference="fixture:ledger"
            ),
        ),
    )
    assert len(service.records(query(as_of=as_of)).records) == 1
    with pytest.raises(DataFailure, match="unverified"):
        service.records(query(as_of=datetime(2015, 8, 1, tzinfo=UTC)))


def test_page_checksum_and_cross_scope_record_rejected(setup):
    service, _, _ = setup
    page = service.records(query())
    with pytest.raises(ValidationError):
        ResearchRecordPage(**(page.model_dump() | {"page_sha256": "b" * 64}))
    with pytest.raises(ValidationError):
        ResearchRecordPage(**(page.model_dump() | {"scope_id": UUID(int=999)}))


def test_actual_collector_routes_auth_roles_versions_and_pages(monkeypatch, tmp_path, setup):
    monkeypatch.setenv("COLLECTOR_SERVICE_TOKEN", "x" * 32)
    monkeypatch.setenv("DATA_COLLECTION_DATABASE_URL", "postgresql://unused/fixture")
    monkeypatch.setenv("COLLECTOR_ARCHIVE_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "COLLECTOR_SCOPE_GRANTS_JSON",
        json.dumps({f"{ACTOR}:{SCOPE}": "manage", f"{READER}:{SCOPE}": "read"}),
    )
    monkeypatch.delenv("COLLECTOR_AUTHORIZER_URL", raising=False)
    from qtrade_collector.main import create_app

    app = create_app()
    app.state.research_service = setup[0]
    client = TestClient(app)
    headers = {
        "Authorization": "Bearer " + "x" * 32,
        "X-Data-Actor": str(ACTOR),
        "X-Data-Scope": str(SCOPE),
    }
    assert client.get("/v2/research/catalog").status_code == 401
    assert client.get("/v2/research/catalog", headers=headers).json()[0]["evidence"] == "fixture"
    result = client.post(
        "/v2/research/preview", headers=headers, json=request().model_dump(mode="json")
    )
    assert result.status_code == 200
    assert ResearchPlan.model_validate_json(result.content).request == request()
    assert (
        client.post(
            "/v2/research/query", headers=headers, json=query().model_dump(mode="json")
        ).status_code
        == 200
    )
    for updates, status in (
        ({"schema_version": 3}, 422),
        ({"extra": "x"}, 422),
        ({"scope_id": str(UUID(int=999))}, 403),
    ):
        assert (
            client.post(
                "/v2/research/preview",
                headers=headers,
                json=request().model_dump(mode="json") | updates,
            ).status_code
            == status
        )
    readonly = headers | {"X-Data-Actor": str(READER)}
    assert (
        client.post(
            "/v2/research/preview",
            headers=readonly,
            json=request(actor_id=READER).model_dump(mode="json"),
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/v2/research/query",
            headers=readonly,
            json=query(actor_id=READER).model_dump(mode="json"),
        ).status_code
        == 200
    )
