"""Comparable ephemeral TimescaleDB/ClickHouse OHLCV microbenchmark.

Requires Docker and the pinned images in docs/adr/0002-market-data-store.md.
This is a local phase-0 workload probe, not a production-capacity claim.
"""

from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import random
import socket
import subprocess
import time
import urllib.request
import uuid
from statistics import median

import psycopg

TIMESCALE_IMAGE = (
    "timescale/timescaledb:2.30.2-pg17@sha256:"
    "b346edcdb51a1fd6020e3965e0bd1c9f3406fa6d5fbce1e28f4852587ef934e2"
)
CLICKHOUSE_IMAGE = (
    "clickhouse/clickhouse-server:26.8.4@sha256:"
    "b1d9f9baec7727c779a6febb2a95eb08a8e1de3f463294e3ebb35c3d3fb30360"
)


def port() -> int:
    with socket.socket() as handle:
        handle.bind(("127.0.0.1", 0))
        return handle.getsockname()[1]


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def wait_pg(url: str) -> None:
    for _ in range(120):
        try:
            with psycopg.connect(url, connect_timeout=1):
                return
        except psycopg.Error:
            time.sleep(1)
    raise RuntimeError("TimescaleDB did not become ready")


def ch(port_number: int, statement: str, password: str, query_id: str | None = None) -> str:
    suffix = f"?query_id={query_id}" if query_id else ""
    request = urllib.request.Request(
        f"http://127.0.0.1:{port_number}/{suffix}",
        data=statement.encode(),
        method="POST",
    )
    authorization = base64.b64encode(f"default:{password}".encode()).decode()
    request.add_header("Authorization", f"Basic {authorization}")
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read().decode()


