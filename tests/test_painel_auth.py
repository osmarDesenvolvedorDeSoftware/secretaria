from __future__ import annotations

from flask.testing import FlaskClient

from app.config import settings as config_settings
from app.services.auth import encode_jwt


def test_panel_login_success_sets_cookie(client: FlaskClient) -> None:
    response = client.post("/auth/token", json={"password": "painel-teste", "company_id": 1})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["access_token"]
    assert payload["token_type"] == "bearer"
    assert "expires_in" in payload
    assert "panel_token=" in response.headers.get("Set-Cookie", "")

    protected = client.get("/projects/")
    assert protected.status_code == 200
    assert protected.get_json() == []

    protected_with_header = client.get(
        "/projects/",
        headers={"Authorization": f"Bearer {payload['access_token']}"},
    )
    assert protected_with_header.status_code == 200


def test_panel_logout_revokes_cookie_access(client: FlaskClient) -> None:
    login = client.post("/auth/token", json={"password": "painel-teste", "company_id": 1})
    assert login.status_code == 200
    assert client.get("/projects/").status_code == 200

    client.delete_cookie("panel_token")
    response = client.get("/projects/")
    assert response.status_code == 401
    assert response.get_json() == {"error": "unauthorized"}


def test_panel_expired_token_returns_unauthorized(client: FlaskClient) -> None:
    expired_token = encode_jwt(
        {"sub": "panel", "scope": "panel:admin", "company_id": 1},
        config_settings.panel_jwt_secret,
        expires_in=-30,
    )
    response = client.get(
        "/projects/",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert response.status_code == 401
    assert response.get_json() == {"error": "unauthorized"}
