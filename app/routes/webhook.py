from __future__ import annotations

import json
from typing import Any

from flask import Blueprint, Response, current_app, jsonify, request
from pydantic import BaseModel, ValidationError, field_validator, model_validator
from rq import Queue

from app.metrics import webhook_received_counter
from app.services.payload import extract_number, extract_text_and_kind
from app.services.rate_limit import RateLimiter
from app.services.security import sanitize_text, validate_hmac_signature, validate_webhook_token
from app.services.tasks import TaskService


webhook_bp = Blueprint("webhook", __name__, url_prefix="/webhook")


class IncomingWebhook(BaseModel):
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

    @model_validator(mode="before")
    @classmethod
    def from_payload_dict(cls, values: Any) -> Any:
        if isinstance(values, dict) and ("text" not in values or "kind" not in values):
            try:
                number = extract_number(values)
                text, kind = extract_text_and_kind(values)
            except ValueError as exc:
                raise ValueError(str(exc)) from exc
            values = {"number": number, "text": text, "kind": kind, **values}
        return values

    @classmethod
    def parse_raw_body(cls, raw_body: bytes) -> "IncomingWebhook":
        data = json.loads(raw_body)
        return cls.from_payload(data)


    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "IncomingWebhook":
        return cls.model_validate(payload)


@webhook_bp.post("/whaticket")
def whaticket_webhook() -> Response:
    raw_body = request.get_data()

    if not validate_hmac_signature(request):
        webhook_received_counter.labels(status="unauthorized").inc()
        return jsonify({"error": "invalid signature"}), 401

    if not validate_webhook_token(request):
        webhook_received_counter.labels(status="unauthorized").inc()
        return jsonify({"error": "invalid token"}), 401

    try:
        payload = IncomingWebhook.parse_raw_body(raw_body)
    except (json.JSONDecodeError, ValidationError) as exc:
        webhook_received_counter.labels(status="bad_request").inc()
        return jsonify({"error": "invalid payload", "details": str(exc)}), 400

    rate_limiter = RateLimiter(current_app.redis)  # type: ignore[attr-defined]
    if not rate_limiter.check_ip(request.remote_addr or "unknown"):
        webhook_received_counter.labels(status="rate_limited_ip").inc()
        return jsonify({"error": "too_many_requests_ip"}), 429
    if not rate_limiter.check_number(payload.number):
        webhook_received_counter.labels(status="rate_limited_number").inc()
        return jsonify({"error": "too_many_requests_number"}), 429

    sanitized_number = payload.number
    sanitized_text = sanitize_text(payload.text)

    queue: Queue = current_app.task_queue  # type: ignore[attr-defined]
    service = TaskService(current_app.redis, current_app.db_session, queue)  # type: ignore[attr-defined]

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
    webhook_received_counter.labels(status="accepted").inc()
    return jsonify({"queued": True}), 202
