"""First-login mapping. OIDC verification is required before entering this use case."""

from qtrade_domain.identity_port import IdentityStorePort, LoginIdentity, VerifiedIdentity


class IdentityService:
    def __init__(self, store: IdentityStorePort):
        self._store = store

    def initialize(self, identity: VerifiedIdentity) -> LoginIdentity:
        return self._store.initialize(identity, workspace_name="个人工作区")
