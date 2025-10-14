from __future__ import annotations

import hmac
from hashlib import sha256

from app.config import settings
from app.services.security import validate_hmac_signature


class DummyRequest:
    def __init__(self, body: bytes, signature: str):
        self._body = body
        self.headers = {"X-Signature": signature}

    def get_data(self) -> bytes:  # type: ignore[override]
        return self._body


def test_validate_hmac_signature_valid(monkeypatch):
    secret = "supersecret"
    monkeypatch.setattr(settings, "shared_secret", secret)
    body = b'{"foo": "bar"}'
    signature = hmac.new(secret.encode(), body, sha256).hexdigest()
    request = DummyRequest(body, signature)
    assert validate_hmac_signature(request)


def test_validate_hmac_signature_invalid(monkeypatch):
    secret = "supersecret"
    monkeypatch.setattr(settings, "shared_secret", secret)
    body = b'{"foo": "bar"}'
    request = DummyRequest(body, "invalid")
    assert not validate_hmac_signature(request)
