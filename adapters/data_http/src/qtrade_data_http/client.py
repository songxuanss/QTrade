"""Small authenticated collection client and interchangeable live/static authorization."""

from typing import Any, cast
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from qtrade_data_contracts.models import ErrorCode
from qtrade_data_core.ports import Action, DataFailure


def validate_endpoint(url: str, *, allow_http: bool) -> str:
    parsed = urlsplit(url)
    if (
        parsed.scheme not in ({"https", "http"} if allow_http else {"https"})
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("invalid collection endpoint")
    return url.rstrip("/")


class CollectorClient:
    def __init__(
        self, url: str, token: str, *, allow_http: bool = False, client: httpx.Client | None = None
    ):
        self.url = validate_endpoint(url, allow_http=allow_http)
        if len(token) < 32:
            raise ValueError("collection service token must have at least 32 characters")
        self.token = token
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(90, connect=5), follow_redirects=False
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        actor_id: UUID | None = None,
        scope_id: UUID | None = None,
        body: Any = None,
        params: Any = None,
    ) -> Any:
        headers = {"Authorization": "Bearer " + self.token}
        if actor_id is not None and scope_id is not None:
            headers.update({"X-Data-Actor": str(actor_id), "X-Data-Scope": str(scope_id)})
        try:
            response = self.client.request(
                method, self.url + path, headers=headers, json=body, params=params
            )
            if not response.is_success:
                code = {
                    401: "unauthorized",
                    403: "forbidden",
                    404: "not_found",
                    409: "conflict",
                    429: "rate_limited",
                    400: "invalid_input",
                    422: "invalid_input",
                }.get(response.status_code, "unavailable")
                try:
                    known = response.json().get("detail", {}).get("code")
                    if known in {"unsupported", "unverified", "timeout", "rate_limited"}:
                        code = known
                except (ValueError, AttributeError):
                    pass
                raise DataFailure(cast(ErrorCode, code))
            return response.json()
        except httpx.TimeoutException:
            raise DataFailure("timeout") from None
        except (httpx.HTTPError, ValueError):
            raise DataFailure("unavailable") from None


class RemoteDataAuthorizer:
    def __init__(self, url: str, token: str, *, allow_http: bool = False):
        self.client = CollectorClient(url, token, allow_http=allow_http)

    def require(self, actor_id: UUID, scope_id: UUID, action: Action) -> None:
        result = self.client.request(
            "POST",
            "",
            body={"actor_id": str(actor_id), "scope_id": str(scope_id), "action": action},
        )
        if result != {"allowed": True}:
            raise DataFailure("forbidden")


class StaticScopeAuthorizer:
    """For another consuming application: explicitly provisioned service subjects/scopes."""

    def __init__(self, grants: dict[str, str]):
        self.grants = grants

    def require(self, actor_id: UUID, scope_id: UUID, action: Action) -> None:
        role = self.grants.get(f"{actor_id}:{scope_id}")
        if role not in {"manage", "read"} or (action == "manage" and role != "manage"):
            raise DataFailure("forbidden")
