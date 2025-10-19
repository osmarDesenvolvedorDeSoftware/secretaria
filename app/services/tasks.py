from __future__ import annotations

import json
import time
import uuid

import structlog
from redis import Redis
from rq import Queue, Retry, get_current_job
from rq.job import Job

from app.metrics import (
    llm_errors,
    llm_latency,
    llm_prompt_injection_blocked_total,
    task_latency_histogram,
    whaticket_errors,
    whaticket_latency,
    whaticket_send_retry_total,
    whaticket_send_success_total,
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
    def __init__(
        self,
        redis_client: Redis,
        session_factory,
        queue: Queue,
        dead_letter_queue: Queue | None = None,
    ) -> None:
        self.redis = redis_client
        self.session_factory = session_factory
        self.queue = queue
        self.llm_client = LLMClient(redis_client)
        self.whaticket_client = WhaticketClient(redis_client)
        self.dead_letter_queue = dead_letter_queue or Queue(
            settings.dead_letter_queue_name,
            connection=redis_client,
        )

    def enqueue(self, number: str, body: str, kind: str, correlation_id: str) -> None:
        delays = list(settings.rq_retry_delays)
        retry: Retry | None = None
        max_retries = max(settings.rq_retry_max_attempts, 0)
        if max_retries > 0:
            if delays:
                retry = Retry(max=max_retries, interval=delays)
            else:
                retry = Retry(max=max_retries)
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
            meta={
                "number": number,
                "body": body,
                "kind": kind,
                "correlation_id": correlation_id,
            },
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
        self.redis.setex(key, settings.context_ttl, json.dumps(truncated))

    def send_to_dead_letter(
        self,
        payload: dict[str, str],
        failure_reason: str | None = None,
        original_job_id: str | None = None,
        attempt: int | None = None,
    ) -> str:
        job = self.dead_letter_queue.enqueue(
            store_dead_letter_message,
            payload,
            failure_reason,
            meta={
                "payload": payload,
                "failure_reason": failure_reason,
                "original_job_id": original_job_id,
                "attempt": attempt,
            },
            job_timeout=settings.dead_letter_job_timeout,
            result_ttl=settings.dead_letter_result_ttl,
        )
        return getattr(job, "id", str(uuid.uuid4()))


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
    dead_letter_queue = getattr(current_app, "dead_letter_queue", None)
    service = TaskService(redis_client, session_factory, queue, dead_letter_queue)

    job = get_current_job()
    attempt = 1
    if job is not None:
        attempt = int(job.meta.get("attempt", 0)) + 1
        job.meta["attempt"] = attempt
        job.meta.setdefault(
            "payload",
            {
                "number": number,
                "body": body,
                "kind": kind,
                "correlation_id": correlation_id,
            },
        )
        job.save_meta()
        logger = logger.bind(job_id=job.id, attempt=attempt, retries_left=job.retries_left)
    max_attempts = max(len(settings.rq_retry_delays) + 1, settings.rq_retry_max_attempts + 1)

    with structlog.contextvars.bound_contextvars(correlation_id=correlation_id):
        success = False
        delivery_status = "FAILED_TEMPORARY"
        error_detail = None
        try:
            sanitized = sanitize_text(body)
            if detect_prompt_injection(sanitized):
                logger.warning("prompt_injection_detected")
                final_message = "Desculpe, não posso executar esse tipo de comando."
                user_message = sanitized
                context_messages = service.get_context(number)
                llm_prompt_injection_blocked_total.inc()
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
            try:
                external_id = service.whaticket_client.send_text(number, final_message)
                whaticket_latency.observe(time.time() - whaticket_start)
                success = True
                delivery_status = "SENT"
                whaticket_send_success_total.inc()
            except WhaticketError as exc:
                error_detail = sanitize_for_log(str(exc))
                logger.exception("whaticket_failure", error=error_detail)
                whaticket_errors.inc()
                whaticket_latency.observe(time.time() - whaticket_start)
                if exc.retryable:
                    whaticket_send_retry_total.inc()
                    if job is not None and job.retries_left == 0:
                        delivery_status = "FAILED_PERMANENT"
                    elif attempt >= max_attempts:
                        delivery_status = "FAILED_PERMANENT"
                    else:
                        delivery_status = "FAILED_TEMPORARY"
                else:
                    delivery_status = "FAILED_PERMANENT"
                raise
            except Exception as exc:
                error_detail = sanitize_for_log(str(exc))
                logger.exception("whaticket_failure_unexpected", error=error_detail)
                whaticket_errors.inc()
                whaticket_latency.observe(time.time() - whaticket_start)
                delivery_status = "FAILED_PERMANENT"
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
                            delivery_status,
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
            if not success and delivery_status == "FAILED_PERMANENT":
                if job is None or not job.meta.get("sent_to_dead_letter"):
                    payload = {
                        "number": number,
                        "body": body,
                        "kind": kind,
                        "correlation_id": correlation_id,
                    }
                    dead_letter_id = service.send_to_dead_letter(
                        payload,
                        error_detail,
                        getattr(job, "id", None) if job is not None else None,
                        attempt,
                    )
                    logger.warning("dead_letter_enqueued", dead_letter_job_id=dead_letter_id)
                    if job is not None:
                        job.meta["sent_to_dead_letter"] = True
                        job.save_meta()
        finally:
            task_latency_histogram.observe(time.time() - start_time)


def store_dead_letter_message(payload: dict[str, str], failure_reason: str | None = None) -> dict[str, str]:
    logger = structlog.get_logger().bind(task="store_dead_letter", **payload)
    logger.warning("dead_letter_recorded", failure_reason=failure_reason)
    return payload


def requeue_dead_letter_job(redis_client: Redis, job_id: str) -> bool:
    """Reenvia manualmente um job da fila de dead-letter para a fila principal."""

    try:
        job = Job.fetch(job_id, connection=redis_client)
    except Exception:
        return False

    if job.origin != settings.dead_letter_queue_name:
        return False

    payload: dict[str, str] | None = job.meta.get("payload") if job.meta else None
    if not payload and job.args:
        candidate = job.args[0]
        if isinstance(candidate, dict):
            payload = candidate

    if not payload:
        return False

    number = payload.get("number")
    body = payload.get("body")
    kind = payload.get("kind", "text")
    correlation_id = payload.get("correlation_id", str(uuid.uuid4()))

    if not number or body is None:
        return False

    queue = Queue(settings.queue_name, connection=redis_client)
    queue.enqueue(
        process_incoming_message,
        number,
        body,
        kind,
        correlation_id,
        job_timeout=settings.llm_timeout_seconds + settings.request_timeout_seconds,
        meta={
            "number": number,
            "body": body,
            "kind": kind,
            "correlation_id": correlation_id,
            "reprocessed_from_dead_letter": True,
        },
    )
    job.delete()
    return True
