#!/usr/bin/env python3
from __future__ import annotations

import argparse
import atexit
import os
import signal
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import redis
from rq import Connection, Worker


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings
from app.services.tenancy import namespaced_key, queue_name_for_company


def build_tenant_redis_url(base_url: str, company_id: int) -> str:
    from urllib.parse import urlparse, urlunparse

    parsed = urlparse(base_url)
    path = f"/tenant_{company_id}"
    updated = parsed._replace(path=path)
    return urlunparse(updated)


def register_worker(
    registry: redis.Redis,
    company_id: int,
    worker_id: str,
    *,
    queue_name: str,
    redis_url: str,
    status: str = "starting",
    extra: dict[str, Any] | None = None,
) -> None:
    state_key = namespaced_key(company_id, "workers")
    metadata_key = namespaced_key(company_id, "worker", worker_id)
    payload: dict[str, Any] = {
        "status": status,
        "queue": queue_name,
        "redis_url": redis_url,
        "pid": os.getpid(),
        "updated_at": datetime.utcnow().isoformat(),
    }
    if extra:
        payload.update(extra)
    registry.sadd(state_key, worker_id)
    registry.hset(metadata_key, mapping={str(k): str(v) for k, v in payload.items()})
    registry.expire(metadata_key, 3600)


def mark_stopped(registry: redis.Redis, company_id: int, worker_id: str) -> None:
    state_key = namespaced_key(company_id, "workers")
    metadata_key = namespaced_key(company_id, "worker", worker_id)
    registry.hset(
        metadata_key,
        mapping={
            "status": "stopped",
            "updated_at": datetime.utcnow().isoformat(),
        },
    )
    registry.srem(state_key, worker_id)
    registry.expire(metadata_key, 300)


def main() -> int:
    parser = argparse.ArgumentParser(description="Inicia um worker RQ isolado por tenant")
    parser.add_argument("--company-id", type=int, required=True, help="Identificador numérico da empresa")
    parser.add_argument(
        "--queue",
        help="Nome completo da fila a ser consumida (default: fila isolada do tenant)",
    )
    parser.add_argument("--burst", action="store_true", help="Processa jobs pendentes e encerra")
    parser.add_argument("--worker-id", help="Identificador opcional para o worker")
    args = parser.parse_args()

    company_id = args.company_id
    if company_id <= 0:
        parser.error("company-id deve ser positivo")

    worker_id = args.worker_id or str(uuid.uuid4())
    queue_name = args.queue or queue_name_for_company(settings.queue_name, company_id)

    registry = redis.from_url(settings.redis_url, decode_responses=True)
    tenant_redis_url = build_tenant_redis_url(settings.redis_url, company_id)
    tenant_redis = redis.from_url(tenant_redis_url)

    register_worker(
        registry,
        company_id,
        worker_id,
        queue_name=queue_name,
        redis_url=tenant_redis_url,
        status="starting",
        extra={"burst": args.burst},
    )

    def _cleanup(*_args: Any) -> None:
        try:
            register_worker(
                registry,
                company_id,
                worker_id,
                queue_name=queue_name,
                redis_url=tenant_redis_url,
                status="stopping",
            )
        finally:
            mark_stopped(registry, company_id, worker_id)

    atexit.register(_cleanup)

    def _signal_handler(signum: int, _frame: Any) -> None:
        register_worker(
            registry,
            company_id,
            worker_id,
            queue_name=queue_name,
            redis_url=tenant_redis_url,
            status="stopping",
            extra={"signal": signum},
        )
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    with Connection(tenant_redis):
        register_worker(
            registry,
            company_id,
            worker_id,
            queue_name=queue_name,
            redis_url=tenant_redis_url,
            status="running",
            extra={"started_at": datetime.utcnow().isoformat()},
        )
        worker = Worker([queue_name])
        worker.work(burst=args.burst)

    return 0


if __name__ == "__main__":
    sys.exit(main())
