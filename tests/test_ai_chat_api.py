import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import geopandas as gpd  # type: ignore[import-untyped]
from fastapi.testclient import TestClient
from shapely.geometry import Point

from geo_agent_service.core.config import settings
from geo_agent_service.main import app
from geo_agent_service.modules.ai_chat.repository import AiChatRepository
from geo_agent_service.modules.ai_chat.routes import get_ai_chat_service
from geo_agent_service.modules.ai_chat.service import AiChatService
from geo_agent_service.modules.gis_data.repository import DatasetRepository
from geo_agent_service.modules.gis_data.schemas import DatasetRecord, FieldSummary, InputDataSummary
from geo_agent_service.modules.gis_data.service import GisDatasetService
from geo_agent_service.modules.gis_data.storage import GisDataStorage
from geo_agent_service.tools.base import GisTool, GisToolResult
from geo_agent_service.tools.registry import GisToolRegistry, create_default_tool_registry


class FakeModelClient:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []
        self.tool_results: list[dict[str, Any]] = []

    async def stream_response(
        self,
        *,
        messages: list[dict[str, str]],
        tool_results: list[dict[str, Any]],
    ) -> AsyncIterator[str]:
        self.messages = messages
        self.tool_results = tool_results
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
    name = "metadata_search"
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
        dataset_repository = DatasetRepository(storage.metadata_path())
        return AiChatService(
            repository=AiChatRepository(settings.ai_chat_storage_root),
            dataset_repository=dataset_repository,
            dataset_service=GisDatasetService(storage=storage, repository=dataset_repository),
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


def event_payloads(stream_text: str, event_name: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    current_event: str | None = None
    for line in stream_text.splitlines():
        if line.startswith("event: "):
            current_event = line.removeprefix("event: ")
        elif line.startswith("data: ") and current_event == event_name:
            payloads.append(json.loads(line.removeprefix("data: ")))
    return payloads


def write_dataset(storage: GisDataStorage) -> InputDataSummary:
    dataset_id = "dataset_schools"
    path = storage.normalized_path(dataset_id)
    path.write_text(
        """
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "properties": {"name": "A School", "type": "primary", "student_count": 100},
      "geometry": {"type": "Point", "coordinates": [116.1, 39.7]}
    },
    {
      "type": "Feature",
      "properties": {"name": "B School", "type": "middle", "student_count": 200},
      "geometry": {"type": "Point", "coordinates": [116.2, 39.8]}
    },
    {
      "type": "Feature",
      "properties": {"name": "C School", "type": "primary", "student_count": 150},
      "geometry": {"type": "Point", "coordinates": [116.3, 39.9]}
    }
  ]
}
        """.strip(),
        encoding="utf-8",
    )
    summary = InputDataSummary(
        datasetId=dataset_id,
        name="schools",
        sourceType="upload",
        geometryType="Point",
        crs=None,
        featureCount=3,
        bbox=(116.1, 39.7, 116.3, 39.9),
        fields=[
            FieldSummary(name="name", type="string"),
            FieldSummary(name="type", type="string", sampleValues=["primary", "middle"]),
            FieldSummary(name="student_count", type="number"),
        ],
        warnings=["CRS is missing; spatial distance and area calculations need confirmation."],
        dataRef=storage.normalized_uri(dataset_id),
    )
    DatasetRepository(storage.metadata_path()).save(
        DatasetRecord(
            summary=summary,
            rawUri=storage.upload_uri(dataset_id),
            normalizedUri=storage.normalized_uri(dataset_id),
        )
    )
    return summary


def write_sichuan_dataset(storage: GisDataStorage) -> InputDataSummary:
    dataset_id = "dataset_f2838ae521d6"
    path = storage.normalized_path(dataset_id)
    path.write_text(
        """
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "properties": {"name": "Alpha", "childrenNum": 3},
      "geometry": {"type": "Point", "coordinates": [104.1, 30.7]}
    },
    {
      "type": "Feature",
      "properties": {"name": "Beta", "childrenNum": 11},
      "geometry": {"type": "Point", "coordinates": [105.2, 31.8]}
    },
    {
      "type": "Feature",
      "properties": {"name": "Gamma", "childrenNum": 7},
      "geometry": {"type": "Point", "coordinates": [102.3, 29.9]}
    }
  ]
}
        """.strip(),
        encoding="utf-8",
    )
    summary = InputDataSummary(
        datasetId=dataset_id,
        name="四川省",
        sourceType="upload",
        geometryType="Point",
        crs="EPSG:4326",
        featureCount=3,
        bbox=(102.3, 29.9, 105.2, 31.8),
        fields=[
            FieldSummary(name="name", type="string"),
            FieldSummary(name="childrenNum", type="number"),
        ],
        dataRef=storage.normalized_uri(dataset_id),
    )
    DatasetRepository(storage.metadata_path()).save(
        DatasetRecord(
            summary=summary,
            rawUri=storage.upload_uri(dataset_id),
            normalizedUri=storage.normalized_uri(dataset_id),
        )
    )
    return summary


def write_crs_dataset(storage: GisDataStorage) -> InputDataSummary:
    repository = DatasetRepository(storage.metadata_path())
    service = GisDatasetService(storage=storage, repository=repository)
    return service.register_generated_dataset(
        name="schools",
        geodata=gpd.GeoDataFrame(
            {"name": ["A School", "B School"], "type": ["school", "hospital"]},
            geometry=[Point(116.1, 39.7), Point(116.2, 39.8)],
            crs="EPSG:4326",
        ),
        source_tool_call_id="test_setup",
    )


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
            "data.summary",
            "message.delta",
            "message.delta",
            "message.completed",
        ]
        assert "dataset_1" in response.text
        assert "AI summary from 0 tool result(s)" in response.text

        session_response = client.get(
            "/api/ai-chat/sessions/session_demo",
            headers=auth_headers(token),
        )
        assert session_response.status_code == 200
        session = session_response.json()["session"]
        assert session["status"] == "completed"
        assert [message["role"] for message in session["messages"]] == ["user", "assistant"]
        assert session["messages"][1]["content"] == "AI summary from 0 tool result(s)"
        assert session["toolCalls"] == []
    finally:
        clear_overrides()


def test_ai_chat_loads_sample_dataset_summary(tmp_path: Path) -> None:
    configure_app(tmp_path)
    try:
        client = TestClient(app)
        token = login(client)

        response = client.post(
            "/api/ai-chat/sessions/session_demo/messages",
            headers=auth_headers(token),
            json={"message": "看看机场图层", "selectedDatasetIds": ["sample_airports"]},
        )

        assert response.status_code == 200
        summary_event = event_payloads(response.text, "data.summary")[0]
        assert summary_event["data"]["missingDatasetIds"] == []
        [dataset] = summary_event["data"]["datasets"]
        assert dataset["datasetId"] == "sample_airports"
        assert dataset["geometryType"] == "Point"
        assert dataset["crs"] == "EPSG:4326"
        assert dataset["bbox"] is not None
        assert dataset["dataRef"] == "sample://sample_airports"
    finally:
        clear_overrides()


def test_ai_chat_merges_frontend_sample_layers_with_backend_summaries(tmp_path: Path) -> None:
    settings.auth_storage_root = str(tmp_path / "auth")
    settings.gis_storage_root = str(tmp_path / "gis")
    settings.ai_chat_storage_root = str(tmp_path / "ai-chat")
    settings.auth_username = "admin"
    settings.auth_password = "secret"
    settings.auth_token_secret = "test-secret"
    settings.auth_token_expire_minutes = 60
    storage = GisDataStorage(settings.gis_storage_root)
    model_client = FakeModelClient()

    def fake_service() -> AiChatService:
        dataset_repository = DatasetRepository(storage.metadata_path())
        return AiChatService(
            repository=AiChatRepository(settings.ai_chat_storage_root),
            dataset_repository=dataset_repository,
            dataset_service=GisDatasetService(storage=storage, repository=dataset_repository),
            tool_registry=create_default_tool_registry(
                dataset_repository=dataset_repository,
                storage=storage,
            ),
            model_client=model_client,
        )

    app.dependency_overrides[get_ai_chat_service] = fake_service
    try:
        client = TestClient(app)
        token = login(client)

        response = client.post(
            "/api/ai-chat/sessions/session_demo/messages",
            headers=auth_headers(token),
            json={
                "message": "请告诉我当前可用图层的数据摘要、字段、坐标系、几何类型和空间范围。",
                "selectedDatasetIds": [
                    "sample_airports",
                    "sample_ports",
                    "sample_populated_places",
                ],
                "metadata": {
                    "mapView": {"bbox": [-180, -90, 180, 90], "crs": "EPSG:4326"},
                    "layers": [
                        {
                            "id": "layer_sample_airports",
                            "datasetId": "sample_airports",
                            "name": "机场",
                            "geometryType": None,
                            "bbox": None,
                            "dataRef": "dataset:sample_airports",
                        }
                    ],
                },
            },
        )

        assert response.status_code == 200
        assert event_names(response.text) == [
            "data.summary",
            "tool.started",
            "tool.completed",
            "message.delta",
            "message.delta",
            "message.completed",
        ]
        assert '"toolName": "metadata_search"' in response.text
        assert '"toolName": "geoprocess"' not in response.text
        assert "layer.created" not in response.text
        assert '"datasetId": "sample_ports"' in response.text
        assert '"datasetId": "sample_populated_places"' in response.text
        assert '"crs": "EPSG:4326"' in response.text

        system_prompt = model_client.messages[0]["content"]
        assert '"id": "layer_sample_airports"' in system_prompt
        assert '"geometryType": "Point"' in system_prompt
        assert '"crs": "EPSG:4326"' in system_prompt
        assert '"dataRef": "sample://sample_airports"' in system_prompt
        assert "以 data.summary 为准" in system_prompt
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
            json={"message": "这个图层有哪些字段"},
        )

        assert response.status_code == 200
        assert event_names(response.text) == [
            "data.summary",
            "tool.started",
            "tool.failed",
            "message.delta",
            "message.delta",
            "message.delta",
            "message.completed",
        ]
        assert "tool exploded" in response.text
        assert "本轮曾发生工具调用失败" in response.text
        session = client.get(
            "/api/ai-chat/sessions/session_demo",
            headers=auth_headers(token),
        ).json()["session"]
        assert session["toolCalls"][0]["status"] == "failed"
        assert session["messages"][1]["status"] == "completed"
        assert "本轮曾发生工具调用失败" in session["messages"][1]["content"]
    finally:
        clear_overrides()


