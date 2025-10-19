from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import logging
import os
import random
import string
import time
from dataclasses import dataclass
from statistics import mean
from typing import Any

import aiohttp
from prometheus_client import CollectorRegistry, Counter, Gauge, generate_latest


LOGGER = logging.getLogger("load_test")


@dataclass
class LoadTestResult:
    status: int | None
    latency: float
    error: str | None = None


def _default_shared_secret() -> str:
    secret = os.getenv("SHARED_SECRET")
    if not secret:
        raise SystemExit("SHARED_SECRET não configurado. Use --shared-secret ou defina a variável de ambiente.")
    return secret


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Executa teste de carga no webhook Whaticket.")
    parser.add_argument("--url", default="http://localhost:8080/webhook/whaticket", help="Endpoint do webhook alvo")
    parser.add_argument("--shared-secret", default=None, help="Segredo HMAC para assinar requisições")
    parser.add_argument("--webhook-token", default=None, help="Token opcional a ser enviado no header X-Webhook-Token")
    parser.add_argument("--requests", type=int, default=100, help="Número total de requisições a enviar")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=100,
        help="Número máximo de requisições simultâneas",
    )
    parser.add_argument("--request-timeout", type=float, default=15.0, help="Timeout total por requisição (segundos)")
    parser.add_argument(
        "--payload-text",
        default="Teste de carga",
        help="Texto base utilizado no corpo das mensagens simuladas",
    )
    args = parser.parse_args()
    if args.shared_secret is None:
        args.shared_secret = _default_shared_secret()
    if args.requests <= 0:
        raise SystemExit("--requests deve ser maior que zero")
    if args.concurrency <= 0:
        raise SystemExit("--concurrency deve ser maior que zero")
    return args


def _random_number(seed: int) -> str:
    suffix = f"{seed:04d}"[-4:]
    return f"551199{suffix}{random.randint(1000, 9999)}"


def _random_suffix() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=6))


def _build_payload(base_text: str, index: int) -> dict[str, Any]:
    return {
        "message": {"conversation": f"{base_text} #{index}-{_random_suffix()}"},
        "number": _random_number(index),
    }


def _build_headers(secret: str, token: str | None, body: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    message = f"{timestamp}.".encode() + body
    signature = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-Timestamp": timestamp,
        "X-Signature": signature,
    }
    if token:
        headers["X-Webhook-Token"] = token
    return headers


async def _dispatch_request(
    session: aiohttp.ClientSession,
    url: str,
    payload: dict[str, Any],
    secret: str,
    token: str | None,
    semaphore: asyncio.Semaphore,
) -> LoadTestResult:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    headers = _build_headers(secret, token, body)
    start = time.perf_counter()
    async with semaphore:
        try:
            async with session.post(url, data=body, headers=headers) as response:
                await response.text()
                latency = time.perf_counter() - start
                return LoadTestResult(status=response.status, latency=latency)
        except Exception as exc:  # pragma: no cover - exceções dependem do ambiente
            latency = time.perf_counter() - start
            return LoadTestResult(status=None, latency=latency, error=str(exc))


async def run_load_test(args: argparse.Namespace) -> list[LoadTestResult]:
    connector = aiohttp.TCPConnector(limit=args.concurrency)
    timeout = aiohttp.ClientTimeout(total=args.request_timeout)
    semaphore = asyncio.Semaphore(args.concurrency)
    results: list[LoadTestResult] = []
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        tasks = [
            asyncio.create_task(
                _dispatch_request(
                    session,
                    args.url,
                    _build_payload(args.payload_text, index),
                    args.shared_secret,
                    args.webhook_token,
                    semaphore,
                )
            )
            for index in range(args.requests)
        ]
        for task in asyncio.as_completed(tasks):
            results.append(await task)
    return results


def _collect_metrics(results: list[LoadTestResult], total_duration: float) -> CollectorRegistry:
    success_latencies = [result.latency for result in results if result.status and result.status < 500]
    avg_latency = mean(success_latencies) if success_latencies else 0.0
    http_errors = sum(1 for result in results if (result.status is None) or (result.status >= 400))

    registry = CollectorRegistry()
    Gauge(
        "webhook_load_test_average_latency_seconds",
        "Latência média das requisições bem-sucedidas",
        registry=registry,
    ).set(avg_latency)
    Gauge(
        "webhook_load_test_duration_seconds",
        "Tempo total do teste de carga",
        registry=registry,
    ).set(total_duration)
    requests_counter = Counter(
        "webhook_load_test_requests_total",
        "Total de requisições disparadas",
        registry=registry,
    )
    requests_counter.inc(len(results))
    Counter(
        "webhook_load_test_http_errors_total",
        "Total de erros HTTP detectados durante o teste",
        registry=registry,
    ).inc(http_errors)
    return registry


def _log_summary(results: list[LoadTestResult], duration: float) -> None:
    success = sum(1 for result in results if result.status and result.status < 400)
    errors = len(results) - success
    worst_latency = max((result.latency for result in results), default=0.0)
    LOGGER.info(
        "Teste concluído | total=%d sucesso=%d erros=%d duração=%.3fs latência_máx=%.2fms",
        len(results),
        success,
        errors,
        duration,
        worst_latency * 1000,
    )


async def main_async() -> None:
    args = _parse_args()
    start = time.perf_counter()
    results = await run_load_test(args)
    total_duration = time.perf_counter() - start
    _log_summary(results, total_duration)
    registry = _collect_metrics(results, total_duration)
    metrics_blob = generate_latest(registry).decode("utf-8")
    LOGGER.info("Prometheus payload:\n%s", metrics_blob.strip())


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:  # pragma: no cover - interrupção manual
        LOGGER.warning("Execução interrompida pelo usuário")


if __name__ == "__main__":
    main()
