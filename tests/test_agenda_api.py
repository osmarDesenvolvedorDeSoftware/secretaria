from __future__ import annotations

import hmac
from datetime import datetime
import json
from hashlib import sha256

from flask import Flask
from flask.testing import FlaskClient

from app.models import Appointment, Company
from app.services import cal_service


def _auth_headers(client: FlaskClient) -> dict[str, str]:
    response = client.post("/auth/token", json={"password": "painel-teste", "company_id": 1})
    assert response.status_code == 200
    data = response.get_json()
    token = data.get("access_token")
    assert token
    return {"Authorization": f"Bearer {token}"}


def test_availability_requires_auth(client: FlaskClient) -> None:
    response = client.get("/api/agenda/availability")
    assert response.status_code == 401


def test_availability_success(app: Flask, client: FlaskClient, monkeypatch) -> None:
    headers = _auth_headers(client)

    def fake_listar(usuario_id, data_inicial, data_final, company_id=None):
        assert usuario_id == "host"
        assert company_id == 1
        return [{"start": "2024-04-05T10:00:00Z"}]

    monkeypatch.setattr(cal_service, "listar_disponibilidade", fake_listar)

    response = client.get(
        "/api/agenda/availability?user_id=host&start=2024-04-01&end=2024-04-07",
        headers=headers,
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["slots"][0]["start"] == "2024-04-05T10:00:00Z"


def test_appointments_listing(client: FlaskClient, app: Flask) -> None:
    headers = _auth_headers(client)
    with app.app_context():
        session = app.db_session()
        appointment = Appointment(
            company_id=1,
            client_name="João",
            client_phone="+5511999",
            start_time=datetime.utcnow(),
            end_time=datetime.utcnow(),
            title="Reunião",
            cal_booking_id="abc",
            status="confirmed",
        )
        session.add(appointment)
        session.commit()

    response = client.get("/api/agenda/appointments?company_id=1", headers=headers)
    assert response.status_code == 200
    data = response.get_json()
    assert any(item["cal_booking_id"] == "abc" for item in data["appointments"])


def test_manual_booking_and_cancel(client: FlaskClient, monkeypatch) -> None:
    headers = _auth_headers(client)

    def fake_create(company_id, cliente, horario, titulo, duracao):
        assert company_id == 1
        return {
            "booking_id": "booking-test",
            "meeting_url": "https://agenda.example/meeting/booking-test",
            "start": horario["start"],
            "end": horario.get("end"),
        }

    def fake_cancel(company_id, booking_id):
        assert company_id == 1
        assert booking_id == "booking-test"
        return True

    monkeypatch.setattr(cal_service, "criar_agendamento", fake_create)
    monkeypatch.setattr(cal_service, "cancelar_agendamento", fake_cancel)

    create_payload = {
        "company_id": 1,
        "client": {"name": "João", "phone": "+5511999"},
        "horario": {"start": "2024-04-05T14:00:00Z", "end": "2024-04-05T14:30:00Z"},
        "titulo": "Apresentação",
        "duracao": 30,
    }
    response = client.post("/api/agenda/book", json=create_payload, headers=headers)
    assert response.status_code == 200
    data = response.get_json()
    assert data["booking_id"] == "booking-test"

    cancel_payload = {"company_id": 1, "booking_id": "booking-test"}
    cancel_response = client.post("/api/agenda/cancel", json=cancel_payload, headers=headers)
    assert cancel_response.status_code == 200
    assert cancel_response.get_json()["cancelled"] is True


def test_webhook_signature_validation(app: Flask, client: FlaskClient, monkeypatch) -> None:
    with app.app_context():
        session = app.db_session()
        company = session.get(Company, 1)
        assert company is not None
        company.cal_webhook_secret = "secret"
        company.cal_api_key = "key"
        session.commit()

    captured = {}

    def fake_sync(payload):
        captured["payload"] = payload

    monkeypatch.setattr(cal_service, "sincronizar_webhook", fake_sync)

    body = {"company_id": 1, "event": "booking.created", "data": {"booking": {"id": "hook-1"}}}
    raw = json.dumps(body, separators=(",", ":")).encode()
    signature = hmac.new(b"secret", raw, sha256).hexdigest()

    response = client.post(
        "/api/agenda/webhook/cal",
        data=raw,
        headers={
            "X-Cal-Company": "1",
            "X-Cal-Signature": signature,
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 200
    assert captured["payload"]["event"] == "booking.created"


def test_webhook_invalid_signature(app: Flask, client: FlaskClient) -> None:
    with app.app_context():
        session = app.db_session()
        company = session.get(Company, 1)
        company.cal_webhook_secret = "secret"
        company.cal_api_key = "key"
        session.commit()

    raw = json.dumps({"company_id": 1, "event": "booking.created"}, separators=(",", ":")).encode()
    response = client.post(
        "/api/agenda/webhook/cal",
        data=raw,
        headers={"X-Cal-Company": "1", "X-Cal-Signature": "invalid", "Content-Type": "application/json"},
    )
    assert response.status_code == 401
