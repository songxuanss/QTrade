"""Unambiguous supplier JSON, decoded only after the HTTP capture callback.

This applies to new acquisitions, not frozen normalization inputs or old cached
artifacts: replay must continue to reproduce their registered mapping versions.
"""

import json
from decimal import Decimal
from typing import Any, NoReturn


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        # JSON escapes are already resolved, so "value" and "\u0076alue" are
        # the same field. Last-value-wins would silently change source meaning.
        if key in result:
            raise ValueError("ambiguous supplier JSON")
        result[key] = value
    return result


def _constant(_: str) -> NoReturn:
    # Python's JSON decoder otherwise accepts NaN/Infinity outside JSON's
    # number grammar. Never let them turn into nulls in later serialization.
    raise ValueError("non-finite supplier JSON")


def decode_response(body: bytes) -> Any:
    """Preserve finite decimals/text while rejecting ambiguous source values.

    Errors contain fixed messages only; the caller maps them to its public
    failure code. The privately archived response remains the recovery evidence.
    """
    try:
        return json.loads(
            body, parse_float=Decimal, parse_constant=_constant, object_pairs_hook=_object
        )
    except (ArithmeticError, RecursionError):
        # Unrepresentable exponents and excessive nesting are invalid input,
        # not uncaught Worker errors or reasons to use a partially decoded page.
        raise ValueError("invalid supplier JSON") from None
