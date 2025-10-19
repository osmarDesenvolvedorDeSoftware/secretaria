from __future__ import annotations

from typing import Iterable

from flask import Flask
from flask.testing import FlaskClient

from app.models import Company, Plan
from app.services.tenancy import namespaced_key


def _auth_headers(client: FlaskClient) -> dict[str, str]:
    response = client.post("/auth/token", json={"password": "painel-teste", "company_id": 1})
    assert response.status_code == 200
    token = response.get_json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _list_companies(client: FlaskClient, headers: dict[str, str]) -> Iterable[dict[str, object]]:
    response = client.get("/painel/empresas", headers=headers)
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)
    return data


def test_list_companies_returns_usage_summary(app: Flask, client: FlaskClient) -> None:
    headers = _auth_headers(client)
    usage_key = namespaced_key(1, "usage")
    app.redis.hset(usage_key, {"messages": 5, "tokens": 1234})  # type: ignore[attr-defined]

    companies = _list_companies(client, headers)
    company = next(item for item in companies if item["company_id"] == 1)

    assert company["company_name"] == "Empresa Teste"
    assert company["usage"]["messages"] == 5
    assert company["usage"]["tokens"] == 1234
    assert company["plan"]["name"] == "Starter"


def test_create_company_assigns_plan_and_blocks_duplicate_domain(app: Flask, client: FlaskClient) -> None:
    headers = _auth_headers(client)

    with app.app_context():
        session = app.db_session()  # type: ignore[attr-defined]
        try:
            premium = Plan(
                name="Premium",
                limite_mensagens=5000,
                limite_tokens=2_000_000,
                preco=499,
                features=["prioridade-suporte", "webhook-billing"],
            )
            session.add(premium)
            session.flush()
            premium_id = premium.id
            session.commit()
        finally:
            session.close()

    payload = {
        "name": "Nova Empresa",
        "domain": "nova.local",
        "plan_id": premium_id,
        "status": "ativo",
        "ciclo": "mensal",
    }

    response = client.post("/painel/empresas", headers=headers, json=payload)
    assert response.status_code == 201
    summary = response.get_json()
    assert summary["company_name"] == "Nova Empresa"
    assert summary["plan"]["id"] == premium_id
    assert summary["subscription"]["status"] == "ativa"

    duplicate = client.post("/painel/empresas", headers=headers, json=payload)
    assert duplicate.status_code == 409
    assert duplicate.get_json()["error"] == "domain_in_use"


def test_update_company_changes_domain_and_plan(app: Flask, client: FlaskClient) -> None:
    headers = _auth_headers(client)

    with app.app_context():
        session = app.db_session()  # type: ignore[attr-defined]
        try:
            company = session.query(Company).filter(Company.domain == "teste.local").one()
            extra_plan = Plan(
                name="Scale",
                limite_mensagens=10000,
                limite_tokens=4_000_000,
                preco=999,
                features=["sla-4h"],
            )
            session.add(extra_plan)
            session.flush()
            extra_plan_id = extra_plan.id
            company_id = company.id
            session.commit()
        finally:
            session.close()

    response = client.put(
        f"/painel/empresas/{company_id}",
        headers=headers,
        json={"domain": "teste-atualizado.local", "plan_id": extra_plan_id, "ciclo": "anual"},
    )
    assert response.status_code == 200
    summary = response.get_json()
    assert summary["domain"] == "teste-atualizado.local"
    assert summary["plan"]["id"] == extra_plan_id
    assert summary["subscription"]["ciclo"] == "anual"

