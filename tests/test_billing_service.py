from __future__ import annotations

from datetime import date

from flask import Flask

from app.models import Plan
from app.services.billing import BillingService


def _build_service(app: Flask) -> BillingService:
    return BillingService(app.db_session, app.redis)  # type: ignore[attr-defined]


def test_assign_plan_normalizes_company_status(app: Flask) -> None:
    service = _build_service(app)

    with app.app_context():
        session = app.db_session()  # type: ignore[attr-defined]
        try:
            pro_plan = Plan(
                name="Pro",
                limite_mensagens=2000,
                limite_tokens=1_000_000,
                preco=249,
                features=["sla-8h"],
            )
            session.add(pro_plan)
            session.flush()
            plan_id = pro_plan.id
            session.commit()
        finally:
            session.close()

    subscription = service.assign_plan(company_id=1, plan_id=plan_id, status="ativo", ciclo="mensal")

    assert subscription.plan_id == plan_id
    assert subscription.status == "ativa"


def test_handle_payment_webhook_updates_subscription(app: Flask) -> None:
    service = _build_service(app)

    with app.app_context():
        session = app.db_session()  # type: ignore[attr-defined]
        try:
            enterprise = Plan(
                name="Enterprise",
                limite_mensagens=25000,
                limite_tokens=12_000_000,
                preco=2999,
                features=["suporte-dedicado"],
            )
            session.add(enterprise)
            session.flush()
            plan_id = enterprise.id
            session.query(Plan).filter(Plan.id == 1).update({Plan.name: "Starter"})
            session.commit()
        finally:
            session.close()

    payload = {
        "event": "invoice.payment_succeeded",
        "data": {
            "company_id": 1,
            "plan": "Enterprise",
            "status": "ativa",
            "cycle": "anual",
            "due_date": date.today().isoformat(),
        },
    }

    service.handle_payment_webhook(payload)

    with app.app_context():
        session = app.db_session()  # type: ignore[attr-defined]
        try:
            subscription = service.summarize_company(1)["subscription"]
        finally:
            session.close()

    assert subscription["plan_id"] == plan_id
    assert subscription["status"] == "ativa"
    assert subscription["ciclo"] == "anual"

