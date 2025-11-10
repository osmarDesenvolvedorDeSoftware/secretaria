from __future__ import annotations

import json
import time
from typing import Any

import structlog
from flask import Blueprint, Response, current_app, g, jsonify, request
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator
from rq import Queue

from app.metrics import webhook_latency_seconds, webhook_received_counter
from app.services.billing import BillingService
from app.services.payload import extract_number, extract_text_and_kind
from app.services.rate_limit import RateLimiter
from app.services.security import (
    sanitize_for_log,
    sanitize_text,
    validate_hmac_signature,
    validate_webhook_token,
)
from app.services.tasks import TaskService


webhook_bp = Blueprint("webhook", __name__, url_prefix="/webhook")

LOGGER = structlog.get_logger().bind(endpoint="webhook.whaticket")
PAYLOAD_LOGGER = structlog.get_logger("webhook_payloads")


class IncomingWebhook(BaseModel):
    model_config = ConfigDict(extra="allow")

    number: str
    text: str
    kind: str

    @field_validator("number")
    @classmethod
    def normalize_number(cls, value: str) -> str:
        digits = "".join(ch for ch in value if ch.isdigit())
        if digits.startswith("55"):
            digits = digits
        elif digits:
            digits = "55" + digits
        if not digits:
            raise ValueError("number is required")
        return digits

    @staticmethod
    def _is_proto_payload(values: dict[str, Any]) -> bool:
        key = values.get("key") if isinstance(values, dict) else None
        if not isinstance(key, dict):
            return False
        remote_jid = key.get("remoteJid") or key.get("remotejid")
        if not remote_jid or "@" not in str(remote_jid):
            return False
        message = values.get("message")
        if isinstance(message, dict):
            return True
        messages = values.get("messages")
        if isinstance(messages, list):
            return any(isinstance(item, dict) and item.get("message") for item in messages)
        return False

    @classmethod
    def _normalize_payload(cls, values: dict[str, Any]) -> dict[str, Any]:
        number = extract_number(values)
        if not number:
            raise ValueError("could not extract whatsapp number")
        text, kind = extract_text_and_kind(values)
        normalized = {"number": number, "text": text, "kind": kind}
        normalized["payload_format"] = "proto" if cls._is_proto_payload(values) else "legacy"
        return {**values, **normalized}

    @model_validator(mode="before")
    @classmethod
    def from_payload_dict(cls, values: Any) -> Any:
        if isinstance(values, dict):
            needs_normalization = (
                "number" not in values
                or "text" not in values
                or "kind" not in values
                or cls._is_proto_payload(values)
            )
            if needs_normalization:
                values = cls._normalize_payload(values)
            values.setdefault("payload_format", "legacy")
        return values

    @classmethod
    def parse_raw_body(cls, raw_body: bytes) -> "IncomingWebhook":
        data = json.loads(raw_body)
        return cls.from_payload(data)


    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "IncomingWebhook":
        return cls.model_validate(payload)


def _extract_contact_name(payload: IncomingWebhook, raw_payload: dict[str, Any]) -> str | None:
    def _from_mapping(mapping: dict[str, Any] | None) -> list[str]:
        if not isinstance(mapping, dict):
            return []
        candidates: list[str] = []
        contact = mapping.get("contact")
        if isinstance(contact, dict):
            for key in ("name", "pushName", "displayName", "nome"):
                value = contact.get(key)
                if isinstance(value, str):
                    candidates.append(value)
        for key in ("pushName", "name", "nome"):
            value = mapping.get(key)
            if isinstance(value, str):
                candidates.append(value)
        message = mapping.get("message")
        if isinstance(message, dict):
            for key in ("pushName", "name", "nome"):
                value = message.get(key)
                if isinstance(value, str):
                    candidates.append(value)
        return candidates

    extras = getattr(payload, "model_extra", {})
    candidates = []
    if isinstance(extras, dict):
        candidates.extend(_from_mapping(extras))
    candidates.extend(_from_mapping(raw_payload))

    for candidate in candidates:
        cleaned = str(candidate).strip()
        if cleaned:
            return cleaned
    return None


