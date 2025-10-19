from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

from app.config import settings

webhook_received_counter = Counter(
    f"{settings.metrics_namespace}_webhook_received_total",
    "Total de webhooks recebidos",
    ["company", "status"],
)

task_latency_histogram = Histogram(
    f"{settings.metrics_namespace}_task_latency_seconds",
    "Latência do processamento de mensagens",
    ["company"],
)

queue_gauge = Gauge(
    f"{settings.metrics_namespace}_queue_size",
    "Tamanho atual da fila RQ",
    ["company"],
)

dead_letter_queue_gauge = Gauge(
    f"{settings.metrics_namespace}_dead_letter_queue_size",
    "Tamanho atual da fila dead-letter",
    ["company"],
)

redis_memory_usage_gauge = Gauge(
    f"{settings.metrics_namespace}_redis_memory_usage_bytes",
    "Uso de memória do Redis em bytes",
    ["type"],
)

active_workers_gauge = Gauge(
    f"{settings.metrics_namespace}_active_workers",
    "Quantidade de workers RQ ativos registrados",
)

tenant_worker_gauge = Gauge(
    f"{settings.metrics_namespace}_tenant_active_workers",
    "Quantidade de workers RQ ativos por tenant",
    ["company"],
)

whaticket_latency = Histogram(
    f"{settings.metrics_namespace}_whaticket_latency_seconds",
    "Latência de envio para Whaticket",
    ["company"],
)

whaticket_errors = Counter(
    f"{settings.metrics_namespace}_whaticket_errors_total",
    "Falhas no envio para Whaticket",
    ["company"],
)

whaticket_send_success_total = Counter(
    f"{settings.metrics_namespace}_whaticket_send_success_total",
    "Total de mensagens enviadas com sucesso ao Whaticket",
    ["company"],
)

whaticket_send_retry_total = Counter(
    f"{settings.metrics_namespace}_whaticket_send_retry_total",
    "Total de re-tentativas de envio ao Whaticket",
    ["company"],
)

llm_latency = Histogram(
    f"{settings.metrics_namespace}_llm_latency_seconds",
    "Latência nas chamadas ao LLM",
    ["company"],
)

llm_errors = Counter(
    f"{settings.metrics_namespace}_llm_errors_total",
    "Falhas nas chamadas ao LLM",
    ["company"],
)

llm_prompt_injection_blocked_total = Counter(
    f"{settings.metrics_namespace}_llm_prompt_injection_blocked_total",
    "Total de mensagens bloqueadas por detecção de prompt injection",
    ["company"],
)

fallback_transfers_total = Counter(
    f"{settings.metrics_namespace}_fallback_transfer_total",
    "Total de transferências humanas acionadas pelo fallback",
    ["company"],
)

sentiment_average_gauge = Gauge(
    f"{settings.metrics_namespace}_sentiment_average",
    "Humor médio detectado por número",
    ["company", "number"],
)

satisfaction_ratio_gauge = Gauge(
    f"{settings.metrics_namespace}_satisfaction_ratio",
    "Taxa de satisfação baseada em feedbacks positivos e negativos",
    ["company", "number"],
)

intention_distribution_total = Counter(
    f"{settings.metrics_namespace}_intention_detected_total",
    "Distribuição de intenções detectadas nas mensagens",
    ["company", "intention"],
)

context_learning_updates_total = Counter(
    f"{settings.metrics_namespace}_context_learning_updates_total",
    "Total de atualizações de embeddings por cliente",
    ["company", "number"],
)

context_volume_gauge = Gauge(
    f"{settings.metrics_namespace}_context_volume_messages",
    "Quantidade de mensagens consideradas no contexto personalizado",
    ["company", "number"],
)

message_usage_total = Counter(
    f"{settings.metrics_namespace}_messages_processed_total",
    "Mensagens processadas por empresa",
    ["company", "kind"],
)

token_usage_total = Counter(
    f"{settings.metrics_namespace}_token_usage_total",
    "Tokens estimados consumidos por empresa",
    ["company", "direction"],
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
    "dead_letter_queue_gauge",
    "redis_memory_usage_gauge",
    "active_workers_gauge",
    "tenant_worker_gauge",
    "whaticket_latency",
    "whaticket_errors",
    "whaticket_send_success_total",
    "whaticket_send_retry_total",
    "llm_latency",
    "llm_errors",
    "llm_prompt_injection_blocked_total",
    "fallback_transfers_total",
    "sentiment_average_gauge",
    "satisfaction_ratio_gauge",
    "intention_distribution_total",
    "context_learning_updates_total",
    "context_volume_gauge",
    "healthcheck_failures_total",
    "message_usage_total",
    "token_usage_total",
]
