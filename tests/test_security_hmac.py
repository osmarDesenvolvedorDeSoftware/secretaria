from __future__ import annotations

import json
import time
from hashlib import sha256
import hmac

import pytest

from app.config import settings
from app.services.tasks import TaskService


@pytest.fixture(autouse=True)
def configure_settings(monkeypatch):
    monkeypatch.setattr(settings, "shared_secret", "super-secret")
    monkeypatch.setattr(settings, "webhook_token_optional", None)
    monkeypatch.setattr(settings, "webhook_rate_limit_ip", 10)
    monkeypatch.setattr(settings, "webhook_rate_limit_number", 10)


def _sign(ts: int, body: bytes) -> str:
    message = f"{ts}.".encode() + body
    return hmac.new(settings.shared_secret.encode(), message, sha256).hexdigest()


def test_valid_signature_returns_202(client, monkeypatch):
    payload = {"message": {"conversation": "olá"}, "number": "5511999999999"}
    body = json.dumps(payload).encode()
    ts = 1_700_000_000
    signature = _sign(ts, body)
    monkeypatch.setattr(time, "time", lambda: ts + 10)

    called = {}

    def fake_enqueue(self, number, text, kind, correlation_id):
        called.update(number=number, text=text, kind=kind, correlation_id=correlation_id)

    monkeypatch.setattr(TaskService, "enqueue", fake_enqueue)

    response = client.post(
        "/webhook/whaticket",
        data=body,
        headers={"X-Signature": signature, "X-Timestamp": str(ts)},
        content_type="application/json",
    )

    assert response.status_code == 202
    assert called["kind"] == "text"


def test_timestamp_outside_window(client, monkeypatch):
    payload = {"message": {"conversation": "olá"}, "number": "5511999999999"}
    body = json.dumps(payload).encode()
    ts = 1_700_000_000
    signature = _sign(ts, body)
    monkeypatch.setattr(time, "time", lambda: ts + 600)

    response = client.post(
        "/webhook/whaticket",
        data=body,
        headers={"X-Signature": signature, "X-Timestamp": str(ts)},
        content_type="application/json",
    )

    assert response.status_code == 401


def test_invalid_signature(client, monkeypatch):
    payload = {"message": {"conversation": "olá"}, "number": "5511999999999"}
    body = json.dumps(payload).encode()
    ts = 1_700_000_000
    monkeypatch.setattr(time, "time", lambda: ts)

    response = client.post(
        "/webhook/whaticket",
        data=body,
        headers={"X-Signature": "bad", "X-Timestamp": str(ts)},
        content_type="application/json",
    )

    assert response.status_code == 401
