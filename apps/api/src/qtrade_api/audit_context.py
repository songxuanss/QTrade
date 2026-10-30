"""Server-created request correlation; never trust forwarding headers or payloads."""

from ipaddress import ip_address
from uuid import UUID, uuid4

from fastapi import Request

from qtrade_domain.audit_port import AuditContext


def request_context(request: Request, source: str) -> AuditContext:
    trace = getattr(request.state, "audit_trace", None)
    if not isinstance(trace, UUID):
        trace = uuid4()
        request.state.audit_trace = trace
    address = None
    if request.client is not None:
        try:
            address = str(ip_address(request.client.host))
        except ValueError:
            pass
    return AuditContext(source, trace, address)
