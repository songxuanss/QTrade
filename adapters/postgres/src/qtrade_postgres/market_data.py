"""TimescaleDB immutable Bar snapshots, atomic retries and bounded indexed queries."""

import base64
import json
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

from qtrade_contracts import Bar
from qtrade_domain.market_data import bar_payload, dataset_bars, dataset_digest
from qtrade_domain.ports import BarPage, BarQuery, ErrorCode, PortError
from qtrade_postgres.connection import PostgresStore


def query_identity(query: BarQuery) -> str:
    return sha256(
        json.dumps(
            [
                str(query.workspace_id),
                str(query.dataset_id),
                query.instrument_id,
                query.granularity,
                query.start.astimezone(UTC).isoformat(),
                query.end.astimezone(UTC).isoformat(),
            ],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def cursor(query: BarQuery, instant: datetime) -> str:
    return base64.urlsafe_b64encode(
        json.dumps([1, query_identity(query), instant.astimezone(UTC).isoformat()]).encode()
    ).decode()


def after(query: BarQuery) -> datetime | None:
    if query.cursor is None:
        return None
    try:
        payload = json.loads(base64.b64decode(query.cursor, altchars=b"-_", validate=True))
        if not isinstance(payload, list) or len(payload) != 3 or type(payload[0]) is not int:
            raise ValueError("invalid cursor")
        if payload[0] != 1 or payload[1] != query_identity(query):
            raise ValueError("invalid cursor")
        instant = datetime.fromisoformat(payload[2])
        if instant.utcoffset() is None or not query.start <= instant < query.end:
            raise ValueError("invalid cursor")
        return instant
    except (ValueError, TypeError, UnicodeError):
        raise PortError(ErrorCode.INVALID_INPUT, "invalid bar cursor") from None


class TimescaleMarketData(PostgresStore):
    def __init__(self, database_url: str, *, schema: str = "qtrade_market"):
        super().__init__(database_url, schema=schema)

    def write_bars(self, workspace_id: UUID, dataset_id: UUID, bars: tuple[Bar, ...]) -> None:
        ordered = dataset_bars(workspace_id, dataset_id, bars)
        digest = dataset_digest(ordered)
        with self._connection() as conn:
            created = conn.execute(
                """INSERT INTO market_dataset_snapshot
                (workspace_id,dataset_id,content_sha256,record_count)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT(workspace_id,dataset_id) DO NOTHING RETURNING dataset_id""",
                (workspace_id, dataset_id, digest, len(ordered)),
            ).fetchone()
            if created is None:
                previous = conn.execute(
                    """SELECT content_sha256,record_count FROM market_dataset_snapshot
                    WHERE workspace_id=%s AND dataset_id=%s""",
                    (workspace_id, dataset_id),
                ).fetchone()
                if previous is None or previous != (digest, len(ordered)):
                    raise PortError(ErrorCode.CONFLICT, "immutable dataset already exists")
                return
            with conn.cursor().copy(
                "COPY market_bar(workspace_id,dataset_id,instrument_id,"
                "granularity,timestamp,payload) "
                "FROM STDIN"
            ) as copy:
                for bar in ordered:
                    copy.write_row(
                        (
                            workspace_id,
                            dataset_id,
                            bar.instrument_id,
                            bar.granularity,
                            bar.timestamp,
                            bar_payload(bar),
                        )
                    )

    def instruments_for_dataset(
        self, workspace_id: UUID, dataset_id: UUID
    ) -> tuple[tuple[str, str], ...]:
        """Enumerate the immutable dataset's bar streams for event replay."""
        with self._connection() as conn:
            if (
                conn.execute(
                    "SELECT 1 FROM market_dataset_snapshot WHERE workspace_id=%s AND dataset_id=%s",
                    (workspace_id, dataset_id),
                ).fetchone()
                is None
            ):
                raise PortError(ErrorCode.NOT_FOUND, "dataset unavailable")
            rows = conn.execute(
                """SELECT DISTINCT instrument_id,granularity FROM market_bar
                WHERE workspace_id=%s AND dataset_id=%s
                ORDER BY instrument_id,granularity""",
                (workspace_id, dataset_id),
            ).fetchall()
        if not rows:
            raise PortError(ErrorCode.NOT_FOUND, "dataset bars not found")
        return tuple((str(row[0]), str(row[1])) for row in rows)

    def bars_for_dataset(
        self,
        workspace_id: UUID,
        dataset_id: UUID,
        start: datetime,
        end: datetime,
        *,
        limit: int = 10_000,
    ) -> tuple[Bar, ...] | None:
        """Load one dataset's whole series in a single round trip when it fits.

        The replay engine caps a run at a few thousand bars, so one ordered query
        is cheaper than one connection per instrument stream. Larger tick
        datasets return ``None`` and fall back to paginated per-stream reads.
        """
        if type(limit) is not int or not 1 <= limit <= 100_000:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid bar limit")
        with self._connection() as conn:
            if (
                conn.execute(
                    "SELECT 1 FROM market_dataset_snapshot WHERE workspace_id=%s AND dataset_id=%s",
                    (workspace_id, dataset_id),
                ).fetchone()
                is None
            ):
                raise PortError(ErrorCode.NOT_FOUND, "dataset unavailable")
            rows = conn.execute(
                """SELECT payload FROM market_bar WHERE workspace_id=%s AND dataset_id=%s
                AND timestamp>=%s AND timestamp<%s
                ORDER BY timestamp,instrument_id,granularity LIMIT %s""",
                (workspace_id, dataset_id, start, end, limit + 1),
            ).fetchall()
        if len(rows) > limit:
            return None
        try:
            return tuple(Bar.model_validate_json(json.dumps(row[0])) for row in rows)
        except (ValueError, TypeError):
            raise PortError(ErrorCode.UNAVAILABLE, "invalid stored bar") from None

    def query_bars(self, query: BarQuery) -> BarPage:
        position = after(query)
        with self._connection() as conn:
            if (
                conn.execute(
                    "SELECT 1 FROM market_dataset_snapshot WHERE workspace_id=%s AND dataset_id=%s",
                    (query.workspace_id, query.dataset_id),
                ).fetchone()
                is None
            ):
                raise PortError(ErrorCode.NOT_FOUND, "dataset unavailable")
            rows = conn.execute(
                """SELECT payload FROM market_bar WHERE workspace_id=%s AND dataset_id=%s
                AND instrument_id=%s AND granularity=%s AND timestamp>=%s AND timestamp<%s
                AND (%s::timestamptz IS NULL OR timestamp>%s)
                ORDER BY timestamp LIMIT %s""",
                (
                    query.workspace_id,
                    query.dataset_id,
                    query.instrument_id,
                    query.granularity,
                    query.start,
                    query.end,
                    position,
                    position,
                    query.limit + 1,
                ),
            ).fetchall()
        try:
            bars = tuple(Bar.model_validate_json(json.dumps(row[0])) for row in rows)
        except (ValueError, TypeError):
            raise PortError(ErrorCode.UNAVAILABLE, "invalid stored bar") from None
        page = bars[: query.limit]
        return BarPage(page, cursor(query, page[-1].timestamp) if len(bars) > query.limit else None)
