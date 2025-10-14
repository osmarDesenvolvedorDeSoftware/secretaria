from __future__ import annotations

import hmac
import re
import time
from hashlib import sha256
from typing import Optional

from flask import Request

from app.config import settings

PROMPT_INJECTION_PATTERNS = [
    re.compile(r"forget previous instructions", re.IGNORECASE),
    re.compile(r"ignore all (prior|previous)", re.IGNORECASE),
    re.compile(r"\b(curl|python|system|delete|rm|exec|sudo)\b", re.IGNORECASE),
]

def sanitize_for_log(value: str) -> str:
    if not value:
        return value

    sanitized = re.sub(r"Bearer\s+[A-Za-z0-9._\-]+", "Bearer ***", value, flags=re.IGNORECASE)
    sanitized = re.sub(
        r"(?i)(api[-_]?key|x-api-key|token|authorization)(\s*[:=]\s*)(['\"]?)[^'\"\s]+(['\"]?)",
        r"\1\2\3***\4",
        sanitized,
    )
    return sanitized


def validate_hmac(
    secret: str,
    timestamp: Optional[str],
    raw_body: bytes,
    signature: Optional[str],
    *,
    skew_seconds: int = 300,
) -> bool:
    if not secret or not timestamp or not signature:
        return False

    try:
        ts = float(timestamp)
    except (TypeError, ValueError):
        return False

    now = time.time()
    if abs(now - ts) > skew_seconds:
        return False

    ts_str = str(int(ts))
    message = ts_str.encode() + b"." + raw_body
    computed = hmac.new(secret.encode(), message, sha256).hexdigest()
    return hmac.compare_digest(signature, computed)


def validate_hmac_signature(request: Request) -> bool:
    return validate_hmac(
        settings.shared_secret,
        request.headers.get("X-Timestamp"),
        request.get_data(),
        request.headers.get("X-Signature"),
    )


def validate_webhook_token(request: Request) -> bool:
    if not settings.webhook_token_optional:
        return True
    token = request.headers.get("X-Webhook-Token")
    return token == settings.webhook_token_optional


def sanitize_text(text: str, max_length: int = 1000) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    return clean[:max_length]


def detect_prompt_injection(text: str) -> bool:
    return any(pattern.search(text) for pattern in PROMPT_INJECTION_PATTERNS)
