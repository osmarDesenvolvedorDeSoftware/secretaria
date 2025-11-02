from datetime import datetime, timedelta, timezone

from app.models import Appointment, AuditLog, FeedbackEvent
from app.services import followup_service
from app.metrics import (
    appointment_followups_negative_total,
    appointment_followups_positive_total,
)


def _create_appointment(session, **overrides) -> Appointment:
    now = datetime.utcnow()
    appointment = Appointment(
        company_id=1,
        client_name=overrides.get("client_name", "Cliente Teste"),
        client_phone=overrides.get("client_phone", "+5511999999999"),
        start_time=overrides.get("start_time", now - timedelta(hours=1)),
        end_time=overrides.get("end_time", now + timedelta(minutes=30)),
        title=overrides.get("title", "Reunião"),
        cal_booking_id=overrides.get("cal_booking_id", f"booking-{now.timestamp()}"),
        status=overrides.get("status", "confirmed"),
        allow_followup=overrides.get("allow_followup", True),
        followup_sent_at=overrides.get("followup_sent_at"),
    )
    session.add(appointment)
    session.commit()
    session.refresh(appointment)
    return appointment


def test_agendar_followup_enqueues_job(app) -> None:
    with app.app_context():
        session = app.db_session()
        appointment = _create_appointment(session)
        appointment_id = appointment.id
        company_id = appointment.company_id

        expected_time = (appointment.end_time or datetime.utcnow())
        expected_time = expected_time.replace(tzinfo=timezone.utc) + timedelta(hours=1)
        expected_time_naive = expected_time.astimezone(timezone.utc).replace(tzinfo=None)
        session.close()

        queue = app.get_task_queue(company_id)
        queue.scheduled.clear()

        followup_service.agendar_followup(appointment)

        assert queue.scheduled, "Follow-up deveria ser agendado no RQ"
        scheduled_time, (func, args, kwargs), meta = queue.scheduled[0]
        assert func == followup_service.enviar_followup
        assert args == (appointment_id,)
        assert meta.get("kind") == "followup"
        assert abs((scheduled_time - expected_time_naive).total_seconds()) < 1

        session = app.db_session()
        stored = session.get(Appointment, appointment_id)
        assert stored is not None
        assert stored.followup_next_scheduled is not None
        assert abs(
            (
                stored.followup_next_scheduled.astimezone(timezone.utc)
                - expected_time
            ).total_seconds()
        ) < 1


def test_agendar_followup_respects_consent(app) -> None:
    with app.app_context():
        session = app.db_session()
        appointment = _create_appointment(session, allow_followup=False, cal_booking_id="booking-consent")
        appointment_id = appointment.id
        company_id = appointment.company_id
        session.close()

        queue = app.get_task_queue(company_id)
        queue.scheduled.clear()

        followup_service.agendar_followup(appointment_id)

        assert not queue.scheduled, "Follow-up não deve ser agendado sem consentimento"
        session = app.db_session()
        stored = session.get(Appointment, appointment_id)
        assert stored is not None
        assert stored.followup_next_scheduled is None


def test_enviar_followup_updates_state_and_audit(app, monkeypatch) -> None:
    sent_messages: list[str] = []

    def fake_send(self, number, message, *_args, **_kwargs):
        sent_messages.append(message)
        return "msg-followup"

    monkeypatch.setattr("app.services.whaticket.WhaticketClient.send_text", fake_send)

    with app.app_context():
        session = app.db_session()
        appointment = _create_appointment(
            session,
            followup_sent_at=None,
            cal_booking_id="booking-send",
        )
        appointment_id = appointment.id
        company_id = appointment.company_id
        session.close()

        queue = app.get_task_queue(company_id)
        queue.scheduled.clear()

        result = followup_service.enviar_followup(appointment_id)
        assert result is True
        assert sent_messages and "Sim, quero marcar" in sent_messages[-1]

        session = app.db_session()
        stored = session.get(Appointment, appointment_id)
        assert stored is not None
        assert stored.followup_sent_at is not None
        assert stored.followup_next_scheduled is None

        audit_entries = (
            session.query(AuditLog)
            .filter(AuditLog.action == "followup_sent", AuditLog.resource == "appointment")
            .all()
        )
        assert audit_entries, "Envio de follow-up deve registrar auditoria"


