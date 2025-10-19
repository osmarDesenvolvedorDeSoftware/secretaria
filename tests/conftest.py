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
from app.models import Company, Plan
from app.models.base import Base


class DummyRedis:
    def __init__(self):
        self.storage: dict[str, Any] = {}
        self.zsets: dict[str, list[float]] = {}
        self.sets: dict[str, set[str]] = {}
        self.hashes: dict[str, dict[str, Any]] = {}
        self.expiry: dict[str, int] = {}

    @staticmethod
    def from_url(url: str, decode_responses: bool = True):  # type: ignore[override]
        return DummyRedis()

    def pipeline(self):
        redis = self

        class Pipeline:
            def __init__(self) -> None:
                self._zcard_count = 0

            def zremrangebyscore(self, key, _min, _max):
                scores = redis.zsets.get(key, [])
                redis.zsets[key] = [score for score in scores if not (_min <= score <= _max)]
                return self

            def zadd(self, key, mapping: dict[str, float]):
                scores = redis.zsets.setdefault(key, [])
                scores.extend(mapping.values())
                return self

            def zcard(self, key):
                self._zcard_count = len(redis.zsets.get(key, []))
                return self

            def expire(self, key, ttl):
                return self

            def execute(self):
                return [None, None, self._zcard_count, None]

        return Pipeline()

    def ping(self):
        return True

    def get(self, key: str):
        return self.storage.get(key)

    def set(self, key: str, value: str):
        self.storage[key] = value

    def setex(self, key: str, ttl: int, value: str):
        self.storage[key] = value
        self.expiry[key] = ttl

    def delete(self, key: str):
        self.storage.pop(key, None)
        self.hashes.pop(key, None)
        self.sets.pop(key, None)
        self.expiry.pop(key, None)

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
        self.expiry[key] = ttl
        return True

    def info(self, section: str | None = None):
        if section == "memory":
            used_memory = sum(len(str(value)) for value in self.storage.values())
            return {"used_memory": used_memory, "maxmemory": 0}
        return {}

    def ttl(self, key: str):
        if key in self.expiry:
            return self.expiry[key]
        if key in self.hashes or key in self.sets or key in self.storage:
            return 60
        return -2

    def exists(self, key: str) -> bool:
        return key in self.storage or key in self.hashes or key in self.sets

    def smembers(self, key: str):
        return set(self.sets.get(key, set()))

    def sadd(self, key: str, *values: str):
        members = self.sets.setdefault(key, set())
        members.update(values)

    def hset(self, key: str, mapping: dict[str, Any]):
        data = self.hashes.setdefault(key, {})
        data.update(mapping)

    def hgetall(self, key: str):
        return dict(self.hashes.get(key, {}))

    def hincrby(self, key: str, field: str, amount: int = 1):
        data = self.hashes.setdefault(key, {})
        current = int(data.get(field, 0))
        current += amount
        data[field] = current
        return current


class DummyQueue:
    def __init__(self, *args, **kwargs):
        self.enqueued: list[tuple[Any, tuple[Any, ...], dict[str, Any]]] = []

    def enqueue(self, *args, **kwargs):
        func = args[0]
        job_args = args[1:]
        self.enqueued.append((func, job_args, kwargs))

        class _Job:
            def __init__(self, job_id: int, meta: dict[str, Any]):
                self.id = str(job_id)
                self.meta = meta

        return _Job(len(self.enqueued), kwargs.get("meta", {}))

    def count(self):
        return len(self.enqueued)


@pytest.fixture
def app(monkeypatch) -> Generator[Flask, None, None]:
    monkeypatch.setattr(app_init, "Redis", DummyRedis)
    monkeypatch.setattr(tasks_module, "Redis", DummyRedis)
    monkeypatch.setattr(whaticket_module, "Redis", DummyRedis)
    monkeypatch.setattr(llm_module, "Redis", DummyRedis)
    monkeypatch.setattr(app_init, "Queue", DummyQueue)
    monkeypatch.setattr(tasks_module, "Queue", DummyQueue)
    config_settings.database_url = "sqlite+pysqlite:///:memory:"
    config_settings.panel_password = "painel-teste"
    config_settings.panel_jwt_secret = "painel-secret"
    config_settings.panel_token_ttl_seconds = 3600
    config_settings.context_ttl = 600
    config_settings.context_ttl_seconds = 600
    config_settings.rate_limit_ttl = 60
    config_settings.rate_limit_ttl_seconds = 60
    test_app = init_app()
    test_app.redis = DummyRedis()  # type: ignore[attr-defined]
    test_app.task_queue = DummyQueue()  # type: ignore[attr-defined]
    Base.metadata.create_all(test_app.db_engine)  # type: ignore[attr-defined]
    with test_app.app_context():
        session = test_app.db_session()  # type: ignore[attr-defined]
        try:
            plan = Plan(name="Starter", limite_mensagens=1000, limite_tokens=500000, preco=0, features=[])
            session.add(plan)
            session.flush()
            company = Company(name="Empresa Teste", domain="teste.local", status="ativo", current_plan_id=plan.id)
            session.add(company)
            session.commit()
        finally:
            session.close()
        yield test_app


@pytest.fixture
def client(app: Flask):
    return app.test_client()
