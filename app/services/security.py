from __future__ import annotations

import hmac
import re
from hashlib import sha256

from flask import Request

from app.config import settings

PROMPT_INJECTION_PATTERNS = [
    re.compile(r"forget previous instructions", re.IGNORECASE),
    re.compile(r"ignore all (prior|previous)", re.IGNORECASE),
]


def validate_hmac_signature(request: Request) -> bool:
    signature = request.headers.get("X-Signature")
    if not signature or not settings.shared_secret:
        return False

    computed = hmac.new(
        settings.shared_secret.encode(),
        request.get_data(),
        sha256,
    ).hexdigest()
    return hmac.compare_digest(signature, computed)


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
