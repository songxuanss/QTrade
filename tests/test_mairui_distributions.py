"""Finite source semantics from controlled rows; no complete event ledger claim."""

from datetime import date

import pytest
from test_research_evidence import NOW
from test_research_interface import request

from qtrade_data_core.ports import DataFailure
from qtrade_data_core.research import ResearchRawPage
from qtrade_data_mairui.collection import MaiRuiCollection
from qtrade_data_mairui.research import MaiRuiDistributions


def setup(tmp_path):
    provider = MaiRuiDistributions(MaiRuiCollection(None, cache=tmp_path))
    req = request(
        source=provider.source,
        kind="corporate_action",
        date_axis="event_date",
        start=date(2025, 1, 1),
        end=date(2026, 1, 1),
    )
    row = dict(
        _instrument_id="600519.SH",
        sdate="2025-06-20",
        edate="2025-06-24",
        cdate="2025-06-25",
        hdate="--",
        send="10.25",
        give="1",
        change="2",
        line="实施",
    )
    return provider, req, row


def test_dates_per_ten_units_unknown_payment_and_repeat_dedup(tmp_path):
    p, req, row = setup(tmp_path)
    records = p.normalize(req, ResearchRawPage((row, row), None, None), NOW)
    assert len(records) == 1
    r = records[0]
    assert r.basis_shares == 10 and r.cash_currency == "CNY"
    assert r.cash_tax_basis == "before_tax" and r.bonus_shares == 1 and r.transfer_shares == 2
    assert r.payment_date is None and r.listing_date is None and r.knowledge.status == "unknown"
    changed = p.normalize(req, ResearchRawPage((row | {"send": "11"},), None, None), NOW)[0]
    assert (
        changed.event_identity == r.event_identity and changed.content_version != r.content_version
    )
    with pytest.raises(DataFailure, match="unverified"):
        p.normalize(req, ResearchRawPage((row, row | {"send": "11"}), None, None), NOW)


def test_announcement_range_and_bad_dates_rejected(tmp_path):
    p, req, row = setup(tmp_path)
    assert (
        p.normalize(req, ResearchRawPage((row | {"sdate": "2026-01-01"},), None, None), NOW) == ()
    )
    for update in ({"sdate": "--"}, {"cdate": "broken"}, {"send": "-1"}):
        with pytest.raises(DataFailure, match="unverified"):
            p.normalize(req, ResearchRawPage((row | update,), None, None), NOW)
