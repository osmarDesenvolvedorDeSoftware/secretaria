from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

from app.config import settings

webhook_received_counter = Counter(
    f"{settings.metrics_namespace}_webhook_received_total",
    "Total de webhooks recebidos",
    ["status"],
)

task_latency_histogram = Histogram(
    f"{settings.metrics_namespace}_task_latency_seconds",
    "Latência do processamento de mensagens",
)

queue_gauge = Gauge(
    f"{settings.metrics_namespace}_queue_size",
    "Tamanho atual da fila RQ",
)

whaticket_latency = Histogram(
    f"{settings.metrics_namespace}_whaticket_latency_seconds",
    "Latência de envio para Whaticket",
)

whaticket_errors = Counter(
    f"{settings.metrics_namespace}_whaticket_errors_total",
    "Falhas no envio para Whaticket",
)

whaticket_send_success_total = Counter(
    f"{settings.metrics_namespace}_whaticket_send_success_total",
    "Total de mensagens enviadas com sucesso ao Whaticket",
)

whaticket_send_retry_total = Counter(
    f"{settings.metrics_namespace}_whaticket_send_retry_total",
    "Total de re-tentativas de envio ao Whaticket",
)

llm_latency = Histogram(
    f"{settings.metrics_namespace}_llm_latency_seconds",
    "Latência nas chamadas ao LLM",
)

llm_errors = Counter(
    f"{settings.metrics_namespace}_llm_errors_total",
    "Falhas nas chamadas ao LLM",
)

llm_prompt_injection_blocked_total = Counter(
    f"{settings.metrics_namespace}_llm_prompt_injection_blocked_total",
    "Total de mensagens bloqueadas por detecção de prompt injection",
)

healthcheck_failures_total = Counter(
    f"{settings.metrics_namespace}_healthcheck_failures_total",
    "Total de falhas de healthcheck por dependência",
    ["component"],
)

__all__ = [
    "webhook_received_counter",
    "task_latency_histogram",
    "queue_gauge",
    "whaticket_latency",
    "whaticket_errors",
    "whaticket_send_success_total",
    "whaticket_send_retry_total",
    "llm_latency",
    "llm_errors",
    "llm_prompt_injection_blocked_total",
    "healthcheck_failures_total",
]
