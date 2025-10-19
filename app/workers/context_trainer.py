from __future__ import annotations

import argparse
import structlog
from flask import Flask
from redis import Redis
from sqlalchemy.orm import Session

from app import init_app
from app.metrics import context_learning_updates_total, context_volume_gauge
from app.models import Conversation
from app.services.context_engine import ContextEngine


class ContextTrainer:
    def __init__(self, app: Flask) -> None:
        self.app = app
        self.redis: Redis = app.redis  # type: ignore[attr-defined]
        self.session_factory = app.db_session  # type: ignore[attr-defined]
        self.logger = structlog.get_logger().bind(worker="context_trainer")
        self.context_engine = ContextEngine(self.redis, self.session_factory)

    def _session(self) -> Session:
        return self.session_factory()  # type: ignore[call-arg]

    def run(self, limit: int | None = None) -> None:
        session = self._session()
        try:
            query = session.query(Conversation.number).distinct().order_by(Conversation.number)
            if isinstance(limit, int) and limit > 0:
                query = query.limit(limit)
            numbers = [row[0] for row in query]
        finally:
            session.close()
            self.session_factory.remove()

        for number in numbers:
            if not number:
                continue
            self._process_number(number)

    def _process_number(self, number: str) -> None:
        session = self._session()
        try:
            conversation = (
                session.query(Conversation)
                .filter(Conversation.number == number)
                .order_by(Conversation.updated_at.desc().nullslast(), Conversation.id.desc())
                .first()
            )
        finally:
            session.close()
            self.session_factory.remove()

        if conversation is None:
            return

        messages = conversation.context_json or []
        profile = self.context_engine.retrain_profile(
            number,
            messages,
            conversation.user_name,
        )
        context_learning_updates_total.labels(number=number).inc()
        context_volume_gauge.labels(number=number).set(len(messages))
        self.logger.info(
            "context_profile_updated",
            number=number,
            topics=profile.get("frequent_topics", []),
            products=profile.get("product_mentions", []),
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Atualiza embeddings personalizados por cliente")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Número máximo de clientes para processar",
    )
    args = parser.parse_args()

    application = init_app()
    with application.app_context():
        trainer = ContextTrainer(application)
        trainer.run(limit=args.limit)
