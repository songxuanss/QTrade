"""Identity persistence boundary; callers must supply already verified OIDC claims."""

from dataclasses import dataclass
from typing import Protocol

from qtrade_domain.identity import Membership, User, Workspace
from qtrade_domain.ports import ErrorCode, PortError


@dataclass(frozen=True)
class VerifiedIdentity:
    issuer: str
    subject: str
    display_name: str
    email: str | None = None

    def __post_init__(self) -> None:
        if (
            not self.issuer
            or len(self.issuer) > 2048
            or not self.subject
            or len(self.subject) > 256
            or not self.display_name
            or len(self.display_name) > 256
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid identity claims")


@dataclass(frozen=True)
class LoginIdentity:
    user: User
    personal_workspace: Workspace
    membership: Membership | None


class IdentityStorePort(Protocol):
    def initialize(self, identity: VerifiedIdentity, *, workspace_name: str) -> LoginIdentity:
        """Map issuer/sub atomically; preserve existing workspace and authorization."""
        ...
