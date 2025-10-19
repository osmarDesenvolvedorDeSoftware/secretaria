from __future__ import annotations

import re
import time
import uuid
from datetime import datetime, timedelta

import structlog
from redis import Redis
from rq import Queue, Retry, get_current_job
from rq.job import Job

from app.metrics import (
    fallback_transfers_total,
    llm_prompt_injection_blocked_total,
    message_usage_total,
    task_latency_histogram,
    token_usage_total,
    whaticket_errors,
    whaticket_latency,
    whaticket_send_retry_total,
    whaticket_send_success_total,
    whaticket_delivery_success_ratio,
)
from app.config import settings
from app.services.abtest_service import ABTestService
from app.services.analytics_service import AnalyticsService
from app.services.billing import BillingService
from app.services.context_engine import ContextEngine, RuntimeContext
from app.services import cal_service
from app.services.llm import LLMClient
from app.services.persistence import (
    add_delivery_log,
    get_or_create_conversation,
    update_conversation_context,
)
from app.services.security import detect_prompt_injection, sanitize_for_log, sanitize_text
from app.services.tenancy import TenantContext, queue_name_for_company
from app.services.whaticket import WhaticketClient, WhaticketError
from app.models import Company


APPOINTMENT_CONFIRMATION_WORDS = {
    "sim",
    "confirmo",
    "pode ser",
    "perfeito",
    "fechado",
    "vamos",
}
APPOINTMENT_NEGATIVE_WORDS = {"nao", "não", "outro", "depois", "cancelar"}


def _parse_iso_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        try:
            return datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return datetime.utcnow()


def _format_slot_label(start: datetime) -> str:
    try:
        localized = start.astimezone()
    except ValueError:
        localized = start
    return localized.strftime("%d/%m às %Hh%M")


def _humanize_start(start: datetime) -> str:
    try:
        localized = start.astimezone()
    except ValueError:
        localized = start
    today = datetime.utcnow().date()
    local_date = localized.date()
    if local_date == today:
        prefix = "hoje"
    elif local_date == today + timedelta(days=1):
        prefix = "amanhã"
    else:
        prefix = localized.strftime("%d/%m")
    return f"{prefix} às {localized.strftime('%Hh%M')}"


def _build_options_message(options: list[dict[str, str]]) -> str:
    lines = ["Encontrei estes horários disponíveis:"]
    for index, option in enumerate(options, start=1):
        start_dt = _parse_iso_datetime(option["start"])
        lines.append(f"{index}. {_format_slot_label(start_dt)}")
    lines.append("Responda com o número da opção desejada ou indique outro horário.")
    return "\n".join(lines)


def _select_agenda_option(message: str, options: list[dict[str, str]]) -> dict[str, str] | None:
    if not options:
        return None
    digits = re.findall(r"\d+", message)
    if digits:
        try:
            index = int(digits[0])
        except ValueError:
            index = -1
        if 1 <= index <= len(options):
            return options[index - 1]
    for option in options:
        label = option.get("label", "").lower()
        if label and label in message:
            return option
        start = option.get("start", "").lower()
        if start and start[:16] in message:
            return option
    if len(options) == 1 and any(word in message for word in APPOINTMENT_CONFIRMATION_WORDS):
        return options[0]
    return None


