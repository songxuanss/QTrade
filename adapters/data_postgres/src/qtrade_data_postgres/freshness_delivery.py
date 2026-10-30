"""Recoverable outbox leases, with per-key ordering and fenced acknowledgements."""

import json
from uuid import UUID, uuid4

from qtrade_data_contracts.freshness_delivery import FreshnessNoticeClaim, FreshnessNoticeLease
from qtrade_data_contracts.freshness_notice import FreshnessNotice
from qtrade_data_core.ports import DataFailure
from qtrade_data_postgres.freshness import PostgresFreshness


class PostgresFreshnessDelivery:
    def __init__(self, database_url: str, *, lease_seconds: int = 60):
        if type(lease_seconds) is not int or not 10 <= lease_seconds <= 300:
            raise ValueError("bounded freshness delivery lease required")
        self.repository = PostgresFreshness(database_url)
        self.lease_seconds = lease_seconds

    def claim(self, request: FreshnessNoticeClaim) -> tuple[FreshnessNoticeLease, ...]:
        request = FreshnessNoticeClaim.model_validate_json(request.model_dump_json())
        with self.repository._connection() as conn:
            conn.execute("SET LOCAL lock_timeout='5s'")
            # Bootstrap at most 100 new rows per poll. A newly added consumer
            # can catch up incrementally without copying an unbounded history.
            conn.execute(
                """INSERT INTO qtrade_data.collection_freshness_delivery
                (scope_id,notice_id,consumer_id)
                SELECT n.scope_id,n.notice_id,%s
                FROM qtrade_data.collection_freshness_notice n
                WHERE NOT EXISTS(SELECT 1 FROM qtrade_data.collection_freshness_delivery d
                    WHERE d.scope_id=n.scope_id AND d.notice_id=n.notice_id AND d.consumer_id=%s)
                ORDER BY n.sequence LIMIT 100 ON CONFLICT DO NOTHING""",
                (request.consumer_id, request.consumer_id),
            )
            rows = conn.execute(
                """SELECT n.scope_id,n.notice_id,n.key_sha256,n.episode_id,n.kind,
                n.occurred_at,n.snapshot_id,n.notice_sha256,n.payload
                FROM qtrade_data.collection_freshness_delivery d
                JOIN qtrade_data.collection_freshness_notice n
                    ON n.scope_id=d.scope_id AND n.notice_id=d.notice_id
                WHERE d.consumer_id=%s AND d.state<>'delivered'
                AND d.available_at<=clock_timestamp()
                AND NOT EXISTS(
                    SELECT 1 FROM qtrade_data.collection_freshness_notice earlier
                    WHERE earlier.scope_id=n.scope_id AND earlier.key_sha256=n.key_sha256
                    AND earlier.sequence<n.sequence AND NOT EXISTS(
                        SELECT 1 FROM qtrade_data.collection_freshness_delivery done
                        WHERE done.scope_id=earlier.scope_id AND done.notice_id=earlier.notice_id
                        AND done.consumer_id=%s AND done.state='delivered'))
                ORDER BY d.available_at,n.sequence LIMIT %s FOR UPDATE OF d SKIP LOCKED""",
                (request.consumer_id, request.consumer_id, request.limit),
            ).fetchall()
            leases = []
            for row in rows:
                try:
                    notice = FreshnessNotice.model_validate_json(json.dumps(row[8]))
                    if notice.scope_id != row[0]:
                        raise ValueError("delivery scope mismatch")
                    notice = self.repository._read_notice(tuple(row[1:]), notice.result.key)
                except (TypeError, ValueError):
                    raise DataFailure("unverified") from None
                token = uuid4()
                expires = conn.execute(
                    """UPDATE qtrade_data.collection_freshness_delivery
                    SET state='leased',attempts=attempts+1,lease_token=%s,
                    lease_expires_at=clock_timestamp()+(%s*interval '1 second'),
                    available_at=clock_timestamp()+(%s*interval '1 second')
                    WHERE scope_id=%s AND notice_id=%s AND consumer_id=%s
                    RETURNING lease_expires_at""",
                    (
                        token,
                        self.lease_seconds,
                        self.lease_seconds,
                        notice.scope_id,
                        notice.notice_id,
                        request.consumer_id,
                    ),
                ).fetchone()
                if expires is None:
                    raise DataFailure("unavailable")
                leases.append(
                    FreshnessNoticeLease(
                        schema_version=1,
                        scope_id=notice.scope_id,
                        consumer_id=request.consumer_id,
                        notice=notice,
                        token=token,
                        expires_at=expires[0],
                    )
                )
            return tuple(leases)

    def acknowledge(
        self, actor: UUID, scope: UUID, notice_id: UUID, consumer: str, token: UUID
    ) -> bool:
        """Only the current lease holder may acknowledge, after durable consumption.

        A repeated acknowledgement of the already accepted token is idempotent;
        expiry or reassignment cannot be defeated by an old worker's response.
        Caller authentication/current manage permission is enforced by the route.
        """
        with self.repository._connection() as conn:
            row = conn.execute(
                """SELECT d.state,d.lease_token,d.lease_expires_at,
                n.payload->>'actor_id' FROM qtrade_data.collection_freshness_delivery d
                JOIN qtrade_data.collection_freshness_notice n
                    ON n.scope_id=d.scope_id AND n.notice_id=d.notice_id
                WHERE d.scope_id=%s AND d.notice_id=%s AND d.consumer_id=%s FOR UPDATE OF d""",
                (scope, notice_id, consumer),
            ).fetchone()
            if row is None or row[1] != token or row[3] != str(actor):
                return False
            if row[0] == "delivered":
                return True
            changed = conn.execute(
                """UPDATE qtrade_data.collection_freshness_delivery
                SET state='delivered',delivered_at=clock_timestamp(),lease_expires_at=NULL
                WHERE scope_id=%s AND notice_id=%s AND consumer_id=%s AND state='leased'
                AND lease_token=%s AND lease_expires_at>clock_timestamp() RETURNING notice_id""",
                (scope, notice_id, consumer, token),
            ).fetchone()
            return changed is not None