def test_ai_chat_default_tools_use_selected_dataset_summary_and_full_data(tmp_path: Path) -> None:
    settings.auth_storage_root = str(tmp_path / "auth")
    settings.gis_storage_root = str(tmp_path / "gis")
    settings.ai_chat_storage_root = str(tmp_path / "ai-chat")
    settings.auth_username = "admin"
    settings.auth_password = "secret"
    settings.auth_token_secret = "test-secret"
    settings.auth_token_expire_minutes = 60
    storage = GisDataStorage(settings.gis_storage_root)
    write_dataset(storage)

    def fake_service() -> AiChatService:
        dataset_repository = DatasetRepository(storage.metadata_path())
        return AiChatService(
            repository=AiChatRepository(settings.ai_chat_storage_root),
            dataset_repository=dataset_repository,
            dataset_service=GisDatasetService(storage=storage, repository=dataset_repository),
            tool_registry=create_default_tool_registry(
                dataset_repository=dataset_repository,
                storage=storage,
            ),
            model_client=FakeModelClient(),
        )

    app.dependency_overrides[get_ai_chat_service] = fake_service
    try:
        client = TestClient(app)
        token = login(client)

        response = client.post(
            "/api/ai-chat/sessions/session_demo/messages",
            headers=auth_headers(token),
            json={
                "message": "按 type 统计 schools 数量和 student_count 总和，有哪些字段",
                "selectedDatasetIds": ["dataset_schools"],
            },
        )

        assert response.status_code == 200
        assert event_names(response.text) == [
            "data.summary",
            "tool.started",
            "tool.completed",
            "tool.started",
            "tool.completed",
            "message.delta",
            "message.delta",
            "message.completed",
        ]
        assert '"type": "data.summary"' in response.text
        assert '"toolName": "metadata_search"' in response.text
        assert '"toolName": "attribute_summary"' in response.text
        assert '"type": "primary"' in response.text
        assert '"count": 2' in response.text
        assert '"student_count_sum": 250' in response.text
        session = client.get(
            "/api/ai-chat/sessions/session_demo",
            headers=auth_headers(token),
        ).json()["session"]
        assert session["dataSummaries"][0]["dataRef"].endswith(
            "/normalized/dataset_schools/data.geojson"
        )
        assert [tool_call["toolName"] for tool_call in session["toolCalls"]] == [
            "metadata_search",
            "attribute_summary",
        ]
    finally:
        clear_overrides()


