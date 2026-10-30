"""Only the authenticated BFF submits already verified provider logout notices."""

from datetime import datetime
from hmac import compare_digest

from fastapi import APIRouter, HTTPException, Request, Response

from qtrade_api.audit_context import request_context
from qtrade_application.logout import LogoutService
from qtrade_domain.logout_port import VerifiedLogout
from qtrade_domain.ports import ErrorCode, PortError


def logout_bridge(
    service: LogoutService | None, *, secret: str | None, issuer: str | None
) -> APIRouter:
    router = APIRouter(prefix="/internal/auth", tags=["internal identity"])

    @router.post("/backchannel-logout")
    async def consume(request: Request) -> Response:
        headers = {"Cache-Control": "no-store"}
        if service is None or secret is None or issuer is None:
            raise HTTPException(503, "identity connection unavailable", headers=headers)
        if not compare_digest(
            request.headers.get("Authorization", "").encode(), ("Bearer " + secret).encode()
        ):
            raise HTTPException(401, "trusted BFF authentication required", headers=headers)
        try:
            if len(await request.body()) > 8192:
                raise ValueError("invalid payload")
            payload = await request.json()
            if not isinstance(payload, dict) or set(payload) != {
                "issuer",
                "jti",
                "expires_at",
                "subject",
                "sid",
            }:
                raise ValueError("invalid payload")
            if (
                payload["issuer"] != issuer
                or any(not isinstance(payload[k], str) for k in ("issuer", "jti", "expires_at"))
                or any(
                    payload[k] is not None and not isinstance(payload[k], str)
                    for k in ("subject", "sid")
                )
            ):
                raise ValueError("invalid payload")
            event = VerifiedLogout(
                **{**payload, "expires_at": datetime.fromisoformat(payload["expires_at"])}
            )
            service.consume(event, audit_context=request_context(request, "api.oidc.backchannel"))
        except (ValueError, TypeError, KeyError):
            raise HTTPException(400, "invalid verified logout", headers=headers) from None
        except PortError as error:
            raise HTTPException(
                503 if error.code == ErrorCode.UNAVAILABLE else 401,
                "logout rejected",
                headers=headers,
            ) from None
        return Response(status_code=204, headers=headers)

    return router
