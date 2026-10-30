"""Infrastructure readiness shared by the process composition entries."""

import os
from urllib.parse import urlsplit

import httpx
import psycopg
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from redis import Redis
from redis.exceptions import RedisError

from qtrade_runtime.observability import instrument_app


def create_health_app(service: str) -> FastAPI:
    app = FastAPI(title=service)
    instrument_app(app, service)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": service}

    @app.get("/ready")
    def ready() -> JSONResponse:
        checks: dict[str, bool] = {}
        try:
            with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=3) as connection:
                connection.execute("SELECT 1")
            checks["database"] = True
        except (psycopg.Error, KeyError):
            checks["database"] = False
        if "MARKET_DATABASE_URL" in os.environ:
            try:
                with psycopg.connect(
                    os.environ["MARKET_DATABASE_URL"], connect_timeout=3
                ) as connection:
                    row = connection.execute(
                        "SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname='timescaledb') "
                        "AND to_regclass('qtrade_market.market_bar') IS NOT NULL"
                    ).fetchone()
                checks["market_database"] = row is not None and row[0] is True
            except (psycopg.Error, KeyError):
                checks["market_database"] = False
        try:
            with Redis.from_url(
                os.environ["REDIS_URL"], socket_timeout=3, socket_connect_timeout=3
            ) as cache:
                checks["cache"] = bool(cache.ping())
        except (RedisError, OSError, KeyError):
            checks["cache"] = False
        try:
            endpoint = os.environ["OBJECT_STORE_URL"]
            if urlsplit(endpoint).scheme not in {"http", "https"}:
                raise ValueError("object-store probe requires HTTP(S)")
            response = httpx.get(
                endpoint + "/minio/health/ready", timeout=3, follow_redirects=False
            )
            checks["object_store"] = response.status_code == 200
        except (httpx.HTTPError, OSError, KeyError, ValueError):
            checks["object_store"] = False
        for dependency, available in checks.items():
            app.state.telemetry.dependencies.labels(dependency).set(int(available))
        ok = all(checks.values())
        return JSONResponse(
            {"status": "ready" if ok else "unavailable", "service": service, "checks": checks},
            status_code=200 if ok else 503,
        )

    return app
