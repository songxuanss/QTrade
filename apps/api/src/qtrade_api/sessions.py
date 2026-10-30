"""Session HTTP interface. OIDC tokens have no browser-facing representation."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from qtrade_api.audit_context import request_context
from qtrade_application.sessions import SessionService
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_runtime.cookies import SessionCookie


class WorkspaceSelection(BaseModel):
    workspace_id: UUID


def session_router(service: SessionService | None, policy: SessionCookie) -> APIRouter:
    router = APIRouter(prefix="/auth", tags=["session"])

    def configured() -> SessionService:
        if service is None:
            raise HTTPException(
                503, "identity connection unavailable", headers={"Cache-Control": "no-store"}
            )
        return service

    @router.get("/session")
    def context(request: Request, response: Response) -> dict[str, object]:
        cookie = request.cookies.get(policy.name)
        try:
            session = configured().authenticate(cookie)
            csrf = configured().csrf(cookie or "")
        except PortError as error:
            raise HTTPException(
                503 if error.code == ErrorCode.UNAVAILABLE else 401,
                "authentication required",
                headers={"Cache-Control": "no-store"},
            ) from None
        response.headers["Cache-Control"] = "no-store"
        return {
            "authenticated": True,
            "user_id": str(session.user_id),
            "workspace_id": str(session.current_workspace_id),
            "expires_at": session.expires_at.isoformat(),
            "csrf_token": csrf,
        }

    @router.post("/session/workspace")
    def select_workspace(body: WorkspaceSelection, request: Request) -> dict[str, str]:
        cookie = request.cookies.get(policy.name) or ""
        try:
            configured().verify_csrf(cookie, request.headers.get("X-CSRF-Token"))
            result = configured().switch_workspace(
                cookie,
                body.workspace_id,
                audit_context=request_context(request, "api.auth.workspace.switch"),
            )
            return {"workspace_id": str(result.current_workspace_id)}
        except PortError as error:
            raise HTTPException(
                503 if error.code == ErrorCode.UNAVAILABLE else 403,
                "workspace selection denied",
                headers={"Cache-Control": "no-store"},
            ) from None

    @router.post("/logout", status_code=204)
    def logout(request: Request) -> Response:
        cookie = request.cookies.get(policy.name)
        try:
            configured().verify_csrf(cookie or "", request.headers.get("X-CSRF-Token"))
            configured().revoke(cookie, audit_context=request_context(request, "api.auth.logout"))
        except PortError as error:
            raise HTTPException(
                503 if error.code == ErrorCode.UNAVAILABLE else 403,
                "logout denied",
                headers={"Cache-Control": "no-store"},
            ) from None
        response = Response(status_code=204)
        policy.clear(response)
        return response

    return router
