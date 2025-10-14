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
from app.services.security import detect_prompt_injection, sanitize_for_log, sanitize_text
from app.services.whaticket import WhaticketClient, WhaticketError


CONTEXT_REDIS_KEY = "ctx:{number}"


class TaskService:
    def __init__(self, redis_client: Redis, session_factory, queue: Queue) -> None:
        self.redis = redis_client
        self.session_factory = session_factory
        self.queue = queue
        self.llm_client = LLMClient(redis_client)
        self.whaticket_client = WhaticketClient(redis_client)

    def enqueue(self, number: str, body: str, kind: str, correlation_id: str) -> None:
        delays = list(settings.rq_retry_delays)
        retry = Retry(max=len(delays), interval=delays) if delays else None
        enqueue_kwargs = {}
        if retry:
            enqueue_kwargs["retry"] = retry
        self.queue.enqueue(
            process_incoming_message,
            number,
            body,
            kind,
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


def process_incoming_message(number: str, body: str, kind: str, correlation_id: str) -> None:
    from flask import current_app

    logger = structlog.get_logger().bind(
        task="process_incoming_message",
        number=number,
        kind=kind,
    )
    start_time = time.time()

    redis_client: Redis = current_app.redis  # type: ignore[attr-defined]
    session_factory = current_app.db_session  # type: ignore[attr-defined]
    queue = current_app.task_queue  # type: ignore[attr-defined]
    service = TaskService(redis_client, session_factory, queue)

    with structlog.contextvars.bound_contextvars(correlation_id=correlation_id):
        try:
            sanitized = sanitize_text(body)
            if detect_prompt_injection(sanitized):
                logger.warning("prompt_injection_detected")
                final_message = "Desculpe, não posso executar esse tipo de comando."
                user_message = sanitized
                context_messages = service.get_context(number)
            else:
                context_messages = service.get_context(number)
                user_message = sanitized
                llm_start = time.time()
                try:
                    response = service.llm_client.generate_reply(
                        sanitized,
                        context_messages,
                    )
                    llm_latency.observe(time.time() - llm_start)
                    final_message = response
                except Exception as exc:  # pragma: no cover - ensures metrics capture
                    logger.exception("llm_failure", error=sanitize_for_log(str(exc)))
                    llm_errors.inc()
                    llm_latency.observe(time.time() - llm_start)
                    final_message = "Estou com dificuldades técnicas. " + settings.transfer_to_human_message

            whaticket_start = time.time()
            external_id = None
            success = False
            error_detail = None
            try:
                external_id = service.whaticket_client.send_text(number, final_message)
                whaticket_latency.observe(time.time() - whaticket_start)
                success = True
            except WhaticketError as exc:
                error_detail = sanitize_for_log(str(exc))
                logger.exception("whaticket_failure", error=error_detail)
                whaticket_errors.inc()
                whaticket_latency.observe(time.time() - whaticket_start)
                if exc.retryable:
                    queue.enqueue(
                        process_incoming_message,
                        number,
                        body,
                        kind,
                        correlation_id,
                    )
                raise
            except Exception as exc:
                error_detail = sanitize_for_log(str(exc))
                logger.exception("whaticket_failure_unexpected", error=error_detail)
                whaticket_errors.inc()
                whaticket_latency.observe(time.time() - whaticket_start)
                raise
            finally:
                session = session_factory()  # type: ignore[operator]
                try:
                    if success:
                        conversation = get_or_create_conversation(session, number)
                        context_messages.append({"role": "user", "body": user_message})
                        context_messages.append({"role": "assistant", "body": final_message})
                        update_conversation_context(session, conversation, context_messages)
                        add_delivery_log(session, number, final_message, "SENT", external_id)
                        session.commit()
                        service.set_context(number, context_messages)
                    else:
                        add_delivery_log(
                            session,
                            number,
                            final_message,
                            "FAILED",
                            external_id,
                            error_detail,
                        )
                        session.commit()
                except Exception:
                    session.rollback()
                    raise
                finally:
                    session.close()
                    session_factory.remove()
        finally:
            task_latency_histogram.observe(time.time() - start_time)