def test_ai_chat_prefers_dataset_explicitly_named_in_message(tmp_path: Path) -> None:
    settings.auth_storage_root = str(tmp_path / "auth")
    settings.gis_storage_root = str(tmp_path / "gis")
    settings.ai_chat_storage_root = str(tmp_path / "ai-chat")
    settings.auth_username = "admin"
    settings.auth_password = "secret"
    settings.auth_token_secret = "test-secret"
    settings.auth_token_expire_minutes = 60
    storage = GisDataStorage(settings.gis_storage_root)
    write_sichuan_dataset(storage)
    model_client = FakeModelClient()

    def fake_service() -> AiChatService:
        dataset_repository = DatasetRepository(storage.metadata_path())
        return AiChatService(
            repository=AiChatRepository(settings.ai_chat_storage_root),
            dataset_repository=dataset_repository,
            dataset_service=GisDatasetService(storage=storage, repository=dataset_repository),
            tool_registry=create_default_tool_registry(
                dataset_repository=dataset_repository,
                storage=storage,
            ),
            model_client=model_client,
        )

    app.dependency_overrides[get_ai_chat_service] = fake_service
    try:
        client = TestClient(app)
        token = login(client)

        response = client.post(
            "/api/ai-chat/sessions/session_sichuan/messages",
            headers=auth_headers(token),
            json={
                "message": (
                    "请只使用 dataset_f2838ae521d6，不要读取或调用任何 sample_airports、"
                    "sample_ports、sample_populated_places 图层。请调用属性统计工具，"
                    "按 childrenNum 从高到低列出四川省地市。"
                ),
                "selectedDatasetIds": [
                    "sample_airports",
                    "sample_ports",
                    "sample_populated_places",
                    "dataset_f2838ae521d6",
                ],
                "metadata": {
                    "layers": [
                        {"id": "layer_sample_airports", "datasetId": "sample_airports"},
                        {"id": "layer_sichuan", "datasetId": "dataset_f2838ae521d6"},
                    ],
                },
            },
        )

        assert response.status_code == 200
        summary_event = event_payloads(response.text, "data.summary")[0]
        assert summary_event["data"]["effectiveDatasetIds"] == ["dataset_f2838ae521d6"]
        assert [
            dataset["datasetId"] for dataset in summary_event["data"]["datasets"]
        ] == ["dataset_f2838ae521d6"]

        tool_started = event_payloads(response.text, "tool.started")
        assert tool_started
        attribute_start = [
            event for event in tool_started if event["data"]["toolName"] == "attribute_summary"
        ][0]
        assert attribute_start["data"]["input"]["datasetIds"] == ["dataset_f2838ae521d6"]
        assert attribute_start["data"]["input"]["sortBy"] == "childrenNum"
        assert attribute_start["data"]["input"]["sortOrder"] == "desc"
        assert "sample_airports" not in attribute_start["data"]["input"]["datasetIds"]

        attribute_completed = [
            event
            for event in event_payloads(response.text, "tool.completed")
            if event["data"]["toolName"] == "attribute_summary"
        ][0]
        rows = attribute_completed["data"]["output"]["summary"]["rows"]
        assert [row["childrenNum"] for row in rows] == [11, 7, 3]

        system_prompt = model_client.messages[0]["content"]
        assert "dataset_f2838ae521d6 - 四川省" in system_prompt
        assert "sample_airports" not in system_prompt
    finally:
        clear_overrides()


