from flask.testing import FlaskClient

from app.models import AuditLog, FeedbackEvent


def authenticate(client: FlaskClient) -> str:
    response = client.post("/auth/token", json={"password": "painel-teste", "company_id": 1})
    assert response.status_code == 200
    payload = response.get_json()
    return payload["access_token"]


def test_feedback_ingest_creates_event(client: FlaskClient) -> None:
    token = authenticate(client)
    response = client.post(
        "/api/feedback/ingest",
        json={
            "company_id": 1,
            "number": "559999999999",
            "feedback_type": "thumbs_up",
            "score": 9,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201
    data = response.get_json()
    assert data["feedback_type"] == "thumbs_up"

    events = client.application.db_session()  # type: ignore[attr-defined]
    try:
        stored = events.query(FeedbackEvent).filter_by(company_id=1).all()
        assert len(stored) == 1
        audit_entries = events.query(AuditLog).filter_by(company_id=1, action="feedback_ingest").all()
        assert len(audit_entries) == 1
    finally:
        events.close()

    redis_client = client.application.redis  # type: ignore[attr-defined]
    aggregate = redis_client.hgetall("company:1:feedback:aggregate")
    assert int(aggregate.get("positive", 0)) == 1
