"""Strict provider normalization; immutable snapshots retain reference conflicts."""

import re
from datetime import UTC, datetime
from hashlib import sha256
from threading import RLock
from time import monotonic
from uuid import UUID

import httpx
from pydantic import ValidationError

from qtrade_data_contracts import (
    ReferenceIdentity,
    ReferenceIssue,
    UniverseContent,
    UniverseDefinition,
    UniverseMember,
)
from qtrade_data_core.ports import DataFailure


def identity(row: dict[str, object]) -> ReferenceIdentity:
    code, name, exchange = row.get("dm"), row.get("mc"), row.get("jys")
    if not isinstance(code, str) or not isinstance(name, str) or not isinstance(exchange, str):
        raise DataFailure("unavailable")
    exchange = exchange.upper()
    match = re.fullmatch(r"([0-9]{6})(?:\.(SH|SZ|BJ))?", code.upper())
    if not match or exchange not in {"SH", "SZ", "BJ"}:
        raise DataFailure("unavailable")
    if match[2] and match[2] != exchange:
        raise DataFailure("unavailable")
    try:
        return ReferenceIdentity(
            instrument_id=f"{match[1]}.{exchange}",
            name=name.strip(),
            exchange=exchange,
            instrument_kind="stock",
        )
    except ValidationError:
        raise DataFailure("unavailable") from None


class MaiRuiReference:
    def __init__(
        self, licence: str, *, client: httpx.Client | None = None, cache_seconds: int = 900
    ):
        if not licence or cache_seconds < 0:
            raise ValueError("reference source configuration missing")
        self._licence = licence
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(20, connect=4), follow_redirects=False
        )
        self._cache_seconds = cache_seconds
        self._cache: dict[str, tuple[float, list[dict[str, object]], datetime]] = {}
        self._lock = RLock()

    def _rows(self, path: str) -> list[dict[str, object]]:
        with self._lock:
            cached = self._cache.get(path)
            if cached and monotonic() < cached[0]:
                return cached[1]
            try:
                response = self._client.get(f"https://api.mairuiapi.com/{path}/{self._licence}")
                if response.status_code in {401, 403}:
                    raise DataFailure("unauthorized")
                if response.status_code == 429:
                    raise DataFailure("rate_limited")
                if response.status_code != 200:
                    raise DataFailure("unavailable")
                if len(response.content) > 5_000_000:
                    raise DataFailure("unavailable")
                rows = response.json()
                if (
                    not isinstance(rows, list)
                    or not rows
                    or len(rows) > 20000
                    or any(not isinstance(row, dict) for row in rows)
                ):
                    raise DataFailure("unavailable")
            except httpx.TimeoutException:
                raise DataFailure("timeout") from None
            except (httpx.HTTPError, ValueError):
                raise DataFailure("unavailable") from None
            self._cache[path] = (monotonic() + self._cache_seconds, rows, datetime.now(UTC))
            return rows

    def definitions(self) -> tuple[UniverseDefinition, ...]:
        grouped: dict[str, list[UniverseDefinition]] = {}
        categories = {0: "industry", 1: "industry", 2: "theme", 3: "theme", 7: "index_members"}
        for row in self._rows("hszg/list"):
            type2 = row.get("type2")
            if (
                type(type2) is not int
                or not isinstance(type2, int)
                or row.get("type1") != 0
                or row.get("isleaf") != 1
                or type2 not in categories
            ):
                continue
            try:
                code = row["code"]
                if not isinstance(code, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", code):
                    raise ValueError
                item = UniverseDefinition.model_validate(
                    {
                        "universe_id": "mairui:" + code,
                        "source_code": code,
                        "name": row["name"],
                        "category": categories[type2],
                        "source": "mairui.reference",
                        "parent_code": row.get("pcode"),
                    }
                )
                bucket = grouped.setdefault(item.source_code, [])
                if item not in bucket:
                    bucket.append(item)
            except (KeyError, TypeError, ValueError):
                raise DataFailure("unavailable") from None
        if not grouped:
            raise DataFailure("unavailable")
        result: list[UniverseDefinition] = []
        for items in grouped.values():
            if len(items) == 1:
                result.append(items[0])
                continue
            for item in items:
                body = item.model_dump()
                body["universe_id"] += ":" + sha256(item.name.encode()).hexdigest()[:12]
                body["source_status"] = "ambiguous"
                result.append(UniverseDefinition.model_validate(body))
        return tuple(result)

    def _stocks(self) -> dict[str, ReferenceIdentity]:
        result: dict[str, ReferenceIdentity] = {}
        for path in ("hslt/list", "bj/list/all"):
            for row in self._rows(path):
                item = identity(row)
                if item.instrument_id in result and result[item.instrument_id] != item:
                    raise DataFailure("unavailable")
                result[item.instrument_id] = item
        return result

    def members(self, scope_id: UUID, definition: UniverseDefinition) -> UniverseContent:
        if definition.source_status != "available":
            raise DataFailure("unverified")
        if definition.source != "mairui.reference" or not re.fullmatch(
            r"[a-zA-Z0-9_-]{1,128}", definition.source_code
        ):
            raise DataFailure("invalid_input")
        known = self._stocks()
        members: dict[str, UniverseMember] = {}
        issues: list[ReferenceIssue] = []
        for row in self._rows("hszg/gg/" + definition.source_code):
            item = identity(row)
            if item.instrument_id in members:
                raise DataFailure("unavailable")
            directory = known.get(item.instrument_id)
            if directory is None or directory.name != item.name:
                issues.append(
                    ReferenceIssue(
                        instrument_id=item.instrument_id,
                        code="directory_member_missing" if directory is None else "name_conflict",
                        directory_name=directory.name if directory else None,
                        member_name=item.name,
                    )
                )
            members[item.instrument_id] = UniverseMember(
                instrument=item,
                affiliations=(definition.universe_id,),
                classification_evidence=("mairui.hszg:" + definition.source_code,),
            )
        return UniverseContent(
            schema_version=1,
            scope_id=scope_id,
            universe_id=definition.universe_id,
            version=1,
            name=definition.name,
            category=definition.category,
            source="mairui.reference",
            observed_at=self._cache["hszg/gg/" + definition.source_code][2],
            membership_basis="current_snapshot",
            market_scope=tuple(sorted({m.instrument.exchange for m in members.values()})),
            classification_version="mairui.hszg.v1",
            members=tuple(members[key] for key in sorted(members)),
            reference_issues=tuple(issues),
        )

    def custom(self, scope_id: UUID, name: str, identifiers: tuple[str, ...]) -> UniverseContent:
        known = self._stocks()
        normalized: set[str] = set()
        for value in identifiers:
            value = value.strip().upper()
            matches = [key for key in known if key == value or key.split(".")[0] == value]
            if len(matches) != 1:
                raise DataFailure("invalid_input")
            normalized.add(matches[0])
        if not normalized:
            raise DataFailure("invalid_input")
        return UniverseContent(
            schema_version=1,
            scope_id=scope_id,
            universe_id="custom:" + sha256(name.encode()).hexdigest()[:32],
            version=1,
            name=name,
            category="custom",
            source="user_selection",
            observed_at=datetime.now(UTC),
            membership_basis="current_snapshot",
            market_scope=tuple(sorted({known[key].exchange for key in normalized})),
            classification_version="user-selection.v1",
            members=tuple(UniverseMember(instrument=known[key]) for key in sorted(normalized)),
        )
