from __future__ import annotations

import time
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify
from rq.worker import Worker
from sqlalchemy import text

from app.metrics import healthcheck_failures_total
from app.services.security import sanitize_for_log

health_bp = Blueprint("health", __name__)


@health_bp.get("/healthz")
def healthcheck():
    app = current_app
    dependencies: dict[str, dict[str, object]] = {}
    http_status = 200

    # PostgreSQL check
    db_start = time.perf_counter()
    try:
        with app.db_engine.connect() as conn:  # type: ignore[attr-defined]
            conn.execute(text("SELECT 1"))
        db_latency = round((time.perf_counter() - db_start) * 1000, 2)
        dependencies["postgres"] = {"status": "ok", "latency_ms": db_latency}
    except Exception as exc:  # pragma: no cover - exercised in tests
        http_status = 503
        dependencies["postgres"] = {
            "status": "error",
            "latency_ms": None,
            "error": sanitize_for_log(str(exc)),
        }
        healthcheck_failures_total.labels(component="postgres").inc()

    # Redis check
    redis_start = time.perf_counter()
    try:
        app.redis.ping()  # type: ignore[attr-defined]
        redis_latency = round((time.perf_counter() - redis_start) * 1000, 2)
        dependencies["redis"] = {"status": "ok", "latency_ms": redis_latency}
    except Exception as exc:  # pragma: no cover - exercised in tests
        http_status = 503
        dependencies["redis"] = {
            "status": "error",
            "latency_ms": None,
            "error": sanitize_for_log(str(exc)),
        }
        healthcheck_failures_total.labels(component="redis").inc()

    # RQ worker heartbeat
    worker_start = time.perf_counter()
    worker_status: dict[str, object]
    try:
        redis_client = app.redis  # type: ignore[attr-defined]
        worker_keys = redis_client.smembers(Worker.redis_workers_keys)
        active_workers = 0
        latest_age: float | None = None
        now = datetime.now(timezone.utc)
        for worker_key in worker_keys:
            info = redis_client.hgetall(worker_key) or {}
            heartbeat_raw = info.get("last_heartbeat")
            age = None
            if heartbeat_raw:
                try:
                    last_heartbeat = datetime.fromisoformat(heartbeat_raw)
                    age = (now - last_heartbeat).total_seconds()
                except ValueError:
                    age = None
            ttl = redis_client.ttl(worker_key)
            if ttl is None or ttl < 0:
                ttl = None
            if age is not None and age > 180:
                continue
            if ttl is not None and ttl <= 0:
                continue
            active_workers += 1
            if age is not None:
                latest_age = age if latest_age is None else min(latest_age, age)
        if active_workers == 0:
            raise RuntimeError("Nenhum worker RQ ativo")
        worker_latency = round((time.perf_counter() - worker_start) * 1000, 2)
        worker_status = {
            "status": "ok",
            "latency_ms": worker_latency,
            "workers": active_workers,
        }
        if latest_age is not None:
            worker_status["freshest_heartbeat_age_seconds"] = round(latest_age, 3)
        dependencies["rq_worker"] = worker_status
    except Exception as exc:  # pragma: no cover - exercised in tests
        http_status = 503
        dependencies["rq_worker"] = {
            "status": "error",
            "latency_ms": None,
            "error": sanitize_for_log(str(exc)),
        }
        healthcheck_failures_total.labels(component="rq_worker").inc()

    overall = "ok" if http_status == 200 else "degraded"
    payload = {
        "status": overall,
        "dependencies": dependencies,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return jsonify(payload), http_status
