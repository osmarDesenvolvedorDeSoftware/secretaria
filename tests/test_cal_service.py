from __future__ import annotations

from datetime import datetime

from app.models import Appointment, AuditLog, Company
from app.services import cal_service


class DummyResponse:
    def __init__(self, status_code: int, payload: dict[str, object] | None = None):
        self.status_code = status_code
        self._payload = payload or {}
        self.content = b"{}" if payload is not None else b""

    def json(self) -> dict[str, object]:
        return dict(self._payload)


def _prepare_company(app) -> Company:
    with app.app_context():
        session = app.db_session()
        company = session.get(Company, 1)
        assert company is not None
        company.cal_api_key = "test-key"
        company.cal_default_user_id = "host-1"
        company.cal_webhook_secret = "secret"
        session.commit()
        return company


def test_listar_disponibilidade_success(app, monkeypatch) -> None:
    _prepare_company(app)

    def fake_request(method: str, url: str, *, headers, params=None, **_: object) -> DummyResponse:
        assert method == "GET"
        assert "availability" in url
        assert headers["Authorization"] == "Bearer test-key"
        assert params["userId"] == "host"
        return DummyResponse(200, {"slots": [{"start": "2024-04-01T14:00:00Z"}]})

    monkeypatch.setattr(cal_service, "_perform_request", fake_request)

    with app.app_context():
        slots = cal_service.listar_disponibilidade("host", "2024-04-01", "2024-04-07", company_id=1)
        assert slots == [{"start": "2024-04-01T14:00:00Z"}]

        session = app.db_session()
        logs = session.query(AuditLog).filter_by(action="cal.list_availability").all()
        assert logs


def test_criar_agendamento_persists_appointment(app, monkeypatch) -> None:
    _prepare_company(app)

    def fake_request(method: str, url: str, *, headers, json=None, **_: object) -> DummyResponse:
        assert method == "POST"
        assert "bookings" in url
        assert headers["Authorization"] == "Bearer test-key"
        assert json is not None
        return DummyResponse(
            200,
            {
                "booking": {
                    "id": "booking-1",
                    "meetingUrl": "https://agenda.example/meeting/booking-1",
                }
            },
        )

    monkeypatch.setattr(cal_service, "_perform_request", fake_request)
    monkeypatch.setattr("app.services.reminder_service.agendar_lembretes_padrao", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.services.no_show_service.agendar_verificacao_no_show", lambda *_args, **_kwargs: None)

    with app.app_context():
        result = cal_service.criar_agendamento(
            1,
            {"name": "Cliente Teste", "phone": "+5511999999999"},
            {"start": "2024-04-01T14:00:00Z", "end": "2024-04-01T14:30:00Z"},
            "Reunião",
            30,
        )
        assert result["booking_id"] == "booking-1"
        session = app.db_session()
        appointment = session.query(Appointment).filter_by(cal_booking_id="booking-1").one()
        assert appointment.client_name == "Cliente Teste"
        assert appointment.status == "pending"
        assert appointment.meeting_url == "https://agenda.example/meeting/booking-1"


def test_cancelar_agendamento_updates_status(app, monkeypatch) -> None:
    company = _prepare_company(app)

    def fake_request(method: str, url: str, *, headers, **_: object) -> DummyResponse:
        assert method == "DELETE"
        assert headers["Authorization"] == "Bearer test-key"
        return DummyResponse(200, {})

    monkeypatch.setattr(cal_service, "_perform_request", fake_request)

    with app.app_context():
        session = app.db_session()
        appointment = Appointment(
            company_id=company.id,
            client_name="Cliente",
            client_phone="+55119999",
            start_time=datetime.utcnow(),
            end_time=datetime.utcnow(),
            title="Reunião",
            cal_booking_id="booking-cancel",
            status="confirmed",
        )
        session.add(appointment)
        session.commit()

        cal_service.cancelar_agendamento(company.id, "booking-cancel")
        refreshed = session.query(Appointment).filter_by(cal_booking_id="booking-cancel").one()
        assert refreshed.status == "cancelled"


def test_sincronizar_webhook_rescheduled(app) -> None:
    company = _prepare_company(app)

    payload = {
        "event": "booking.rescheduled",
        "company_id": company.id,
        "data": {
            "booking": {
                "id": "booking-hook",
                "start": "2024-04-02T15:00:00Z",
                "end": "2024-04-02T15:30:00Z",
                "title": "Alinhamento",
                "customer": {"name": "Maria", "phone": "+551198888"},
                "meetingUrl": "https://agenda.example/meeting/booking-hook",
            }
        },
    }

    with app.app_context():
        session = app.db_session()
        appointment = Appointment(
            company_id=company.id,
            client_name="Maria",
            client_phone="+551198888",
            start_time=datetime.utcnow(),
            end_time=datetime.utcnow(),
            title="Antigo",
            cal_booking_id="booking-hook",
            status="confirmed",
        )
        session.add(appointment)
        session.commit()

        cal_service.sincronizar_webhook(payload)
        refreshed = session.query(Appointment).filter_by(cal_booking_id="booking-hook").one()
        assert refreshed.status == "rescheduled"
        assert refreshed.title == "Antigo"
        assert refreshed.meeting_url == "https://agenda.example/meeting/booking-hook"
