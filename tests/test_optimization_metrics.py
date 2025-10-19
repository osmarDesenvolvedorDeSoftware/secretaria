from flask import Flask

from app.metrics import (
    agenda_optimization_runs_total,
    appointments_auto_rescheduled_total,
    appointments_risk_high_total,
)


def _extract_metric(body: str, metric: str, label: str) -> float:
    marker = f"{metric}{{company=\"{label}\"}} "
    for line in body.splitlines():
        if line.startswith(marker):
            return float(line.split(" ")[-1])
    return 0.0


def test_agenda_ai_metrics_are_exposed(app: Flask, client) -> None:
    with app.app_context():
        appointments_risk_high_total.labels(company="1").inc()
        appointments_auto_rescheduled_total.labels(company="1").inc()
        agenda_optimization_runs_total.labels(company="1").inc()

    response = client.get("/metrics")
    assert response.status_code == 200
    body = response.data.decode()
    risk_value = _extract_metric(body, "secretaria_appointments_risk_high_total", "1")
    auto_value = _extract_metric(body, "secretaria_appointments_auto_rescheduled_total", "1")
    run_value = _extract_metric(body, "secretaria_agenda_optimization_runs_total", "1")
    assert risk_value >= 1.0
    assert auto_value >= 1.0
    assert run_value >= 1.0
