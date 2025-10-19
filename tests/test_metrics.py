from __future__ import annotations

import re

from flask import Flask

from app.config import settings
from app.metrics import task_latency_histogram, whaticket_send_retry_total
from app.services.tenancy import queue_name_for_company
from tests.conftest import DummyQueue


def _get_metric_value(body: str, metric: str, labels: dict[str, str] | None = None) -> float:
    if labels:
        parts = ",".join(f'{key}="{labels[key]}"' for key in sorted(labels))
        metric = f"{metric}{{{parts}}}"
    pattern = rf"^{re.escape(metric)} ([0-9eE+\-.]+)$"
    match = re.search(pattern, body, re.MULTILINE)
    if not match:
        return 0.0
    return float(match.group(1))


def test_metrics_endpoint_updates_operational_gauges(app: Flask, client) -> None:
    primary_queue = DummyQueue()
    dead_queue = DummyQueue()
    app.task_queue = primary_queue
    app.dead_letter_queue = dead_queue
    app._queue_cache[queue_name_for_company(settings.queue_name, 1)] = primary_queue  # type: ignore[attr-defined]
    app._dead_letter_queue_cache[queue_name_for_company(settings.dead_letter_queue_name, 1)] = dead_queue  # type: ignore[attr-defined]
    app.redis.storage.clear()

    baseline = client.get("/metrics")
    assert baseline.status_code == 200
    baseline_body = baseline.data.decode()
    base_queue = _get_metric_value(baseline_body, "secretaria_queue_size", {"company": "0"})
    base_dead_letter = _get_metric_value(
        baseline_body, "secretaria_dead_letter_queue_size", {"company": "0"}
    )
    base_retries = _get_metric_value(
        baseline_body, "secretaria_whaticket_send_retry_total", {"company": "0"}
    )
    base_latency_count = _get_metric_value(
        baseline_body, "secretaria_task_latency_seconds_count", {"company": "0"}
    )
    base_used_memory = _get_metric_value(
        baseline_body,
        "secretaria_redis_memory_usage_bytes",
        {"type": "used"},
    )

    app.task_queue.enqueue(lambda: None)
    app.task_queue.enqueue(lambda: None)
    app.dead_letter_queue.enqueue(lambda: None)
    task_latency_histogram.labels(company="0").observe(0.42)
    whaticket_send_retry_total.labels(company="0").inc()
    app.redis.set("sample", "valor-de-teste")

    response = client.get("/metrics")
    assert response.status_code == 200
    body = response.data.decode()

    queue_value = _get_metric_value(body, "secretaria_queue_size", {"company": "0"})
    dead_letter_value = _get_metric_value(
        body, "secretaria_dead_letter_queue_size", {"company": "0"}
    )
    retries_value = _get_metric_value(
        body, "secretaria_whaticket_send_retry_total", {"company": "0"}
    )
    latency_count = _get_metric_value(
        body, "secretaria_task_latency_seconds_count", {"company": "0"}
    )
    used_memory = _get_metric_value(body, "secretaria_redis_memory_usage_bytes", {"type": "used"})

    assert queue_value == base_queue + 2.0
    assert dead_letter_value == base_dead_letter + 1.0
    assert retries_value == base_retries + 1.0
    assert latency_count == base_latency_count + 1.0
    assert used_memory >= base_used_memory
