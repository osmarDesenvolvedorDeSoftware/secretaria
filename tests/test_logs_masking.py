from __future__ import annotations

import pytest

from app.services.tasks import process_incoming_message
from app.services.whaticket import WhaticketError
from app.models import Conversation, DeliveryLog


def _clear_db(app):
    session = app.db_session()  # type: ignore[attr-defined]
    session.query(DeliveryLog).delete()
    session.query(Conversation).delete()
    session.commit()
    session.close()
    app.task_queue.enqueued.clear()  # type: ignore[attr-defined]


def test_logs_are_sanitized(app, monkeypatch, caplog):
    with app.app_context():
        _clear_db(app)

        monkeypatch.setattr(
            "app.services.tasks.LLMClient.generate_reply",
            lambda self, text, context: "Resposta",
        )

        def fail_send(*_):
            raise WhaticketError(
                "Authorization: Bearer secret-token apiKey=abc123",
                retryable=False,
            )

        monkeypatch.setattr("app.services.tasks.WhaticketClient.send_text", fail_send)

        with caplog.at_level("ERROR"):
            with pytest.raises(WhaticketError):
                process_incoming_message(1, "5511999999999", "Olá", "text", "corr")

        log_output = " ".join(record.getMessage() for record in caplog.records)
        assert "secret-token" not in log_output
        assert "abc123" not in log_output
        assert "***" in log_output
