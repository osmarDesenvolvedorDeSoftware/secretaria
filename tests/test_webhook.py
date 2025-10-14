from __future__ import annotations

import json
from hashlib import sha256

import hmac

import pytest

from app.config import settings
from app.services.tasks import TaskService


@pytest.fixture(autouse=True)
def configure_settings(monkeypatch):
    monkeypatch.setattr(settings, "shared_secret", "secret")
    monkeypatch.setattr(settings, "webhook_token_optional", None)
    monkeypatch.setattr(settings, "webhook_rate_limit_ip", 10)
    monkeypatch.setattr(settings, "webhook_rate_limit_number", 10)
    monkeypatch.setattr(settings, "context_max_messages", 5)
    monkeypatch.setattr(settings, "context_ttl_seconds", 600)


def sign(body: bytes) -> str:
    return hmac.new(settings.shared_secret.encode(), body, sha256).hexdigest()


def test_webhook_rejects_invalid_signature(client):
    payload = {"message": {"body": "hello"}, "number": "5511999999999"}
    body = json.dumps(payload).encode()
    response = client.post(
        "/webhook/whaticket",
        data=body,
        headers={"X-Signature": "invalid"},
        content_type="application/json",
    )
    assert response.status_code == 401


def test_webhook_enqueues_task(client, monkeypatch):
    payload = {"message": {"body": "hello"}, "number": "11999999999"}
    body = json.dumps(payload).encode()
    signature = sign(body)
    called = {}

    def fake_enqueue(self, number, text, correlation_id):
        called.update({"number": number, "text": text, "correlation_id": correlation_id})

    monkeypatch.setattr(TaskService, "enqueue", fake_enqueue)

    response = client.post(
        "/webhook/whaticket",
        data=body,
        headers={"X-Signature": signature},
        content_type="application/json",
    )
    assert response.status_code == 202
    assert called["number"].startswith("55")
    assert called["text"] == "hello"


def test_webhook_rate_limit_number(client, monkeypatch):
    monkeypatch.setattr(settings, "webhook_rate_limit_number", 0)
    payload = {"message": {"body": "hello"}, "number": "5511999999999"}
    body = json.dumps(payload).encode()
    signature = sign(body)
    response = client.post(
        "/webhook/whaticket",
        data=body,
        headers={"X-Signature": signature},
        content_type="application/json",
    )
    assert response.status_code == 429
