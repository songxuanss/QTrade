from uuid import UUID

from starlette.requests import Request

from qtrade_api.audit_context import request_context


def test_request_audit_context_uses_transport_address_and_server_trace():
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "scheme": "http",
            "headers": [
                (b"x-forwarded-for", b"192.0.2.9"),
                (b"x-request-id", b"00000000-0000-0000-0000-000000000000"),
            ],
            "client": ("203.0.113.7", 1234),
        }
    )
    first = request_context(request, "api.auth.login")
    second = request_context(request, "api.auth.logout")
    assert first.source_ip == second.source_ip == "203.0.113.7"
    assert isinstance(first.trace_id, UUID) and first.trace_id.int != 0
    assert first.trace_id == second.trace_id
    assert first.source == "api.auth.login" and second.source == "api.auth.logout"
