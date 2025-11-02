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
    monkeypatch.setattr(settings, "webhook_rate_limit_ip", 100)
    monkeypatch.setattr(settings, "webhook_rate_limit_number", 100)
    monkeypatch.setattr(settings, "rq_retry_delays", ())


def _sign(ts: int, body: bytes) -> str:
    message = f"{ts}.".encode() + body
    return hmac.new(settings.shared_secret.encode(), message, sha256).hexdigest()


def _post_payload(client, monkeypatch, payload: dict[str, object]):
    body = json.dumps(payload).encode()
    timestamp = int(time.time())
    signature = _sign(timestamp, body)
    calls: list[dict[str, str]] = []

    def fake_enqueue(self, number: str, text: str, kind: str, correlation_id: str) -> None:  # type: ignore[unused-argument]
        calls.append({"number": number, "text": text, "kind": kind, "correlation_id": correlation_id})

    monkeypatch.setattr(TaskService, "enqueue", fake_enqueue)

    response = client.post(
        "/webhook/whaticket",
        data=body,
        headers={
            "X-Signature": signature,
            "X-Timestamp": str(timestamp),
            "X-Company-Domain": "teste.local",
        },
        content_type="application/json",
    )
    return response, calls


def test_proto_conversation_payload(client, monkeypatch):
    payload = {
        "key": {"remoteJid": "5511999998888@s.whatsapp.net", "fromMe": False},
        "messageTimestamp": 1_762_101_460,
        "pushName": "Cliente",
        "message": {"conversation": "  eai  "},
    }

    response, calls = _post_payload(client, monkeypatch, payload)

    assert response.status_code == 202
    assert len(calls) == 1
    call = calls[0]
    assert call["number"] == "5511999998888"
    assert call["text"] == "eai"
    assert call["kind"] == "text"


def test_proto_extended_text_payload(client, monkeypatch):
    payload = {
        "key": {"remoteJid": "5511987654321@s.whatsapp.net", "fromMe": False},
        "messageTimestamp": 1_762_101_461,
        "pushName": "Cliente",
        "message": {"extendedTextMessage": {"text": "Oi!\nTudo bem?"}},
    }

    response, calls = _post_payload(client, monkeypatch, payload)

    assert response.status_code == 202
    assert len(calls) == 1
    call = calls[0]
    assert call["number"] == "5511987654321"
    assert call["text"] == "Oi! Tudo bem?"
    assert call["kind"] == "text"


def test_legacy_payload_fallback(client, monkeypatch):
    payload = {
        "event": "message",
        "body": "Teste direto do Whaticket — Osmar Dev",
        "contact": {"name": "Osmar", "number": "+55 (16) 99624-6673"},
    }

    response, calls = _post_payload(client, monkeypatch, payload)

    assert response.status_code == 202
    assert len(calls) == 1
    call = calls[0]
    assert call["number"] == "5516996246673"
    assert call["text"] == "Teste direto do Whaticket — Osmar Dev"
    assert call["kind"] == "text"
