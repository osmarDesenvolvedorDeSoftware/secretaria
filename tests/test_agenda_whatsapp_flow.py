from __future__ import annotations

from datetime import datetime

from app.models import Appointment, Company
from app.services import cal_service
from app.services.tasks import process_incoming_message


def test_whatsapp_agenda_flow(app, monkeypatch) -> None:
    with app.app_context():
        session = app.db_session()
        company = session.get(Company, 1)
        assert company is not None
        company.cal_api_key = "test-key"
        company.cal_default_user_id = "host-1"
        session.commit()

    availability = [
        {"start": "2024-05-01T14:00:00Z", "end": "2024-05-01T14:30:00Z", "duration": 30},
        {"start": "2024-05-01T16:00:00Z", "end": "2024-05-01T16:30:00Z", "duration": 30},
    ]

    monkeypatch.setattr(cal_service, "listar_disponibilidade", lambda *_, **__: availability)

    sent_messages: list[str] = []

    def fake_send(self, number, message, *_args, **_kwargs):
        sent_messages.append(message)
        return "whaticket-1"

    monkeypatch.setattr("app.services.whaticket.WhaticketClient.send_text", fake_send)

    def fake_llm(*_args, **_kwargs):  # pragma: no cover - should not be called
        raise AssertionError("LLM should not be invoked during agenda flow")

    monkeypatch.setattr("app.services.llm.LLMClient.generate_reply", fake_llm)

    def fake_create(company_id, cliente, horario, titulo, duracao):
        assert company_id == 1
        assert cliente["name"]
        start = datetime.fromisoformat(horario["start"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(horario["end"].replace("Z", "+00:00"))
        session = app.db_session()
        appointment = Appointment(
            company_id=company_id,
            client_name=cliente["name"],
            client_phone=cliente["phone"],
            start_time=start,
            end_time=end,
            title=titulo,
            cal_booking_id="booking-flow",
            status="confirmed",
            meeting_url="https://agenda.example/meeting/booking-flow",
        )
        session.add(appointment)
        session.commit()
        return {
            "booking_id": "booking-flow",
            "meeting_url": "https://agenda.example/meeting/booking-flow",
            "start": start,
            "end": end,
        }

    monkeypatch.setattr(cal_service, "criar_agendamento", fake_create)

    process_incoming_message(1, "+5511999999999", "Quero marcar reunião", "text", "cid-1")
    assert sent_messages
    assert "1." in sent_messages[0]

    process_incoming_message(1, "+5511999999999", "1", "text", "cid-2")
    assert len(sent_messages) >= 2
    assert "Reunião confirmada" in sent_messages[-1]
    assert "https://agenda.example/meeting/booking-flow" in sent_messages[-1]

    with app.app_context():
        session = app.db_session()
        stored = session.query(Appointment).filter_by(cal_booking_id="booking-flow").first()
        assert stored is not None
        assert stored.client_phone == "+5511999999999"
