"""Atomic source/scoped credit admission and fenced completion, without vendor URLs."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, time, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

from qtrade_data_contracts.usage import (
    UsageBudget,
    UsageDenialView,
    UsageRequestView,
    UsageSnapshot,
)
from qtrade_data_core.ports import DataFailure
from qtrade_data_core.usage import UsageDenied, UsageLease, UsagePolicy, UsageRequest, UsageResult


class PostgresUsageLedger:
    def __init__(self, database_url: str):
        self._url = database_url

    @contextmanager
    def _connection(self) -> Iterator[psycopg.Connection[Any]]:
        try:
            with psycopg.connect(self._url) as conn:
                yield conn
        except psycopg.Error:
            raise DataFailure("unavailable") from None

    @staticmethod
    def _time(now: datetime) -> None:
        if not isinstance(now, datetime) or now.utcoffset() is None:
            raise ValueError("explicit usage clock required")

    def reserve(self, request: UsageRequest, policy: UsagePolicy, now: datetime) -> UsageLease:
        try:
            return self._reserve(request, policy, now)
        except UsageDenied as denied:
            # Admission rolls back first; otherwise raising the rejection would
            # silently roll its diagnostic record back along with the credits.
            with self._connection() as conn:
                conn.execute(
                    "INSERT INTO qtrade_data.usage_denial "
                    "(request_id,source,scope_id,operation,credits,day,denied_at,reason,policy) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING",
                    (
                        request.request_id,
                        request.source,
                        request.scope_id,
                        request.operation,
                        request.credits,
                        now.astimezone(ZoneInfo(policy.timezone)).date(),
                        now,
                        denied.reason,
                        json.dumps(policy.payload()),
                    ),
                )
            raise

    def _reserve(self, request: UsageRequest, policy: UsagePolicy, now: datetime) -> UsageLease:
        self._time(now)
        day = now.astimezone(ZoneInfo(policy.timezone)).date()
        with self._connection() as conn:
            # A request identity lock precedes the source lock. Reuse under a
            # different source cannot race the unique row or double-charge.
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                ("usage-id/" + str(request.request_id),),
            )
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                ("usage-source/" + request.source,),
            )
            conn.execute(
                "UPDATE qtrade_data.usage_request SET state='expired',finished_at=%s,"
                "outcome='expired' "
                "WHERE source=%s AND state='reserved' AND lease_until<=%s",
                (now, request.source, now),
            )
            row = conn.execute(
                "SELECT source,scope_id,operation,credits,token,day,lease_until,state "
                "FROM qtrade_data.usage_request WHERE request_id=%s",
                (request.request_id,),
            ).fetchone()
            if row:
                if row[:4] != (
                    request.source,
                    request.scope_id,
                    request.operation,
                    request.credits,
                ):
                    raise DataFailure("conflict")

                return UsageLease(request, row[4], row[5], row[6], False, row[7])
            keys = ["source"] + ([str(request.scope_id)] if request.scope_id else [])
            spent = {}
            for key in keys:
                conn.execute(
                    "INSERT INTO qtrade_data.usage_day(source,day,scope_key) "
                    "VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                    (request.source, day, key),
                )
                credit_row = conn.execute(
                    "SELECT credits FROM qtrade_data.usage_day "
                    "WHERE source=%s AND day=%s AND scope_key=%s",
                    (request.source, day, key),
                ).fetchone()
                if credit_row is None:
                    raise DataFailure("unavailable")
                spent[key] = credit_row[0]
            if spent["source"] + request.credits > policy.source_daily:
                raise UsageDenied("source_daily")
            if (
                request.scope_id
                and spent[str(request.scope_id)] + request.credits > policy.scope_daily
            ):
                raise UsageDenied("scope_daily")
            active = conn.execute(
                "SELECT count(*),count(*) FILTER (WHERE scope_id=%s) "
                "FROM qtrade_data.usage_request "
                "WHERE source=%s AND state='reserved' AND lease_until>%s",
                (request.scope_id, request.source, now),
            ).fetchone()
            if active is None:
                raise DataFailure("unavailable")
            if active[0] >= policy.source_concurrency:
                raise UsageDenied("source_concurrency")
            if request.scope_id and active[1] >= policy.scope_concurrency:
                raise UsageDenied("scope_concurrency")
            token, until = uuid4(), now + timedelta(seconds=policy.lease_seconds)
            conn.execute(
                "INSERT INTO qtrade_data.usage_request(request_id,source,scope_id,"
                "operation,credits,"
                "day,policy,token,reserved_at,lease_until) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)",
                (
                    request.request_id,
                    request.source,
                    request.scope_id,
                    request.operation,
                    request.credits,
                    day,
                    json.dumps(policy.payload()),
                    token,
                    now,
                    until,
                ),
            )
            for key in keys:
                conn.execute(
                    "UPDATE qtrade_data.usage_day SET credits=credits+%s,requests=requests+1 "
                    "WHERE source=%s AND day=%s AND scope_key=%s",
                    (request.credits, request.source, day, key),
                )
            return UsageLease(request, token, day, until, True, "reserved")

    def finish(self, lease: UsageLease, result: UsageResult, now: datetime) -> None:
        self._time(now)
        if not lease.newly_reserved or lease.state != "reserved":
            raise DataFailure("conflict")
        with self._connection() as conn:
            updated = conn.execute(
                "UPDATE qtrade_data.usage_request SET state='finished',finished_at=%s,outcome=%s,"
                "error_code=%s,duration_ms=%s,response_bytes=%s WHERE request_id=%s AND token=%s "
                "AND state='reserved' AND lease_until>%s AND reserved_at<=%s AND source=%s "
                "AND scope_id IS NOT DISTINCT FROM %s",
                (
                    now,
                    result.outcome,
                    result.error_code,
                    result.duration_ms,
                    result.response_bytes,
                    lease.request.request_id,
                    lease.token,
                    now,
                    now,
                    lease.request.source,
                    lease.request.scope_id,
                ),
            )
            if updated.rowcount != 1:
                # A lost completion acknowledgement can be retried with the
                # same frozen metrics; changed results or stale owners conflict.
                row = conn.execute(
                    "SELECT token,state,outcome,error_code,duration_ms,response_bytes "
                    "FROM qtrade_data.usage_request WHERE request_id=%s AND source=%s "
                    "AND scope_id IS NOT DISTINCT FROM %s",
                    (lease.request.request_id, lease.request.source, lease.request.scope_id),
                ).fetchone()
                if row != (
                    lease.token,
                    "finished",
                    result.outcome,
                    result.error_code,
                    result.duration_ms,
                    result.response_bytes,
                ):
                    raise DataFailure("conflict")

    def snapshot(
        self, source: str, scope: UUID, policy: UsagePolicy, now: datetime
    ) -> UsageSnapshot:
        """Read one consistent scoped snapshot; never mutate expiry or charge a read.

        Global budget aggregates are shared safeguards; request/denial details
        are strictly scoped. Active reservations include previous calendar days.
        A lapsed reservation is evidence of lost ownership, not a live socket.
        """
        self._time(now)
        day = now.astimezone(ZoneInfo(policy.timezone)).date()
        with self._connection() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            credits = dict(
                conn.execute(
                    "SELECT scope_key,credits FROM qtrade_data.usage_day "
                    "WHERE source=%s AND day=%s AND scope_key IN ('source',%s)",
                    (source, day, str(scope)),
                ).fetchall()
            )
            active = conn.execute(
                "SELECT count(*),count(*) FILTER(WHERE scope_id=%s) "
                "FROM qtrade_data.usage_request WHERE source=%s AND state='reserved' "
                "AND lease_until>%s",
                (scope, source, now),
            ).fetchone()
            totals = conn.execute(
                "SELECT count(*),count(*) FILTER(WHERE outcome='success'),"
                "count(*) FILTER(WHERE outcome='failure'),"
                "count(*) FILTER(WHERE state='expired' OR (state='reserved' AND lease_until<=%s)),"
                "coalesce(sum(response_bytes),0) FROM qtrade_data.usage_request "
                "WHERE source=%s AND scope_id=%s AND day=%s",
                (now, source, scope, day),
            ).fetchone()
            denial = conn.execute(
                "SELECT count(*) FROM qtrade_data.usage_denial "
                "WHERE source=%s AND scope_id=%s AND day=%s",
                (source, scope, day),
            ).fetchone()
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT request_id,operation,credits,reserved_at,lease_until,state,"
                    "(lease_until<=%s) AS lease_elapsed,outcome,error_code,"
                    "duration_ms,response_bytes "
                    "FROM qtrade_data.usage_request WHERE source=%s AND scope_id=%s AND day=%s "
                    "ORDER BY reserved_at DESC,request_id DESC LIMIT 50",
                    (now, source, scope, day),
                )
                requests = cur.fetchall()
                cur.execute(
                    "SELECT request_id,operation,credits,reason,denied_at "
                    "FROM qtrade_data.usage_denial WHERE source=%s AND scope_id=%s AND day=%s "
                    "ORDER BY denied_at DESC,request_id DESC LIMIT 50",
                    (source, scope, day),
                )
                denials = cur.fetchall()
        if active is None or totals is None or denial is None:
            raise DataFailure("unavailable")
        return UsageSnapshot(
            schema_version=1,
            scope_id=scope,
            source=source,
            day=day,
            timezone=policy.timezone,
            generated_at=now,
            resets_at=datetime.combine(day + timedelta(days=1), time(), ZoneInfo(policy.timezone)),
            source_budget=UsageBudget(
                daily_limit=policy.source_daily,
                credits_used=credits.get("source", 0),
                active_reservations=active[0],
                concurrency_limit=policy.source_concurrency,
            ),
            scope_budget=UsageBudget(
                daily_limit=policy.scope_daily,
                credits_used=credits.get(str(scope), 0),
                active_reservations=active[1],
                concurrency_limit=policy.scope_concurrency,
            ),
            request_count=totals[0],
            success_count=totals[1],
            failure_count=totals[2],
            expired_count=totals[3],
            response_bytes=int(totals[4]),
            denial_count=denial[0],
            recent_requests=tuple(UsageRequestView.model_validate(r) for r in requests),
            recent_denials=tuple(UsageDenialView.model_validate(r) for r in denials),
            accounting="internal_credit_safeguard",
        )
