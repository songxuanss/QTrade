from fastapi.testclient import TestClient

from qtrade_runtime.health import create_health_app


def test_liveness_is_independent_of_dependencies(monkeypatch):
    for key in ("DATABASE_URL", "MARKET_DATABASE_URL", "REDIS_URL", "OBJECT_STORE_URL"):
        monkeypatch.delenv(key, raising=False)
    with TestClient(create_health_app("probe-test")) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "service": "probe-test"}
        readiness = client.get("/ready")
        assert readiness.status_code == 503
        assert readiness.json()["checks"] == {
            "database": False,
            "cache": False,
            "object_store": False,
        }
