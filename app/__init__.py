from __future__ import annotations

import atexit
import logging
import logging.config
import os
import time
import uuid
from contextlib import contextmanager
from typing import Generator

from pathlib import Path

import structlog
from flask import Flask, g, request
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from redis import Redis
from rq import Queue, Worker
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .config import settings
from .metrics import (
    active_workers_gauge,
    dead_letter_queue_gauge,
    llm_errors,
    llm_latency,
    queue_gauge,
    redis_memory_usage_gauge,
    task_latency_histogram,
    tenant_worker_gauge,
    webhook_received_counter,
    whaticket_errors,
    whaticket_latency,
)
from .services.analytics_service import AnalyticsService
from .services.billing import BillingService
from .services import project_sync_service
from .services.scheduler_service import SchedulerService
from .services.tenancy import (
    build_tenant_context,
    extract_domain_from_request,
    iter_companies,
    namespaced_key,
    queue_name_for_company,
    resolve_company,
)
from .routes.health import health_bp
from .routes.webhook import webhook_bp

LOGGER = structlog.get_logger()


def configure_logging() -> None:
    config_path = Path(os.getenv("LOGGING_CONFIG", "logging.conf"))
    log_defaults = {
        "logfilename": os.getenv("APP_LOG_FILE", "/var/log/secretaria/app.log"),
    }

    try:
        Path(log_defaults["logfilename"]).parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        logging.getLogger(__name__).warning(
            "unable_to_create_log_directory",
            directory=log_defaults["logfilename"],
        )

    if config_path.exists():
        logging.config.fileConfig(
            config_path,
            disable_existing_loggers=False,
            defaults=log_defaults,
        )
    else:
        logging.basicConfig(level=logging.INFO)

    timestamper = structlog.processors.TimeStamper(fmt="iso")

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            timestamper,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def init_app() -> Flask:
    configure_logging()

    app = Flask(__name__, template_folder="../templates")
    app.config["SQLALCHEMY_DATABASE_URI"] = settings.database_url

    engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    SessionLocal = scoped_session(session_factory)

    redis_client = Redis.from_url(settings.redis_url, decode_responses=True)

    app.redis = redis_client  # type: ignore[attr-defined]
    app.db_session = SessionLocal  # type: ignore[attr-defined]
    app.db_engine = engine  # type: ignore[attr-defined]
    app._queue_cache: dict[str, Queue] = {}
    app._dead_letter_queue_cache: dict[str, Queue] = {}

    queue_class = Queue
    worker_class = Worker
    app.queue_class = queue_class  # type: ignore[attr-defined]
    app.worker_class = worker_class  # type: ignore[attr-defined]
    def get_task_queue(company_id: int) -> Queue:
        name = queue_name_for_company(settings.queue_name, company_id)
        queue_cls = getattr(app, "queue_class", queue_class)
        queue = app._queue_cache.get(name)
        if queue is None or not isinstance(queue, queue_cls):
            queue = queue_cls(name, connection=redis_client)
            app._queue_cache[name] = queue
        return queue

    def get_dead_letter_queue(company_id: int) -> Queue:
        name = queue_name_for_company(settings.dead_letter_queue_name, company_id)
        queue_cls = getattr(app, "queue_class", queue_class)
        queue = app._dead_letter_queue_cache.get(name)
        if queue is None or not isinstance(queue, queue_cls):
            queue = queue_cls(name, connection=redis_client)
            app._dead_letter_queue_cache[name] = queue
        return queue

    app.get_task_queue = get_task_queue  # type: ignore[attr-defined]
    app.get_dead_letter_queue = get_dead_letter_queue  # type: ignore[attr-defined]
    app.task_queue = get_task_queue(0)  # type: ignore[attr-defined]
    app.dead_letter_queue = get_dead_letter_queue(0)  # type: ignore[attr-defined]

    analytics_service = AnalyticsService(SessionLocal, redis_client)
    billing_service = BillingService(SessionLocal, redis_client, analytics_service)
    app.analytics_service = analytics_service  # type: ignore[attr-defined]
    app.billing_service = billing_service  # type: ignore[attr-defined]

    scheduler_service = SchedulerService(redis_client, SessionLocal, get_task_queue)
    app.scheduler_service = scheduler_service  # type: ignore[attr-defined]
    try:
        scheduler_service.ensure_daily_agenda_optimization()
    except Exception:
        LOGGER.warning("scheduler_initialization_failed")

    if settings.internal_sync_mode:
        app.github_auto_sync_scheduler = None  # type: ignore[attr-defined]
        scheduler_logger = LOGGER.bind(job="github_auto_sync")

        def _run_github_auto_sync() -> None:
            session = SessionLocal()
            try:
                result = project_sync_service.sync_github_projects_to_db(
                    session,
                    settings.default_company_id,
                )
                scheduler_logger.info(
                    "github_auto_sync_result",
                    status=result.get("status"),
                    summary=result.get("summary"),
                )
            except Exception:
                scheduler_logger.exception("github_auto_sync_failed")
            finally:
                try:
                    session.close()
                finally:
                    SessionLocal.remove()

        try:
            github_scheduler = BackgroundScheduler()
            github_scheduler.add_job(
                _run_github_auto_sync,
                IntervalTrigger(hours=max(1, settings.sync_interval_hours)),
                id="github_auto_sync",
                replace_existing=True,
            )
            github_scheduler.start()
            atexit.register(lambda: github_scheduler.shutdown(wait=False))
            app.github_auto_sync_scheduler = github_scheduler  # type: ignore[attr-defined]
        except Exception:
            scheduler_logger.error("github_auto_sync_scheduler_failed", exc_info=True)

    @app.teardown_appcontext
    def remove_session(exception: Exception | None) -> None:
        SessionLocal.remove()

    @app.before_request
    def inject_correlation_id() -> None:
        corr_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(correlation_id=corr_id)
        g.correlation_id = corr_id
        g.start_time = time.time()
        domain = extract_domain_from_request(request)
        company = None
        if domain:
            session = SessionLocal()
            try:
                company = resolve_company(session, domain)
            finally:
                session.close()
        g.company = company
        if company is not None:
            tenant = build_tenant_context(company)
            g.tenant = tenant
            structlog.contextvars.bind_contextvars(company_id=company.id)
        else:
            g.tenant = None

    @app.after_request
    def add_response_headers(response):
        duration = time.time() - getattr(g, "start_time", time.time())
        response.headers["X-Correlation-ID"] = getattr(g, "correlation_id", "")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self' 'unsafe-inline'",
        )
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), microphone=(), camera=(), fullscreen=(self)",
        )
        if request.is_secure:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains; preload",
            )
        LOGGER.info(
            "request_completed",
            path=request.path,
            status=response.status_code,
            method=request.method,
            duration=duration,
        )
        return response

    @app.route("/metrics")
    def metrics():
        redis_client = getattr(app, "redis", None)
        try:
            companies = iter_companies(SessionLocal)
        except Exception:
            companies = []
        seen_companies: set[str] = set()
        for company in companies:
            label = str(company.id)
            seen_companies.add(label)
            queue_obj = get_task_queue(company.id)
            queue_count = getattr(queue_obj, "count", None)
            if callable(queue_count):
                queue_size = queue_count()
            else:
                queue_size = int(queue_count or 0)
            queue_gauge.labels(company=label).set(queue_size)

            dead_letter_obj = get_dead_letter_queue(company.id)
            dl_count = getattr(dead_letter_obj, "count", None)
            if callable(dl_count):
                dead_letter_size = dl_count()
            else:
                dead_letter_size = int(dl_count or 0)
            dead_letter_queue_gauge.labels(company=label).set(dead_letter_size)

            worker_count = 0
            if redis_client is not None:
                try:
                    workers_key = namespaced_key(company.id, "workers")
                    members = redis_client.smembers(workers_key)
                    worker_count = len(members) if members else 0
                except Exception:
                    worker_count = 0
            tenant_worker_gauge.labels(company=label).set(worker_count)

        # Garantir que métricas da fila padrão (company 0) também sejam expostas
        if "0" not in seen_companies:
            default_queue = getattr(app, "task_queue", None)
            default_dead_letter = getattr(app, "dead_letter_queue", None)
            if default_queue is not None:
                count_attr = getattr(default_queue, "count", None)
                queue_size = count_attr() if callable(count_attr) else int(count_attr or 0)
                queue_gauge.labels(company="0").set(queue_size)
            if default_dead_letter is not None:
                count_attr = getattr(default_dead_letter, "count", None)
                dead_letter_size = (
                    count_attr() if callable(count_attr) else int(count_attr or 0)
                )
                dead_letter_queue_gauge.labels(company="0").set(dead_letter_size)
            tenant_worker_gauge.labels(company="0").set(0)

        if redis_client is not None:
            try:
                info = redis_client.info("memory")  # type: ignore[union-attr]
            except Exception:
                info = {}
            used_memory = info.get("used_memory")
            if isinstance(used_memory, (int, float)):
                redis_memory_usage_gauge.labels("used").set(float(used_memory))
            max_memory = info.get("maxmemory")
            if isinstance(max_memory, (int, float)) and max_memory > 0:
                redis_memory_usage_gauge.labels("max_configured").set(float(max_memory))
            redis_memory_usage_gauge.labels("warning_threshold").set(
                float(settings.redis_memory_warning_bytes)
            )
            redis_memory_usage_gauge.labels("critical_threshold").set(
                float(settings.redis_memory_critical_bytes)
            )
            worker_cls = getattr(app, "worker_class", Worker)
            try:
                if hasattr(worker_cls, "all"):
                    workers = worker_cls.all(connection=redis_client)  # type: ignore[arg-type]
                    worker_count = len(workers)
                else:
                    worker_count = 0
            except Exception:
                worker_count = 0
            active_workers_gauge.set(worker_count)
        else:
            active_workers_gauge.set(0)
        return app.response_class(generate_latest(), mimetype=CONTENT_TYPE_LATEST)

    from app.routes.analytics import analytics_bp
    from app.routes.abtests import abtest_bp
    from app.routes.compliance import compliance_bp
    from app.routes.feedback import feedback_bp
    from app.routes.recommendations import recommendation_bp
    from app.routes.projects import bp as projects_bp
    from app.routes.agenda import agenda_bp

    app.register_blueprint(health_bp)
    app.register_blueprint(webhook_bp)
    app.register_blueprint(analytics_bp)
    app.register_blueprint(recommendation_bp)
    app.register_blueprint(abtest_bp)
    app.register_blueprint(feedback_bp)
    app.register_blueprint(compliance_bp)
    app.register_blueprint(projects_bp)
    app.register_blueprint(agenda_bp)

    return app


@contextmanager
def get_db_session(app: Flask) -> Generator:
    session = app.db_session()  # type: ignore[attr-defined]
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


__all__ = [
    "init_app",
    "get_db_session",
    "webhook_received_counter",
    "task_latency_histogram",
    "queue_gauge",
    "whaticket_latency",
    "whaticket_errors",
    "llm_latency",
    "llm_errors",
]
