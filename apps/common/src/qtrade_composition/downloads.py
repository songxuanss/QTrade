"""One authoritative environment setting for user-selected tick download ranges."""

import os
from collections.abc import Mapping

from qtrade_domain.download_policy import DownloadRangePolicy

KEY = "QTRADE_TICK_DOWNLOAD_MAX_DAYS"


def tick_download_policy_from_env(env: Mapping[str, str] = os.environ) -> DownloadRangePolicy:
    value = env.get(KEY, "0").strip()
    if not value.isascii() or not value.isdecimal():
        raise ValueError(f"{KEY} must be 0 (unlimited) or a positive integer")
    days = int(value)
    return DownloadRangePolicy(days or None)
