from __future__ import annotations

from datetime import datetime, timedelta

from app.metrics import appointment_reminders_sent_total
from app.models import Appointment, AuditLog
from app.services import reminder_service


def test_agendar_lembretes_padrao(app, monkeypatch) -> None:
    with app.app_context():
        session = app.db_session()
        appointment = Appointment(
            company_id=1,
            client_name="Maria",
            client_phone="+5511988888888",
            start_time=datetime.utcnow() + timedelta(days=2),
            end_time=datetime.utcnow() + timedelta(days=2, hours=1),
            title="Demo",
            cal_booking_id="sched-1",
            status="pending",
        )
        session.add(appointment)
        session.commit()

        class StubQueue:
            def __init__(self) -> None:
                self.enqueue_calls: list[tuple] = []
                self.enqueue_at_calls: list[tuple] = []

            def enqueue(self, func, *args, **kwargs):
                self.enqueue_calls.append((func, args, kwargs))
                return type("Job", (), {"id": str(len(self.enqueue_calls))})()

            def enqueue_at(self, when, func, *args, **kwargs):
                self.enqueue_at_calls.append((when, func, args, kwargs))
                return self.enqueue(func, *args, **kwargs)

        stub_queue = StubQueue()
        monkeypatch.setattr(
            "app.services.reminder_service._queue_for_company",
            lambda _company_id: stub_queue,
        )

        reminder_service.agendar_lembretes_padrao(appointment)

        assert len(stub_queue.enqueue_at_calls) == 2
        assert all(call[1] == reminder_service.enviar_lembrete for call in stub_queue.enqueue_at_calls)


def test_enviar_lembrete_registra_evento(app, monkeypatch) -> None:
    with app.app_context():
        session = app.db_session()
        appointment = Appointment(
            company_id=1,
            client_name="Carlos",
            client_phone="+5511977777777",
            start_time=datetime.utcnow() + timedelta(days=1),
            end_time=datetime.utcnow() + timedelta(days=1, hours=1),
            title="Alinhamento",
            cal_booking_id="reminder-1",
            status="pending",
        )
        session.add(appointment)
        session.commit()
        appointment_id = appointment.id

    sent_messages: list[str] = []
    monkeypatch.setattr(
        "app.services.whaticket.WhaticketClient.send_text",
        lambda self, number, message: sent_messages.append(message) or "message-id",
    )

    counter = appointment_reminders_sent_total.labels(company="1", type="24h")
    base_value = counter._value.get()

    with app.app_context():
        assert reminder_service.enviar_lembrete(appointment_id, "24h") is True
        session = app.db_session()
        updated = session.get(Appointment, appointment_id)
        assert updated is not None
        assert updated.reminder_24h_sent is not None
        logs = session.query(AuditLog).filter_by(action="appointment.reminder_sent").all()
        assert logs

    assert sent_messages
    assert "Deseja confirmar" in sent_messages[0]
    assert counter._value.get() == base_value + 1