def test_ai_chat_readiness_only_narrows_datasets_and_blocks_geoprocess(
    tmp_path: Path,
) -> None:
    settings.auth_storage_root = str(tmp_path / "auth")
    settings.gis_storage_root = str(tmp_path / "gis")
    settings.ai_chat_storage_root = str(tmp_path / "ai-chat")
    settings.auth_username = "admin"
    settings.auth_password = "secret"
    settings.auth_token_secret = "test-secret"
    settings.auth_token_expire_minutes = 60
    storage = GisDataStorage(settings.gis_storage_root)
    write_sichuan_dataset(storage)

    def fake_service() -> AiChatService:
        dataset_repository = DatasetRepository(storage.metadata_path())
        return AiChatService(
            repository=AiChatRepository(settings.ai_chat_storage_root),
            dataset_repository=dataset_repository,
            dataset_service=GisDatasetService(storage=storage, repository=dataset_repository),
            tool_registry=create_default_tool_registry(
                dataset_repository=dataset_repository,
                storage=storage,
            ),
            model_client=FakeModelClient(),
        )

    app.dependency_overrides[get_ai_chat_service] = fake_service
    try:
        client = TestClient(app)
        token = login(client)

        response = client.post(
            "/api/ai-chat/sessions/session_sichuan_airports/messages",
            headers=auth_headers(token),
            json={
                "message": (
                    "请只使用 dataset_f2838ae521d6 和 sample_airports，不要读取 "
                    "sample_ports、sample_populated_places 或任何 dataset_31f2a63f830d / "
                    "dataset_759c50dc2a47。只判断这两个图层是否适合做“四川省内机场筛选”，"
                    "不要执行筛选，不要调用 attribute_filter。"
                ),
                "selectedDatasetIds": [
                    "sample_airports",
                    "sample_ports",
                    "sample_populated_places",
                    "dataset_f2838ae521d6",
                    "dataset_31f2a63f830d",
                    "dataset_759c50dc2a47",
                ],
                "metadata": {
                    "layers": [
                        {"id": "layer_sample_airports", "datasetId": "sample_airports"},
                        {"id": "layer_sample_ports", "datasetId": "sample_ports"},
                        {
                            "id": "layer_sample_populated_places",
                            "datasetId": "sample_populated_places",
                        },
                        {"id": "layer_sichuan", "datasetId": "dataset_f2838ae521d6"},
                        {"id": "layer_old_1", "datasetId": "dataset_31f2a63f830d"},
                        {"id": "layer_old_2", "datasetId": "dataset_759c50dc2a47"},
                    ],
                },
            },
        )

        assert response.status_code == 200
        assert event_names(response.text) == [
            "data.summary",
            "tool.started",
            "tool.completed",
            "message.delta",
            "message.delta",
            "message.completed",
        ]

        summary_event = event_payloads(response.text, "data.summary")[0]
        assert summary_event["data"]["availableDatasetIds"] == [
            "sample_airports",
            "sample_ports",
            "sample_populated_places",
            "dataset_f2838ae521d6",
            "dataset_31f2a63f830d",
            "dataset_759c50dc2a47",
        ]
        assert summary_event["data"]["selectedDatasetIds"] == [
            "sample_airports",
            "sample_ports",
            "sample_populated_places",
            "dataset_f2838ae521d6",
            "dataset_31f2a63f830d",
            "dataset_759c50dc2a47",
        ]
        assert summary_event["data"]["effectiveDatasetIds"] == [
            "dataset_f2838ae521d6",
            "sample_airports",
        ]
        assert [
            dataset["datasetId"] for dataset in summary_event["data"]["datasets"]
        ] == ["dataset_f2838ae521d6", "sample_airports"]

        started_events = event_payloads(response.text, "tool.started")
        assert [event["data"]["toolName"] for event in started_events] == [
            "metadata_search"
        ]
        metadata_input = started_events[0]["data"]["input"]
        assert metadata_input["datasetIds"] == [
            "dataset_f2838ae521d6",
            "sample_airports",
        ]
        assert metadata_input["effectiveDatasetIds"] == [
            "dataset_f2838ae521d6",
            "sample_airports",
        ]
        assert '"operation": "attribute_filter"' not in response.text
        assert '"toolName": "geoprocess"' not in response.text
    finally:
        clear_overrides()


