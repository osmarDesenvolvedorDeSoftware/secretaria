from datetime import datetime, timedelta

from app.models import Appointment, Company, FeedbackEvent
from app.services import cal_service
from app.services.tasks import process_incoming_message


def _create_followup_appointment(app, phone: str, *, allow_followup: bool = True) -> Appointment:
    with app.app_context():
        session = app.db_session()
        company = session.get(Company, 1)
        if company is not None:
            company.cal_api_key = "test-key"
            company.cal_default_user_id = "host-followup"
            session.add(company)
        appointment = Appointment(
            company_id=1,
            client_name="Cliente Follow-up",
            client_phone=phone,
            start_time=datetime.utcnow() - timedelta(hours=2),
            end_time=datetime.utcnow() - timedelta(hours=1),
            title="Reunião pós-venda",
            cal_booking_id=f"followup-{phone[-4:]}-{datetime.utcnow().timestamp()}",
            status="confirmed",
            allow_followup=allow_followup,
            followup_sent_at=datetime.utcnow() - timedelta(minutes=30),
        )
        session.add(appointment)
        session.commit()
        session.refresh(appointment)
        return appointment


def test_followup_positive_triggers_reengagement(app, monkeypatch) -> None:
    availability = [
        {"start": "2024-05-01T14:00:00Z", "end": "2024-05-01T14:30:00Z", "duration": 30},
        {"start": "2024-05-01T16:00:00Z", "end": "2024-05-01T16:30:00Z", "duration": 30},
    ]
    monkeypatch.setattr(cal_service, "listar_disponibilidade", lambda *_, **__: availability)

    sent_messages: list[str] = []

    def fake_send(self, number, message, *_args, **_kwargs):
        sent_messages.append(message)
        return "mid-followup"

    monkeypatch.setattr("app.services.whaticket.WhaticketClient.send_text", fake_send)

    appointment = _create_followup_appointment(app, "+5511999991111")

    process_incoming_message(1, appointment.client_phone, "Sim, quero marcar", "text", "cid-followup-positive")

    assert sent_messages, "Fluxo deve responder com opções de agendamento"
    assert "Que ótimo" in sent_messages[-1]
    assert "1." in sent_messages[-1]

    with app.app_context():
        session = app.db_session()
        stored = session.get(Appointment, appointment.id)
        assert stored is not None
        assert stored.followup_response == "positive"


def test_followup_negative_acknowledges(app, monkeypatch) -> None:
    sent_messages: list[str] = []

    def fake_send(self, number, message, *_args, **_kwargs):
        sent_messages.append(message)
        return "mid-negative"

    monkeypatch.setattr("app.services.whaticket.WhaticketClient.send_text", fake_send)

    appointment = _create_followup_appointment(app, "+5511999992222")

    process_incoming_message(1, appointment.client_phone, "Não, obrigado", "text", "cid-followup-negative")

    assert sent_messages, "Fluxo deve enviar confirmação negativa"
    assert "Sem problemas" in sent_messages[-1]

    with app.app_context():
        session = app.db_session()
        stored = session.get(Appointment, appointment.id)
        assert stored is not None
        assert stored.followup_response == "negative"


def test_followup_feedback_records_event(app, monkeypatch) -> None:
    sent_messages: list[str] = []

    def fake_send(self, number, message, *_args, **_kwargs):
        sent_messages.append(message)
        return "mid-feedback"

    monkeypatch.setattr("app.services.whaticket.WhaticketClient.send_text", fake_send)

    appointment = _create_followup_appointment(app, "+5511999993333")

    feedback_message = "O atendimento foi ótimo, gostaria de deixar um feedback completo sobre a consultoria."
    process_incoming_message(1, appointment.client_phone, feedback_message, "text", "cid-followup-feedback")

    assert sent_messages, "Fluxo deve agradecer o feedback"
    assert "Agradeço" in sent_messages[-1]

    with app.app_context():
        session = app.db_session()
        stored = session.get(Appointment, appointment.id)
        assert stored is not None
        assert stored.followup_response == "feedback"
        events = (
            session.query(FeedbackEvent)
            .filter(FeedbackEvent.company_id == 1)
            .filter(FeedbackEvent.feedback_type == "followup_text")
            .order_by(FeedbackEvent.created_at.desc())
            .all()
        )
        assert events, "Feedback textual deve ser persistido"
        assert any(str(event.details.get("appointment_id")) == str(appointment.id) for event in events)
        assert any((event.comment or "").startswith(feedback_message.lower()[:10]) for event in events)
