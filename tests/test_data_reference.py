"""Provider fixtures exercise normalization and failures; live proof is recorded separately."""

from datetime import UTC
from uuid import uuid4

import httpx
import pytest

from qtrade_data_core.ports import DataFailure
from qtrade_data_core.universes import UniverseCatalog
from qtrade_data_mairui.reference import MaiRuiReference, identity


def provider(*, status=200, conflict=False, ambiguous=False):
    rows = {
        "hszg/list": [
            dict(name="半导体", code="chips", type1=0, type2=1, isleaf=1, pcode="industries"),
            dict(name="港股半导体", code="hkchips", type1=4, type2=22, isleaf=1),
        ],
        "hslt/list": [dict(dm="601288.SH", mc="农业银行", jys="SH")],
        "bj/list/all": [dict(dm="920179.BJ", mc="凯德石英", jys="BJ")],
        "hszg/gg/chips": [
            dict(dm="601288", mc="不同名称" if conflict else "农业银行", jys="sh"),
            dict(dm="920179", mc="凯德石英", jys="bj"),
        ],
    }

    if ambiguous:
        rows["hszg/list"].append(
            dict(name="另一个板块", code="chips", type1=0, type2=1, isleaf=1, pcode="industries")
        )

    def handle(request):
        assert request.url.host == "api.mairuiapi.com"
        return httpx.Response(
            status, json=rows.get(request.url.path.strip("/").removesuffix("/private-test-key"), [])
        )

    return MaiRuiReference(
        "private-test-key", client=httpx.Client(transport=httpx.MockTransport(handle))
    )


def test_source_variants_and_bj_members_are_retained():
    p = provider()
    definitions = p.definitions()
    assert len(definitions) == 1
    content = p.members(uuid4(), definitions[0])
    assert [m.instrument.instrument_id for m in content.members] == ["601288.SH", "920179.BJ"]
    assert content.market_scope == ("BJ", "SH")
    assert content.observed_at.tzinfo is UTC
    assert not content.reference_issues


def test_names_are_not_silently_replaced_by_directory():
    p = provider(conflict=True)
    content = p.members(uuid4(), p.definitions()[0])
    assert content.members[0].instrument.name == "不同名称"
    assert content.reference_issues[0].code == "name_conflict"
    assert content.reference_issues[0].directory_name == "农业银行"


@pytest.mark.parametrize(
    "status,code", [(403, "unauthorized"), (429, "rate_limited"), (302, "unavailable")]
)
def test_safe_provider_errors_do_not_include_credential(status, code):
    with pytest.raises(DataFailure) as error:
        provider(status=status).definitions()
    assert error.value.code == code
    assert "private-test-key" not in str(error.value)


def test_invalid_exchange_and_unknown_custom_member_are_rejected():
    with pytest.raises(DataFailure):
        identity(dict(dm="601288.SH", mc="农业银行", jys="sz"))
    with pytest.raises(DataFailure):
        provider().custom(uuid4(), "我的股票池", ("999999.SH",))
    content = provider().custom(uuid4(), "我的股票池", ("601288", "601288.SH"))
    assert len(content.members) == 1


def test_catalog_guards_before_source_or_repo_and_checks_return_scope():
    class Authorizer:
        def require(self, actor_id, scope_id, action):
            raise DataFailure("forbidden")

    class NeverUsed:
        def __getattr__(self, name):
            raise AssertionError("dependency accessed without authorization")

    service = UniverseCatalog(Authorizer(), NeverUsed(), NeverUsed())
    for call in (
        lambda: service.definitions(uuid4(), uuid4()),
        lambda: service.resolve(uuid4(), uuid4(), "mairui:chips"),
        lambda: service.custom(uuid4(), uuid4(), "name", ("601288.SH",)),
        lambda: service.snapshot(uuid4(), uuid4(), uuid4()),
        lambda: service.snapshots(uuid4(), uuid4()),
    ):
        with pytest.raises(DataFailure, match="forbidden"):
            call()


def test_catalog_rechecks_write_access_after_source_lookup():
    class Authorizer:
        calls = 0

        def require(self, actor_id, scope_id, action):
            self.calls += 1
            if self.calls > 1:
                raise DataFailure("forbidden")

    class Repo:
        def append(self, *args):
            raise AssertionError("permission was revoked before persist")

    service = UniverseCatalog(Authorizer(), provider(), Repo())
    with pytest.raises(DataFailure, match="forbidden"):
        service.resolve(uuid4(), uuid4(), "mairui:chips")


def test_ambiguous_source_code_is_preserved_as_disabled_entries():
    p = provider(ambiguous=True)
    items = p.definitions()
    assert len(items) == 2 and len({i.universe_id for i in items}) == 2
    assert all(i.source_status == "ambiguous" for i in items)
    with pytest.raises(DataFailure, match="unverified"):
        p.members(uuid4(), items[0])
