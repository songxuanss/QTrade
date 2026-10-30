"""HTTP cookie policy shared by API composition and its tests."""

from dataclasses import dataclass

from starlette.responses import Response


@dataclass(frozen=True)
class SessionCookie:
    environment: str = "production"
    insecure_local: bool = False

    def __post_init__(self) -> None:
        if self.environment not in {"production", "staging", "local", "dev", "test"}:
            raise ValueError("invalid cookie environment")
        if self.insecure_local and self.environment not in {"local", "test"}:
            raise ValueError("insecure cookies are only permitted in local/test environments")

    @property
    def name(self) -> str:
        return "qtrade-session" if self.insecure_local else "__Host-qtrade-session"

    def set(self, response: Response, value: str, *, max_age: int) -> None:
        response.set_cookie(
            self.name,
            value,
            max_age=max_age,
            secure=not self.insecure_local,
            httponly=True,
            samesite="lax",
            path="/",
        )
        response.headers["Cache-Control"] = "no-store"

    def clear(self, response: Response) -> None:
        response.delete_cookie(
            self.name, secure=not self.insecure_local, httponly=True, samesite="lax", path="/"
        )
        response.headers["Cache-Control"] = "no-store"
