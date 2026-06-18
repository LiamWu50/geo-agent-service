from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from geo_agent_service.core.config import settings
from geo_agent_service.main import app
from geo_agent_service.modules.ai_chat.repository import AiChatRepository
from geo_agent_service.modules.ai_chat.routes import get_ai_chat_service
from geo_agent_service.modules.ai_chat.service import AiChatService
from geo_agent_service.modules.gis_data.repository import DatasetRepository
from geo_agent_service.modules.gis_data.storage import GisDataStorage
from geo_agent_service.tools.base import GisTool, GisToolResult
from geo_agent_service.tools.registry import GisToolRegistry


class FakeModelClient:
    async def stream_response(
        self,
        *,
        messages: list[dict[str, str]],
        tool_results: list[dict[str, Any]],
    ) -> AsyncIterator[str]:
        yield "AI summary "
        yield f"from {len(tool_results)} tool result(s)"


class EchoTool(GisTool):
    name = "echo_context"
    description = "Echoes chat context for tests."

    async def run(self, payload: dict[str, Any]) -> GisToolResult:
        return GisToolResult(
            data_ref="memory://echo",
            summary={
                "message": payload["message"],
                "selectedDatasetIds": payload["selectedDatasetIds"],
            },
        )


class FailingTool(GisTool):
    name = "failing_tool"
    description = "Fails for tests."

    async def run(self, payload: dict[str, Any]) -> GisToolResult:
        raise RuntimeError("tool exploded")


def configure_app(tmp_path: Path, *, failing_tool: bool = False) -> None:
    settings.auth_storage_root = str(tmp_path / "auth")
    settings.gis_storage_root = str(tmp_path / "gis")
    settings.ai_chat_storage_root = str(tmp_path / "ai-chat")
    settings.auth_username = "admin"
    settings.auth_password = "secret"
    settings.auth_token_secret = "test-secret"
    settings.auth_token_expire_minutes = 60

    def fake_service() -> AiChatService:
        registry = GisToolRegistry()
        registry.register(FailingTool() if failing_tool else EchoTool())
        storage = GisDataStorage(settings.gis_storage_root)
        return AiChatService(
            repository=AiChatRepository(settings.ai_chat_storage_root),
            dataset_repository=DatasetRepository(storage.metadata_path()),
            tool_registry=registry,
            model_client=FakeModelClient(),
        )

    app.dependency_overrides[get_ai_chat_service] = fake_service


def clear_overrides() -> None:
    app.dependency_overrides.clear()


def login(client: TestClient) -> str:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "secret"},
    )
    assert response.status_code == 200
    return str(response.json()["accessToken"])


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def event_names(stream_text: str) -> list[str]:
    return [
        line.removeprefix("event: ")
        for line in stream_text.splitlines()
        if line.startswith("event: ")
    ]


def test_ai_chat_requires_authentication(tmp_path: Path) -> None:
    configure_app(tmp_path)
    try:
        client = TestClient(app)

        response = client.post(
            "/api/ai-chat/sessions/session_demo/messages",
            json={"message": "hello"},
        )

        assert response.status_code == 401
        assert response.json() == {"detail": "Unauthorized."}
    finally:
        clear_overrides()


def test_ai_chat_streams_tool_and_message_events(tmp_path: Path) -> None:
    configure_app(tmp_path)
    try:
        client = TestClient(app)
        token = login(client)

        response = client.post(
            "/api/ai-chat/sessions/session_demo/messages",
            headers=auth_headers(token),
            json={"message": "analyze schools", "selectedDatasetIds": ["dataset_1"]},
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert event_names(response.text) == [
            "tool.started",
            "tool.completed",
            "message.delta",
            "message.delta",
            "message.completed",
        ]
        assert "analyze schools" in response.text
        assert "AI summary from 1 tool result(s)" in response.text

        session_response = client.get(
            "/api/ai-chat/sessions/session_demo",
            headers=auth_headers(token),
        )
        assert session_response.status_code == 200
        session = session_response.json()["session"]
        assert session["status"] == "completed"
        assert [message["role"] for message in session["messages"]] == ["user", "assistant"]
        assert session["messages"][1]["content"] == "AI summary from 1 tool result(s)"
        assert session["toolCalls"][0]["status"] == "completed"
    finally:
        clear_overrides()


def test_ai_chat_records_repeated_messages_in_same_session(tmp_path: Path) -> None:
    configure_app(tmp_path)
    try:
        client = TestClient(app)
        token = login(client)
        headers = auth_headers(token)

        first = client.post(
            "/api/ai-chat/sessions/session_demo/messages",
            headers=headers,
            json={"message": "first"},
        )
        second = client.post(
            "/api/ai-chat/sessions/session_demo/messages",
            headers=headers,
            json={"message": "second"},
        )

        assert first.status_code == 200
        assert second.status_code == 200
        session_response = client.get("/api/ai-chat/sessions/session_demo", headers=headers)
        session = session_response.json()["session"]
        assert [message["role"] for message in session["messages"]] == [
            "user",
            "assistant",
            "user",
            "assistant",
        ]
        assert session["messages"][0]["content"] == "first"
        assert session["messages"][2]["content"] == "second"
    finally:
        clear_overrides()


def test_ai_chat_streams_recoverable_tool_failure(tmp_path: Path) -> None:
    configure_app(tmp_path, failing_tool=True)
    try:
        client = TestClient(app)
        token = login(client)

        response = client.post(
            "/api/ai-chat/sessions/session_demo/messages",
            headers=auth_headers(token),
            json={"message": "try a failing tool"},
        )

        assert response.status_code == 200
        assert event_names(response.text) == [
            "tool.started",
            "tool.failed",
            "message.delta",
            "message.delta",
            "message.completed",
        ]
        assert "tool exploded" in response.text
        session = client.get(
            "/api/ai-chat/sessions/session_demo",
            headers=auth_headers(token),
        ).json()["session"]
        assert session["toolCalls"][0]["status"] == "failed"
        assert session["messages"][1]["status"] == "completed"
    finally:
        clear_overrides()


def test_ai_chat_rejects_blank_message(tmp_path: Path) -> None:
    configure_app(tmp_path)
    try:
        client = TestClient(app)
        token = login(client)

        response = client.post(
            "/api/ai-chat/sessions/session_demo/messages",
            headers=auth_headers(token),
            json={"message": "   "},
        )

        assert response.status_code == 422
    finally:
        clear_overrides()
