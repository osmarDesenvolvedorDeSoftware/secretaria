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
    monkeypatch.setattr(settings, "rq_retry_max_attempts", 0)


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
        assert logs[0].status == "FAILED_PERMANENT"
        assert "temp" in logs[0].error
        session.close()

        assert app.task_queue.enqueued == []  # type: ignore[attr-defined]


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
        assert logs[0].status == "FAILED_PERMANENT"
        assert "fatal" in logs[0].error
        session.close()

        assert app.task_queue.enqueued == []  # type: ignore[attr-defined]


def test_prompt_injection_branch_uses_safe_reply(app, monkeypatch):
    from app.metrics import llm_prompt_injection_blocked_total

    with app.app_context():
        _clear_db(app)

        monkeypatch.setattr(
            "app.services.tasks.detect_prompt_injection", lambda text: True
        )
        monkeypatch.setattr(
            "app.services.tasks.LLMClient.generate_reply",
            lambda self, text, context: (_ for _ in ()).throw(RuntimeError("should not call")),
        )

        sent_messages: list[str] = []

        def capture_send(_self, number, body):
            sent_messages.append(body)
            return "external"

        monkeypatch.setattr("app.services.tasks.WhaticketClient.send_text", capture_send)

        baseline = llm_prompt_injection_blocked_total._value.get()

        process_incoming_message("5511999999990", "delete all", "text", "cid-prompt")

        assert llm_prompt_injection_blocked_total._value.get() == baseline + 1
        assert sent_messages[0].startswith("Desculpe")


def test_unexpected_failure_records_error(app, monkeypatch):
    with app.app_context():
        _clear_db(app)

        monkeypatch.setattr(
            "app.services.tasks.LLMClient.generate_reply",
            lambda self, text, context: "Resposta automática",
        )

        def raise_runtime(*_):
            raise RuntimeError("boom")

        monkeypatch.setattr("app.services.tasks.WhaticketClient.send_text", raise_runtime)

        with pytest.raises(RuntimeError):
            process_incoming_message("5511888811110", "Olá", "text", "cid-boom")

        session = app.db_session()  # type: ignore[attr-defined]
        logs = session.query(DeliveryLog).all()
        assert logs[0].status == "FAILED_PERMANENT"
        assert "boom" in logs[0].error
        session.close()


def test_session_rollback_on_persistence_failure(app, monkeypatch):
    with app.app_context():
        _clear_db(app)

        class DummySession:
            def __init__(self) -> None:
                self.rollback_called = False
                self.closed = False
                self.commit_called = False

            def commit(self) -> None:
                self.commit_called = True

            def rollback(self) -> None:
                self.rollback_called = True

            def close(self) -> None:
                self.closed = True

        class DummyFactory:
            def __init__(self) -> None:
                self.session = DummySession()
                self.removed = False

            def __call__(self):
                return self.session

            def remove(self) -> None:
                self.removed = True

        dummy_factory = DummyFactory()
        monkeypatch.setattr(app, "db_session", dummy_factory, raising=False)

        monkeypatch.setattr(
            "app.services.tasks.LLMClient.generate_reply",
            lambda self, text, context: "Resposta automática",
        )
        monkeypatch.setattr(
            "app.services.tasks.WhaticketClient.send_text",
            lambda self, number, body: "external",
        )
        monkeypatch.setattr(
            "app.services.tasks.get_or_create_conversation",
            lambda session, number: {"number": number},
        )
        monkeypatch.setattr(
            "app.services.tasks.update_conversation_context",
            lambda session, conversation, context: None,
        )

        def fail_log(*args, **kwargs):
            raise RuntimeError("db failure")

        monkeypatch.setattr("app.services.tasks.add_delivery_log", fail_log)

        with pytest.raises(RuntimeError):
            process_incoming_message("5511777666000", "Olá", "text", "cid-db")

        assert dummy_factory.session.rollback_called is True
        assert dummy_factory.session.closed is True
        assert dummy_factory.removed is True