def _handle_agenda_flow(
    service: "TaskService",
    runtime_context: RuntimeContext,
    number: str,
    sanitized_message: str,
    template_vars: dict[str, str],
    session_factory,
):
    logger = structlog.get_logger().bind(task="agenda_flow", company_id=service.company_id, number=number)
    state = service.context_engine.get_agenda_state(number)
    if state:
        options = state.get("options") or []
        option = _select_agenda_option(sanitized_message, options)
        if option:
            cliente_nome = template_vars.get("nome") or runtime_context.profile.get("preferences", {}).get("nome") or "Cliente"
            cliente = {"name": cliente_nome, "phone": number}
            titulo = state.get("title") or f"Reunião com {cliente_nome}"
            duracao = int(option.get("duration") or 30)
            horario = {"start": option.get("start"), "end": option.get("end")}
            try:
                result = cal_service.criar_agendamento(
                    service.company_id,
                    cliente,
                    horario,
                    titulo,
                    duracao,
                )
            except cal_service.CalServiceError as exc:
                logger.warning("agenda_confirm_error", error=str(exc))
                service.context_engine.clear_agenda_state(number)
                return "Encontrei um problema ao confirmar o agendamento. Posso tentar novamente com outros horários?"

            service.context_engine.clear_agenda_state(number)
            start_dt = _parse_iso_datetime(option.get("start"))
            human_time = _humanize_start(start_dt)
            meeting_url = result.get("meeting_url") or "https://cal.com"
            return f"Reunião confirmada para {human_time}! Aqui está o link: {meeting_url}"

        if any(word in sanitized_message for word in APPOINTMENT_NEGATIVE_WORDS):
            service.context_engine.clear_agenda_state(number)
            return "Sem problemas! Me diga qual período prefere que eu verifico novos horários."

        return "Por favor, responda com o número da opção desejada ou informe outro horário que eu verifico para você."

    if runtime_context.intention != "appointment_request":
        return None

    session = session_factory()
    try:
        company = session.get(Company, service.company_id)
    finally:
        session.close()

    if company is None or not company.cal_default_user_id:
        logger.warning("agenda_missing_user", company_id=service.company_id)
        return "Ainda não tenho acesso à agenda automática desta empresa. Posso encaminhar para um atendente humano?"

    start_range = datetime.utcnow()
    end_range = start_range + timedelta(days=settings.cal_default_days_ahead)
    try:
        availability = cal_service.listar_disponibilidade(
            company.cal_default_user_id,
            start_range.isoformat(),
            end_range.isoformat(),
            company_id=service.company_id,
        )
    except cal_service.CalServiceConfigError:
        logger.warning("agenda_api_key_missing")
        return "A agenda automática ainda não está configurada. Posso agendar manualmente para você?"
    except cal_service.CalServiceError as exc:
        logger.warning("agenda_availability_error", error=str(exc))
        return "Não consegui acessar a agenda agora. Posso tentar novamente mais tarde?"

    if not availability:
        return "No momento não encontrei horários disponíveis. Informe uma sugestão e verifico com a equipe."

    prepared_options: list[dict[str, str]] = []
    for slot in availability[:3]:
        start_value = slot.get("start") or slot.get("startTime")
        end_value = slot.get("end") or slot.get("endTime")
        start_dt = _parse_iso_datetime(start_value)
        end_dt = _parse_iso_datetime(end_value) if end_value else start_dt + timedelta(minutes=int(slot.get("duration") or 30))
        duration = int(slot.get("duration") or max(int((end_dt - start_dt).total_seconds() // 60), 15))
        prepared_options.append(
            {
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
                "duration": duration,
                "label": _format_slot_label(start_dt).lower(),
            }
        )

    cliente_nome = template_vars.get("nome") or runtime_context.profile.get("preferences", {}).get("nome") or "Cliente"
    title = f"Reunião com {cliente_nome}"
    service.context_engine.set_agenda_state(
        number,
        {
            "phase": "awaiting_confirmation",
            "options": prepared_options,
            "title": title,
        },
        ttl=settings.context_ttl,
    )
    return _build_options_message(prepared_options)
class TaskService:
    def __init__(
        self,
        redis_client: Redis,
        session_factory,
        tenant: TenantContext,
        queue: Queue,
        dead_letter_queue: Queue | None = None,
        billing_service: BillingService | None = None,
        analytics_service: AnalyticsService | None = None,
    ) -> None:
        self.redis = redis_client
        self.session_factory = session_factory
        self.tenant = tenant
        self.company_id = tenant.company_id
        self.company_label = tenant.label
        self.queue = queue
        self.llm_client = LLMClient(redis_client, tenant)
        self.whaticket_client = WhaticketClient(redis_client, tenant)
        self.context_engine = ContextEngine(redis_client, session_factory, tenant)
        self.abtest_service = ABTestService(session_factory, redis_client)
        self.dead_letter_queue = dead_letter_queue or Queue(
            queue_name_for_company(settings.dead_letter_queue_name, tenant.company_id),
            connection=redis_client,
        )
        self.analytics_service = analytics_service
        if billing_service is None:
            billing_service = BillingService(
                session_factory,
                redis_client,
                analytics_service,
            )
        else:
            if analytics_service is None and getattr(billing_service, "analytics_service", None) is not None:
                analytics_service = billing_service.analytics_service  # type: ignore[attr-defined]
            elif analytics_service is not None:
                billing_service.attach_analytics_service(analytics_service)
        self.analytics_service = analytics_service or getattr(billing_service, "analytics_service", None)
        self.billing_service = billing_service

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
            self.company_id,
            number,
            body,
            kind,
            correlation_id,
            job_timeout=settings.llm_timeout_seconds + settings.request_timeout_seconds,
            meta={
                "company_id": self.company_id,
                "number": number,
                "body": body,
                "kind": kind,
                "correlation_id": correlation_id,
            },
            **enqueue_kwargs,
        )

    def send_to_dead_letter(
        self,
        payload: dict[str, object],
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

    def _update_delivery_ratio(self, success: bool) -> None:
        key = self.tenant.namespaced_key("metrics", "delivery", "whaticket")
        try:
            field = "success" if success else "failure"
            self.redis.hincrby(key, field, 1)
            data = self.redis.hgetall(key) or {}
            success_count = int(data.get("success", 0) or 0)
            failure_count = int(data.get("failure", 0) or 0)
            total = success_count + failure_count
            if total > 0:
                ratio = success_count / total
                whaticket_delivery_success_ratio.labels(company=self.company_label).set(ratio)
        except Exception:
            pass


def process_incoming_message(
    company_id: int,
    number: str,
    body: str,
    kind: str,
    correlation_id: str,
) -> None:
    from flask import current_app

    logger = structlog.get_logger().bind(
        task="process_incoming_message",
        company_id=company_id,
        number=number,
        kind=kind,
    )
    start_time = time.time()

    redis_client: Redis = current_app.redis  # type: ignore[attr-defined]
    session_factory = current_app.db_session  # type: ignore[attr-defined]
    tenant = TenantContext(company_id=company_id, label=str(company_id))
    queue = current_app.get_task_queue(company_id)  # type: ignore[attr-defined]
    dead_letter_queue = current_app.get_dead_letter_queue(company_id)  # type: ignore[attr-defined]
    analytics_service = getattr(current_app, "analytics_service", None)
    billing_service = getattr(current_app, "billing_service", None)
    if billing_service is None:
        billing_service = BillingService(
            session_factory,
            redis_client,
            analytics_service,
        )
        current_app.billing_service = billing_service  # type: ignore[attr-defined]
    service = TaskService(
        redis_client,
        session_factory,
        tenant,
        queue,
        dead_letter_queue,
        billing_service=billing_service,
        analytics_service=analytics_service,
    )

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
            message_usage_total.labels(
                company=service.company_label,
                kind=kind or "unknown",
            ).inc()
            inbound_tokens = 0
            if sanitized:
                inbound_tokens = max(len(sanitized.split()), 1)
                token_usage_total.labels(
                    company=service.company_label,
                    direction="inbound",
                ).inc(inbound_tokens)
            service.billing_service.record_usage(
                service.company_id,
                inbound_messages=1,
                inbound_tokens=inbound_tokens,
            )
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
            ab_selection = None
            try:
                ab_selection = service.abtest_service.select_variant(
                    service.company_id,
                    selected_template,
                )
            except Exception:
                logger.debug("abtest_selection_failed", template=selected_template)
                ab_selection = None
            if ab_selection is not None:
                selected_template = ab_selection.template_name or selected_template
            if not service.context_engine.template_exists(selected_template):
                selected_template = "default"

            final_message = ""
            agenda_message = _handle_agenda_flow(
                service,
                runtime_context,
                number,
                sanitized.lower(),
                template_vars,
                session_factory,
            )
            agenda_override = isinstance(agenda_message, str)
            if agenda_override:
                final_message = str(agenda_message)
                template_vars["resposta"] = final_message
            elif detect_prompt_injection(sanitized):
                logger.warning("prompt_injection_detected")
                llm_prompt_injection_blocked_total.labels(
                    company=service.company_label
                ).inc()
                template_vars["resposta"] = ""
                final_message = service.context_engine.render_template("fallback", template_vars)
                fallback_transfers_total.labels(company=service.company_label).inc()
            elif not runtime_context.ai_enabled:
                template_vars["resposta"] = ""
                final_message = service.context_engine.render_template("ai_disabled", template_vars)
                fallback_transfers_total.labels(company=service.company_label).inc()
            else:
                response_text = ""
                try:
                    response_text = service.llm_client.generate_reply(
                        sanitized,
                        llm_context,
                    )
                except Exception as exc:  # pragma: no cover - ensures metrics capture
                    logger.exception("llm_failure", error=sanitize_for_log(str(exc)))
                    template_vars["resposta"] = ""
                    final_message = service.context_engine.render_template("technical_issue", template_vars)
                    fallback_transfers_total.labels(company=service.company_label).inc()
                else:
                    template_vars["resposta"] = response_text
                    if response_text and response_text.strip():
                        final_message = service.context_engine.render_template(selected_template, template_vars)
                    else:
                        final_message = service.context_engine.render_template("fallback", template_vars)
                        fallback_transfers_total.labels(company=service.company_label).inc()

            context_messages_for_db.append({"role": "user", "body": user_message})
            context_messages_for_db.append({"role": "assistant", "body": final_message})
            response_time_for_analytics: float | None = None
            outbound_tokens = 0
            if final_message:
                message_usage_total.labels(
                    company=service.company_label,
                    kind="assistant",
                ).inc()
                outbound_tokens = max(len(final_message.split()), 1)
                token_usage_total.labels(
                    company=service.company_label,
                    direction="outbound",
                ).inc(outbound_tokens)
                response_time_for_analytics = time.time() - start_time
                service.billing_service.record_usage(
                    service.company_id,
                    outbound_messages=1,
                    outbound_tokens=outbound_tokens,
                    response_time=response_time_for_analytics,
                )
                if ab_selection is not None:
                    try:
                        service.abtest_service.record_event(
                            service.company_id,
                            ab_selection.ab_test_id,
                            ab_selection.variant,
                            "response",
                            response_time=response_time_for_analytics,
                        )
                    except Exception:
                        logger.debug(
                            "abtest_event_record_failed",
                            test_id=ab_selection.ab_test_id,
                            variant=ab_selection.variant,
                        )

            whaticket_start = time.time()
            external_id = None
            try:
                external_id = service.whaticket_client.send_text(number, final_message)
                whaticket_latency.labels(company=service.company_label).observe(
                    time.time() - whaticket_start
                )
                success = True
                delivery_status = "SENT"
                whaticket_send_success_total.labels(company=service.company_label).inc()
                service._update_delivery_ratio(True)
            except WhaticketError as exc:
                error_detail = sanitize_for_log(str(exc))
                logger.exception("whaticket_failure", error=error_detail)
                whaticket_errors.labels(company=service.company_label).inc()
                whaticket_latency.labels(company=service.company_label).observe(
                    time.time() - whaticket_start
                )
                service._update_delivery_ratio(False)
                if exc.retryable:
                    whaticket_send_retry_total.labels(company=service.company_label).inc()
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
                whaticket_errors.labels(company=service.company_label).inc()
                whaticket_latency.labels(company=service.company_label).observe(
                    time.time() - whaticket_start
                )
                delivery_status = "FAILED_PERMANENT"
                service._update_delivery_ratio(False)
                raise
            finally:
                session = session_factory()  # type: ignore[operator]
                try:
                    if success:
                        conversation = get_or_create_conversation(
                            session,
                            service.company_id,
                            number,
                        )
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
                        add_delivery_log(
                            session,
                            service.company_id,
                            number,
                            final_message,
                            "SENT",
                            external_id,
                        )
                        session.commit()
                    else:
                        add_delivery_log(
                            session,
                            service.company_id,
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
                        "company_id": service.company_id,
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
            task_latency_histogram.labels(company=service.company_label).observe(
                time.time() - start_time
            )


def store_dead_letter_message(
    payload: dict[str, object], failure_reason: str | None = None
) -> dict[str, object]:
    company_id = payload.get("company_id")
    logger = structlog.get_logger().bind(
        task="store_dead_letter",
        company_id=company_id,
        **{k: v for k, v in payload.items() if k != "company_id"},
    )
    logger.warning("dead_letter_recorded", failure_reason=failure_reason)
    return payload


def requeue_dead_letter_job(redis_client: Redis, job_id: str) -> bool:
    """Reenvia manualmente um job da fila de dead-letter para a fila principal."""

    try:
        job = Job.fetch(job_id, connection=redis_client)
    except Exception:
        return False

    payload: dict[str, object] | None = job.meta.get("payload") if job.meta else None
    if not payload and job.args:
        candidate = job.args[0]
        if isinstance(candidate, dict):
            payload = candidate

    if not payload:
        return False

    company_id = payload.get("company_id")
    if not company_id:
        return False

    expected_origin = queue_name_for_company(settings.dead_letter_queue_name, int(company_id))
    if job.origin != expected_origin:
        return False

    number = payload.get("number")
    body = payload.get("body")
    kind = payload.get("kind", "text")
    correlation_id = payload.get("correlation_id", str(uuid.uuid4()))

    if not number or body is None:
        return False

    queue = Queue(
        queue_name_for_company(settings.queue_name, int(company_id)),
        connection=redis_client,
    )
    queue.enqueue(
        process_incoming_message,
        int(company_id),
        number,
        body,
        kind,
        correlation_id,
        job_timeout=settings.llm_timeout_seconds + settings.request_timeout_seconds,
        meta={
            "company_id": int(company_id),
            "number": number,
            "body": body,
            "kind": kind,
            "correlation_id": correlation_id,
            "reprocessed_from_dead_letter": True,
        },
    )
    job.delete()
    return True
