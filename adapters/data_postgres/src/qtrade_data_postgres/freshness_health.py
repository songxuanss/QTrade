"""One read-only MVCC page across saved checks, watches and consumer acknowledgements."""

import json

from qtrade_data_contracts.freshness import FreshnessKey
from qtrade_data_contracts.freshness_health import ConsumerState, FreshnessHealthQuery
from qtrade_data_core.freshness_health import HealthBatch, HealthRecord
from qtrade_data_core.ports import DataFailure
from qtrade_data_postgres.freshness import PostgresFreshness
from qtrade_data_postgres.freshness_watch import PostgresFreshnessWatches


class PostgresFreshnessHealth:
    def __init__(self, url: str):
        self.repository = PostgresFreshness(url)
        self.url = url

    def page(self, query: FreshnessHealthQuery, consumer: str) -> HealthBatch:
        """Return one scoped keyset page without advancing any delivery cursor.

        A disabled watch still belongs to the inventory. The inventory excludes
        publications with neither a check nor a watch; absence is not health.
        """
        with self.repository._connection() as conn:
            # Concurrent checks/acks must not splice different database views
            # into one card. Pagination is bounded but not a cross-page snapshot.
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            conn.execute("SET LOCAL statement_timeout='10s'")
            now = conn.execute("SELECT clock_timestamp()").fetchone()
            if now is None:
                raise DataFailure("unavailable")
            read_at = now[0]
            keys = conn.execute(
                """SELECT key_sha256 FROM (
                SELECT key_sha256 FROM qtrade_data.collection_freshness_snapshot WHERE scope_id=%s
                UNION SELECT key_sha256 FROM qtrade_data.collection_freshness_watch
                WHERE scope_id=%s
                ) inventory WHERE (%s::text IS NULL OR key_sha256>%s)
                ORDER BY key_sha256 LIMIT %s""",
                (query.scope_id, query.scope_id, query.after_key, query.after_key, query.limit + 1),
            ).fetchall()
            scoped = PostgresFreshness(self.url, conn)
            records = []
            for (key_hash,) in keys[: query.limit]:
                snapshot_row = conn.execute(
                    """SELECT snapshot_id,key_sha256,checked_at,
                    result_sha256,payload
                    FROM qtrade_data.collection_freshness_snapshot
                    WHERE scope_id=%s AND key_sha256=%s
                    ORDER BY checked_at DESC LIMIT 1""",
                    (query.scope_id, key_hash),
                ).fetchone()
                watch_row = conn.execute(
                    """SELECT scope_id,watch_id,key_sha256,revision,enabled,
                    next_due_at,payload
                    FROM qtrade_data.collection_freshness_watch
                    WHERE scope_id=%s AND key_sha256=%s""",
                    (query.scope_id, key_hash),
                ).fetchone()
                watch = PostgresFreshnessWatches._read(watch_row) if watch_row else None
                try:
                    if watch is not None:
                        key = watch.key
                    elif snapshot_row is not None:
                        key = FreshnessKey.model_validate_json(json.dumps(snapshot_row[4]["key"]))
                    else:
                        raise DataFailure("unverified")
                except (ValueError, TypeError, KeyError):
                    raise DataFailure("unverified") from None
                if key.scope_id != query.scope_id:
                    raise DataFailure("unverified")
                snapshot = scoped._read(snapshot_row, key) if snapshot_row else None
                notices = scoped.notices(key, limit=1)
                notice = notices[0] if notices else None
                state: ConsumerState = "not_applicable" if notice is None else "not_claimed"
                expired = False
                if notice:
                    # 'delivered' here is the collector consumer's durable ack,
                    # not proof that an external notification channel delivered.
                    delivery = conn.execute(
                        """SELECT state,lease_expires_at
                        FROM qtrade_data.collection_freshness_delivery
                        WHERE scope_id=%s AND notice_id=%s AND consumer_id=%s""",
                        (query.scope_id, notice.notice_id, consumer),
                    ).fetchone()
                    if delivery:
                        raw, expires = delivery
                        state = {
                            "pending": "pending",
                            "leased": "leased",
                            "delivered": "delivered",
                        }.get(raw)  # type: ignore[assignment]
                        if state is None:
                            raise DataFailure("unverified")
                        expired = state == "leased" and expires is not None and expires <= read_at
                name = conn.execute(
                    """SELECT publication->>'instrument_name'
                    FROM qtrade_data.collection_part WHERE scope_id=%s AND state='completed'
                    AND publication->>'source'=%s AND publication->>'instrument_id'=%s
                    AND publication->>'mapping_version'=%s
                    ORDER BY updated_at DESC,part_id DESC LIMIT 1""",
                    (query.scope_id, key.source, key.instrument_id, key.mapping_version),
                ).fetchone()
                records.append(
                    HealthRecord(
                        key,
                        name[0] if name and name[0] else "名称未核验",
                        snapshot,
                        watch,
                        notice,
                        state,
                        expired,
                    )
                )
            return HealthBatch(
                read_at,
                tuple(records),
                keys[query.limit - 1][0] if len(keys) > query.limit else None,
            )
