from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


@dataclass
class Config:
    shared_secret: str = os.getenv("SHARED_SECRET", "")
    webhook_token_optional: Optional[str] = os.getenv("WEBHOOK_TOKEN_OPTIONAL")
    whatsapp_api_url: str = os.getenv("WHATSAPP_API_URL", "http://whaticket:8080/api/messages/send")
    whatsapp_bearer_token: str = os.getenv("WHATSAPP_BEARER_TOKEN", "")
    whaticket_jwt_email: Optional[str] = os.getenv("WHATICKET_JWT_EMAIL")
    whaticket_jwt_password: Optional[str] = os.getenv("WHATICKET_JWT_PASSWORD")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    redis_url: str = os.getenv("REDIS_URL", "redis://redis:6379/0")
    database_url: str = os.getenv("DATABASE_URL", "postgresql+psycopg2://postgres:postgres@postgres:5432/postgres")
    context_ttl_seconds: int = _int("CONTEXT_TTL_SECONDS", 600)
    context_max_messages: int = _int("CONTEXT_MAX_MESSAGES", 5)
    request_timeout_seconds: float = _float("REQUEST_TIMEOUT_SECONDS", 10.0)
    llm_timeout_seconds: float = _float("LLM_TIMEOUT_SECONDS", 30.0)
    llm_retry_attempts: int = _int("LLM_RETRY_ATTEMPTS", 3)
    llm_circuit_breaker_threshold: int = _int("LLM_CIRCUIT_BREAKER_THRESHOLD", 5)
    llm_circuit_breaker_reset_seconds: int = _int("LLM_CIRCUIT_BREAKER_RESET_SECONDS", 300)
    webhook_rate_limit_ip: int = _int("WEBHOOK_RATE_LIMIT_IP", 60)
    webhook_rate_limit_number: int = _int("WEBHOOK_RATE_LIMIT_NUMBER", 20)
    rate_limit_window_seconds: int = _int("RATE_LIMIT_WINDOW_SECONDS", 60)
    whaticket_retry_attempts: int = _int("WHATICKET_RETRY_ATTEMPTS", 3)
    whaticket_retry_backoff_seconds: int = _int("WHATICKET_RETRY_BACKOFF_SECONDS", 5)
    queue_name: str = os.getenv("RQ_QUEUE", "default")
    rq_retry_delays: tuple[int, ...] = field(default_factory=lambda: (5, 15, 45, 90))
    rq_retry_max_attempts: int = _int("RQ_RETRY_MAX_ATTEMPTS", 5)
    metrics_namespace: str = os.getenv("METRICS_NAMESPACE", "secretaria")
    enable_jwt_login: bool = field(default_factory=lambda: bool(os.getenv("WHATICKET_JWT_EMAIL") and os.getenv("WHATICKET_JWT_PASSWORD")))
    transfer_to_human_message: str = os.getenv("TRANSFER_TO_HUMAN_MESSAGE", "Estamos encaminhando seu atendimento para um agente humano.")

    @property
    def llm_circuit_breaker_reset(self) -> timedelta:
        return timedelta(seconds=self.llm_circuit_breaker_reset_seconds)


settings = Config()
