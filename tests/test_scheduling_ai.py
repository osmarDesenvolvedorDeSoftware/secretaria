from datetime import datetime, timedelta

from app.metrics import agenda_optimization_runs_total
from app.models import Appointment, SchedulingInsights
from app.services import scheduling_ai


def _create_appointment(session, company_id, start_time, status="pending", **kwargs):
    end_time = start_time + timedelta(minutes=30)
    appointment = Appointment(
        company_id=company_id,
        client_name=kwargs.get("client_name", "Cliente"),
        client_phone=kwargs.get("client_phone", "+5511900000000"),
        start_time=start_time,
        end_time=end_time,
        title=kwargs.get("title", "Consulta"),
        cal_booking_id=kwargs.get("cal_booking_id", f"booking-{start_time.isoformat()}"),
        status=status,
        confirmed_at=kwargs.get("confirmed_at"),
        reminder_24h_sent=kwargs.get("reminder_24h_sent"),
        reminder_1h_sent=kwargs.get("reminder_1h_sent"),
    )
    session.add(appointment)
    session.flush()
    return appointment


def test_analisar_padroes_persiste_insights(app) -> None:
    with app.app_context():
        session = app.db_session()
        session.query(SchedulingInsights).delete()
        session.query(Appointment).delete()
        session.commit()
        base = datetime.utcnow() - timedelta(days=7)
        for offset in range(4):
            moment = base + timedelta(days=offset)
            _create_appointment(session, 1, moment.replace(hour=14), status="confirmed", cal_booking_id=f"ok-{offset}")
        for offset in range(3):
            moment = base + timedelta(days=offset)
            _create_appointment(session, 1, moment.replace(hour=8), status="no_show", cal_booking_id=f"ns-{offset}")
        session.commit()

        before = agenda_optimization_runs_total.labels(company="1")._value.get()
        insights = scheduling_ai.analisar_padroes(1)
        after = agenda_optimization_runs_total.labels(company="1")._value.get()
        assert after == before + 1
        assert insights["heatmap"], "insights heatmap should not be empty"
        stored = session.query(SchedulingInsights).filter_by(company_id=1).all()
        assert stored
        assert len(stored) == len(insights["heatmap"])
        heatmap = {(item["weekday"], item["hour"]): item for item in insights["heatmap"]}
        assert heatmap
        assert any(slot["attendance_rate"] >= 0.75 for slot in heatmap.values())
        assert any(slot["no_show_prob"] >= 0.5 for slot in heatmap.values())


def test_prever_no_show_usa_historico(app) -> None:
    with app.app_context():
        session = app.db_session()
        session.query(SchedulingInsights).delete()
        session.query(Appointment).delete()
        session.commit()
        base = datetime.utcnow() + timedelta(days=1)
        risky = _create_appointment(
            session,
            1,
            base.replace(hour=7),
            status="pending",
            cal_booking_id="risk",
        )
        safe = _create_appointment(
            session,
            1,
            base.replace(hour=15),
            status="confirmed",
            cal_booking_id="safe",
            confirmed_at=base - timedelta(hours=1),
        )
        session.commit()
        scheduling_ai.analisar_padroes(1)

        high_prob = scheduling_ai.prever_no_show(risky)
        low_prob = scheduling_ai.prever_no_show(safe)
        assert high_prob > low_prob
        assert 0.0 < high_prob <= 1.0


def test_sugerir_horarios_otimizados_ordena_por_melhor_taxa(app) -> None:
    with app.app_context():
        session = app.db_session()
        session.query(SchedulingInsights).delete()
        session.query(Appointment).delete()
        session.commit()
        base = datetime.utcnow() - timedelta(days=3)
        for offset in range(5):
            moment = base + timedelta(days=offset)
            _create_appointment(session, 1, moment.replace(hour=13), status="confirmed", cal_booking_id=f"a-{offset}")
        for offset in range(5):
            moment = base + timedelta(days=offset)
            _create_appointment(session, 1, moment.replace(hour=18), status="no_show", cal_booking_id=f"b-{offset}")
        session.commit()
        scheduling_ai.analisar_padroes(1)

        suggestions = scheduling_ai.sugerir_horarios_otimizados(1)
        assert suggestions
        assert suggestions[0]["attendance_rate"] >= suggestions[-1]["attendance_rate"]
