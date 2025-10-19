from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

import structlog
from flask import current_app
from sqlalchemy.orm import Session

from app.metrics import agenda_optimization_runs_total
from app.models import Appointment, SchedulingInsights

LOGGER = structlog.get_logger().bind(service="scheduling_ai")

HIGH_RISK_THRESHOLD = 0.8
_DEFAULT_NO_SHOW = 0.2
_MIN_OBSERVATIONS_FOR_SUGGESTION = 2
_MAX_SUGGESTIONS = 3
_ANALYSIS_WINDOW_DAYS = 180


class SchedulingAIError(Exception):
    """Erro genérico da IA de otimização de agenda."""


@dataclass(slots=True)
class SlotStats:
    total: int = 0
    attended: int = 0
    absences: float = 0.0
    confirmations: int = 0
    avg_duration: float = 0.0

    def register(self, appointment: Appointment) -> None:
        self.total += 1
        status = (appointment.status or "").lower()
        if status in {"confirmed", "rescheduled"}:
            self.attended += 1
        elif status in {"cancelled", "no_show"}:
            self.absences += 1.0
        else:
            # peso intermediário para ausências implícitas (sem confirmação)
            self.absences += 0.5
        if appointment.confirmed_at:
            self.confirmations += 1
        if appointment.start_time and appointment.end_time:
            duration = (appointment.end_time - appointment.start_time).total_seconds() / 60
            if duration > 0:
                if self.avg_duration == 0:
                    self.avg_duration = duration
                else:
                    # média móvel simples
                    self.avg_duration = (self.avg_duration + duration) / 2

    @property
    def attendance_rate(self) -> float:
        if self.total <= 0:
            return 0.0
        return max(0.0, min(1.0, self.attended / self.total))

    @property
    def no_show_probability(self) -> float:
        if self.total <= 0:
            return _DEFAULT_NO_SHOW
        return max(0.0, min(1.0, self.absences / self.total))

    @property
    def confirmation_ratio(self) -> float:
        if self.total <= 0:
            return 0.0
        return max(0.0, min(1.0, self.confirmations / self.total))


def _session() -> Session:
    session_factory = getattr(current_app, "db_session", None)
    if session_factory is None:
        raise RuntimeError("session_factory_not_configured")
    return session_factory()


def _normalize_datetime(value: datetime) -> datetime:
    try:
        return value.astimezone()
    except Exception:
        return value


def _slot_key(moment: datetime) -> tuple[int, int]:
    localized = _normalize_datetime(moment)
    return localized.weekday(), localized.hour


def _weekday_label(weekday: int) -> str:
    labels = [
        "Segundas",
        "Terças",
        "Quartas",
        "Quintas",
        "Sextas",
        "Sábados",
        "Domingos",
    ]
    if 0 <= weekday < len(labels):
        return labels[weekday]
    return "Horários"


def _score_slot(stats: SlotStats, avg_volume: float) -> float:
    attendance_score = stats.attendance_rate
    reliability_score = 1 - stats.no_show_probability
    volume_factor = stats.total / avg_volume if avg_volume > 0 else 1.0
    volume_score = max(0.5, min(1.5, volume_factor)) / 1.5
    confirmation_bonus = stats.confirmation_ratio * 0.1
    return (attendance_score * 0.55) + (reliability_score * 0.35) + (volume_score * 0.1) + confirmation_bonus


def _build_recommendation(heatmap: list[dict[str, Any]], suggestions: list[dict[str, Any]]) -> str | None:
    if not suggestions:
        return None
    best = suggestions[0]
    weekday = best["weekday"]
    hour = best["hour"]
    best_absence = best.get("no_show_prob", _DEFAULT_NO_SHOW)
    if heatmap:
        avg_absence = sum(item.get("no_show_prob", _DEFAULT_NO_SHOW) for item in heatmap) / max(len(heatmap), 1)
    else:
        avg_absence = 0.3
    reduction = max(0.0, avg_absence - best_absence)
    reduction_pct = int(round(reduction * 100))
    hour_end = (hour + 1) % 24
    weekday_label = _weekday_label(weekday)
    if reduction_pct <= 0:
        return f"{weekday_label} entre {hour:02d}h e {hour_end:02d}h concentram os agendamentos mais confiáveis."
    return f"{weekday_label} entre {hour:02d}h–{hour_end:02d}h têm {reduction_pct}% menos faltas."


