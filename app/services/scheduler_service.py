from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable

import structlog
from redis import Redis
from rq import Queue

from app.services import scheduling_ai
from app.services.tenancy import iter_companies

LOGGER = structlog.get_logger().bind(service="scheduler")


class SchedulerService:
    def __init__(
        self,
        redis_client: Redis,
        session_factory: Callable[[], object],
        queue_resolver: Callable[[int], Queue],
    ) -> None:
        self.redis = redis_client
        self.session_factory = session_factory
        self.queue_resolver = queue_resolver

    def ensure_daily_agenda_optimization(self, *, force: bool = False) -> bool:
        key = "scheduler:agenda_ai:last_run"
        now = datetime.utcnow()
        if not force:
            try:
                last_run_raw = self.redis.get(key)
            except Exception:
                last_run_raw = None
            if last_run_raw:
                try:
                    last_run = datetime.fromisoformat(str(last_run_raw))
                except ValueError:
                    last_run = None
                if last_run and now - last_run < timedelta(hours=23):
                    return False

        companies = iter_companies(self.session_factory)
        scheduled = 0
        for company in companies:
            queue = self.queue_resolver(company.id)
            try:
                queue.enqueue(
                    scheduling_ai.atualizar_insights_job,
                    company.id,
                    job_timeout=300,
                    meta={"company_id": company.id, "job": "agenda_ai"},
                )
                scheduled += 1
            except Exception as exc:  # pragma: no cover - logging defensivo
                LOGGER.warning("agenda_ai_enqueue_failed", company_id=company.id, error=str(exc))

        try:
            self.redis.setex(key, 86400, now.isoformat())
        except Exception:  # pragma: no cover - apenas registra
            LOGGER.warning("scheduler_last_run_store_failed")

        LOGGER.info("agenda_ai_jobs_scheduled", total=scheduled)
        return scheduled > 0