def test_ai_chat_plan_only_emits_plan_created_without_tools(tmp_path: Path) -> None:
    settings.auth_storage_root = str(tmp_path / "auth")
    settings.gis_storage_root = str(tmp_path / "gis")
    settings.ai_chat_storage_root = str(tmp_path / "ai-chat")
    settings.auth_username = "admin"
    settings.auth_password = "secret"
    settings.auth_token_secret = "test-secret"
    settings.auth_token_expire_minutes = 60
    storage = GisDataStorage(settings.gis_storage_root)
    write_sichuan_dataset(storage)
    model_client = FakeModelClient()

    def fake_service() -> AiChatService:
        dataset_repository = DatasetRepository(storage.metadata_path())
        return AiChatService(
            repository=AiChatRepository(settings.ai_chat_storage_root),
            dataset_repository=dataset_repository,
            dataset_service=GisDatasetService(storage=storage, repository=dataset_repository),
            tool_registry=create_default_tool_registry(
                dataset_repository=dataset_repository,
                storage=storage,
            ),
            model_client=model_client,
        )

    app.dependency_overrides[get_ai_chat_service] = fake_service
    try:
        client = TestClient(app)
        token = login(client)

        response = client.post(
            "/api/ai-chat/sessions/session_sichuan_plan/messages",
            headers=auth_headers(token),
            json={
                "message": (
                    "请只使用 dataset_f2838ae521d6 和 sample_airports，找出四川省范围内的所有机场。"
                    "执行前先生成分步骤计划，计划中区分数据准备、空间计算、结果输出，"
                    "不要立即执行工具。"
                ),
                "selectedDatasetIds": [
                    "sample_airports",
                    "sample_ports",
                    "sample_populated_places",
                    "dataset_f2838ae521d6",
                ],
                "metadata": {
                    "activeDatasetIds": [
                        "sample_airports",
                        "sample_ports",
                        "sample_populated_places",
                        "dataset_f2838ae521d6",
                    ],
                    "layers": [
                        {"id": "layer_sample_airports", "datasetId": "sample_airports"},
                        {"id": "layer_sample_ports", "datasetId": "sample_ports"},
                        {
                            "id": "layer_sample_populated_places",
                            "datasetId": "sample_populated_places",
                        },
                        {"id": "layer_sichuan", "datasetId": "dataset_f2838ae521d6"},
                    ],
                },
            },
        )

        assert response.status_code == 200
        assert event_names(response.text) == [
            "data.summary",
            "plan.created",
            "message.completed",
        ]
        assert event_payloads(response.text, "tool.started") == []
        assert model_client.messages == []

        summary_event = event_payloads(response.text, "data.summary")[0]
        assert summary_event["data"]["effectiveDatasetIds"] == [
            "dataset_f2838ae521d6",
            "sample_airports",
        ]

        plan_event = event_payloads(response.text, "plan.created")[0]
        plan_data = plan_event["data"]
        assert plan_data["type"] == "plan.created"
        assert [step["id"] for step in plan_data["steps"]] == [
            "data-prep",
            "spatial-calc",
            "result-output",
        ]
        assert [step["kind"] for step in plan_data["steps"]] == [
            "data_preparation",
            "deterministic_gis",
            "visualization_or_report",
        ]
        assert plan_data["steps"][0]["expectedInputs"] == [
            "dataset_f2838ae521d6",
            "sample_airports",
        ]
        assert plan_data["steps"][1]["toolCandidates"] == [
            "within",
            "intersects",
            "spatial_join",
        ]
        assert plan_data["steps"][2]["expectedOutputs"] == [
            "GeoJSON FeatureCollection",
            "结果摘要",
            "样本记录",
        ]
    finally:
        clear_overrides()