def test_processar_resposta_interpreta_mensagens(app) -> None:
    with app.app_context():
        session = app.db_session()
        appointment = _create_appointment(
            session,
            followup_sent_at=datetime.utcnow() - timedelta(minutes=5),
            cal_booking_id="booking-process",
        )
        appointment_id = appointment.id
        company_id = appointment.company_id
        session.close()

        positive_counter = appointment_followups_positive_total.labels(company=str(company_id))
        negative_counter = appointment_followups_negative_total.labels(company=str(company_id))
        positive_before = positive_counter._value.get()  # type: ignore[attr-defined]
        negative_before = negative_counter._value.get()  # type: ignore[attr-defined]

        result_positive = followup_service.processar_resposta(appointment_id, "Sim, quero marcar")
        assert result_positive == "positive"

        session = app.db_session()
        stored = session.get(Appointment, appointment_id)
        assert stored is not None and stored.followup_response == "positive"
        session.close()
        assert positive_counter._value.get() == positive_before + 1  # type: ignore[attr-defined]

        result_negative = followup_service.processar_resposta(appointment_id, "Não, obrigado")
        assert result_negative == "negative"

        session = app.db_session()
        stored = session.get(Appointment, appointment_id)
        assert stored is not None and stored.followup_response == "negative"
        session.close()
        assert negative_counter._value.get() == negative_before + 1  # type: ignore[attr-defined]

        feedback_text = "Foi ótimo, quero mais materiais"
        result_feedback = followup_service.processar_resposta(appointment_id, feedback_text)
        assert result_feedback == "feedback"

        session = app.db_session()
        stored = session.get(Appointment, appointment_id)
        assert stored is not None and stored.followup_response == "feedback"
        events = (
            session.query(FeedbackEvent)
            .filter(FeedbackEvent.company_id == company_id)
            .order_by(FeedbackEvent.created_at.desc())
            .all()
        )
        session.close()

        assert events, "Resposta textual deve gerar FeedbackEvent"
        assert any(
            str(event.details.get("appointment_id")) == str(appointment_id)
            for event in events
        )
        assert any("materiais" in (event.comment or "") for event in events)


def test_registrar_resposta_tracks_metrics(app) -> None:
    with app.app_context():
        session = app.db_session()
        appointment = _create_appointment(
            session,
            followup_sent_at=datetime.utcnow(),
            cal_booking_id="booking-response",
        )
        appointment_id = appointment.id
        company_id = appointment.company_id
        session.close()

        positive_counter = appointment_followups_positive_total.labels(company=str(company_id))
        negative_counter = appointment_followups_negative_total.labels(company=str(company_id))
        positive_before = positive_counter._value.get()  # type: ignore[attr-defined]
        negative_before = negative_counter._value.get()  # type: ignore[attr-defined]

        followup_service.registrar_resposta(appointment_id, "positive")
        session = app.db_session()
        stored = session.get(Appointment, appointment_id)
        assert stored is not None
        assert stored.followup_response == "positive"
        assert positive_counter._value.get() == positive_before + 1  # type: ignore[attr-defined]

        followup_service.registrar_resposta(appointment_id, "negative")
        session = app.db_session()
        stored = session.get(Appointment, appointment_id)
        assert stored is not None
        assert stored.followup_response == "negative"
        assert negative_counter._value.get() == negative_before + 1  # type: ignore[attr-defined]

        audit_entries = (
            session.query(AuditLog)
            .filter(AuditLog.action == "followup_response", AuditLog.resource == "appointment")
            .all()
        )
        assert audit_entries, "Registrar resposta deve criar auditoria"
