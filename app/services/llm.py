from __future__ import annotations

import json
import time
from typing import Any

import requests
import structlog
from redis import Redis
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_random_exponential

from app.metrics import llm_errors, llm_error_rate_gauge, llm_latency, token_usage_total
from app.config import settings
from app.services.security import detect_prompt_injection, sanitize_for_log
from app.services.tenancy import TenantContext


class LLMError(Exception):
    pass


class CircuitBreaker:
    def __init__(self, redis_client: Redis, tenant: TenantContext) -> None:
        self.redis = redis_client
        self.tenant = tenant

    def _key(self) -> str:
        return self.tenant.namespaced_key("llm", "circuit")

    def allow(self) -> bool:
        data = self.redis.get(self._key())
        if not data:
            return True
        payload = json.loads(data)
        if payload.get("open"):
            opened_at = payload.get("opened_at", 0)
            if time.time() - opened_at > settings.llm_circuit_breaker_reset_seconds:
                self.redis.delete(self._key())
                return True
            return False
        return True

    def record_success(self) -> None:
        self.redis.delete(self._key())

    def record_failure(self) -> None:
        data = self.redis.get(self._key())
        if data:
            payload = json.loads(data)
            failures = payload.get("failures", 0) + 1
        else:
            payload = {"failures": 1}
            failures = 1
        payload["failures"] = failures
        if failures >= settings.llm_circuit_breaker_threshold:
            payload["open"] = True
            payload["opened_at"] = time.time()
        self.redis.set(self._key(), json.dumps(payload))


class LLMClient:
    def __init__(self, redis_client: Redis, tenant: TenantContext) -> None:
        self.redis = redis_client
        self.tenant = tenant
        self.company_label = tenant.label
        self.logger = structlog.get_logger().bind(service="gemini", company=self.company_label)
        self.circuit_breaker = CircuitBreaker(redis_client, tenant)

    def _update_error_rate(self, success: bool) -> None:
        key = self.tenant.namespaced_key("metrics", "llm", "error_rate")
        try:
            field = "success" if success else "failure"
            self.redis.hincrby(key, field, 1)
            data = self.redis.hgetall(key) or {}
            success_count = int(data.get("success", 0) or 0)
            failure_count = int(data.get("failure", 0) or 0)
            total = success_count + failure_count
            if total > 0:
                rate = failure_count / total
                llm_error_rate_gauge.labels(company=self.company_label).set(rate)
        except Exception:
            pass

    @retry(
        stop=stop_after_attempt(settings.llm_retry_attempts),
        wait=wait_random_exponential(multiplier=1, max=10),
        retry=retry_if_exception_type(LLMError),
        reraise=True,
    )
    def generate_reply(
        self,
        text: str,
        context: list[dict[str, str]],
        system_prompt: str = "Você é uma assistente virtual da secretaria, responda de forma educada e objetiva.",
    ) -> str:
        if not self.circuit_breaker.allow():
            raise LLMError("Circuit breaker aberto")

        if detect_prompt_injection(text):
            preview = sanitize_for_log(text[:128])
            self.logger.warning("prompt_injection_detected", preview=preview)
            return "Desculpe, não posso executar esse tipo de comando."

        messages = context[-settings.context_max_messages :] + [{"role": "user", "body": text}]
        formatted_context = []
        for item in messages:
            role = item.get("role", "user")
            body = item.get("body", "")
            if body:
                formatted_context.append(f"{role}: {body}")
        combined_text = f"{system_prompt}\n\n" + "\n".join(formatted_context)
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": combined_text}
                    ]
                }
            ]
        }
        headers = {
            "x-goog-api-key": settings.gemini_api_key,
            "Content-Type": "application/json",
        }

        start = time.time()
        try:
            response = requests.post(
                "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
                headers=headers,
                json=payload,
                timeout=settings.llm_timeout_seconds,
            )
            if response.status_code >= 400:
                self.circuit_breaker.record_failure()
                raise LLMError(f"Gemini returned status {response.status_code}")
            data: Any = response.json()
            candidates = data.get("candidates")
            if not candidates:
                raise LLMError("Resposta inválida do LLM")
            text_output = candidates[0].get("content", {}).get("parts", [{}])[0].get("text")
            if not text_output:
                raise LLMError("Conteúdo ausente na resposta")
            self.circuit_breaker.record_success()
            self._update_error_rate(True)
            clean_response = text_output.strip()
            if clean_response:
                token_usage_total.labels(company=self.company_label, direction="outbound").inc(
                    max(len(clean_response.split()), 1)
                )
            return clean_response
        except (requests.RequestException, json.JSONDecodeError) as exc:
            self.logger.exception(
                "llm_request_error",
                error=sanitize_for_log(str(exc)),
            )
            self.circuit_breaker.record_failure()
            llm_errors.labels(company=self.company_label).inc()
            self._update_error_rate(False)
            raise LLMError("Falha ao chamar LLM")
        finally:
            duration = time.time() - start
            llm_latency.labels(company=self.company_label).observe(duration)
            structlog.get_logger().info(
                "llm_call",
                duration=duration,
                company=self.company_label,
            )