def test_ai_chat_geoprocess_buffer_creates_layer_and_map_command(tmp_path: Path) -> None:
    settings.auth_storage_root = str(tmp_path / "auth")
    settings.gis_storage_root = str(tmp_path / "gis")
    settings.ai_chat_storage_root = str(tmp_path / "ai-chat")
    settings.auth_username = "admin"
    settings.auth_password = "secret"
    settings.auth_token_secret = "test-secret"
    settings.auth_token_expire_minutes = 60
    storage = GisDataStorage(settings.gis_storage_root)
    source_summary = write_crs_dataset(storage)

    def fake_service() -> AiChatService:
        dataset_repository = DatasetRepository(storage.metadata_path())
        return AiChatService(
            repository=AiChatRepository(settings.ai_chat_storage_root),
            dataset_repository=dataset_repository,
            dataset_service=GisDatasetService(storage=storage, repository=dataset_repository),
            tool_registry=create_default_tool_registry(
                dataset_repository=dataset_repository,
                storage=storage,
            ),
            model_client=FakeModelClient(),
        )

    app.dependency_overrides[get_ai_chat_service] = fake_service
    try:
        client = TestClient(app)
        token = login(client)

        response = client.post(
            "/api/ai-chat/sessions/session_demo/messages",
            headers=auth_headers(token),
            json={
                "message": "给 schools 做 500 米缓冲区并显示",
                "selectedDatasetIds": [source_summary.dataset_id],
            },
        )

        assert response.status_code == 200
        assert event_names(response.text) == [
            "data.summary",
            "tool.started",
            "tool.completed",
            "layer.created",
            "map.command",
            "message.delta",
            "message.delta",
            "message.completed",
        ]
        assert '"toolName": "geoprocess"' in response.text
        assert '"operation": "buffer"' in response.text
        assert '"action": "layer.addDataset"' in response.text

        session = client.get(
            "/api/ai-chat/sessions/session_demo",
            headers=auth_headers(token),
        ).json()["session"]
        tool_call = session["toolCalls"][0]
        result_dataset_id = tool_call["output"]["summary"]["resultDatasetId"]
        assert tool_call["toolName"] == "geoprocess"
        assert result_dataset_id.startswith("dataset_")

        preview_response = client.get(f"/api/datasets/{result_dataset_id}/preview")
        assert preview_response.status_code == 200
        preview = preview_response.json()
        assert preview["featureCount"] == 2
        assert preview["data"]["features"][0]["geometry"]["type"] in {
            "Polygon",
            "MultiPolygon",
        }
    finally:
        clear_overrides()


