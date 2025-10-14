from __future__ import annotations

from flask.testing import FlaskClient


def create_project(client: FlaskClient, **payload) -> int:
    base_payload = {"name": "Projeto X", "client": "Cliente", "description": "Desc", "status": "ativo"}
    base_payload.update(payload)
    response = client.post("/projects/", json=base_payload)
    assert response.status_code == 201
    data = response.get_json()
    assert data["ok"] is True
    assert isinstance(data["id"], int)
    return data["id"]


def test_create_project(client: FlaskClient) -> None:
    project_id = create_project(client, name="Projeto API", status="pausado")

    response = client.get("/projects/")
    assert response.status_code == 200
    projects = response.get_json()
    assert isinstance(projects, list)
    assert projects[0]["id"] == project_id
    assert projects[0]["name"] == "Projeto API"
    assert projects[0]["status"] == "pausado"


def test_list_projects_ordering(client: FlaskClient) -> None:
    first_id = create_project(client, name="Projeto Antigo")
    second_id = create_project(client, name="Projeto Recente")

    response = client.get("/projects/")
    assert response.status_code == 200
    projects = response.get_json()
    assert [projects[0]["id"], projects[1]["id"]] == [second_id, first_id]


def test_update_project(client: FlaskClient) -> None:
    project_id = create_project(client, name="Projeto Editar", status="ativo")

    response = client.put(
        f"/projects/{project_id}",
        json={"status": "concluido", "description": "Finalizado"},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["ok"] is True

    list_response = client.get("/projects/")
    project = next(p for p in list_response.get_json() if p["id"] == project_id)
    assert project["status"] == "concluido"
    assert project["description"] == "Finalizado"


def test_delete_project(client: FlaskClient) -> None:
    project_id = create_project(client, name="Projeto Remover")

    response = client.delete(f"/projects/{project_id}")
    assert response.status_code == 200
    data = response.get_json()
    assert data["ok"] is True

    list_response = client.get("/projects/")
    assert all(p["id"] != project_id for p in list_response.get_json())


def test_not_found_returns_404(client: FlaskClient) -> None:
    update_response = client.put("/projects/9999", json={"status": "pausado"})
    assert update_response.status_code == 404
    assert update_response.get_json()["error"] == "not found"

    delete_response = client.delete("/projects/9999")
    assert delete_response.status_code == 404
    assert delete_response.get_json()["error"] == "not found"


def test_project_stats(client: FlaskClient) -> None:
    create_project(client, name="Ativo", status="ativo")
    create_project(client, name="Pausado", status="pausado")
    create_project(client, name="Concluido", status="concluido")

    response = client.get("/projects/stats")
    assert response.status_code == 200
    stats = response.get_json()
    assert stats == {"ativos": 1, "pausados": 1, "concluidos": 1}
