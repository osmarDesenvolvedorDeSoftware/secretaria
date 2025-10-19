from __future__ import annotations

from datetime import datetime, timedelta

from app.metrics import appointment_no_show_total
from app.models import Appointment, AuditLog, FeedbackEvent
from app.services import no_show_service


def test_agendar_verificacao_no_show_enfileira_job(app, monkeypatch) -> None:
    with app.app_context():
        session = app.db_session()
        appointment = Appointment(
            company_id=1,
            client_name="Teste",
            client_phone="+5511912345678",
            start_time=datetime.utcnow(),
            end_time=datetime.utcnow() + timedelta(minutes=30),
            title="Chamada",
            cal_booking_id="no-show-1",
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
            "app.services.no_show_service._queue_for_company",
            lambda _company_id: stub_queue,
        )

        no_show_service.agendar_verificacao_no_show(appointment.id, datetime.utcnow() + timedelta(minutes=10))

        assert len(stub_queue.enqueue_at_calls) == 1
        assert stub_queue.enqueue_at_calls[0][1] == no_show_service.verificar_no_show


def test_verificar_no_show_registra_feedback(app) -> None:
    with app.app_context():
        session = app.db_session()
        appointment = Appointment(
            company_id=1,
            client_name="Cliente",
            client_phone="+5511912345678",
            start_time=datetime.utcnow() - timedelta(hours=1),
            end_time=datetime.utcnow() - timedelta(minutes=30),
            title="Reunião",
            cal_booking_id="no-show-2",
            status="pending",
        )
        session.add(appointment)
        session.commit()
        appointment_id = appointment.id

        counter = appointment_no_show_total.labels(company="1")
        base_value = counter._value.get()

        assert no_show_service.verificar_no_show(appointment_id) is True

        updated = session.get(Appointment, appointment_id)
        assert updated is not None
        assert updated.status == "no_show"
        assert updated.no_show_checked is not None

        feedback = session.query(FeedbackEvent).filter_by(company_id=1, number="+5511912345678").first()
        assert feedback is not None
        logs = session.query(AuditLog).filter_by(action="appointment.no_show_detected").all()
        assert logs
        assert counter._value.get() == base_value + 1

        assert no_show_service.verificar_no_show(appointment_id) is False
