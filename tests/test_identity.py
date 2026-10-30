import pytest

from qtrade_application.identity import IdentityService
from qtrade_domain.identity_port import VerifiedIdentity
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_postgres.identity import PostgresIdentityStore


@pytest.mark.parametrize(
    "issuer,subject,name",
    [
        ("", "subject", "name"),
        ("issuer", "", "name"),
        ("issuer", "subject", ""),
        ("issuer", "x" * 257, "name"),
    ],
)
def test_invalid_claims_rejected(issuer, subject, name):
    with pytest.raises(PortError) as caught:
        VerifiedIdentity(issuer, subject, name)
    assert caught.value.code == ErrorCode.INVALID_INPUT


def test_domain_persistence_errors_safe_and_retryable():
    service = IdentityService(
        PostgresIdentityStore("postgresql://test:test@127.0.0.1:1/test?connect_timeout=1")
    )
    with pytest.raises(PortError) as caught:
        service.initialize(VerifiedIdentity("https://sso.test", "subject", "name"))
    assert caught.value.code == ErrorCode.UNAVAILABLE
    assert "test:test" not in str(caught.value)
