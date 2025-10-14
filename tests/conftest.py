from __future__ import annotations

from typing import Any, Generator

import pytest
from flask import Flask

import app.__init__ as app_init
import app.services.llm as llm_module
import app.services.tasks as tasks_module
import app.services.whaticket as whaticket_module
from app import init_app
from app.config import settings as config_settings
from app.models.base import Base


class DummyRedis:
    def __init__(self):
        self.storage: dict[str, Any] = {}
        self.zsets: dict[str, list[float]] = {}

    @staticmethod
    def from_url(url: str, decode_responses: bool = True):  # type: ignore[override]
        return DummyRedis()

    def pipeline(self):
        class Pipeline:
            def __init__(self):
                self.count = 0

            def zremrangebyscore(self, *args, **kwargs):
                return self

            def zadd(self, *args, **kwargs):
                if args and isinstance(args[-1], dict):
                    self.count += len(args[-1])
                elif "mapping" in kwargs:
                    self.count += len(kwargs["mapping"])
                else:
                    self.count += 1
                return self

            def zcard(self, *args, **kwargs):
                return self

            def expire(self, *args, **kwargs):
                return self

            def execute(self):
                return [None, None, max(self.count, 1), None]

        return Pipeline()

    def get(self, key: str):
        return self.storage.get(key)

    def set(self, key: str, value: str):
        self.storage[key] = value

    def setex(self, key: str, ttl: int, value: str):
        self.storage[key] = value

    def delete(self, key: str):
        self.storage.pop(key, None)

    def llen(self, key: str) -> int:
        return 0

    def zremrangebyscore(self, key: str, min_score: float, max_score: float):
        pass

    def zadd(self, key: str, mapping: dict[str, float]):
        scores = self.zsets.setdefault(key, [])
        scores.extend(mapping.values())

    def zcard(self, key: str):
        return len(self.zsets.get(key, []))

    def expire(self, key: str, ttl: int):
        return True


class DummyQueue:
    def __init__(self, *args, **kwargs):
        pass

    def enqueue(self, *args, **kwargs):
        pass

    def count(self):
        return 0


@pytest.fixture
def app(monkeypatch) -> Generator[Flask, None, None]:
    monkeypatch.setattr(app_init, "Redis", DummyRedis)
    monkeypatch.setattr(tasks_module, "Redis", DummyRedis)
    monkeypatch.setattr(whaticket_module, "Redis", DummyRedis)
    monkeypatch.setattr(llm_module, "Redis", DummyRedis)
    monkeypatch.setattr(app_init, "Queue", DummyQueue)
    monkeypatch.setattr(tasks_module, "Queue", DummyQueue)
    config_settings.database_url = "sqlite+pysqlite:///:memory:"
    test_app = init_app()
    test_app.redis = DummyRedis()  # type: ignore[attr-defined]
    test_app.task_queue = DummyQueue()  # type: ignore[attr-defined]
    Base.metadata.create_all(test_app.db_engine)  # type: ignore[attr-defined]
    with test_app.app_context():
        yield test_app


@pytest.fixture
def client(app: Flask):
    return app.test_client()
