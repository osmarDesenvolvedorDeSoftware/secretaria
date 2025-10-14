from __future__ import annotations

from flask import current_app

from app.models import Conversation, DeliveryLog
from app.services.tasks import process_incoming_message
from app.services.llm import LLMClient
from app.services.whaticket import WhaticketClient


def test_process_incoming_message_success(app, monkeypatch):
    monkeypatch.setattr(LLMClient, "generate_reply", lambda self, text, context: "Resposta")
    monkeypatch.setattr(WhaticketClient, "send_message", lambda self, number, body: "external")

    with app.app_context():
        process_incoming_message("5511999999999", "Olá", "corr-1")
        session = current_app.db_session()  # type: ignore[attr-defined]
        try:
            conversations = session.query(Conversation).all()
            logs = session.query(DeliveryLog).all()
        finally:
            session.close()
        assert len(conversations) == 1
        assert conversations[0].last_message is not None
        assert len(logs) == 1
        assert logs[0].status == "SENT"
