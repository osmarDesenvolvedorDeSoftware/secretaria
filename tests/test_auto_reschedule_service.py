from datetime import datetime, timedelta

from app.metrics import (
    appointments_auto_rescheduled_total,
    appointments_risk_high_total,
)
from app.models import Appointment, Company, SchedulingInsights
from app.services import auto_reschedule_service, scheduling_ai


class TestAutoRescheduleService:
    def test_executar_reagendamento_envia_sugestao(self, app, monkeypatch) -> None:
        with app.app_context():
            session = app.db_session()
            session.query(SchedulingInsights).delete()
            session.query(Appointment).delete()
            session.commit()

            company = session.get(Company, 1)
            assert company is not None
            company.cal_default_user_id = "cal-user"
            session.add(company)
            session.commit()

            base = datetime.utcnow() - timedelta(days=14)
            for offset in range(5):
                moment = base + timedelta(days=offset)
                session.add(
                    Appointment(
                        company_id=1,
                        client_name="Histórico",
                        client_phone="+5511999990000",
                        start_time=moment.replace(hour=14, minute=0, second=0, microsecond=0),
                        end_time=moment.replace(hour=15, minute=0, second=0, microsecond=0),
                        title="Sessão",
                        cal_booking_id=f"hist-confirm-{offset}",
                        status="confirmed",
                        confirmed_at=moment,
                    )
                )
            for offset in range(5):
                moment = base + timedelta(days=offset)
                session.add(
                    Appointment(
                        company_id=1,
                        client_name="Ausente",
                        client_phone="+5511888880000",
                        start_time=moment.replace(hour=7, minute=0, second=0, microsecond=0),
                        end_time=moment.replace(hour=7, minute=30, second=0, microsecond=0),
                        title="Sessão",
                        cal_booking_id=f"hist-noshow-{offset}",
                        status="no_show",
                    )
                )
            session.commit()
            scheduling_ai.analisar_padroes(1)
            suggestions = scheduling_ai.sugerir_horarios_otimizados(1)
            assert suggestions
            preferred = suggestions[0]

            now = datetime.utcnow()
            upcoming_start = now + timedelta(hours=6)
            upcoming = Appointment(
                company_id=1,
                client_name="Cliente",
                client_phone="+551177700000",
                start_time=upcoming_start.replace(minute=0, second=0, microsecond=0),
                end_time=upcoming_start.replace(minute=30, second=0, microsecond=0),
                title="Consulta",
                cal_booking_id="upcoming-risk",
                status="pending",
                reminder_24h_sent=now - timedelta(hours=1),
                reminder_1h_sent=now - timedelta(minutes=30),
            )
            session.add(upcoming)
            session.commit()

            def fake_availability(user_id, start, end, company_id=None):
                base_date = upcoming.start_time + timedelta(days=1)
                offset_days = (preferred["weekday"] - base_date.weekday()) % 7
                slot_start = base_date + timedelta(days=offset_days)
                slot_start = slot_start.replace(hour=preferred["hour"], minute=0, second=0, microsecond=0)
                if slot_start <= upcoming.start_time:
                    slot_start += timedelta(days=7)
                slot_end = slot_start + timedelta(minutes=30)
                return [
                    {
                        "start": slot_start.isoformat(),
                        "end": slot_end.isoformat(),
                        "duration": 30,
                    }
                ]

            monkeypatch.setattr(
                "app.services.auto_reschedule_service.cal_service.listar_disponibilidade",
                fake_availability,
            )

            sent_messages: list[tuple[str, str]] = []

            def fake_send_text(self, number, body):
                sent_messages.append((number, body))
                return "msg-1"

            monkeypatch.setattr(
                "app.services.auto_reschedule_service.WhaticketClient.send_text",
                fake_send_text,
            )

            risk_counter = appointments_risk_high_total.labels(company="1")._value.get()
            auto_counter = appointments_auto_rescheduled_total.labels(company="1")._value.get()

            result = auto_reschedule_service.executar_reagendamento(1, threshold=0.5, lookahead_hours=48)

            assert result["processed"] >= 1
            assert sent_messages
            assert appointments_risk_high_total.labels(company="1")._value.get() == risk_counter + 1
            assert appointments_auto_rescheduled_total.labels(company="1")._value.get() == auto_counter + 1
            assert result["results"][0]["status"] == "message_sent"
            message = sent_messages[0][1]
            assert f"{preferred['hour']:02d}h" in message
            assert "Responda 1" in message
