"""Reusable HTTP guards. Browser writes require both session CSRF and live RBAC."""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import HTTPException, Request, Response, WebSocket

from qtrade_api.audit_context import request_context
from qtrade_application.sessions import SessionService
from qtrade_application.workspaces import WorkspaceService
from qtrade_domain.audit_port import AuditDetails, AuditRecord, AuditStorePort, contextualize
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.identity import Session
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_runtime.cookies import SessionCookie


def public_error(error: PortError) -> HTTPException:
    status = {
        ErrorCode.UNAUTHORIZED: 401,
        ErrorCode.NOT_FOUND: 404,
        ErrorCode.REJECTED: 403,
        ErrorCode.CONFLICT: 409,
        ErrorCode.INVALID_INPUT: 400,
    }.get(error.code, 503)
    detail = {
        401: "authentication required",
        404: "workspace resource unavailable",
        403: "workspace permission denied",
        409: "workspace change conflicts",
        400: "invalid workspace request",
    }.get(status, "workspace service unavailable")
    return HTTPException(status, detail, headers={"Cache-Control": "no-store"})


class ApiSecurity:
    def __init__(
        self,
        sessions: SessionService | None,
        workspaces: WorkspaceService | None,
        policy: SessionCookie,
        audit: AuditStorePort | None = None,
        public_origin: str | None = None,
    ):
        self._sessions, self._workspaces, self._policy = sessions, workspaces, policy
        self._audit = audit
        self._public_origin = public_origin

    def websocket_workspace(
        self, websocket: WebSocket, workspace_id: UUID, permission: Permission
    ) -> WorkspaceAccess:
        origin = websocket.headers.get("origin")
        if not self._public_origin or origin != self._public_origin:
            raise PortError(ErrorCode.REJECTED, "websocket origin is not allowed")
        if self._sessions is None or self._workspaces is None:
            raise PortError(ErrorCode.UNAVAILABLE, "workspace identity is unavailable")
        session = self._sessions.authenticate(websocket.cookies.get(self._policy.name))
        return self._workspaces.authorize(session.user_id, workspace_id, permission)

    def actor(self, request: Request, response: Response) -> Session:
        response.headers["Cache-Control"] = "no-store"
        if self._sessions is None:
            raise HTTPException(
                503, "identity connection unavailable", headers={"Cache-Control": "no-store"}
            )
        try:
            return self._sessions.authenticate(request.cookies.get(self._policy.name))
        except PortError as error:
            raise public_error(error) from None

    def workspace(
        self, permission: Permission, *, mutate: bool = False
    ) -> Callable[[Request, Response, UUID], WorkspaceAccess]:
        if mutate and permission == Permission.READ:
            raise ValueError("mutation requires a write permission")

        def guard(request: Request, response: Response, workspace_id: UUID) -> WorkspaceAccess:
            session = self.actor(request, response)
            if self._workspaces is None:
                raise HTTPException(
                    503, "workspace service unavailable", headers={"Cache-Control": "no-store"}
                )
            try:
                if mutate:
                    if self._sessions is None:
                        raise HTTPException(
                            503,
                            "identity connection unavailable",
                            headers={"Cache-Control": "no-store"},
                        )
                    try:
                        self._sessions.verify_csrf(
                            request.cookies.get(self._policy.name) or "",
                            request.headers.get("X-CSRF-Token"),
                        )
                    except PortError as error:
                        if error.code == ErrorCode.UNAUTHORIZED:
                            self._denied(request, session, workspace_id, permission, error.code)
                            raise HTTPException(
                                403,
                                "request verification failed",
                                headers={"Cache-Control": "no-store"},
                            ) from None
                        raise
                return self._workspaces.authorize(session.user_id, workspace_id, permission)
            except PortError as error:
                if self._audit is not None and error.code in {
                    ErrorCode.REJECTED,
                    ErrorCode.NOT_FOUND,
                }:
                    self._denied(request, session, workspace_id, permission, error.code)
                raise public_error(error) from None

        return guard

    def _denied(
        self,
        request: Request,
        session: Session,
        workspace_id: UUID,
        permission: Permission,
        reason: ErrorCode,
    ) -> None:
        if self._audit is None:
            return
        try:
            self._audit.append(
                contextualize(
                    AuditRecord(
                        workspace_id
                        if reason == ErrorCode.REJECTED
                        else session.current_workspace_id,
                        "user",
                        session.user_id,
                        "permission.denied",
                        "denied",
                        datetime.now(UTC),
                        "api",
                        uuid4(),
                        details=AuditDetails(
                            target_workspace_id=workspace_id, permission=permission, reason=reason
                        ),
                    ),
                    request_context(request, "api.permission"),
                )
            )
        except PortError as error:
            raise public_error(error) from None
