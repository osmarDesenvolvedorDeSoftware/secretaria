from __future__ import annotations

from datetime import datetime, timezone

import pytest


def _prime_worker(redis_client):
    redis_client.sadd("rq:workers", "rq:worker:test")
    redis_client.hset(
        "rq:worker:test",
        {
            "last_heartbeat": datetime.now(timezone.utc).isoformat(),
        },
    )
    redis_client.expire("rq:worker:test", 120)


@pytest.fixture(autouse=True)
def prepare_worker(app):
    _prime_worker(app.redis)  # type: ignore[attr-defined]
    yield


def test_healthz_ok(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ok"
    assert payload["dependencies"]["postgres"]["status"] == "ok"
    assert payload["dependencies"]["redis"]["status"] == "ok"
    assert payload["dependencies"]["rq_worker"]["status"] == "ok"
    assert "latency_ms" in payload["dependencies"]["postgres"]


def test_healthz_redis_down(client, app, monkeypatch):
    def fail_ping():
        raise ConnectionError("redis down")

    monkeypatch.setattr(app.redis, "ping", fail_ping, raising=False)  # type: ignore[attr-defined]
    response = client.get("/healthz")
    assert response.status_code == 503
    payload = response.get_json()
    assert payload["status"] == "degraded"
    assert payload["dependencies"]["redis"]["status"] == "error"


def test_healthz_postgres_down(client, app, monkeypatch):
    def fail_connect(*_, **__):
        raise RuntimeError("db down")

    monkeypatch.setattr(app.db_engine, "connect", fail_connect, raising=False)  # type: ignore[attr-defined]
    response = client.get("/healthz")
    assert response.status_code == 503
    payload = response.get_json()
    assert payload["dependencies"]["postgres"]["status"] == "error"
