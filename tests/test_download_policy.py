from datetime import UTC, datetime, timedelta

import pytest

from qtrade_composition.downloads import KEY, tick_download_policy_from_env
from qtrade_domain.download_policy import DownloadRangePolicy
from qtrade_domain.ports import PortError


def test_default_and_zero_allow_arbitrary_ranges():
    start = datetime(2026, 8, 14, tzinfo=UTC)
    for env in ({}, {KEY: "0"}):
        policy = tick_download_policy_from_env(env)
        assert policy.max_days is None
        policy.validate(start, start + timedelta(days=365))


def test_configured_limit_accepts_boundary_and_rejects_excess():
    start = datetime(2026, 8, 14, tzinfo=UTC)
    policy = tick_download_policy_from_env({KEY: "7"})
    assert policy.max_days == 7
    policy.validate(start, start + timedelta(days=7))
    with pytest.raises(PortError):
        policy.validate(start, start + timedelta(days=7, microseconds=1))


@pytest.mark.parametrize("value", ["", "-1", "1.5", "unlimited", "７"])
def test_invalid_environment_fails_configuration(value):
    with pytest.raises(ValueError, match=KEY):
        tick_download_policy_from_env({KEY: value})


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_invalid_domain_limit(value):
    with pytest.raises(ValueError):
        DownloadRangePolicy(value)