def test_ai_chat_geoprocess_attribute_filter_creates_filtered_dataset(tmp_path: Path) -> None:
    settings.auth_storage_root = str(tmp_path / "auth")
    settings.gis_storage_root = str(tmp_path / "gis")
    settings.ai_chat_storage_root = str(tmp_path / "ai-chat")
    settings.auth_username = "admin"
    settings.auth_password = "secret"
    settings.auth_token_secret = "test-secret"
    settings.auth_token_expire_minutes = 60
    storage = GisDataStorage(settings.gis_storage_root)
    source_summary = write_crs_dataset(storage)

    def fake_service() -> AiChatService:
        dataset_repository = DatasetRepository(storage.metadata_path())
        return AiChatService(
            repository=AiChatRepository(settings.ai_chat_storage_root),
            dataset_repository=dataset_repository,
            dataset_service=GisDatasetService(storage=storage, repository=dataset_repository),
            tool_registry=create_default_tool_registry(
                dataset_repository=dataset_repository,
                storage=storage,
            ),
            model_client=FakeModelClient(),
        )

    app.dependency_overrides[get_ai_chat_service] = fake_service
    try:
        client = TestClient(app)
        token = login(client)

        response = client.post(
            "/api/ai-chat/sessions/session_filter/messages",
            headers=auth_headers(token),
            json={
                "message": "筛选 type 等于 school 的要素并显示",
                "selectedDatasetIds": [source_summary.dataset_id],
            },
        )

        assert response.status_code == 200
        assert event_names(response.text) == [
            "data.summary",
            "tool.started",
            "tool.completed",
            "layer.created",
            "map.command",
            "message.delta",
            "message.delta",
            "message.completed",
        ]
        assert '"operation": "attribute_filter"' in response.text
        assert '"field": "type"' in response.text
        assert '"value": "school"' in response.text

        session = client.get(
            "/api/ai-chat/sessions/session_filter",
            headers=auth_headers(token),
        ).json()["session"]
        result_dataset_id = session["toolCalls"][0]["output"]["summary"]["resultDatasetId"]
        preview_response = client.get(f"/api/datasets/{result_dataset_id}/preview")
        assert preview_response.status_code == 200
        features = preview_response.json()["data"]["features"]
        assert len(features) == 1
        assert features[0]["properties"]["type"] == "school"
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