def analisar_padroes(company_id: int) -> dict[str, Any]:
    session = _session()
    try:
        cutoff = datetime.utcnow() - timedelta(days=_ANALYSIS_WINDOW_DAYS)
        items: Iterable[Appointment] = (
            session.query(Appointment)
            .filter(
                Appointment.company_id == company_id,
                Appointment.start_time.isnot(None),
                Appointment.start_time >= cutoff,
            )
            .all()
        )
        stats_map: dict[tuple[int, int], SlotStats] = defaultdict(SlotStats)
        for appointment in items:
            if not appointment.start_time:
                continue
            stats_map[_slot_key(appointment.start_time)].register(appointment)

        if not stats_map:
            # nenhuma base histórica, limpamos possíveis registros antigos
            session.query(SchedulingInsights).filter_by(company_id=company_id).delete(synchronize_session=False)
            session.commit()
            agenda_optimization_runs_total.labels(company=str(company_id)).inc()
            return {
                "company_id": company_id,
                "heatmap": [],
                "suggestions": [],
                "recommendation": None,
                "updated_at": datetime.utcnow().isoformat(),
            }

        avg_volume = sum(slot.total for slot in stats_map.values()) / max(len(stats_map), 1)
        scored_slots: list[tuple[tuple[int, int], float, SlotStats]] = []
        for slot_key, slot_stats in stats_map.items():
            score = _score_slot(slot_stats, avg_volume)
            scored_slots.append((slot_key, score, slot_stats))

        scored_slots.sort(key=lambda item: item[1], reverse=True)
        suggestions_keys = [
            key
            for key, _score, stats in scored_slots
            if stats.total >= _MIN_OBSERVATIONS_FOR_SUGGESTION
        ]
        suggestions_keys = suggestions_keys[:_MAX_SUGGESTIONS]
        if not suggestions_keys and scored_slots:
            # Garante pelo menos um slot sugerido quando ainda estamos coletando histórico.
            suggestions_keys = [scored_slots[0][0]]

        existing = {
            (item.weekday, item.hour): item
            for item in session.query(SchedulingInsights).filter_by(company_id=company_id).all()
        }

        now = datetime.utcnow()
        for slot_key, score, slot_stats in scored_slots:
            weekday, hour = slot_key
            entry = existing.pop(slot_key, None)
            if entry is None:
                entry = SchedulingInsights(company_id=company_id, weekday=weekday, hour=hour)
            entry.attendance_rate = round(slot_stats.attendance_rate, 4)
            entry.no_show_prob = round(slot_stats.no_show_probability, 4)
            entry.suggested_slot = slot_key in suggestions_keys
            entry.updated_at = now
            session.add(entry)

        # remove registros antigos que não possuem mais observações
        for leftover in existing.values():
            session.delete(leftover)

        session.commit()

        agenda_optimization_runs_total.labels(company=str(company_id)).inc()

        return obter_insights(company_id)
    finally:
        session.close()


def obter_insights(company_id: int) -> dict[str, Any]:
    session = _session()
    try:
        entries = (
            session.query(SchedulingInsights)
            .filter(SchedulingInsights.company_id == company_id)
            .order_by(SchedulingInsights.weekday.asc(), SchedulingInsights.hour.asc())
            .all()
        )
        if not entries:
            return {
                "company_id": company_id,
                "heatmap": [],
                "suggestions": [],
                "recommendation": None,
                "updated_at": None,
            }
        updated_at = max((entry.updated_at for entry in entries if entry.updated_at), default=datetime.utcnow())
        heatmap = [
            {
                "weekday": entry.weekday,
                "hour": entry.hour,
                "attendance_rate": round(entry.attendance_rate, 4),
                "no_show_prob": round(entry.no_show_prob, 4),
                "suggested": bool(entry.suggested_slot),
            }
            for entry in entries
        ]
        suggestions = [item for item in heatmap if item["suggested"]]
        recommendation = _build_recommendation(heatmap, suggestions)
        return {
            "company_id": company_id,
            "heatmap": heatmap,
            "suggestions": suggestions,
            "recommendation": recommendation,
            "updated_at": updated_at.isoformat() if updated_at else None,
        }
    finally:
        session.close()


