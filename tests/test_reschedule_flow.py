from __future__ import annotations

from datetime import datetime, timedelta

from app.metrics import appointment_confirmations_total, appointment_reschedules_total
from app.models import Appointment, AuditLog, Company
from app.services import cal_service
from app.services.tasks import process_incoming_message


class DummyResponse:
    def __init__(self, payload: dict[str, object]):
        self.status_code = 200
        self._payload = payload
        self.content = b"{}"

    def json(self) -> dict[str, object]:
        return dict(self._payload)


def test_reschedule_and_confirm_flow(app, monkeypatch) -> None:
    with app.app_context():
        session = app.db_session()
        company = session.get(Company, 1)
        assert company is not None
        company.cal_api_key = "fake-key"
        company.cal_default_user_id = "user-1"
        session.commit()
        start_time = datetime.utcnow() + timedelta(days=2)
        appointment = Appointment(
            company_id=1,
            client_name="Ana",
            client_phone="+5511900000000",
            start_time=start_time,
            end_time=start_time + timedelta(minutes=30),
            title="Consulta",
            cal_booking_id="orig-1",
            status="pending",
        )
        session.add(appointment)
        session.commit()
        original_id = appointment.id

    availability = [
        {"start": (start_time + timedelta(days=1)).isoformat(), "end": (start_time + timedelta(days=1, minutes=30)).isoformat()},
        {"start": (start_time + timedelta(days=2)).isoformat(), "end": (start_time + timedelta(days=2, minutes=30)).isoformat()},
    ]

    monkeypatch.setattr(cal_service, "listar_disponibilidade", lambda *args, **kwargs: availability)
    monkeypatch.setattr(
        cal_service,
        "_perform_request",
        lambda method, url, *, headers, json=None, **_kwargs: DummyResponse(
            {
                "booking": {
                    "id": "booking-new",
                    "meetingUrl": "https://agenda.example/meeting/new",
                }
            }
        ),
    )
    monkeypatch.setattr("app.services.reminder_service.agendar_lembretes_padrao", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.services.no_show_service.agendar_verificacao_no_show", lambda *_args, **_kwargs: None)

    sent_messages: list[str] = []

    def fake_send(_self, _number, message):
        sent_messages.append(message)
        return "whaticket-id"

    monkeypatch.setattr("app.services.whaticket.WhaticketClient.send_text", fake_send)

    company_label = "1"
    base_confirm = appointment_confirmations_total.labels(company=company_label)._value.get()
    base_reschedule = appointment_reschedules_total.labels(company=company_label)._value.get()

    process_incoming_message(1, "+5511900000000", "preciso remarcar", "text", "cid-1")
    assert any("horários" in message.lower() for message in sent_messages)

    process_incoming_message(1, "+5511900000000", "1", "text", "cid-2")
    assert any("reagendamos" in message.lower() for message in sent_messages)

    with app.app_context():
        session = app.db_session()
        original = session.get(Appointment, original_id)
        assert original is not None
        assert original.status == "rescheduled"
        new_appointment = (
            session.query(Appointment)
            .filter(Appointment.company_id == 1, Appointment.id != original_id)
            .order_by(Appointment.id.desc())
            .first()
        )
        assert new_appointment is not None
        new_id = new_appointment.id
        assert new_appointment.status == "pending"

    process_incoming_message(1, "+5511900000000", "confirmar", "text", "cid-3")
    assert any("presença está confirmada" in message.lower() for message in sent_messages)

    with app.app_context():
        session = app.db_session()
        updated = session.get(Appointment, new_id)
        assert updated is not None
        assert updated.status == "confirmed"
        assert updated.confirmed_at is not None
        confirm_logs = session.query(AuditLog).filter_by(action="appointment.confirmed").all()
        assert confirm_logs
        reschedule_logs = session.query(AuditLog).filter_by(action="appointment.rescheduled").all()
        assert reschedule_logs

    assert appointment_reschedules_total.labels(company=company_label)._value.get() == base_reschedule + 1
    assert appointment_confirmations_total.labels(company=company_label)._value.get() == base_confirm + 1
