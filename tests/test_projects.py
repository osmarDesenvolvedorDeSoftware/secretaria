from __future__ import annotations

from __future__ import annotations

import pytest
from flask.testing import FlaskClient


@pytest.fixture
def panel_headers(client: FlaskClient) -> dict[str, str]:
    response = client.post("/auth/token", json={"password": "painel-teste"})
    assert response.status_code == 200
    token = response.get_json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def create_project(client: FlaskClient, headers: dict[str, str], **payload) -> int:
    base_payload = {"name": "Projeto X", "client": "Cliente", "description": "Desc", "status": "ativo"}
    base_payload.update(payload)
    response = client.post("/projects/", json=base_payload, headers=headers)
    assert response.status_code == 201
    data = response.get_json()
    assert data["ok"] is True
    assert isinstance(data["id"], int)
    return data["id"]


def test_create_project(client: FlaskClient, panel_headers: dict[str, str]) -> None:
    project_id = create_project(client, panel_headers, name="Projeto API", status="pausado")

    response = client.get("/projects/", headers=panel_headers)
    assert response.status_code == 200
    projects = response.get_json()
    assert isinstance(projects, list)
    assert projects[0]["id"] == project_id
    assert projects[0]["name"] == "Projeto API"
    assert projects[0]["status"] == "pausado"


def test_list_projects_ordering(client: FlaskClient, panel_headers: dict[str, str]) -> None:
    first_id = create_project(client, panel_headers, name="Projeto Antigo")
    second_id = create_project(client, panel_headers, name="Projeto Recente")

    response = client.get("/projects/", headers=panel_headers)
    assert response.status_code == 200
    projects = response.get_json()
    assert [projects[0]["id"], projects[1]["id"]] == [second_id, first_id]


def test_update_project(client: FlaskClient, panel_headers: dict[str, str]) -> None:
    project_id = create_project(client, panel_headers, name="Projeto Editar", status="ativo")

    response = client.put(
        f"/projects/{project_id}",
        json={"status": "concluido", "description": "Finalizado"},
        headers=panel_headers,
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["ok"] is True

    list_response = client.get("/projects/", headers=panel_headers)
    project = next(p for p in list_response.get_json() if p["id"] == project_id)
    assert project["status"] == "concluido"
    assert project["description"] == "Finalizado"


def test_delete_project(client: FlaskClient, panel_headers: dict[str, str]) -> None:
    project_id = create_project(client, panel_headers, name="Projeto Remover")

    response = client.delete(f"/projects/{project_id}", headers=panel_headers)
    assert response.status_code == 200
    data = response.get_json()
    assert data["ok"] is True

    list_response = client.get("/projects/", headers=panel_headers)
    assert all(p["id"] != project_id for p in list_response.get_json())


def test_not_found_returns_404(client: FlaskClient, panel_headers: dict[str, str]) -> None:
    update_response = client.put(
        "/projects/9999",
        json={"status": "pausado"},
        headers=panel_headers,
    )
    assert update_response.status_code == 404
    assert update_response.get_json()["error"] == "not found"

    delete_response = client.delete("/projects/9999", headers=panel_headers)
    assert delete_response.status_code == 404
    assert delete_response.get_json()["error"] == "not found"


def test_project_stats(client: FlaskClient, panel_headers: dict[str, str]) -> None:
    create_project(client, panel_headers, name="Ativo", status="ativo")
    create_project(client, panel_headers, name="Pausado", status="pausado")
    create_project(client, panel_headers, name="Concluido", status="concluido")

    response = client.get("/projects/stats", headers=panel_headers)
    assert response.status_code == 200
    stats = response.get_json()
    assert stats == {"ativos": 1, "pausados": 1, "concluidos": 1}
