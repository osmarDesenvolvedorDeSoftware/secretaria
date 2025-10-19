from flask import Flask

from app.models import AuditLog
from app.services.audit import AuditService


def test_audit_service_record_persists_entry(app: Flask) -> None:
    audit = AuditService(app.db_session)
    with app.app_context():
        entry = audit.record(
            company_id=1,
            actor="tester",
            action="unit_test",
            resource="tests",
            payload={"example": True},
        )
        session = app.db_session()  # type: ignore[attr-defined]
        try:
            stored = session.query(AuditLog).filter_by(id=entry.id).one()
            assert stored.actor == "tester"
            assert stored.payload["example"] is True
        finally:
            session.close()
