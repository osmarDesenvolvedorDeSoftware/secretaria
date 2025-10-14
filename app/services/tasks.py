from __future__ import annotations

import json
import time

import structlog
from redis import Redis
from rq import Queue, Retry

from app.metrics import (
    llm_errors,
    llm_latency,
    task_latency_histogram,
    whaticket_errors,
    whaticket_latency,
)
from app.config import settings
from app.services.llm import LLMClient
from app.services.persistence import (
    add_delivery_log,
    get_or_create_conversation,
    update_conversation_context,
)
from app.services.security import detect_prompt_injection, sanitize_text
from app.services.whaticket import WhaticketClient


CONTEXT_REDIS_KEY = "ctx:{number}"


class TaskService:
    def __init__(self, redis_client: Redis, session_factory, queue: Queue) -> None:
        self.redis = redis_client
        self.session_factory = session_factory
        self.queue = queue
        self.llm_client = LLMClient(redis_client)
        self.whaticket_client = WhaticketClient(redis_client)

    def enqueue(self, number: str, body: str, correlation_id: str) -> None:
        delays = list(settings.rq_retry_delays)
        retry = Retry(max=len(delays), interval=delays) if delays else None
        enqueue_kwargs = {}
        if retry:
            enqueue_kwargs["retry"] = retry
        self.queue.enqueue(
            process_incoming_message,
            number,
            body,
            correlation_id,
            job_timeout=settings.llm_timeout_seconds + settings.request_timeout_seconds,
            **enqueue_kwargs,
        )

    def get_context(self, number: str) -> list[dict[str, str]]:
        key = CONTEXT_REDIS_KEY.format(number=number)
        data = self.redis.get(key)
        if not data:
            return []
        try:
            payload = json.loads(data)
            if isinstance(payload, list):
                return payload
        except json.JSONDecodeError:
            return []
        return []

    def set_context(self, number: str, messages: list[dict[str, str]]) -> None:
        key = CONTEXT_REDIS_KEY.format(number=number)
        truncated = messages[-settings.context_max_messages :]
        self.redis.setex(key, settings.context_ttl_seconds, json.dumps(truncated))


def process_incoming_message(number: str, body: str, correlation_id: str) -> None:
    from flask import current_app

    logger = structlog.get_logger().bind(task="process_incoming_message")
    start_time = time.time()

    redis_client: Redis = current_app.redis  # type: ignore[attr-defined]
    session_factory = current_app.db_session  # type: ignore[attr-defined]
    queue = current_app.task_queue  # type: ignore[attr-defined]
    service = TaskService(redis_client, session_factory, queue)

    with structlog.contextvars.bound_contextvars(correlation_id=correlation_id):
        try:
            sanitized = sanitize_text(body)
            if detect_prompt_injection(sanitized):
                logger.warning("prompt_injection_detected", number=number)
                sanitized = "No momento, não posso responder a isso."

            context_messages = service.get_context(number)
            llm_start = time.time()
            try:
                response = service.llm_client.generate_reply(
                    sanitized,
                    context_messages,
                )
                llm_latency.observe(time.time() - llm_start)
            except Exception as exc:  # pragma: no cover - ensures metrics capture
                logger.exception("llm_failure", number=number)
                llm_errors.inc()
                llm_latency.observe(time.time() - llm_start)
                response = "Estou com dificuldades técnicas. " + settings.transfer_to_human_message

            final_message = response

            whaticket_start = time.time()
            success = True
            external_id = None
            try:
                external_id = service.whaticket_client.send_message(number, final_message)
                whaticket_latency.observe(time.time() - whaticket_start)
            except Exception as exc:
                logger.exception("whaticket_failure", number=number)
                whaticket_errors.inc()
                whaticket_latency.observe(time.time() - whaticket_start)
                success = False
                raise
            finally:
                session = session_factory()  # type: ignore[operator]
                try:
                    conversation = get_or_create_conversation(session, number)
                    context_messages.append({"role": "user", "body": sanitized})
                    context_messages.append({"role": "assistant", "body": final_message})
                    update_conversation_context(session, conversation, context_messages)
                    status = "SENT" if success else "FAILED"
                    add_delivery_log(session, number, final_message, status, external_id)
                    session.commit()
                except Exception:
                    session.rollback()
                    raise
                finally:
                    session.close()
                    session_factory.remove()
                service.set_context(number, context_messages)
        finally:
            task_latency_histogram.observe(time.time() - start_time)