@webhook_bp.post("/whaticket")
def whaticket_webhook() -> Response:
    start_time = time.time()
    raw_body = request.get_data()
    raw_body_text = raw_body.decode("utf-8", errors="replace") if raw_body else ""
    headers = {key: sanitize_for_log(value) for key, value in request.headers.items()}

    PAYLOAD_LOGGER.info(
        "webhook_payload_received",
        remote_addr=request.remote_addr,
        headers=headers,
        raw_body=raw_body_text,
    )

    if not validate_hmac_signature(request):
        webhook_received_counter.labels(company="unknown", status="unauthorized").inc()
        webhook_latency_seconds.labels(company="unknown").observe(time.time() - start_time)
        return jsonify({"error": "invalid signature"}), 401

    if not validate_webhook_token(request):
        webhook_received_counter.labels(company="unknown", status="unauthorized").inc()
        webhook_latency_seconds.labels(company="unknown").observe(time.time() - start_time)
        return jsonify({"error": "invalid token"}), 401

    try:
        payload_dict = json.loads(raw_body_text or "{}")
    except json.JSONDecodeError as exc:
        webhook_received_counter.labels(company="unknown", status="bad_request").inc()
        webhook_latency_seconds.labels(company="unknown").observe(time.time() - start_time)
        PAYLOAD_LOGGER.info(
            "webhook_payload_invalid_json",
            error=str(exc),
            raw_body=raw_body_text,
        )
        return jsonify({"error": "invalid payload", "details": str(exc)}), 400

    try:
        payload = IncomingWebhook.from_payload(payload_dict)
    except ValidationError as exc:
        webhook_received_counter.labels(company="unknown", status="bad_request").inc()
        webhook_latency_seconds.labels(company="unknown").observe(time.time() - start_time)
        PAYLOAD_LOGGER.info(
            "webhook_payload_validation_failed",
            error=str(exc),
            payload=payload_dict,
        )
        return jsonify({"error": "invalid payload", "details": str(exc)}), 400

    tenant = getattr(g, "tenant", None)
    company_label = tenant.label if tenant else "unknown"
    if tenant is None:
        webhook_received_counter.labels(company=company_label, status="company_not_found").inc()
        webhook_latency_seconds.labels(company=company_label).observe(time.time() - start_time)
        return jsonify({"error": "company_not_found"}), 404

    rate_limiter = RateLimiter(current_app.redis, tenant)  # type: ignore[attr-defined]
    if not rate_limiter.check_ip(request.remote_addr or "unknown"):
        webhook_received_counter.labels(company=company_label, status="rate_limited_ip").inc()
        webhook_latency_seconds.labels(company=company_label).observe(time.time() - start_time)
        return jsonify({"error": "too_many_requests_ip"}), 429
    if not rate_limiter.check_number(payload.number):
        webhook_received_counter.labels(company=company_label, status="rate_limited_number").inc()
        webhook_latency_seconds.labels(company=company_label).observe(time.time() - start_time)
        return jsonify({"error": "too_many_requests_number"}), 429

    sanitized_number = payload.number
    sanitized_text = sanitize_text(payload.text)
    payload_format = (
        payload.model_extra.get("payload_format", "legacy")
        if hasattr(payload, "model_extra")
        else "legacy"
    )

    LOGGER.info(
        "webhook_message_normalized",
        number=sanitized_number,
        text=sanitized_text,
        kind=payload.kind,
        payload_format=payload_format,
    )
    PAYLOAD_LOGGER.info(
        "webhook_payload_parsed",
        number=sanitized_number,
        text=sanitized_text,
        kind=payload.kind,
        payload_format=payload_format,
        raw_payload=payload_dict,
    )

    queue: Queue = current_app.get_task_queue(tenant.company_id)  # type: ignore[attr-defined]
    dead_letter_queue: Queue = current_app.get_dead_letter_queue(tenant.company_id)  # type: ignore[attr-defined]
    analytics_service = getattr(current_app, "analytics_service", None)
    billing_service = getattr(current_app, "billing_service", None)
    if billing_service is None:
        billing_service = BillingService(
            current_app.db_session,
            current_app.redis,
            analytics_service,
        )
        current_app.billing_service = billing_service  # type: ignore[attr-defined]
    service = TaskService(
        current_app.redis,
        current_app.db_session,
        tenant,
        queue,
        dead_letter_queue,
        billing_service=billing_service,
        analytics_service=analytics_service,
    )

    contact_name = _extract_contact_name(payload, payload_dict)
    if contact_name:
        try:
            service.context_engine.update_contact_name(sanitized_number, contact_name)
        except Exception as exc:  # pragma: no cover - defensive log
            LOGGER.warning(
                "webhook_contact_name_update_failed",
                number=sanitized_number,
                error=str(exc),
            )

    correlation_id = (
        request.headers.get("X-Correlation-ID")
        or request.headers.get("X-Request-ID")
        or request.headers.get("X-Trace-ID")
        or request.environ.get("HTTP_X_CORRELATION_ID")
    )
    if not correlation_id:
        import uuid

        correlation_id = str(uuid.uuid4())

    service.enqueue(sanitized_number, sanitized_text, payload.kind, correlation_id)
    webhook_received_counter.labels(company=company_label, status="accepted").inc()
    webhook_latency_seconds.labels(company=company_label).observe(time.time() - start_time)
    return jsonify({"queued": True}), 202
