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


def _int_with_fallback(primary: str, fallback: str, default: int) -> int:
    if primary in os.environ and os.environ[primary] != "":
        return int(os.environ[primary])
    if fallback in os.environ and os.environ[fallback] != "":
        return int(os.environ[fallback])
    return default


@dataclass
class Config:
    shared_secret: str = os.getenv("SHARED_SECRET", "")
    webhook_token_optional: Optional[str] = os.getenv("WEBHOOK_TOKEN_OPTIONAL")
    whatsapp_api_url: str = os.getenv("WHATSAPP_API_URL", "http://whaticket:8080/api/messages/send")
    whatsapp_bearer_token: str = os.getenv("WHATSAPP_BEARER_TOKEN", "")
    whaticket_jwt_email: Optional[str] = os.getenv("WHATICKET_JWT_EMAIL")
    whaticket_jwt_password: Optional[str] = os.getenv("WHATICKET_JWT_PASSWORD")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    embedding_provider: str = os.getenv("EMBEDDING_PROVIDER", "gemini")
    redis_url: str = os.getenv("REDIS_URL", "redis://redis:6379/0")
    database_url: str = os.getenv("DATABASE_URL", "postgresql+psycopg2://postgres:postgres@postgres:5432/postgres")
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
    dead_letter_queue_name: str = os.getenv("RQ_DEAD_LETTER_QUEUE", "dead_letter")
    dead_letter_job_timeout: int = _int("DEAD_LETTER_JOB_TIMEOUT", 60)
    dead_letter_result_ttl: int = _int("DEAD_LETTER_RESULT_TTL", 86400)
    rq_retry_delays: tuple[int, ...] = field(default_factory=lambda: (5, 15, 45, 90))
    rq_retry_max_attempts: int = _int("RQ_RETRY_MAX_ATTEMPTS", 5)
    metrics_namespace: str = os.getenv("METRICS_NAMESPACE", "secretaria")
    enable_jwt_login: bool = field(default_factory=lambda: bool(os.getenv("WHATICKET_JWT_EMAIL") and os.getenv("WHATICKET_JWT_PASSWORD")))
    transfer_to_human_message: str = os.getenv(
        "TRANSFER_TO_HUMAN_MESSAGE",
        "Estamos encaminhando seu atendimento para um agente humano.",
    )
    redis_memory_warning_bytes: int = _int("REDIS_MEMORY_WARNING_BYTES", 512 * 1024 * 1024)
    redis_memory_critical_bytes: int = _int("REDIS_MEMORY_CRITICAL_BYTES", 768 * 1024 * 1024)
    panel_password: str = os.getenv("PANEL_PASSWORD", "")
    panel_jwt_secret: str = os.getenv("PANEL_JWT_SECRET", "change-me")
    panel_token_ttl_seconds: int = _int("PANEL_TOKEN_TTL_SECONDS", 3600)
    billing_cost_per_message: float = _float("BILLING_COST_PER_MESSAGE", 0.02)
    billing_cost_per_thousand_tokens: float = _float("BILLING_COST_PER_THOUSAND_TOKENS", 0.35)
    billing_alert_webhook_url: Optional[str] = os.getenv("BILLING_ALERT_WEBHOOK_URL")
    business_ai_default_webhook: Optional[str] = os.getenv("BUSINESS_AI_DEFAULT_WEBHOOK")
    business_ai_insights_ttl: int = _int("BUSINESS_AI_INSIGHTS_TTL", 3600)
    retention_days_contexts: int = _int("RETENTION_DAYS_CONTEXTS", 90)
    retention_days_feedback: int = _int("RETENTION_DAYS_FEEDBACK", 90)
    retention_days_ab_events: int = _int("RETENTION_DAYS_AB_EVENTS", 120)
    cal_api_base_url: str = os.getenv("CAL_API_BASE_URL", "https://api.cal.com/v1")
    cal_default_days_ahead: int = _int("CAL_DEFAULT_DAYS_AHEAD", 7)
    context_ttl: int = field(init=False)
    context_ttl_seconds: int = field(init=False)
    rate_limit_ttl: int = field(init=False)
    rate_limit_ttl_seconds: int = field(init=False)

    @property
    def llm_circuit_breaker_reset(self) -> timedelta:
        return timedelta(seconds=self.llm_circuit_breaker_reset_seconds)

    def __post_init__(self) -> None:
        context_ttl = _int_with_fallback("CONTEXT_TTL", "CONTEXT_TTL_SECONDS", 600)
        rate_limit_ttl = _int_with_fallback("RATE_LIMIT_TTL", "RATE_LIMIT_WINDOW_SECONDS", 60)
        self.context_ttl = context_ttl
        self.context_ttl_seconds = context_ttl
        self.rate_limit_ttl = rate_limit_ttl
        self.rate_limit_ttl_seconds = rate_limit_ttl


settings = Config()
