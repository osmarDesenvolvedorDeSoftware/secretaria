from __future__ import annotations

import json
import time
from hashlib import sha256
import hmac

import pytest

from app.config import settings
from tests.conftest import DummyQueue


@pytest.fixture(autouse=True)
def configure_settings(monkeypatch):
    monkeypatch.setattr(settings, "shared_secret", "super-secret")
    monkeypatch.setattr(settings, "webhook_token_optional", None)
    monkeypatch.setattr(settings, "webhook_rate_limit_ip", 10)
    monkeypatch.setattr(settings, "webhook_rate_limit_number", 10)
    monkeypatch.setattr(settings, "rq_retry_delays", ())


def _sign(ts: int, body: bytes) -> str:
    return hmac.new(settings.shared_secret.encode(), f"{ts}.".encode() + body, sha256).hexdigest()


def test_webhook_success_enqueues_job(app, client, monkeypatch):
    app.task_queue = DummyQueue()  # type: ignore[attr-defined]
    app.dead_letter_queue = DummyQueue()  # type: ignore[attr-defined]
    app.get_task_queue = lambda _company_id: app.task_queue  # type: ignore[attr-defined]
    app.get_dead_letter_queue = lambda _company_id: app.dead_letter_queue  # type: ignore[attr-defined]
    app.task_queue.enqueued.clear()  # type: ignore[attr-defined]
    payload = {
        "message": {"conversation": "olá"},
        "number": "5511999999999",
    }
    body = json.dumps(payload).encode()
    ts = 1_700_000_000
    signature = _sign(ts, body)
    monkeypatch.setattr(time, "time", lambda: ts + 1)

    response = client.post(
        "/webhook/whaticket",
        data=body,
        headers={
            "X-Signature": signature,
            "X-Timestamp": str(ts),
            "X-Company-Domain": "teste.local",
        },
        content_type="application/json",
    )

    assert response.status_code == 202
    assert len(app.task_queue.enqueued) == 1  # type: ignore[attr-defined]
    job = app.task_queue.enqueued[0]  # type: ignore[attr-defined]
    assert job[1][0] == 1
    assert job[1][1] == "5511999999999"


def test_webhook_invalid_signature(app, client, monkeypatch):
    app.task_queue = DummyQueue()  # type: ignore[attr-defined]
    app.get_task_queue = lambda _company_id: app.task_queue  # type: ignore[attr-defined]
    app.task_queue.enqueued.clear()  # type: ignore[attr-defined]
    payload = {"message": {"conversation": "olá"}, "number": "5511999999999"}
    body = json.dumps(payload).encode()
    ts = 1_700_000_000
    monkeypatch.setattr(time, "time", lambda: ts)

    response = client.post(
        "/webhook/whaticket",
        data=body,
        headers={
            "X-Signature": "bad",
            "X-Timestamp": str(ts),
            "X-Company-Domain": "teste.local",
        },
        content_type="application/json",
    )

    assert response.status_code == 401
    assert app.task_queue.enqueued == []  # type: ignore[attr-defined]


def test_webhook_rate_limited(app, client, monkeypatch):
    app.task_queue = DummyQueue()  # type: ignore[attr-defined]
    app.get_task_queue = lambda _company_id: app.task_queue  # type: ignore[attr-defined]
    app.task_queue.enqueued.clear()  # type: ignore[attr-defined]
    monkeypatch.setattr(settings, "webhook_rate_limit_number", 0)

    payload = {"message": {"conversation": "olá"}, "number": "5511999999999"}
    body = json.dumps(payload).encode()
    ts = 1_700_000_000
    signature = _sign(ts, body)
    monkeypatch.setattr(time, "time", lambda: ts)

    response = client.post(
        "/webhook/whaticket",
        data=body,
        headers={
            "X-Signature": signature,
            "X-Timestamp": str(ts),
            "X-Company-Domain": "teste.local",
        },
        content_type="application/json",
    )

    assert response.status_code == 429
    assert app.task_queue.enqueued == []  # type: ignore[attr-defined]


def test_webhook_invalid_token(app, client, monkeypatch):
    app.task_queue = DummyQueue()  # type: ignore[attr-defined]
    app.get_task_queue = lambda _company_id: app.task_queue  # type: ignore[attr-defined]
    app.task_queue.enqueued.clear()  # type: ignore[attr-defined]
    monkeypatch.setattr(settings, "webhook_token_optional", "expected")
    payload = {"message": {"conversation": "olá"}, "number": "5511999999999"}
    body = json.dumps(payload).encode()
    ts = 1_700_000_000
    signature = _sign(ts, body)

    response = client.post(
        "/webhook/whaticket",
        data=body,
        headers={
            "X-Signature": signature,
            "X-Timestamp": str(ts),
            "X-Webhook-Token": "wrong",
            "X-Company-Domain": "teste.local",
        },
        content_type="application/json",
    )

    assert response.status_code == 401
    assert app.task_queue.enqueued == []  # type: ignore[attr-defined]


def test_webhook_invalid_payload_returns_400(app, client, monkeypatch):
    app.task_queue = DummyQueue()  # type: ignore[attr-defined]
    app.get_task_queue = lambda _company_id: app.task_queue  # type: ignore[attr-defined]
    app.task_queue.enqueued.clear()  # type: ignore[attr-defined]
    body = b"{bad json"
    ts = 1_700_000_000
    signature = _sign(ts, body)
    monkeypatch.setattr(time, "time", lambda: ts)

    response = client.post(
        "/webhook/whaticket",
        data=body,
        headers={
            "X-Signature": signature,
            "X-Timestamp": str(ts),
            "X-Company-Domain": "teste.local",
        },
        content_type="application/json",
    )

    assert response.status_code == 400
    assert app.task_queue.enqueued == []  # type: ignore[attr-defined]
