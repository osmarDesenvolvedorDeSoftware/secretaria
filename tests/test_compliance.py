from flask.testing import FlaskClient

from app.models import Conversation, CustomerContext, DeliveryLog, FeedbackEvent


def authenticate(client: FlaskClient) -> str:
    response = client.post("/auth/token", json={"password": "painel-teste", "company_id": 1})
    assert response.status_code == 200
    return response.get_json()["access_token"]


def seed_compliance_records(client: FlaskClient) -> None:
    session = client.application.db_session()  # type: ignore[attr-defined]
    try:
        conversation = Conversation(company_id=1, number="559999999999", last_message="Olá")
        context = CustomerContext(company_id=1, number="559999999999", preferences={})
        feedback = FeedbackEvent(company_id=1, number="559999999999", feedback_type="thumbs_up")
        delivery = DeliveryLog(company_id=1, number="559999999999", body="Oi", status="SENT")
        session.add_all([conversation, context, feedback, delivery])
        session.commit()
    finally:
        session.close()


def test_compliance_export_and_delete(client: FlaskClient) -> None:
    token = authenticate(client)
    seed_compliance_records(client)

    export = client.post(
        "/api/compliance/export_data",
        json={"company_id": 1, "number": "559999999999", "format": "json"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert export.status_code == 200
    payload = export.get_json()
    assert payload["company_id"] == 1
    assert payload["conversations"]

    delete = client.post(
        "/api/compliance/delete_data",
        json={"company_id": 1, "number": "559999999999"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert delete.status_code == 200
    deleted_payload = delete.get_json()
    assert deleted_payload["deleted"]["conversations"] >= 1

    session = client.application.db_session()  # type: ignore[attr-defined]
    try:
        remaining = session.query(Conversation).filter_by(company_id=1, number="559999999999").count()
        assert remaining == 0
    finally:
        session.close()
