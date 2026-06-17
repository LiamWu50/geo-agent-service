from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from geo_agent_service.core.config import settings
from geo_agent_service.main import app
from geo_agent_service.modules.auth.repository import AuthRepository
from geo_agent_service.modules.auth.schemas import AuthSession


def configure_auth(tmp_path: Path) -> None:
    settings.auth_storage_root = str(tmp_path / "auth")
    settings.auth_username = "admin"
    settings.auth_password = "secret"
    settings.auth_token_secret = "test-secret"
    settings.auth_token_expire_minutes = 60


def login(client: TestClient) -> str:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "secret"},
    )
    assert response.status_code == 200
    return str(response.json()["accessToken"])


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_login_returns_token_and_user_profile(tmp_path: Path) -> None:
    configure_auth(tmp_path)
    client = TestClient(app)

    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "secret"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["accessToken"]
    assert payload["tokenType"] == "bearer"
    assert payload["expiresIn"] == 3600
    assert payload["user"] == {
        "id": "default",
        "username": "admin",
        "nickname": "admin",
        "email": None,
        "avatarUrl": None,
    }


def test_login_rejects_invalid_credentials(tmp_path: Path) -> None:
    configure_auth(tmp_path)
    client = TestClient(app)

    wrong_password = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "wrong"},
    )
    wrong_username = client.post(
        "/api/auth/login",
        json={"username": "other", "password": "secret"},
    )

    assert wrong_password.status_code == 401
    assert wrong_password.json() == {"detail": "Unauthorized."}
    assert wrong_username.status_code == 401
    assert wrong_username.json() == {"detail": "Unauthorized."}


def test_me_requires_bearer_token(tmp_path: Path) -> None:
    configure_auth(tmp_path)
    client = TestClient(app)

    response = client.get("/api/auth/me")

    assert response.status_code == 401
    assert response.json() == {"detail": "Unauthorized."}


def test_me_returns_current_user_with_valid_token(tmp_path: Path) -> None:
    configure_auth(tmp_path)
    client = TestClient(app)
    token = login(client)

    response = client.get("/api/auth/me", headers=auth_headers(token))

    assert response.status_code == 200
    assert response.json()["username"] == "admin"


def test_update_current_user_persists_profile(tmp_path: Path) -> None:
    configure_auth(tmp_path)
    client = TestClient(app)
    token = login(client)

    update_response = client.put(
        "/api/auth/me",
        headers=auth_headers(token),
        json={
            "nickname": "Geo Admin",
            "email": "admin@example.com",
            "avatarUrl": "https://example.com/avatar.png",
        },
    )
    get_response = client.get("/api/auth/me", headers=auth_headers(token))

    assert update_response.status_code == 200
    assert get_response.status_code == 200
    assert get_response.json() == {
        "id": "default",
        "username": "admin",
        "nickname": "Geo Admin",
        "email": "admin@example.com",
        "avatarUrl": "https://example.com/avatar.png",
    }


def test_logout_invalidates_current_token(tmp_path: Path) -> None:
    configure_auth(tmp_path)
    client = TestClient(app)
    token = login(client)

    logout_response = client.post("/api/auth/logout", headers=auth_headers(token))
    me_response = client.get("/api/auth/me", headers=auth_headers(token))

    assert logout_response.status_code == 204
    assert me_response.status_code == 401


def test_relogin_invalidates_previous_token(tmp_path: Path) -> None:
    configure_auth(tmp_path)
    client = TestClient(app)
    first_token = login(client)
    second_token = login(client)

    first_response = client.get("/api/auth/me", headers=auth_headers(first_token))
    second_response = client.get("/api/auth/me", headers=auth_headers(second_token))

    assert first_response.status_code == 401
    assert second_response.status_code == 200


def test_expired_token_returns_401(tmp_path: Path) -> None:
    configure_auth(tmp_path)
    client = TestClient(app)
    token = login(client)
    repository = AuthRepository(settings.auth_storage_root)
    session = repository.get_session()
    repository.save_session(
        AuthSession(
            tokenHash=session.token_hash,
            expiresAt=datetime.now(UTC) - timedelta(minutes=1),
        )
    )

    response = client.get("/api/auth/me", headers=auth_headers(token))

    assert response.status_code == 401
