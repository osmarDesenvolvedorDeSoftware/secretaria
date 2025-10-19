from __future__ import annotations

from flask import Flask
from flask.testing import FlaskClient


def _auth_headers(client: FlaskClient) -> dict[str, str]:
    response = client.post("/auth/token", json={"password": "painel-teste", "company_id": 1})
    assert response.status_code == 200
    data = response.get_json()
    token = data.get("access_token")
    assert token
    return {"Authorization": f"Bearer {token}"}


def test_analytics_requires_auth(client: FlaskClient) -> None:
    response = client.get("/api/analytics/summary?company_id=1")
    assert response.status_code == 401
    assert response.get_json()["error"] == "unauthorized"


def test_analytics_summary_and_history(app: Flask, client: FlaskClient) -> None:
    headers = _auth_headers(client)
    with app.app_context():
        service = app.analytics_service  # type: ignore[attr-defined]
        service.record_usage(1, inbound_messages=2, outbound_messages=1, inbound_tokens=30, outbound_tokens=45, response_time=0.5)

    summary_response = client.get("/api/analytics/summary?company_id=1", headers=headers)
    assert summary_response.status_code == 200
    summary = summary_response.get_json()
    assert summary["company_id"] == 1
    assert summary["current_usage"]["messages_total"] >= 3
    assert "daily" in summary and summary["daily"] is not None

    history_response = client.get("/api/analytics/history?company_id=1&period=week", headers=headers)
    assert history_response.status_code == 200
    history = history_response.get_json()
    assert history["period"] == "week"
    assert isinstance(history["data"], list)
    assert len(history["data"]) >= 1


def test_analytics_export_csv(app: Flask, client: FlaskClient) -> None:
    headers = _auth_headers(client)
    response = client.get("/api/analytics/export?company_id=1&format=csv", headers=headers)
    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    assert "attachment" in response.headers.get("Content-Disposition", "")
