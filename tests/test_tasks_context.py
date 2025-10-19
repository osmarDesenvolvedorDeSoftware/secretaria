from __future__ import annotations

import json

from app.config import settings
import json

from app.config import settings
from app.services.tasks import TaskService
from tests.conftest import DummyQueue, DummyRedis


class DummySession:
    def query(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):  # pragma: no cover - simple passthrough
        return self

    def order_by(self, *args, **kwargs):  # pragma: no cover - simple passthrough
        return self

    def first(self):  # pragma: no cover - no persistence in tests
        return None

    def add(self, *_args, **_kwargs):  # pragma: no cover
        return None

    def commit(self):  # pragma: no cover
        return None

    def rollback(self):  # pragma: no cover
        return None

    def close(self):  # pragma: no cover
        return None


class DummySessionFactory:
    def __init__(self) -> None:
        self.session = DummySession()

    def __call__(self):
        return self.session

    def remove(self):
        return None


def test_get_context_returns_saved_messages(monkeypatch):
    redis_client = DummyRedis()
    service = TaskService(redis_client, DummySessionFactory(), DummyQueue())

    redis_client.set(
        "ctx:5511000000000",
        json.dumps([
            {"role": "user", "body": "Oi"},
        ]),
    )

    assert service.get_context("5511000000000") == [{"role": "user", "body": "Oi"}]


def test_get_context_handles_invalid_json():
    redis_client = DummyRedis()
    service = TaskService(redis_client, DummySessionFactory(), DummyQueue())

    redis_client.set("ctx:5511999999999", "{invalid")

    assert service.get_context("5511999999999") == []


def test_get_context_handles_non_list_payload():
    redis_client = DummyRedis()
    service = TaskService(redis_client, DummySessionFactory(), DummyQueue())

    redis_client.set("ctx:5511888877776", json.dumps({"not": "a list"}))

    assert service.get_context("5511888877776") == []


def test_set_context_truncates_and_sets_ttl(monkeypatch):
    redis_client = DummyRedis()
    service = TaskService(redis_client, DummySessionFactory(), DummyQueue())

    monkeypatch.setattr(settings, "context_max_messages", 3)
    monkeypatch.setattr(settings, "context_ttl", 120)
    monkeypatch.setattr(settings, "context_ttl_seconds", 120)

    messages = [{"role": "user", "body": f"msg-{i}"} for i in range(6)]

    service.set_context("551177665544", messages)

    stored = json.loads(redis_client.get("ctx:551177665544"))
    assert len(stored) == 3
    assert stored[0]["body"] == "msg-3"
    assert redis_client.expiry["ctx:551177665544"] == 120


def test_enqueue_without_retry_does_not_add_retry(monkeypatch):
    redis_client = DummyRedis()
    queue = DummyQueue()
    service = TaskService(redis_client, DummySessionFactory(), queue)

    monkeypatch.setattr(settings, "rq_retry_delays", ())
    monkeypatch.setattr(settings, "rq_retry_max_attempts", 0)

    service.enqueue("5511444433332", "Olá", "text", "cid-1")

    _, _, kwargs = queue.enqueued[0]
    assert "retry" not in kwargs
