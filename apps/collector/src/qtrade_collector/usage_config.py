"""Strict operator-owned scope limits; no request may supply a policy override."""

import json
from uuid import UUID

from qtrade_data_core.usage_execution import ScopeLimits


def scope_limits(raw: str) -> dict[UUID, ScopeLimits]:
    if not isinstance(raw, str) or len(raw.encode()) > 65536:
        raise ValueError("invalid scope limit configuration")

    def unique(pairs):  # type: ignore[no-untyped-def]
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate scope limit configuration")
            result[key] = value
        return result

    try:
        value = json.loads(raw or "{}", object_pairs_hook=unique)
        if not isinstance(value, dict) or len(value) > 1000:
            raise ValueError()
        parsed = {}
        for key, limits in value.items():
            scope = UUID(key)
            if (
                str(scope) != key
                or not isinstance(limits, dict)
                or set(limits) != {"daily_credits", "concurrency"}
            ):
                raise ValueError()
            parsed[scope] = ScopeLimits(limits["daily_credits"], limits["concurrency"])
        return parsed
    except (ValueError, TypeError, AttributeError):
        # Never echo the input config in exceptions/logs, even if malformed.
        raise ValueError("invalid scope limit configuration") from None
