from __future__ import annotations

import pytest

from app.models import Conversation, DeliveryLog
from app.services.tasks import process_incoming_message
from app.services.whaticket import WhaticketError


@pytest.fixture(autouse=True)
def configure_retries(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "context_max_messages", 5)
    monkeypatch.setattr(settings, "context_ttl_seconds", 600)
    monkeypatch.setattr(settings, "rq_retry_delays", ())


def _clear_db(app):
    session = app.db_session()  # type: ignore[attr-defined]
    session.query(DeliveryLog).delete()
    session.query(Conversation).delete()
    session.commit()
    session.close()
    app.task_queue.enqueued.clear()  # type: ignore[attr-defined]


def test_successful_delivery_updates_context(app, monkeypatch):
    with app.app_context():
        _clear_db(app)

        monkeypatch.setattr(
            "app.services.tasks.LLMClient.generate_reply",
            lambda self, text, context: "Resposta automática",
        )
        monkeypatch.setattr(
            "app.services.tasks.WhaticketClient.send_text",
            lambda self, number, body: "external-id",
        )

        process_incoming_message("5511999999999", "Olá", "text", "corr")

        session = app.db_session()  # type: ignore[attr-defined]
        conversations = session.query(Conversation).all()
        assert len(conversations) == 1
        assert conversations[0].context_json[-1]["body"] == "Resposta automática"

        logs = session.query(DeliveryLog).all()
        assert len(logs) == 1
        assert logs[0].status == "SENT"
        session.close()

        assert app.redis.get("ctx:5511999999999") is not None  # type: ignore[attr-defined]
        assert app.task_queue.enqueued == []  # type: ignore[attr-defined]


def test_retryable_failure_logs_and_requeues(app, monkeypatch):
    with app.app_context():
        _clear_db(app)

        monkeypatch.setattr(
            "app.services.tasks.LLMClient.generate_reply",
            lambda self, text, context: "Resposta automática",
        )

        def fail_send(*_):
            raise WhaticketError("temp", retryable=True)

        monkeypatch.setattr("app.services.tasks.WhaticketClient.send_text", fail_send)

        with pytest.raises(WhaticketError):
            process_incoming_message("5511888877777", "Olá", "text", "corr")

        session = app.db_session()  # type: ignore[attr-defined]
        assert session.query(Conversation).count() == 0
        logs = session.query(DeliveryLog).all()
        assert len(logs) == 1
        assert logs[0].status == "FAILED"
        assert "temp" in logs[0].error
        session.close()

        assert len(app.task_queue.enqueued) == 1  # type: ignore[attr-defined]
        job = app.task_queue.enqueued[0]  # type: ignore[attr-defined]
        assert job[1][0] == "5511888877777"


def test_non_retryable_failure_does_not_requeue(app, monkeypatch):
    with app.app_context():
        _clear_db(app)

        monkeypatch.setattr(
            "app.services.tasks.LLMClient.generate_reply",
            lambda self, text, context: "Resposta automática",
        )

        def fail_send(*_):
            raise WhaticketError("fatal", retryable=False)

        monkeypatch.setattr("app.services.tasks.WhaticketClient.send_text", fail_send)

        with pytest.raises(WhaticketError):
            process_incoming_message("5511777666555", "Olá", "text", "corr")

        session = app.db_session()  # type: ignore[attr-defined]
        assert session.query(Conversation).count() == 0
        logs = session.query(DeliveryLog).all()
        assert len(logs) == 1
        assert logs[0].status == "FAILED"
        assert "fatal" in logs[0].error
        session.close()

        assert app.task_queue.enqueued == []  # type: ignore[attr-defined]
