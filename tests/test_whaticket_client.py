from __future__ import annotations

import json

import pytest
import requests

from app.config import settings
from app.services.whaticket import WhaticketClient, WhaticketError
from tests.conftest import DummyRedis


class DummyResponse:
    def __init__(self, status_code: int = 200, json_body: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._json_body = json_body
        self.text = text or json.dumps(json_body or {})

    def json(self):
        if self._json_body is None:
            raise json.JSONDecodeError("invalid", self.text, 0)
        return self._json_body


@pytest.fixture(autouse=True)
def configure_settings(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_api_url", "http://test/api")
    monkeypatch.setattr(settings, "whaticket_retry_backoff_seconds", 0)
    monkeypatch.setattr(settings, "enable_jwt_login", False)
    monkeypatch.setattr(settings, "whatsapp_bearer_token", "token")
    monkeypatch.setattr("tenacity.nap.sleep", lambda *_args, **_kwargs: None)


def test_timeout_is_retryable(monkeypatch):
    client = WhaticketClient(DummyRedis())
    calls = {"count": 0}

    def fake_post(*args, **kwargs):
        calls["count"] += 1
        raise requests.Timeout("boom")

    monkeypatch.setattr(requests, "post", fake_post)

    with pytest.raises(WhaticketError) as exc:
        client.send_text("5511999999999", "olá")

    assert exc.value.retryable is True
    expected_attempts = client.send_text.retry.stop.max_attempt_number
    assert calls["count"] == expected_attempts


def test_server_error_retry(monkeypatch):
    client = WhaticketClient(DummyRedis())
    calls = {"count": 0}

    def fake_post(*args, **kwargs):
        calls["count"] += 1
        return DummyResponse(status_code=500, text="erro interno")

    monkeypatch.setattr(requests, "post", fake_post)

    with pytest.raises(WhaticketError) as exc:
        client.send_text("5511999999999", "olá")

    assert exc.value.retryable is True
    assert calls["count"] == settings.whaticket_retry_attempts
    assert exc.value.status == 500


def test_client_error_not_retryable(monkeypatch):
    client = WhaticketClient(DummyRedis())

    def fake_post(*args, **kwargs):
        return DummyResponse(status_code=400, text="bad request")

    monkeypatch.setattr(requests, "post", fake_post)

    with pytest.raises(WhaticketError) as exc:
        client.send_text("5511999999999", "olá")

    assert exc.value.retryable is False
    assert exc.value.status == 400


def test_invalid_json_returns_text(monkeypatch):
    client = WhaticketClient(DummyRedis())

    def fake_post(*args, **kwargs):
        return DummyResponse(status_code=200, json_body=None, text="ok")

    monkeypatch.setattr(requests, "post", fake_post)

    message_id = client.send_text("5511999999999", "olá")
    assert message_id == "ok"


def test_send_media_success(monkeypatch):
    client = WhaticketClient(DummyRedis())

    def fake_post(url, headers, json, timeout):
        assert json["mediaUrl"] == "http://example.com/img.jpg"
        assert json["mediaType"] == "image"
        return DummyResponse(status_code=200, json_body={"id": "media-1"})

    monkeypatch.setattr(requests, "post", fake_post)

    message_id = client.send_media(
        "5511999999999",
        "http://example.com/img.jpg",
        caption="Foto",
        media_type="image",
    )
    assert message_id == "media-1"


def test_jwt_authentication_is_cached(monkeypatch):
    redis_client = DummyRedis()
    client = WhaticketClient(redis_client)
    monkeypatch.setattr(settings, "enable_jwt_login", True)
    monkeypatch.setattr(settings, "whaticket_jwt_email", "agent@test.io")
    monkeypatch.setattr(settings, "whaticket_jwt_password", "secret")
    monkeypatch.setattr(settings, "whatsapp_api_url", "http://test/api/messages/send")

    def fake_login(*args, **kwargs):
        return DummyResponse(status_code=200, json_body={"token": "abc", "expiresIn": 600})

    monkeypatch.setattr(requests, "post", fake_login)

    token_first = client._get_auth_token()
    assert token_first == "abc"

    def fail_login(*args, **kwargs):
        raise AssertionError("login should not be called after caching")

    monkeypatch.setattr(requests, "post", fail_login)
    token_cached = client._get_auth_token()
    assert token_cached == "abc"