def elapsed(action) -> float:
    start = time.perf_counter()
    action()
    return time.perf_counter() - start


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=1_000_000)
    parser.add_argument("--repeats", type=int, default=7)
    args = parser.parse_args()
    if args.rows < 100_000 or args.repeats < 3:
        parser.error("use at least 100000 rows and 3 query repetitions")
    suffix = uuid.uuid4().hex[:10]
    pg_name, ch_name = f"qtrade-bench-ts-{suffix}", f"qtrade-bench-ch-{suffix}"
    pg_port, ch_port = port(), port()
    pg_password = f"bench-{suffix}"
    ch_password = f"bench-{suffix}"
    pg_url = f"postgresql://postgres:{pg_password}@127.0.0.1:{pg_port}/postgres"
    created: list[str] = []
    try:
        run(
            "docker",
            "run",
            "-d",
            "--name",
            pg_name,
            "-e",
            "POSTGRES_PASSWORD=" + pg_password,
            "-p",
            f"127.0.0.1:{pg_port}:5432",
            TIMESCALE_IMAGE,
        )
        created.append(pg_name)
        run(
            "docker",
            "run",
            "-d",
            "--name",
            ch_name,
            "-e",
            "CLICKHOUSE_USER=default",
            "-e",
            "CLICKHOUSE_PASSWORD=" + ch_password,
            "-p",
            f"127.0.0.1:{ch_port}:8123",
            CLICKHOUSE_IMAGE,
        )
        created.append(ch_name)
        wait_pg(pg_url)
        for _ in range(120):
            try:
                ch(ch_port, "SELECT 1", ch_password)
                break
            except Exception:
                time.sleep(1)
        else:
            raise RuntimeError("ClickHouse did not become ready")

        with psycopg.connect(pg_url) as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
            conn.execute(
                "CREATE TABLE bars (instrument_id text NOT NULL, ts timestamptz NOT NULL, open float8 NOT NULL, high float8 NOT NULL, low float8 NOT NULL, close float8 NOT NULL, volume float8 NOT NULL, PRIMARY KEY(instrument_id, ts))"  # noqa: E501
            )
            conn.execute(
                "SELECT create_hypertable('bars','ts',if_not_exists=>TRUE,migrate_data=>TRUE)"
            )
        ch(
            ch_port,
            "CREATE TABLE bars (instrument_id String, ts DateTime64(0,'UTC'), open Float64, high Float64, low Float64, close Float64, volume Float64) ENGINE=MergeTree ORDER BY (instrument_id,ts)",  # noqa: E501
            ch_password,
        )

        csv_rows = io.StringIO()
        writer = csv.writer(csv_rows, lineterminator="\n")
        base = 1_704_067_200
        rng = random.Random(42)
        prices = [50.0 + symbol / 10 for symbol in range(1000)]
        for index in range(args.rows):
            instrument = index % 1000
            timestamp = base + (index // 1000) * 60
            opening = prices[instrument]
            closing = max(0.01, opening * (1 + rng.gauss(0, 0.0005)))
            prices[instrument] = closing
            high = max(opening, closing) * (1 + rng.random() * 0.0004)
            low = min(opening, closing) * (1 - rng.random() * 0.0004)
            row = (
                f"I{instrument:04}",
                time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(timestamp)),
                opening,
                high,
                low,
                closing,
                rng.randrange(100, 1_000_000),
            )
            writer.writerow(row)
        data = csv_rows.getvalue()
        start = time.perf_counter()
        with psycopg.connect(pg_url) as conn:
            with conn.cursor().copy("COPY bars FROM STDIN WITH (FORMAT CSV)") as copy:
                copy.write(data)
        pg_write = time.perf_counter() - start
        start = time.perf_counter()
        ch(ch_port, "INSERT INTO bars FORMAT CSV\n" + data, ch_password)
        ch_write = time.perf_counter() - start
        with psycopg.connect(pg_url) as conn:
            pg_size = conn.execute(
                "SELECT total_bytes FROM hypertable_detailed_size('bars')"
            ).fetchone()[0]
            range_query = (
                "SELECT count(*),avg(close) FROM bars WHERE instrument_id='I0001' "
                "AND ts >= to_timestamp(%s) AND ts < to_timestamp(%s)"
            )
            range_params = (base, base + max(1, args.rows // 1000 // 2) * 60)
            scan_query = (
                "SELECT ts,avg(close),sum(volume) FROM bars "
                "WHERE ts >= to_timestamp(%s) AND ts < to_timestamp(%s) "
                "GROUP BY ts ORDER BY ts"
            )
            scan_params = (
                base + max(0, args.rows // 1000 - 100) * 60,
                base + max(1, args.rows // 1000) * 60,
            )

            def pg_execution(query: str, params: tuple[int, int]) -> float:
                plan = conn.execute("EXPLAIN (ANALYZE, FORMAT JSON) " + query, params).fetchone()[
                    0
                ][0]
                return float(plan["Execution Time"]) / 1000

            pg_range_times = [pg_execution(range_query, range_params) for _ in range(args.repeats)]
            pg_scan_times = [pg_execution(scan_query, scan_params) for _ in range(args.repeats)]
        ch_size = int(
            ch(
                ch_port,
                "SELECT sum(bytes_on_disk) FROM system.parts WHERE table='bars' AND active FORMAT TabSeparated\n",  # noqa: E501
                ch_password,
            )
        )
        ch_range = (
            f"SELECT count(),avg(close) FROM bars WHERE instrument_id='I0001' "
            f"AND ts >= toDateTime({base}) "
            f"AND ts < toDateTime({base + max(1, args.rows // 1000 // 2) * 60})"
        )
        lower, upper = (
            base + max(0, args.rows // 1000 - 100) * 60,
            base + max(1, args.rows // 1000) * 60,
        )
        ch_scan = (
            f"SELECT ts,avg(close),sum(volume) FROM bars WHERE ts >= toDateTime({lower}) "
            f"AND ts < toDateTime({upper}) GROUP BY ts ORDER BY ts FORMAT JSONEachRow"
        )

        def ch_execution(query: str) -> float:
            query_id = str(uuid.uuid4())
            ch(ch_port, query, ch_password, query_id)
            ch(ch_port, "SYSTEM FLUSH LOGS", ch_password)
            duration_ms = ch(
                ch_port,
                "SELECT query_duration_ms FROM system.query_log "
                f"WHERE query_id='{query_id}' AND type='QueryFinish' FORMAT TabSeparated\n",  # noqa: E501
                ch_password,
            )
            return float(duration_ms.strip()) / 1000

        ch_range_times = [ch_execution(ch_range) for _ in range(args.repeats)]
        ch_scan_times = [ch_execution(ch_scan) for _ in range(args.repeats)]
        print(
            json.dumps(
                {
                    "rows": args.rows,
                    "symbols": 1000,
                    "repeats": args.repeats,
                    "versions": {"timescaledb": TIMESCALE_IMAGE, "clickhouse": CLICKHOUSE_IMAGE},
                    "seconds": {
                        "timescale_load": pg_write,
                        "clickhouse_load": ch_write,
                        "query_timing": (
                            "database execution time; PostgreSQL EXPLAIN ANALYZE and "
                            "ClickHouse query_log (millisecond precision)"
                        ),
                        "single_symbol_range_median": {
                            "timescale": median(pg_range_times),
                            "clickhouse": median(ch_range_times),
                        },
                        "recent_cross_section_median": {
                            "timescale": median(pg_scan_times),
                            "clickhouse": median(ch_scan_times),
                        },
                    },
                    "bytes": {"timescale_relation": pg_size, "clickhouse_active_parts": ch_size},
                },
                indent=2,
            )
        )
    finally:
        for name in reversed(created):
            subprocess.run(["docker", "rm", "-f", name], check=False, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