def prever_no_show(appointment: Appointment | dict[str, Any]) -> float:
    if appointment is None:
        return _DEFAULT_NO_SHOW

    if isinstance(appointment, Appointment):
        company_id = appointment.company_id
        start_time = appointment.start_time
        status = appointment.status
        confirmed_at = appointment.confirmed_at
        reminder_24h = appointment.reminder_24h_sent
        reminder_1h = appointment.reminder_1h_sent
    else:
        company_id = int(appointment.get("company_id", 0) or 0)
        start_time = appointment.get("start_time")
        status = appointment.get("status")
        confirmed_at = appointment.get("confirmed_at")
        reminder_24h = appointment.get("reminder_24h_sent")
        reminder_1h = appointment.get("reminder_1h_sent")
        if isinstance(start_time, str):
            try:
                start_time = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            except ValueError:
                start_time = None
        if isinstance(confirmed_at, str):
            try:
                confirmed_at = datetime.fromisoformat(confirmed_at.replace("Z", "+00:00"))
            except ValueError:
                confirmed_at = None
        for field_name in ("reminder_24h_sent", "reminder_1h_sent"):
            value = appointment.get(field_name)
            if isinstance(value, str):
                try:
                    appointment[field_name] = datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError:
                    appointment[field_name] = None
        reminder_24h = appointment.get("reminder_24h_sent")
        reminder_1h = appointment.get("reminder_1h_sent")

    if not company_id or not start_time:
        return _DEFAULT_NO_SHOW

    insights = obter_insights(company_id)
    weekday, hour = _slot_key(start_time)
    base_prob = _DEFAULT_NO_SHOW
    for entry in insights.get("heatmap", []):
        if entry["weekday"] == weekday and entry["hour"] == hour:
            base_prob = entry.get("no_show_prob", _DEFAULT_NO_SHOW)
            break

    status_text = (status or "").lower()
    probability = float(base_prob)
    if status_text == "confirmed":
        probability *= 0.5
    elif status_text in {"pending", "aguardando"}:
        probability = min(1.0, probability + 0.1)

    if confirmed_at:
        probability *= 0.7

    now = datetime.utcnow()
    if reminder_24h and not confirmed_at and start_time - now <= timedelta(hours=12):
        probability = min(1.0, probability + 0.1)
    if reminder_1h and not confirmed_at:
        probability = min(1.0, probability + 0.15)

    if start_time.hour < 9:
        probability = min(1.0, probability + 0.08)
    if start_time.weekday() in {0, 4}:  # segunda ou sexta
        probability = min(1.0, probability + 0.05)

    return round(max(0.01, min(0.99, probability)), 4)


def sugerir_horarios_otimizados(company_id: int) -> list[dict[str, Any]]:
    insights = obter_insights(company_id)
    suggestions = insights.get("suggestions") or []
    if suggestions:
        enriched: list[dict[str, Any]] = []
        for item in suggestions:
            weekday = item["weekday"]
            hour = item["hour"]
            hour_end = (hour + 1) % 24
            enriched.append(
                {
                    "weekday": weekday,
                    "hour": hour,
                    "attendance_rate": item.get("attendance_rate", 0.0),
                    "no_show_prob": item.get("no_show_prob", _DEFAULT_NO_SHOW),
                    "label": f"{_weekday_label(weekday)} · {hour:02d}h-{hour_end:02d}h",
                }
            )
        return enriched

    # fallback heurístico quando não há histórico suficiente
    baseline = [
        {"weekday": 1, "hour": 14},
        {"weekday": 2, "hour": 15},
        {"weekday": 3, "hour": 11},
    ]
    return [
        {
            "weekday": item["weekday"],
            "hour": item["hour"],
            "attendance_rate": 0.7,
            "no_show_prob": _DEFAULT_NO_SHOW,
            "label": f"{_weekday_label(item['weekday'])} · {item['hour']:02d}h-{(item['hour'] + 1) % 24:02d}h",
        }
        for item in baseline
    ]


def atualizar_insights_job(company_id: int) -> None:
    try:
        analisar_padroes(company_id)
    except Exception as exc:  # pragma: no cover - log defensivo para workers
        LOGGER.warning("scheduling_ai_job_failed", company_id=company_id, error=str(exc))
