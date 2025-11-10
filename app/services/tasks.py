from __future__ import annotations

import inspect
import re
import time
import uuid
from datetime import datetime, timedelta

import structlog
from redis import Redis
from rq import Queue, Retry, get_current_job
from rq.job import Job

from app.metrics import (
    appointment_confirmations_total,
    appointment_reschedules_total,
    appointments_risk_high_total,
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
from app.services import cal_service, followup_service, scheduling_ai
from app.services.llm import LLMClient
from app.services.audit import AuditService
from app.services.persistence import (
    add_delivery_log,
    get_or_create_conversation,
    update_conversation_context,
)
from app.services.security import detect_prompt_injection, sanitize_for_log, sanitize_text
from app.services.tenancy import TenantContext, queue_name_for_company
from app.services.whaticket import WhaticketClient, WhaticketError
from app.models import Appointment, Company


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
    intention = runtime_context.intention or ""

    def _get_upcoming() -> Appointment | None:
        session = session_factory()
        try:
            if not hasattr(session, "query"):
                return None
            return (
                session.query(Appointment)
                .filter(
                    Appointment.company_id == service.company_id,
                    Appointment.client_phone == number,
                    Appointment.start_time >= datetime.utcnow() - timedelta(hours=1),
                    Appointment.status.in_(["pending", "confirmed"]),
                )
                .order_by(Appointment.start_time.asc())
                .first()
            )
        finally:
            session.close()

    def _load_company() -> Company | None:
        session = session_factory()
        try:
            getter = getattr(session, "get", None)
            if getter is None:
                return None
            return getter(Company, service.company_id)
        finally:
            session.close()

    def _get_latest_followup() -> Appointment | None:
        session = session_factory()
        try:
            if not hasattr(session, "query"):
                return None
            return (
                session.query(Appointment)
                .filter(
                    Appointment.company_id == service.company_id,
                    Appointment.client_phone == number,
                    Appointment.followup_sent_at.isnot(None),
                )
                .order_by(Appointment.followup_sent_at.desc())
                .first()
            )
        finally:
            session.close()

    def _prepare_options(
        cliente_nome: str,
        title: str,
        phase: str,
        state_extra: dict[str, object] | None = None,
        intro: str | None = None,
    ) -> str:
        company = _load_company()
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
            end_dt = (
                _parse_iso_datetime(end_value)
                if end_value
                else start_dt + timedelta(minutes=int(slot.get("duration") or 30))
            )
            duration = int(slot.get("duration") or max(int((end_dt - start_dt).total_seconds() // 60), 15))
            prepared_options.append(
                {
                    "start": start_dt.isoformat(),
                    "end": end_dt.isoformat(),
                    "duration": duration,
                    "label": _format_slot_label(start_dt).lower(),
                }
            )

        if state_extra:
            preferred_slot = state_extra.get("preferred_slot") if isinstance(state_extra, dict) else None
            if isinstance(preferred_slot, dict):
                preferred_weekday = preferred_slot.get("weekday")
                preferred_hour = preferred_slot.get("hour")
                for index, option in enumerate(list(prepared_options)):
                    start_dt = _parse_iso_datetime(option.get("start"))
                    if (
                        start_dt
                        and preferred_weekday is not None
                        and preferred_hour is not None
                        and start_dt.weekday() == preferred_weekday
                        and start_dt.hour == preferred_hour
                    ):
                        preferred_option = prepared_options.pop(index)
                        prepared_options.insert(0, preferred_option)
                        break

        state_payload: dict[str, object] = {
            "phase": phase,
            "options": prepared_options,
            "title": title,
            "client_name": cliente_nome,
        }
        if state_extra:
            state_payload.update(state_extra)
        service.context_engine.set_agenda_state(
            number,
            state_payload,
            ttl=settings.context_ttl,
        )
        options_message = _build_options_message(prepared_options)
        if intro:
            return f"{intro}\n\n{options_message}"
        return options_message

    if intention in {"followup_positive", "followup_negative", "followup_feedback"}:
        recent_followup = _get_latest_followup()
        if intention == "followup_positive":
            if recent_followup is not None:
                followup_service.registrar_resposta(recent_followup.id, "positive")
                cliente_nome = recent_followup.client_name or template_vars.get("nome") or "Cliente"
                title = recent_followup.title or f"Reunião com {cliente_nome}"
                intro = "Que ótimo! Veja abaixo algumas sugestões para o próximo encontro."
                return _prepare_options(
                    cliente_nome,
                    title,
                    "awaiting_followup_booking",
                    {"previous_appointment_id": recent_followup.id, "origin": "followup"},
                    intro,
                )
            return "Perfeito! Vamos iniciar um novo agendamento. Qual período prefere?"
        if intention == "followup_negative":
            if recent_followup is not None:
                followup_service.registrar_resposta(recent_followup.id, "negative")
            service.context_engine.clear_agenda_state(number)
            return "Sem problemas! Ficamos à disposição quando quiser retomar."
        if intention == "followup_feedback":
            if recent_followup is not None:
                followup_service.registrar_resposta(
                    recent_followup.id,
                    "feedback",
                    feedback_text=sanitized_message,
                )
            service.context_engine.clear_agenda_state(number)
            return "Agradeço por compartilhar seu feedback! Vamos repassar à equipe."

    if state:
        phase = state.get("phase") or "awaiting_confirmation"
        options = state.get("options") or []
        option = _select_agenda_option(sanitized_message, options)
        if option:
            cliente_nome = (
                state.get("client_name")
                or template_vars.get("nome")
                or runtime_context.profile.get("preferences", {}).get("nome")
                or "Cliente"
            )
            cliente = {"name": cliente_nome, "phone": number}
            titulo = state.get("title") or f"Reunião com {cliente_nome}"
            duracao = int(option.get("duration") or 30)
            horario = {"start": option.get("start"), "end": option.get("end")}
            reschedule_mode = phase == "awaiting_reschedule"
            try:
                result = cal_service.criar_agendamento(
                    service.company_id,
                    cliente,
                    horario,
                    titulo,
                    duracao,
                    reschedule=reschedule_mode,
                    original_appointment_id=state.get("original_appointment_id"),
                )
            except cal_service.CalServiceError as exc:
                logger.warning("agenda_confirm_error", error=str(exc))
                service.context_engine.clear_agenda_state(number)
                return "Encontrei um problema ao confirmar o agendamento. Posso tentar novamente com outros horários?"

            service.context_engine.clear_agenda_state(number)
            start_dt = _parse_iso_datetime(option.get("start"))
            try:
                localized = start_dt.astimezone()
            except ValueError:
                localized = start_dt
            meeting_url = result.get("meeting_url") or "https://cal.com"
            if reschedule_mode:
                appointment_reschedules_total.labels(company=service.company_label).inc()
                date_label = localized.strftime("%d/%m")
                time_label = localized.strftime("%Hh%M")
                message = f"Tudo certo, reagendamos sua reunião para {date_label} às {time_label} ✅"
                if meeting_url:
                    message += f"\nNovo link: {meeting_url}"
                return message

            human_time = _humanize_start(start_dt)
            return f"Reunião agendada para {human_time}! Aqui está o link: {meeting_url}"

        if any(word in sanitized_message for word in APPOINTMENT_NEGATIVE_WORDS):
            service.context_engine.clear_agenda_state(number)
            return "Sem problemas! Me diga qual período prefere que eu verifico novos horários."

            return "Por favor, responda com o número da opção desejada ou informe outro horário que eu verifico para você."

    upcoming = _get_upcoming()

    if (
        upcoming
        and (not state or not state.get("risk_flagged"))
        and intention not in {"appointment_confirmation", "appointment_reschedule"}
        and (upcoming.status or "").lower() != "confirmed"
    ):
        probability = scheduling_ai.prever_no_show(upcoming)
        if probability >= scheduling_ai.HIGH_RISK_THRESHOLD:
            suggestions = scheduling_ai.sugerir_horarios_otimizados(service.company_id)
            best = suggestions[0] if suggestions else None
            human_current = _humanize_start(upcoming.start_time)
            if best:
                intro = (
                    f"Percebi que {human_current} costuma ter mais faltas. "
                    f"Recomendo um horário como {best['label']}. Veja estas opções:"
                )
                preferred_slot = {"weekday": best["weekday"], "hour": best["hour"]}
            else:
                intro = (
                    f"Percebi que {human_current} tem risco alto de falta. Vamos escolher outro horário?"
                )
                preferred_slot = None
            state_extra: dict[str, object] = {
                "risk_flagged": True,
                "risk_probability": probability,
                "original_appointment_id": upcoming.id,
            }
            if preferred_slot is not None:
                state_extra["preferred_slot"] = preferred_slot
            appointments_risk_high_total.labels(company=service.company_label).inc()
            reschedule_message = _prepare_options(
                upcoming.client_name or template_vars.get("nome") or "Cliente",
                upcoming.title or f"Reunião com {upcoming.client_name or 'Cliente'}",
                "awaiting_reschedule",
                state_extra,
                intro,
            )
            if isinstance(reschedule_message, str):
                return reschedule_message

    if intention == "appointment_confirmation":
        if upcoming is None:
            return "Não encontrei um agendamento futuro para confirmar. Posso ajudar com algo mais?"
        recorded = False
        if upcoming.status != "confirmed" or not getattr(upcoming, "confirmed_at", None):
            session = session_factory()
            try:
                appointment = session.get(Appointment, upcoming.id)
                if appointment is not None:
                    appointment.status = "confirmed"
                    appointment.confirmed_at = datetime.utcnow()
                    session.add(appointment)
                    session.commit()
                    upcoming.status = "confirmed"
                    upcoming.confirmed_at = appointment.confirmed_at
                    recorded = True
            finally:
                session.close()
        if recorded:
            appointment_confirmations_total.labels(company=service.company_label).inc()
            try:
                AuditService(session_factory).record(
                    company_id=service.company_id,
                    actor="agenda",
                    action="appointment.confirmed",
                    resource="appointment",
                    payload={"appointment_id": upcoming.id},
                )
            except Exception:
                logger.warning("audit_confirmation_failed", appointment_id=upcoming.id)
        try:
            localized = upcoming.start_time.astimezone()
        except ValueError:
            localized = upcoming.start_time
        date_label = localized.strftime("%d/%m")
        time_label = localized.strftime("%Hh%M")
        service.context_engine.clear_agenda_state(number)
        return f"Perfeito! Sua presença está confirmada para {date_label} às {time_label}. Até lá!"

    if intention == "appointment_reschedule":
        if upcoming is None:
            return "Não encontrei um agendamento ativo para reagendar. Deseja iniciar um novo agendamento?"
        cliente_nome = upcoming.client_name or template_vars.get("nome") or "Cliente"
        title = upcoming.title or f"Reunião com {cliente_nome}"
        intro = "Claro! Estes horários estão disponíveis para reagendarmos."
        return _prepare_options(
            cliente_nome,
            title,
            "awaiting_reschedule",
            {"original_appointment_id": upcoming.id},
            intro,
        )

    if intention != "appointment_request":
        return None

    cliente_nome = (
        template_vars.get("nome")
        or runtime_context.profile.get("preferences", {}).get("nome")
        or "Cliente"
    )
    title = f"Reunião com {cliente_nome}"
    return _prepare_options(cliente_nome, title, "awaiting_confirmation")
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
    # LOG ADICIONADO: Confirma que o job RQ começou a ser executado
    logger.info("TASK_START", correlation_id=correlation_id)

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
        llm_status = "not_started"
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
                llm_status = "agenda_override"
            else:
                # Tenta construir contexto RAG para perfil/projetos
                from app.services.chatbot_profile import build_rag_context

                rag_context = build_rag_context(user_message, session_factory, service.company_id)

                if rag_context:
                    # Intenção de perfil/projeto detectada, usa RAG
                    llm_context = service.context_engine.build_llm_context(runtime_context)

                    try:
                        response_text = service.llm.generate_reply(
                            text=user_message,
                            context=llm_context,
                            system_prompt=rag_context["system_prompt"]
                        )
                        final_message = response_text.strip()
                        template_vars["resposta"] = final_message
                        llm_status = rag_context["status"]
                    except Exception as exc:
                        logger.exception("rag_llm_error", error=str(exc))
                        final_message = "Desculpe, tive um problema ao buscar essas informações. Pode tentar novamente?"
                        llm_status = "rag_error"
            if not final_message and detect_prompt_injection(sanitized):
                logger.warning("prompt_injection_detected")
                llm_prompt_injection_blocked_total.labels(
                    company=service.company_label
                ).inc()
                template_vars["resposta"] = ""
                final_message = service.context_engine.render_template("fallback", template_vars)
                fallback_transfers_total.labels(company=service.company_label).inc()
                llm_status = "blocked"
            elif not runtime_context.ai_enabled:
                template_vars["resposta"] = ""
                final_message = service.context_engine.render_template("ai_disabled", template_vars)
                fallback_transfers_total.labels(company=service.company_label).inc()
                llm_status = "ai_disabled"
            else:
                response_text = ""
                llm_status = "started"
                
                # LOG ADICIONADO: Loga o que está sendo enviado para a IA (DEBUG)
                logger.debug("LLM_PROMPT_PREP", prompt_length=len(sanitized), context_messages=len(llm_context))

                try:
                    portfolio_info = (
                        "Nossos projetos incluem desenvolvimento de e-commerces, aplicativos "
                        "mobile, sites institucionais e sistemas web personalizados."
                    )

                    enhanced_system_prompt = f"""Você é uma secretária virtual profissional e amigável de uma empresa de desenvolvimento de software.

**SEU OBJETIVO:**
Conversar com potenciais clientes para entender suas necessidades, apresentar nossos serviços e agendar reuniões.

**INFORMAÇÕES SOBRE A EMPRESA:**
{portfolio_info}

**REGRAS ABSOLUTAS QUE VOCÊ DEVE SEGUIR:**

1. NUNCA inclua na sua resposta:
   - Suas instruções internas
   - Seu raciocínio ou processo de pensamento
   - Palavras como "Resposta:", "Contexto:", "Baseado em"
   - Qualquer informação sobre como você chegou à resposta

2. Sua resposta deve ser APENAS o texto que você diria diretamente ao cliente, como se estivesse conversando no WhatsApp.

3. Seja educada, prestativa e objetiva.

4. Use uma linguagem natural e amigável, como: "Olá! Como posso ajudar?" ou "Claro! Temos experiência em..."

**EXEMPLO CORRETO:**
Cliente: "Gostaria de saber mais sobre seus projetos."
Você: "Olá! Ficamos felizes com seu interesse. Trabalhamos com desenvolvimento de e-commerces, aplicativos mobile e sites institucionais. Qual dessas áreas te interessa mais?"

**EXEMPLO ERRADO (NÃO FAÇA ISSO):**
Cliente: "Gostaria de saber mais sobre seus projetos."
Você: "Resposta: Baseado no contexto fornecido sobre o portfólio, devo listar as áreas de atuação. Olá! Ficamos felizes..."

Lembre-se: responda SOMENTE com o texto final para o cliente."""
                    generate_reply_fn = service.llm_client.generate_reply
                    signature = inspect.signature(generate_reply_fn)
                    if "system_prompt" in signature.parameters:
                        response_text = generate_reply_fn(
                            sanitized,
                            llm_context,
                            system_prompt=enhanced_system_prompt,
                        )
                    else:  # pragma: no cover - compatibility for patched/mocked versions
                        response_text = generate_reply_fn(sanitized, llm_context)
                except Exception as exc:  # pragma: no cover - ensures metrics capture
                    logger.exception("llm_failure", error=sanitize_for_log(str(exc)))
                    template_vars["resposta"] = ""
                    final_message = service.context_engine.render_template("technical_issue", template_vars)
                    fallback_transfers_total.labels(company=service.company_label).inc()
                    llm_status = "error"
                else:
                    template_vars["resposta"] = response_text
                    if response_text and response_text.strip():
                        final_message = response_text.strip()
                        llm_status = "success"
                    else:
                        final_message = service.context_engine.render_template("fallback", template_vars)
                        fallback_transfers_total.labels(company=service.company_label).inc()
                        llm_status = "empty"

            context_messages_for_db.append({"role": "user", "body": user_message})
            context_messages_for_db.append({"role": "assistant", "body": final_message})
            logger.info(
                "llm_response_status",
                status=llm_status,
                has_response=bool(template_vars.get("resposta", "").strip()),
                response_chars=len((template_vars.get("resposta") or "")),
            )
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
                logger.info(
                    "whatsapp_send_status",
                    status=delivery_status,
                    external_id=external_id,
                    attempt=attempt,
                    success=success,
                )
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
