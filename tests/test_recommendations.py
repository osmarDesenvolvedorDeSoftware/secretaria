from datetime import datetime, timedelta

from flask import Flask

from app.models import Company, Conversation, FeedbackEvent
from app.services.recommendation_service import RecommendationService


def test_recommendation_service_evaluate_generates_insights(app: Flask) -> None:
    service = RecommendationService(app.db_session, app.redis, app.analytics_service)  # type: ignore[attr-defined]
    with app.app_context():
        session = app.db_session()  # type: ignore[attr-defined]
        try:
            company: Company = session.query(Company).first()  # type: ignore[assignment]
            conversation = Conversation(
                company_id=company.id,
                number="559999999999",
                updated_at=datetime.utcnow() - timedelta(days=2),
                created_at=datetime.utcnow() - timedelta(days=10),
            )
            session.add(conversation)
            feedback_event = FeedbackEvent(
                company_id=company.id,
                number="559999999999",
                feedback_type="thumbs_up",
                score=9,
                created_at=datetime.utcnow() - timedelta(days=1),
            )
            session.add(feedback_event)
            session.commit()
        finally:
            session.close()

        app.analytics_service.record_usage(company.id, inbound_messages=800, outbound_messages=200)  # type: ignore[attr-defined]
        insights = service.evaluate(company.id)
        assert insights["company_id"] == company.id
        assert 0.0 <= insights["churn_score"] <= 1.0
        assert "plan_usage" in insights
        cached = service.get_insights(company.id)
        assert cached["company_id"] == company.id
        assert cached["plan_usage"]["messages_ratio"] >= 0
