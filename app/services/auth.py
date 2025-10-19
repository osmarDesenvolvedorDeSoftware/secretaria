from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Dict, Optional


class InvalidTokenError(Exception):
    """Indica que o token JWT fornecido é inválido ou expirou."""


def _urlsafe_b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _urlsafe_b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _sign(message: bytes, secret: str) -> bytes:
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).digest()


def encode_jwt(payload: Dict[str, Any], secret: str, expires_in: int) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    body = payload.copy()
    body.setdefault("iat", now)
    body["exp"] = now + int(expires_in)

    header_segment = _urlsafe_b64encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_segment = _urlsafe_b64encode(json.dumps(body, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    signature = _urlsafe_b64encode(_sign(signing_input, secret))
    return f"{header_segment}.{payload_segment}.{signature}"


def decode_jwt(token: str, secret: str) -> Dict[str, Any]:
    try:
        header_segment, payload_segment, signature_segment = token.split('.')
    except ValueError as exc:  # pragma: no cover - defesa contra tokens malformados
        raise InvalidTokenError("invalid_token_format") from exc

    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    expected_signature = _urlsafe_b64encode(_sign(signing_input, secret))
    if not hmac.compare_digest(expected_signature, signature_segment):
        raise InvalidTokenError("invalid_signature")

    payload_bytes = _urlsafe_b64decode(payload_segment)
    try:
        payload: Dict[str, Any] = json.loads(payload_bytes)
    except json.JSONDecodeError as exc:  # pragma: no cover
        raise InvalidTokenError("invalid_payload") from exc

    exp = payload.get("exp")
    if exp is None or int(exp) < int(time.time()):
        raise InvalidTokenError("token_expired")

    return payload


def verify_jwt(token: Optional[str], secret: str) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    try:
        return decode_jwt(token, secret)
    except InvalidTokenError:
        return None


__all__ = [
    "InvalidTokenError",
    "encode_jwt",
    "decode_jwt",
    "verify_jwt",
]
