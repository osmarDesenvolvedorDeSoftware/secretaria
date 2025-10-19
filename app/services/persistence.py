from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.models import Conversation, DeliveryLog


def get_or_create_conversation(session: Session, company_id: int, number: str) -> Conversation:
    conversation = (
        session.query(Conversation)
        .filter(Conversation.company_id == company_id, Conversation.number == number)
        .order_by(Conversation.id.desc())
        .first()
    )
    if conversation is None:
        conversation = Conversation(
            company_id=company_id,
            number=number,
            context_json=[],
        )
        session.add(conversation)
        session.flush()
    return conversation


def update_conversation_context(
    session: Session,
    conversation: Conversation,
    messages: Iterable[dict[str, Any]],
) -> None:
    conversation.context_json = list(messages)
    conversation.last_message = conversation.context_json[-1]["body"] if conversation.context_json else None


def add_delivery_log(
    session: Session,
    company_id: int,
    number: str,
    body: str,
    status: str,
    external_id: str | None = None,
    error: str | None = None,
) -> DeliveryLog:
    log = DeliveryLog(
        company_id=company_id,
        number=number,
        body=body,
        status=status,
        external_id=external_id,
        error=error,
    )
    session.add(log)
    session.flush()
    return log
