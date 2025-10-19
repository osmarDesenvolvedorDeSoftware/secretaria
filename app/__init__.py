from __future__ import annotations

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

from .config import settings
from .metrics import (
    active_workers_gauge,
    dead_letter_queue_gauge,
    llm_errors,
    llm_latency,
    queue_gauge,
    redis_memory_usage_gauge,
    task_latency_histogram,
    webhook_received_counter,
    whaticket_errors,
    whaticket_latency,
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
    app.task_queue = Queue(settings.queue_name, connection=redis_client)  # type: ignore[attr-defined]
    app.dead_letter_queue = Queue(  # type: ignore[attr-defined]
        settings.dead_letter_queue_name,
        connection=redis_client,
    )

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
        queue_obj = getattr(app, "task_queue", None)
        queue_size = 0
        if queue_obj is not None:
            count_attr = getattr(queue_obj, "count", None)
            if callable(count_attr):
                queue_size = count_attr()
            elif isinstance(count_attr, int):
                queue_size = count_attr
        queue_gauge.set(queue_size)

        dead_letter_obj = getattr(app, "dead_letter_queue", None)
        dead_letter_size = 0
        if dead_letter_obj is not None:
            dlq_count_attr = getattr(dead_letter_obj, "count", None)
            if callable(dlq_count_attr):
                dead_letter_size = dlq_count_attr()
            elif isinstance(dlq_count_attr, int):
                dead_letter_size = dlq_count_attr
        dead_letter_queue_gauge.set(dead_letter_size)

        redis_client = getattr(app, "redis", None)
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
            try:
                worker_count = len(Worker.all(connection=redis_client))  # type: ignore[arg-type]
            except Exception:
                worker_count = 0
            active_workers_gauge.set(worker_count)
        else:
            active_workers_gauge.set(0)
        return app.response_class(generate_latest(), mimetype=CONTENT_TYPE_LATEST)

    from app.routes.projects import bp as projects_bp

    app.register_blueprint(health_bp)
    app.register_blueprint(webhook_bp)
    app.register_blueprint(projects_bp)

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
