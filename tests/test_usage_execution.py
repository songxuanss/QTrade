"""Controlled outbound transport verifies admission order and trusted scope propagation."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest

from qtrade_data_core.ports import DataFailure
from qtrade_data_core.usage import UsageDenied, UsageLease, UsagePolicy
from qtrade_data_core.usage_execution import UsageGate, UsageIdentity, usage_identity, usage_scope
from qtrade_data_mairui.bounded_http import BoundedHttpClient, TransportFailure
from qtrade_data_mairui.collection import MaiRuiCollection


class Ledger:
    def __init__(self, limit=10):
        self.limit, self.requests, self.finished = limit, [], []

    def reserve(self, request, policy, now):
        if sum(r.credits for r in self.requests) + request.credits > self.limit:
            raise UsageDenied("source_daily")
        self.requests.append(request)
        return UsageLease(request, uuid4(), now.date(), now + timedelta(hours=1), True, "reserved")

    def finish(self, lease, result, now):
        self.finished.append((lease, result))


def provider(tmp_path, ledger, handler):
    return MaiRuiCollection(
        "fixture-key",
        pro=True,
        cache=tmp_path,
        usage=UsageGate(ledger, "mairui", UsagePolicy(100, 100, 4, 2)),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_denied_credit_and_missing_scope_make_no_outbound_call(tmp_path):
    ledger, calls = Ledger(limit=4), []
    adapter = provider(tmp_path, ledger, lambda r: calls.append(r) or httpx.Response(200, json=[]))
    with pytest.raises(DataFailure, match="unavailable"):
        adapter._get("factor/history/pe_ttm/600519")
    with usage_scope(uuid4(), uuid4()):
        with pytest.raises(UsageDenied) as blocked:
            adapter._get("factor/history/pe_ttm/600519")
    assert blocked.value.reason == "source_daily"
    assert calls == ledger.requests == ledger.finished == []


def test_weighted_request_scoped_before_io_and_completion_metrics(tmp_path):
    ledger, actor, scope = Ledger(), uuid4(), uuid4()

    def transport(r):
        assert len(ledger.requests) == 1 and ledger.requests[0].scope_id == scope
        assert ledger.requests[0].credits == 5
        return httpx.Response(200, content=b"[]")

    adapter = provider(tmp_path, ledger, transport)
    with usage_scope(actor, scope):
        assert adapter._get("factor/history/pe_ttm/600519") == []
    assert usage_identity.get() is None
    assert ledger.finished[0][1].outcome == "success"
    assert ledger.finished[0][1].response_bytes == 2


@pytest.mark.parametrize(
    "status,code", [(403, "unauthorized"), (429, "rate_limited"), (500, "unavailable")]
)
def test_http_failures_keep_charge_and_safe_result(tmp_path, status, code):
    ledger = Ledger()
    adapter = provider(
        tmp_path, ledger, lambda r: httpx.Response(status, content=b"private upstream body")
    )
    with usage_scope(uuid4(), uuid4()):
        with pytest.raises(DataFailure, match=code):
            adapter._get("hsstock/history/fixture/5/n")
    assert len(ledger.requests) == 1 and ledger.finished[0][1].error_code == code
    assert ledger.finished[0][1].response_bytes == len(b"private upstream body")


def test_timeout_and_background_metadata_do_not_leak_scope(tmp_path):
    ledger = Ledger()

    def timeout(r):
        raise httpx.ReadTimeout("private fixture url", request=r)

    adapter = provider(tmp_path, ledger, timeout)
    with pytest.raises(DataFailure, match="timeout"):
        adapter._get("factor/list")
    assert (
        ledger.requests[0].scope_id is None
        and ledger.requests[0].operation == "metadata.factor_catalog"
    )
    assert ledger.finished[0][1].error_code == "timeout"


@pytest.mark.parametrize("archive_failure", [False, True])
def test_partial_response_capture_keeps_failed_charge_and_no_private_logs(
    tmp_path, caplog, archive_failure
):
    ledger, captured = Ledger(), []

    class FailedTransport(BoundedHttpClient):
        def get(self, url, *, params=None, timeout=90):
            assert len(ledger.requests) == 1
            raise TransportFailure("timeout", response_status=200, response_body=b"private-prefix")

    def capture(kind, envelope):
        captured.append((kind, envelope))
        if archive_failure:
            raise DataFailure("unavailable")

    adapter = provider(tmp_path, ledger, lambda _: httpx.Response(200, json=[]))
    adapter.client = FailedTransport()
    with caplog.at_level("INFO"), usage_scope(uuid4(), uuid4()):
        with pytest.raises(DataFailure, match="unavailable" if archive_failure else "timeout"):
            adapter._get("hscp/jnfh/fixture", capture=capture)
    assert len(captured) == len(ledger.requests) == len(ledger.finished) == 1
    assert captured[0][1]["complete"] is False
    assert captured[0][1]["http_status"] == 200
    result = ledger.finished[0][1]
    assert result.response_bytes == len(b"private-prefix")
    assert result.error_code == ("unavailable" if archive_failure else "timeout")
    assert "private-prefix" not in caplog.text and "fixture-key" not in caplog.text


def test_scope_isolation_across_async_tasks_and_threadpool():
    async def query(identity):
        with usage_scope(identity.actor_id, identity.scope_id):
            await asyncio.sleep(0)
            return await asyncio.to_thread(usage_identity.get)

    identities = [UsageIdentity(uuid4(), uuid4()), UsageIdentity(uuid4(), uuid4())]

    async def run():
        return await asyncio.gather(*(query(i) for i in identities))

    assert asyncio.run(run()) == identities
    assert usage_identity.get() is None


def test_finalization_failure_blocks_response_and_logs_safe_failure(tmp_path, caplog):
    class UnavailableLedger(Ledger):
        def finish(self, lease, result, now):
            raise RuntimeError("private database detail")

    ledger = UnavailableLedger()
    adapter = provider(tmp_path, ledger, lambda r: httpx.Response(200, content=b"[]"))
    with usage_scope(uuid4(), uuid4()), caplog.at_level("INFO"):
        with pytest.raises(DataFailure, match="unavailable") as error:
            adapter._get("factor/history/pe_ttm/600519")
    assert error.value.__cause__ is None
    assert len(ledger.requests) == 1  # No refund or second outbound attempt.
    record = next(r for r in caplog.records if r.getMessage() == "provider_request")
    assert record.error_code == "unavailable" and record.http_status == 200
    assert "private database detail" not in caplog.text


def test_unexpected_transport_failure_is_not_recorded_as_success(tmp_path):
    ledger = Ledger()

    def broken_transport(request):
        raise RuntimeError("controlled transport failure")

    adapter = provider(tmp_path, ledger, broken_transport)
    with usage_scope(uuid4(), uuid4()):
        with pytest.raises(RuntimeError, match="controlled transport failure"):
            adapter._get("factor/history/pe_ttm/600519")
    result = ledger.finished[0][1]
    assert result.outcome == "failure" and result.error_code == "unavailable"
    assert result.response_bytes == 0


def test_transport_budget_rejects_nearly_expired_owner_before_io():
    class ShortLeaseLedger(Ledger):
        def reserve(self, request, policy, now):
            lease = super().reserve(request, policy, now)
            return UsageLease(request, lease.token, lease.day, now + timedelta(seconds=5),
                              True, "reserved")

    gate = UsageGate(ShortLeaseLedger(), "mairui", UsagePolicy(100, 100, 4, 2))
    with usage_scope(uuid4(), uuid4()):
        attempt = gate.begin("bars.history", 1)
    with pytest.raises(DataFailure, match="timeout"):
        attempt.transport_budget(90)
    attempt.finish(0, "timeout")


def test_transport_budget_respects_current_owner_and_configured_maximum():
    gate = UsageGate(Ledger(), "mairui", UsagePolicy(100, 100, 4, 2))
    with usage_scope(uuid4(), uuid4()):
        attempt = gate.begin("bars.history", 1)
    assert attempt.transport_budget(90) == 90
    attempt.finish(0, None)
