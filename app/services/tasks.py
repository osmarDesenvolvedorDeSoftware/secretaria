from __future__ import annotations

import time
import uuid

import structlog
from redis import Redis
from rq import Queue, Retry, get_current_job
from rq.job import Job

from app.metrics import (
    fallback_transfers_total,
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
from app.services.context_engine import ContextEngine, RuntimeContext
from app.services.llm import LLMClient
from app.services.persistence import (
    add_delivery_log,
    get_or_create_conversation,
    update_conversation_context,
)
from app.services.security import detect_prompt_injection, sanitize_for_log, sanitize_text
from app.services.whaticket import WhaticketClient, WhaticketError
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
        self.context_engine = ContextEngine(redis_client, session_factory)
        self.dead_letter_queue = dead_letter_queue or Queue(
            settings.dead_letter_queue_name,
            connection=redis_client,
        )

    def get_context(self, number: str) -> list[dict[str, str]]:
        return self.context_engine.get_history(number)

    def set_context(self, number: str, messages: list[dict[str, str]]) -> None:
        self.context_engine.save_history(number, messages)

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
        runtime_context: RuntimeContext | None = None
        history_messages: list[dict[str, str]] = []
        template_vars: dict[str, str] = {}
        context_messages_for_db: list[dict[str, str]] = []
        success = False
        delivery_status = "FAILED_TEMPORARY"
        error_detail = None
        try:
            sanitized = sanitize_text(body)
            runtime_context = service.context_engine.prepare_runtime_context(number, sanitized)
            history_messages = list(runtime_context.history)
            context_messages_for_db = list(history_messages)
            llm_context = service.context_engine.build_llm_context(runtime_context)
            template_vars = dict(runtime_context.template_vars)
            previous_subject = runtime_context.profile.get("last_subject") if runtime_context.profile else None
            default_subject = previous_subject or sanitized
            default_subject_phrase = f" Último assunto: {default_subject}." if default_subject else ""
            if not template_vars.get("ultimo_assunto"):
                template_vars["ultimo_assunto"] = default_subject_phrase
            if not template_vars.get("último_assunto"):
                template_vars["último_assunto"] = template_vars["ultimo_assunto"]
            template_vars["mensagem_usuario"] = sanitized
            user_message = sanitized
            selected_template = runtime_context.template_name or "default"
            if not service.context_engine.template_exists(selected_template):
                selected_template = "default"

            if detect_prompt_injection(sanitized):
                logger.warning("prompt_injection_detected")
                llm_prompt_injection_blocked_total.inc()
                template_vars["resposta"] = ""
                final_message = service.context_engine.render_template("fallback", template_vars)
                fallback_transfers_total.inc()
            elif not runtime_context.ai_enabled:
                template_vars["resposta"] = ""
                final_message = service.context_engine.render_template("ai_disabled", template_vars)
                fallback_transfers_total.inc()
            else:
                llm_start = time.time()
                response_text = ""
                try:
                    response_text = service.llm_client.generate_reply(
                        sanitized,
                        llm_context,
                    )
                    llm_latency.observe(time.time() - llm_start)
                except Exception as exc:  # pragma: no cover - ensures metrics capture
                    logger.exception("llm_failure", error=sanitize_for_log(str(exc)))
                    llm_errors.inc()
                    llm_latency.observe(time.time() - llm_start)
                    template_vars["resposta"] = ""
                    final_message = service.context_engine.render_template("technical_issue", template_vars)
                    fallback_transfers_total.inc()
                else:
                    template_vars["resposta"] = response_text
                    if response_text and response_text.strip():
                        final_message = service.context_engine.render_template(selected_template, template_vars)
                    else:
                        final_message = service.context_engine.render_template("fallback", template_vars)
                        fallback_transfers_total.inc()

            context_messages_for_db.append({"role": "user", "body": user_message})
            context_messages_for_db.append({"role": "assistant", "body": final_message})

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
                        updated_history = list(context_messages_for_db)
                        personalization = runtime_context.personalization if runtime_context else {}
                        if runtime_context is not None:
                            service.context_engine.record_history(
                                number,
                                history_messages,
                                user_message,
                                final_message,
                                personalization,
                            )
                            fetched_history = service.context_engine.get_history(number)
                            if fetched_history:
                                updated_history = fetched_history
                            preferences = dict(runtime_context.profile.get("preferences") or {})
                            preferences["ultimo_sentimento"] = runtime_context.sentiment
                            preferences["ultima_intencao"] = runtime_context.intention
                            runtime_context.profile["preferences"] = preferences
                            runtime_context.profile = service.context_engine.update_profile_snapshot(
                                number,
                                user_message,
                                runtime_context.profile,
                            )
                        update_conversation_context(session, conversation, updated_history)
                        session.flush()
                        logger.debug(
                            "conversation_context_persisted",
                            history_size=len(updated_history),
                        )
                        add_delivery_log(session, number, final_message, "SENT", external_id)
                        session.commit()
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
