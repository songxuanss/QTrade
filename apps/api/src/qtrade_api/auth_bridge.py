"""Trusted BFF-to-API bridge. Only the BFF may submit verified OIDC identity.

No browser session creation endpoint exists without this separate server secret.
Request validation never echoes token values in errors.
"""

from datetime import UTC, datetime
from hmac import compare_digest
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Response

from qtrade_api.audit_context import request_context
from qtrade_application.identity import IdentityService
from qtrade_application.sessions import SessionService, TokenSet
from qtrade_domain.audit_port import AuditDetails, AuditRecord, AuditStorePort, contextualize
from qtrade_domain.identity_port import VerifiedIdentity
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_runtime.cookies import SessionCookie


def auth_bridge(
    identity: IdentityService | None,
    sessions: SessionService | None,
    policy: SessionCookie,
    *,
    secret: str | None,
    issuer: str | None,
    audit: AuditStorePort | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/internal/auth", tags=["internal identity"])
    if secret is not None and len(secret) < 32:
        raise ValueError("BFF secret must have at least 32 characters")

    @router.post("/login-failed")
    async def failed(request: Request) -> Response:
        headers = {"Cache-Control": "no-store"}
        if secret is None or audit is None:
            raise HTTPException(503, "identity connection unavailable", headers=headers)
        provided = request.headers.get("Authorization", "")
        if not compare_digest(provided.encode(), ("Bearer " + secret).encode()):
            raise HTTPException(401, "trusted BFF authentication required", headers=headers)
        if await request.body():
            raise HTTPException(400, "unexpected authentication failure payload", headers=headers)
        try:
            audit.append(
                contextualize(
                    AuditRecord(
                        None,
                        "anonymous",
                        None,
                        "auth.login",
                        "failure",
                        datetime.now(UTC),
                        "oidc.callback",
                        uuid4(),
                        details=AuditDetails(reason=ErrorCode.UNAUTHORIZED),
                    ),
                    request_context(request, "api.oidc.callback"),
                )
            )
        except PortError:
            raise HTTPException(503, "audit service unavailable", headers=headers) from None
        return Response(status_code=204, headers=headers)

    @router.post("/session")
    async def create(request: Request) -> Response:
        headers = {"Cache-Control": "no-store"}
        if secret is None or sessions is None or identity is None or issuer is None:
            raise HTTPException(503, "identity connection unavailable", headers=headers)
        provided = request.headers.get("Authorization", "")
        if not compare_digest(provided.encode(), ("Bearer " + secret).encode()):
            raise HTTPException(401, "trusted BFF authentication required", headers=headers)
        content = await request.body()
        if len(content) > 200_000:
            raise HTTPException(413, "identity payload too large", headers=headers)
        try:
            payload: Any = await request.json()
            if not isinstance(payload, dict) or set(payload) != {"identity", "tokens", "sso_sid"}:
                raise ValueError("invalid payload")
            claims, tokens = payload["identity"], payload["tokens"]
            if not isinstance(claims, dict) or set(claims) != {
                "issuer",
                "subject",
                "display_name",
                "email",
            }:
                raise ValueError("invalid claims")
            if claims["issuer"] != issuer or any(
                not isinstance(claims[k], str) for k in ("issuer", "subject", "display_name")
            ):
                raise ValueError("invalid claims")
            if claims["email"] is not None and (
                not isinstance(claims["email"], str) or len(claims["email"]) > 512
            ):
                raise ValueError("invalid email")
            if not isinstance(tokens, dict) or set(tokens) != {
                "access_token",
                "refresh_token",
                "id_token",
            }:
                raise ValueError("invalid token fields")
            if any(
                v is not None and (not isinstance(v, str) or len(v) > 65536)
                for v in tokens.values()
            ):
                raise ValueError("invalid tokens")
            if not tokens["access_token"] or not tokens["id_token"]:
                raise ValueError("missing tokens")
            sid = payload["sso_sid"]
            if sid is not None and (not isinstance(sid, str) or len(sid) > 256):
                raise ValueError("invalid provider session")
            mapped = identity.initialize(VerifiedIdentity(**claims))
            created = sessions.create(
                mapped,
                TokenSet(**tokens),
                sso_sid=sid,
                audit_context=request_context(request, "api.auth.login"),
            )
        except (ValueError, TypeError, KeyError):
            raise HTTPException(400, "invalid verified identity payload", headers=headers) from None
        except PortError as error:
            raise HTTPException(
                503 if error.code == ErrorCode.UNAVAILABLE else 403,
                "identity initialization denied",
                headers=headers,
            ) from None
        response = Response(status_code=204)
        max_age = int((created.session.expires_at - created.session.created_at).total_seconds())
        policy.set(response, created.cookie, max_age=max_age)
        return response

    return router
