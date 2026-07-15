import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import geopandas as gpd  # type: ignore[import-untyped]
from fastapi.testclient import TestClient
from shapely.geometry import Point, Polygon  # type: ignore[import-untyped]
from sqlalchemy import create_engine

from geo_agent_service.core.config import settings
from geo_agent_service.main import app
from geo_agent_service.modules.ai_chat.repository import AiChatRepository
from geo_agent_service.modules.ai_chat.routes import get_ai_chat_service
from geo_agent_service.modules.ai_chat.run_repository import AgentRunRepository
from geo_agent_service.modules.ai_chat.service import AiChatService
from geo_agent_service.modules.gis_data.repository import DatasetRepository
from geo_agent_service.modules.gis_data.schemas import (
    DatasetRecord,
    FieldSummary,
    InputDataSummary,
)
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


def configure_app(
    tmp_path: Path,
    *,
    failing_tool: bool = False,
    persist_runs: bool = False,
) -> None:
    settings.auth_storage_root = str(tmp_path / "auth")
    settings.gis_storage_root = str(tmp_path / "gis")
    settings.ai_chat_storage_root = str(tmp_path / "ai-chat")
    settings.auth_username = "admin"
    settings.auth_password = "secret"
    settings.auth_token_secret = "test-secret"
    settings.auth_token_expire_minutes = 60
    settings.database_url = f"sqlite:///{tmp_path / 'agent-runs.sqlite'}"

    def fake_service() -> AiChatService:
        registry = GisToolRegistry()
        registry.register(FailingTool() if failing_tool else EchoTool())
        storage = GisDataStorage(settings.gis_storage_root)
        dataset_repository = DatasetRepository(storage.metadata_path())
        run_repository = (
            AgentRunRepository(create_engine(settings.database_url))
            if persist_runs
            else None
        )
        return AiChatService(
            repository=AiChatRepository(settings.ai_chat_storage_root),
            dataset_repository=dataset_repository,
            dataset_service=GisDatasetService(storage=storage, repository=dataset_repository),
            tool_registry=registry,
            run_repository=run_repository,
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


def write_sichuan_polygon_dataset(storage: GisDataStorage) -> InputDataSummary:
    dataset_id = "dataset_f2838ae521d6"
    path = storage.normalized_path(dataset_id)
    path.write_text(
        """
{
  "type": "FeatureCollection",
  "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
  "features": [
    {
      "type": "Feature",
      "properties": {"name": "四川省"},
      "geometry": {
        "type": "Polygon",
        "coordinates": [[
          [97.35, 26.04],
          [108.55, 26.04],
          [108.55, 34.32],
          [97.35, 34.32],
          [97.35, 26.04]
        ]]
      }
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
        geometryType="Polygon",
        crs="EPSG:4326",
        featureCount=1,
        bbox=(97.35, 26.04, 108.55, 34.32),
        fields=[FieldSummary(name="name", type="string")],
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


def write_sichuan_city_dataset(storage: GisDataStorage) -> InputDataSummary:
    service = GisDatasetService(
        storage=storage,
        repository=DatasetRepository(storage.metadata_path()),
    )
    return service.register_generated_dataset(
        name="四川省",
        geodata=gpd.GeoDataFrame(
            {"name": ["德阳市", "成都市"]},
            geometry=[Point(104.4, 31.1), Point(104.1, 30.7)],
            crs="EPSG:4326",
        ),
        source_tool_call_id="test_setup",
    )


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


def write_airport_spatial_filter_result(
    storage: GisDataStorage,
    dataset_id: str = "dataset_00eb6853cff3",
) -> InputDataSummary:
    path = storage.normalized_path(dataset_id)
    path.write_text(
        """
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "properties": {"name": "Chengdu Shuangliu", "iata_code": "CTU", "type": "airport"},
      "geometry": {"type": "Point", "coordinates": [103.95613648169501, 30.581071264746427]}
    }
  ]
}
        """.strip(),
        encoding="utf-8",
    )
    summary = InputDataSummary(
        datasetId=dataset_id,
        name="机场 空间筛选",
        sourceType="generated",
        geometryType="Point",
        crs="EPSG:4326",
        featureCount=1,
        bbox=(
            103.95613648169501,
            30.581071264746427,
            103.95613648169501,
            30.581071264746427,
        ),
        fields=[
            FieldSummary(name="name", type="string"),
            FieldSummary(name="iata_code", type="string"),
            FieldSummary(name="type", type="string"),
        ],
        dataRef=storage.normalized_uri(dataset_id),
        lineage={
            "operation": "spatial_filter",
            "inputDatasetId": "sample_airports",
            "maskDatasetId": "dataset_f2838ae521d6",
            "predicate": "within",
            "outputFields": ["name", "iata_code", "type"],
        },
    )
    DatasetRepository(storage.metadata_path()).save(
        DatasetRecord(
            summary=summary,
            rawUri=storage.upload_uri(dataset_id),
            normalizedUri=storage.normalized_uri(dataset_id),
        )
    )
    return summary


def write_invalid_airport_self_filter_result(
    storage: GisDataStorage,
    dataset_id: str = "dataset_a9680b3cf0b8",
) -> InputDataSummary:
    path = storage.normalized_path(dataset_id)
    path.write_text(
        """
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "properties": {"name": "Bad self filter", "iata_code": "BAD", "type": "airport"},
      "geometry": {"type": "Point", "coordinates": [0, 0]}
    }
  ]
}
        """.strip(),
        encoding="utf-8",
    )
    summary = InputDataSummary(
        datasetId=dataset_id,
        name="机场 空间筛选",
        sourceType="generated",
        geometryType="Point",
        crs="EPSG:4326",
        featureCount=281,
        bbox=(-175.135635, -53.005069825517666, 178.5600483699593, 71.289299),
        fields=[
            FieldSummary(name="name", type="string"),
            FieldSummary(name="iata_code", type="string"),
            FieldSummary(name="type", type="string"),
        ],
        dataRef=storage.normalized_uri(dataset_id),
        lineage={
            "operation": "spatial_filter",
            "inputDatasetId": "sample_airports",
            "maskDatasetId": "sample_airports",
            "predicate": "within",
            "outputFields": ["name", "iata_code", "type"],
        },
    )
    DatasetRepository(storage.metadata_path()).save(
        DatasetRecord(
            summary=summary,
            rawUri=storage.upload_uri(dataset_id),
            normalizedUri=storage.normalized_uri(dataset_id),
        )
    )
    return summary


def write_airport_buffer_result(
    storage: GisDataStorage,
    *,
    dataset_id: str,
    tool_call_id: str,
) -> InputDataSummary:
    path = storage.normalized_path(dataset_id)
    geodata = gpd.GeoDataFrame(
        {"name": ["Chengdu Shuangliu buffer"]},
        geometry=[
            Polygon(
                [
                    (103.43480642640635, 30.12994002309082),
                    (104.47757568081893, 30.12994002309082),
                    (104.47757568081893, 31.032170519011135),
                    (103.43480642640635, 31.032170519011135),
                    (103.43480642640635, 30.12994002309082),
                ]
            )
        ],
        crs="EPSG:4326",
    )
    geodata.to_file(path, driver="GeoJSON")
    summary = InputDataSummary(
        datasetId=dataset_id,
        name="机场 空间筛选 缓冲区",
        sourceType="generated",
        geometryType="Polygon",
        crs="EPSG:4326",
        featureCount=1,
        bbox=(
            103.43480642640635,
            30.12994002309082,
            104.47757568081893,
            31.032170519011135,
        ),
        fields=[FieldSummary(name="name", type="string")],
        dataRef=storage.normalized_uri(dataset_id),
        lineage={
            "operation": "buffer",
            "sourceDatasetId": "dataset_00eb6853cff3",
            "inputDatasetId": "dataset_00eb6853cff3",
            "distance": 50000,
            "unit": "meters",
            "processingCRS": "EPSG:32648",
            "toolCallId": tool_call_id,
        },
    )
    DatasetRepository(storage.metadata_path()).save(
        DatasetRecord(
            summary=summary,
            rawUri=storage.upload_uri(dataset_id),
            normalizedUri=storage.normalized_uri(dataset_id),
        )
    )
    return summary


def write_population_spatial_filter_result(
    storage: GisDataStorage,
    *,
    dataset_id: str = "dataset_16fb343ba5e6",
    pop_max: int = 13568357,
) -> InputDataSummary:
    path = storage.normalized_path(dataset_id)
    path.write_text(
        """
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "properties": {"NAME": "Chengdu", "NAME_ZH": "成都", "POP_MAX": POP_MAX_VALUE},
      "geometry": {"type": "Point", "coordinates": [104.0680736, 30.6719459]}
    }
  ]
}
        """.replace("POP_MAX_VALUE", str(pop_max)).strip(),
        encoding="utf-8",
    )
    summary = InputDataSummary(
        datasetId=dataset_id,
        name="人口稠密地区 空间筛选",
        sourceType="generated",
        geometryType="Point",
        crs="EPSG:4326",
        featureCount=1,
        bbox=(104.0680736, 30.6719459, 104.0680736, 30.6719459),
        fields=[
            FieldSummary(name="NAME", type="string"),
            FieldSummary(name="NAME_ZH", type="string"),
            FieldSummary(name="POP_MAX", type="number"),
        ],
        dataRef=storage.normalized_uri(dataset_id),
        lineage={
            "operation": "spatial_filter",
            "inputDatasetId": "sample_populated_places",
            "maskDatasetId": "dataset_bb1fc4102e6d",
            "predicate": "within",
            "outputFields": ["NAME", "NAME_ZH", "POP_MAX"],
        },
    )
    DatasetRepository(storage.metadata_path()).save(
        DatasetRecord(
            summary=summary,
            rawUri=storage.upload_uri(dataset_id),
            normalizedUri=storage.normalized_uri(dataset_id),
        )
    )
    return summary


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


def test_ai_chat_persists_completed_run_and_event_log(tmp_path: Path) -> None:
    configure_app(tmp_path, persist_runs=True)
    try:
        client = TestClient(app)
        token = login(client)

        response = client.post(
            "/api/ai-chat/sessions/session_demo/messages",
            headers=auth_headers(token),
            json={"message": "hello", "selectedDatasetIds": ["missing_dataset"]},
        )

        assert response.status_code == 200
        names = event_names(response.text)
        summary_event = event_payloads(response.text, "data.summary")[0]
        run_id = summary_event["runId"]

        runs_response = client.get(
            "/api/ai-chat/sessions/session_demo/runs",
            headers=auth_headers(token),
        )
        assert runs_response.status_code == 200
        runs = runs_response.json()["runs"]
        assert len(runs) == 1
        assert runs[0]["runId"] == run_id
        assert runs[0]["status"] == "completed"
        assert runs[0]["intent"]["task_type"] == "report"
        assert runs[0]["dataReadiness"]["status"] == "partial"
        assert runs[0]["dataReadiness"]["missing_dataset_ids"] == ["missing_dataset"]

        run_response = client.get(
            f"/api/ai-chat/sessions/session_demo/runs/{run_id}",
            headers=auth_headers(token),
        )
        assert run_response.status_code == 200
        run = run_response.json()["run"]
        assert [event["type"] for event in run["events"]] == names
        assert [event["sequence"] for event in run["events"]] == list(
            range(1, len(names) + 1)
        )
        assert run["events"][0]["payload"]["runId"] == run_id
    finally:
        clear_overrides()


def test_ai_chat_persists_failed_run(tmp_path: Path) -> None:
    configure_app(tmp_path, failing_tool=True, persist_runs=True)
    try:
        client = TestClient(app)
        token = login(client)

        response = client.post(
            "/api/ai-chat/sessions/session_demo/messages",
            headers=auth_headers(token),
            json={"message": "字段有哪些", "selectedDatasetIds": ["dataset_1"]},
        )

        assert response.status_code == 200
        run_id = event_payloads(response.text, "data.summary")[0]["runId"]
        run_response = client.get(
            f"/api/ai-chat/sessions/session_demo/runs/{run_id}",
            headers=auth_headers(token),
        )

        assert run_response.status_code == 200
        run = run_response.json()["run"]
        assert run["status"] == "failed"
        assert run["toolResults"][0]["status"] == "failed"
        assert [event["type"] for event in run["events"]] == [
            "data.summary",
            "tool.started",
            "tool.failed",
            "message.delta",
            "message.completed",
        ]
    finally:
        clear_overrides()


def test_ai_chat_persists_plan_only_run_state(tmp_path: Path) -> None:
    configure_app(tmp_path, persist_runs=True)
    try:
        client = TestClient(app)
        token = login(client)

        response = client.post(
            "/api/ai-chat/sessions/session_plan/messages",
            headers=auth_headers(token),
            json={
                "message": "生成分析计划，只生成计划，不要执行任何工具。",
                "selectedDatasetIds": ["dataset_1"],
            },
        )

        assert response.status_code == 200
        run_id = event_payloads(response.text, "data.summary")[0]["runId"]
        run_response = client.get(
            f"/api/ai-chat/sessions/session_plan/runs/{run_id}",
            headers=auth_headers(token),
        )

        assert run_response.status_code == 200
        run = run_response.json()["run"]
        assert run["status"] == "completed"
        assert run["intent"]["requires_plan_only"] is True
        assert run["toolPlan"]["execute"] is False
        assert run["toolPlan"]["reason"] == "plan_only_request"
        assert "plan.created" in [event["type"] for event in run["events"]]
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


def test_ai_chat_selected_layer_metadata_with_feature_count_is_read_only(
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
            "/api/ai-chat/sessions/session_selected_metadata/messages",
            headers=auth_headers(token),
            json={
                "message": (
                    "请说明当前已选图层的数据集 ID、图层 ID、名称、几何类型、"
                    "CRS、bbox、字段和要素数量"
                ),
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
                            "layerId": "layer_sample_airports",
                            "datasetId": "sample_airports",
                            "name": "机场",
                            "geometryType": "Point",
                            "bbox": [
                                -175.135635,
                                -53.005069825517666,
                                178.5600483699593,
                                71.289299,
                            ],
                            "dataRef": "dataset:sample_airports",
                        }
                    ],
                },
            },
        )

        assert response.status_code == 200
        assert '"toolName": "metadata_search"' in response.text
        assert '"toolName": "attribute_summary"' not in response.text
        assert '"type": "error"' not in response.text
        assert "Dataset not found: sample_airports" not in response.text
        assert "本轮工具调用失败" not in response.text
        summary_event = event_payloads(response.text, "data.summary")[0]
        assert summary_event["data"]["missingDatasetIds"] == []
        assert summary_event["data"]["datasets"][0]["featureCount"] == 281
        completed_message = event_payloads(response.text, "message.completed")[0]["data"][
            "message"
        ]["content"]
        assert "图层ID=layer_sample_airports" in completed_message
        assert "数据集ID=sample_airports" in completed_message
        assert "要素数量=281" in completed_message
        assert "字段=scalerank" in completed_message
        assert model_client.messages == []
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
            "message.completed",
        ]
        assert "tool exploded" in response.text
        assert "本轮工具调用失败" in response.text
        assert "不会返回 resultDatasetId、featureCount、bbox 或样本记录" in response.text
        session = client.get(
            "/api/ai-chat/sessions/session_demo",
            headers=auth_headers(token),
        ).json()["session"]
        assert session["toolCalls"][0]["status"] == "failed"
        assert session["messages"][1]["status"] == "completed"
        assert "本轮工具调用失败" in session["messages"][1]["content"]
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


def test_ai_chat_attribute_summary_on_named_result_layer_does_not_spatial_filter(
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
    result_dataset_id = "dataset_dce200755654"
    write_population_spatial_filter_result(
        storage,
        dataset_id=result_dataset_id,
        pop_max=4123000,
    )
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
            "/api/ai-chat/sessions/session_population_summary/messages",
            headers=auth_headers(token),
            json={
                "message": (
                    "请基于“人口稠密地区 空间筛选”结果图层，"
                    "统计 POP_MAX 总人口、平均人口、最大值和最小值。"
                    "只对这个结果图层执行属性统计，不要重新执行空间筛选。"
                ),
                "selectedDatasetIds": [
                    "sample_airports",
                    "sample_ports",
                    "sample_populated_places",
                    "dataset_f2838ae521d6",
                    "dataset_00eb6853cff3",
                    "dataset_bae7bf3355a7",
                    "dataset_bb1fc4102e6d",
                    result_dataset_id,
                ],
                "metadata": {
                    "layers": [
                        {
                            "id": "layer_WWHuPgqiEQf4cxBU",
                            "layerId": "layer_WWHuPgqiEQf4cxBU",
                            "datasetId": result_dataset_id,
                            "name": "人口稠密地区 空间筛选",
                            "visible": True,
                            "geometryType": "Point",
                        },
                    ],
                    "activeDatasetIds": [
                        "sample_airports",
                        "sample_ports",
                        "sample_populated_places",
                        "dataset_f2838ae521d6",
                        "dataset_00eb6853cff3",
                        "dataset_bae7bf3355a7",
                        "dataset_bb1fc4102e6d",
                        result_dataset_id,
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
            "message.completed",
        ]
        assert event_payloads(response.text, "layer.created") == []
        assert event_payloads(response.text, "map.command") == []

        started = event_payloads(response.text, "tool.started")[0]["data"]
        assert started["toolName"] == "attribute_summary"
        tool_input = started["input"]
        assert tool_input["datasetId"] == result_dataset_id
        assert tool_input["datasetIds"] == [result_dataset_id]
        assert tool_input["field"] == "POP_MAX"
        assert tool_input["statistics"] == ["sum", "mean", "max", "min"]
        assert "groupBy" not in tool_input
        assert "inputDatasetId" not in tool_input
        assert "maskDatasetId" not in tool_input

        completed = event_payloads(response.text, "tool.completed")[0]["data"]
        assert completed["toolName"] == "attribute_summary"
        pop_max = [
            field
            for field in completed["output"]["summary"]["fields"]
            if field["name"] == "POP_MAX"
        ][0]
        assert pop_max["sum"] == 4123000
        assert pop_max["mean"] == 4123000
        assert pop_max["max"] == 4123000
        assert pop_max["min"] == 4123000

        message = event_payloads(response.text, "message.completed")[0]["data"]["message"][
            "content"
        ]
        assert "attribute_summary 已执行完成" in message
        assert f"datasetId={result_dataset_id}" in message
        assert "field=POP_MAX" in message
        assert "总人口=4123000" in message
        assert "未触发任何空间操作" not in message
        assert model_client.tool_results == []
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
        assert '"toolName": "spatial_filter"' not in response.text
        assert event_payloads(response.text, "layer.created") == []
        assert event_payloads(response.text, "map.command") == []
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
        assert "未执行筛选" in response.text
        assert model_client.messages == []
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


def test_ai_chat_plan_only_buffer_uses_existing_result_dataset_without_tools(
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
    write_sichuan_polygon_dataset(storage)
    write_airport_spatial_filter_result(storage)
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
            "/api/ai-chat/sessions/session_8091e24d-3369-4c3c-83ca-d78d8ab59d7b/messages",
            headers=auth_headers(token),
            json={
                "message": (
                    "请基于刚才的“机场 空间筛选”结果图层 dataset_00eb6853cff3，"
                    "生成 50 公里缓冲区分析计划。只生成计划，不要执行任何工具。"
                    "计划必须分为：数据准备、坐标系与距离单位处理、缓冲区计算、结果输出。"
                    "请明确说明不会重新执行机场筛选，也不会生成新图层。"
                ),
                "selectedDatasetIds": [
                    "sample_airports",
                    "sample_ports",
                    "sample_populated_places",
                    "dataset_f2838ae521d6",
                    "dataset_00eb6853cff3",
                    "dataset_bae7bf3355a7",
                ],
                "metadata": {
                    "mapView": {
                        "center": [103.954932, 30.582716],
                        "height": 3845.92,
                        "bbox": [103.931775, 30.568767, 103.978088, 30.59666],
                        "crs": "EPSG:4326",
                    },
                    "layers": [
                        {
                            "id": "layer_58Xhznd_LAd_zfaP",
                            "layerId": "layer_58Xhznd_LAd_zfaP",
                            "datasetId": "dataset_00eb6853cff3",
                            "name": "机场 空间筛选",
                            "visible": True,
                            "opacity": 1,
                            "geometryType": "Point",
                            "bbox": [
                                103.95613648169501,
                                30.581071264746427,
                                103.95613648169501,
                                30.581071264746427,
                            ],
                            "dataRef": "storage://normalized/dataset_00eb6853cff3/data.geojson",
                        }
                    ],
                    "activeDatasetIds": [
                        "sample_airports",
                        "sample_ports",
                        "sample_populated_places",
                        "dataset_f2838ae521d6",
                        "dataset_00eb6853cff3",
                        "dataset_bae7bf3355a7",
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
        assert event_payloads(response.text, "tool.completed") == []
        assert event_payloads(response.text, "layer.created") == []
        assert event_payloads(response.text, "map.command") == []
        assert "resultDatasetId" not in response.text
        assert model_client.messages == []

        summary_event = event_payloads(response.text, "data.summary")[0]
        assert summary_event["data"]["effectiveDatasetIds"] == [
            "dataset_00eb6853cff3"
        ]
        assert summary_event["data"]["datasets"][0]["datasetId"] == "dataset_00eb6853cff3"

        plan_data = event_payloads(response.text, "plan.created")[0]["data"]
        assert plan_data["planType"] == "buffer_analysis"
        assert plan_data["targetDatasetId"] == "dataset_00eb6853cff3"
        assert plan_data["distance"] == 50
        assert plan_data["unit"] == "km"
        assert plan_data["execute"] is False
        assert [step["title"] for step in plan_data["steps"]] == [
            "数据准备",
            "坐标系与距离单位处理",
            "缓冲区计算",
            "结果输出",
        ]
        crs_step = plan_data["steps"][1]["description"]
        assert "严格等距投影" not in crs_step
        assert "本地米制投影，适合局部距离计算" in crs_step
        assert "方位等距投影或 geodesic buffer" in crs_step
        assert plan_data["steps"][2]["parameters"] == {
            "distance": 50,
            "unit": "km",
        }

        completed_message = event_payloads(response.text, "message.completed")[0]["data"][
            "message"
        ]["content"]
        assert "只使用 dataset_00eb6853cff3" in completed_message
        assert "不重新执行机场筛选" in completed_message
        assert "不生成新图层" in completed_message
    finally:
        clear_overrides()


def test_ai_chat_plan_only_buffer_resolves_recent_airport_filter_layer_from_metadata(
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
    write_sichuan_polygon_dataset(storage)
    write_airport_spatial_filter_result(storage, dataset_id="dataset_80a075f44398")
    target_summary = write_airport_spatial_filter_result(
        storage,
        dataset_id="dataset_2eaf58343584",
    )
    write_airport_spatial_filter_result(storage, dataset_id="dataset_509febada353")
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
            "/api/ai-chat/sessions/session_26063812-dcbe-42b7-b398-afd63b7eb285/messages",
            headers=auth_headers(token),
            json={
                "message": (
                    "基于刚才生成的机场 空间筛选结果图层，生成 50 公里缓冲区分析计划。"
                    "只生成计划，不要执行任何工具，不重新执行机场筛选，不生成新图层。"
                ),
                "selectedDatasetIds": [
                    "sample_airports",
                    "sample_ports",
                    "sample_populated_places",
                    "dataset_f2838ae521d6",
                    "dataset_2eaf58343584",
                    "dataset_80a075f44398",
                    "dataset_509febada353",
                ],
                "metadata": {
                    "layers": [
                        {
                            "id": "layer_sample_airports",
                            "layerId": "layer_sample_airports",
                            "datasetId": "sample_airports",
                            "name": "机场",
                            "visible": True,
                            "geometryType": "Point",
                        },
                        {
                            "id": "layer_sample_ports",
                            "layerId": "layer_sample_ports",
                            "datasetId": "sample_ports",
                            "name": "港口",
                            "visible": True,
                            "geometryType": "Point",
                        },
                        {
                            "id": "layer_sample_populated_places",
                            "layerId": "layer_sample_populated_places",
                            "datasetId": "sample_populated_places",
                            "name": "人口稠密地区",
                            "visible": True,
                            "geometryType": "Point",
                        },
                        {
                            "id": "layer_to_Z_GF51SOUbjjU",
                            "layerId": "layer_to_Z_GF51SOUbjjU",
                            "datasetId": "dataset_f2838ae521d6",
                            "name": "四川省",
                            "visible": True,
                            "geometryType": "MultiPolygon",
                        },
                        {
                            "id": "layer_CK5u3BKZDR4IbGZ7",
                            "layerId": "layer_CK5u3BKZDR4IbGZ7",
                            "datasetId": "dataset_2eaf58343584",
                            "name": "机场 空间筛选",
                            "visible": True,
                            "opacity": 1,
                            "geometryType": "Point",
                            "bbox": target_summary.bbox,
                            "dataRef": "storage://normalized/dataset_2eaf58343584/data.geojson",
                        },
                        {
                            "id": "layer_qVoeuhBnxan5sFy8",
                            "layerId": "layer_qVoeuhBnxan5sFy8",
                            "datasetId": "dataset_80a075f44398",
                            "name": "机场 空间筛选",
                            "visible": True,
                            "opacity": 1,
                            "geometryType": "Point",
                            "bbox": target_summary.bbox,
                        },
                        {
                            "id": "layer_8671sjERWitiYFE-",
                            "layerId": "layer_8671sjERWitiYFE-",
                            "datasetId": "dataset_509febada353",
                            "name": "机场 空间筛选",
                            "visible": True,
                            "opacity": 1,
                            "geometryType": "Point",
                            "bbox": target_summary.bbox,
                        },
                    ],
                    "activeDatasetIds": [
                        "sample_airports",
                        "sample_ports",
                        "sample_populated_places",
                        "dataset_f2838ae521d6",
                        "dataset_2eaf58343584",
                        "dataset_80a075f44398",
                        "dataset_509febada353",
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
        assert event_payloads(response.text, "tool.completed") == []
        assert event_payloads(response.text, "layer.created") == []
        assert event_payloads(response.text, "map.command") == []
        assert event_payloads(response.text, "error") == []
        assert model_client.messages == []

        summary_event = event_payloads(response.text, "data.summary")[0]["data"]
        assert summary_event["effectiveDatasetIds"] == ["dataset_2eaf58343584"]
        assert [dataset["datasetId"] for dataset in summary_event["datasets"]] == [
            "dataset_2eaf58343584"
        ]

        plan_data = event_payloads(response.text, "plan.created")[0]["data"]
        assert plan_data["planType"] == "buffer_analysis"
        assert plan_data["targetDatasetId"] == "dataset_2eaf58343584"
        assert plan_data["distance"] == 50
        assert plan_data["unit"] == "km"
        assert plan_data["execute"] is False
        assert plan_data["steps"][0]["expectedInputs"] == ["dataset_2eaf58343584"]
        assert "sample_airports" not in json.dumps(plan_data, ensure_ascii=False)
        assert "sample_ports" not in json.dumps(plan_data, ensure_ascii=False)
        assert "sample_populated_places" not in json.dumps(plan_data, ensure_ascii=False)

        completed_message = event_payloads(response.text, "message.completed")[0]["data"][
            "message"
        ]["content"]
        assert "只使用 dataset_2eaf58343584" in completed_message
        assert "sample_airports" not in completed_message
        assert "sample_ports" not in completed_message
        assert "sample_populated_places" not in completed_message
        assert "不重新执行机场筛选" in completed_message
        assert "不生成新图层" in completed_message
    finally:
        clear_overrides()


def test_ai_chat_plan_only_buffer_recovers_airport_filter_layer_from_session_history(
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
    write_sichuan_polygon_dataset(storage)
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
        session_id = "session_history_buffer_plan"

        execute_response = client.post(
            f"/api/ai-chat/sessions/{session_id}/messages",
            headers=auth_headers(token),
            json={
                "message": (
                    "请只使用 dataset_f2838ae521d6 和 sample_airports，执行空间筛选，"
                    "找出四川省范围内的所有机场，返回名称、IATA 代码、类型。"
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
        assert execute_response.status_code == 200
        assert "layer.created" in event_names(execute_response.text)
        result_dataset_id = event_payloads(execute_response.text, "tool.completed")[0][
            "data"
        ]["output"]["resultDatasetId"]
        assert result_dataset_id.startswith("dataset_")

        plan_response = client.post(
            f"/api/ai-chat/sessions/{session_id}/messages",
            headers=auth_headers(token),
            json={
                "message": (
                    "请基于刚才的“机场 空间筛选”结果图层，生成一个 50 公里缓冲区分析计划。"
                    "只生成计划，不要执行工具。请明确说明不会重新执行机场筛选，"
                    "也不会生成新图层。"
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
                        {
                            "id": "layer_sample_airports",
                            "layerId": "layer_sample_airports",
                            "datasetId": "sample_airports",
                            "name": "机场",
                            "visible": True,
                            "geometryType": "Point",
                        },
                        {
                            "id": "layer_sample_ports",
                            "layerId": "layer_sample_ports",
                            "datasetId": "sample_ports",
                            "name": "港口",
                            "visible": True,
                            "geometryType": "Point",
                        },
                        {
                            "id": "layer_sample_populated_places",
                            "layerId": "layer_sample_populated_places",
                            "datasetId": "sample_populated_places",
                            "name": "人口稠密地区",
                            "visible": True,
                            "geometryType": "Point",
                        },
                        {
                            "id": "layer_sichuan",
                            "layerId": "layer_sichuan",
                            "datasetId": "dataset_f2838ae521d6",
                            "name": "四川省",
                            "visible": True,
                            "geometryType": "MultiPolygon",
                        },
                    ],
                },
            },
        )

        assert plan_response.status_code == 200
        assert event_names(plan_response.text) == [
            "data.summary",
            "plan.created",
            "message.completed",
        ]
        assert event_payloads(plan_response.text, "tool.started") == []
        assert event_payloads(plan_response.text, "tool.completed") == []
        assert event_payloads(plan_response.text, "layer.created") == []
        assert event_payloads(plan_response.text, "map.command") == []
        assert event_payloads(plan_response.text, "error") == []

        summary_event = event_payloads(plan_response.text, "data.summary")[0]["data"]
        assert summary_event["availableDatasetIds"] == [
            "sample_airports",
            "sample_ports",
            "sample_populated_places",
            "dataset_f2838ae521d6",
        ]
        assert summary_event["effectiveDatasetIds"] == [result_dataset_id]

        plan_data = event_payloads(plan_response.text, "plan.created")[0]["data"]
        assert plan_data["planType"] == "buffer_analysis"
        assert plan_data["targetDatasetId"] == result_dataset_id
        assert plan_data["distance"] == 50
        assert plan_data["unit"] == "km"
        assert plan_data["execute"] is False
        assert plan_data["steps"][0]["expectedInputs"] == [result_dataset_id]
        assert "sample_airports" not in json.dumps(plan_data, ensure_ascii=False)

        completed_message = event_payloads(plan_response.text, "message.completed")[0]["data"][
            "message"
        ]["content"]
        assert f"只使用 {result_dataset_id}" in completed_message
        assert "只使用 sample_airports" not in completed_message
        assert "不重新执行机场筛选" in completed_message
        assert "不生成新图层" in completed_message
    finally:
        clear_overrides()


def test_ai_chat_plan_only_population_points_in_existing_buffer_is_overlay_plan(
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
    buffer_summary = write_airport_buffer_result(
        storage,
        dataset_id="dataset_c499673bb982",
        tool_call_id="tool_visible_buffer",
    )
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
            "/api/ai-chat/sessions/session_population_buffer_plan/messages",
            headers=auth_headers(token),
            json={
                "message": (
                    "请基于刚才生成的“机场 空间筛选 缓冲区”图层，"
                    "生成一个查询缓冲区内人口稠密地区的分析计划。只生成计划，不要执行工具。"
                    "计划必须说明输入点图层、掩膜面图层、空间关系、输出字段和结果用途。"
                ),
                "selectedDatasetIds": [
                    "sample_airports",
                    "sample_ports",
                    "sample_populated_places",
                    "dataset_f2838ae521d6",
                    "dataset_00eb6853cff3",
                    "dataset_bae7bf3355a7",
                    "dataset_c499673bb982",
                ],
                "metadata": {
                    "layers": [
                        {
                            "id": "layer_sample_populated_places",
                            "layerId": "layer_sample_populated_places",
                            "datasetId": "sample_populated_places",
                            "name": "人口稠密地区",
                            "visible": True,
                            "geometryType": "Point",
                        },
                        {
                            "id": "layer_4AOG4vg_N9bOy_DF",
                            "layerId": "layer_4AOG4vg_N9bOy_DF",
                            "datasetId": "dataset_c499673bb982",
                            "name": "机场 空间筛选 缓冲区",
                            "visible": True,
                            "geometryType": "Polygon",
                            "bbox": buffer_summary.bbox,
                        },
                    ],
                    "activeDatasetIds": [
                        "sample_airports",
                        "sample_ports",
                        "sample_populated_places",
                        "dataset_f2838ae521d6",
                        "dataset_00eb6853cff3",
                        "dataset_bae7bf3355a7",
                        "dataset_c499673bb982",
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
        assert event_payloads(response.text, "tool.completed") == []
        assert event_payloads(response.text, "layer.created") == []
        assert event_payloads(response.text, "map.command") == []
        assert model_client.messages == []

        summary_event = event_payloads(response.text, "data.summary")[0]["data"]
        assert summary_event["effectiveDatasetIds"] == [
            "sample_populated_places",
            "dataset_c499673bb982",
        ]
        assert [dataset["datasetId"] for dataset in summary_event["datasets"]] == [
            "sample_populated_places",
            "dataset_c499673bb982",
        ]

        plan_data = event_payloads(response.text, "plan.created")[0]["data"]
        assert plan_data["planType"] == "points_in_polygon_plan"
        assert plan_data["inputPointDatasetId"] == "sample_populated_places"
        assert plan_data["maskDatasetId"] == "dataset_c499673bb982"
        assert plan_data["predicate"] == "within"
        assert plan_data["alternativePredicates"] == ["intersects"]
        assert plan_data["outputFields"] == [
            "NAME",
            "NAME_ZH",
            "POP_MAX",
            "POP2020",
            "LATITUDE",
            "LONGITUDE",
        ]
        assert [step["title"] for step in plan_data["steps"]] == [
            "数据准备",
            "空间关系设置",
            "字段输出",
            "结果用途",
        ]
        assert plan_data["steps"][0]["expectedInputs"] == [
            "sample_populated_places",
            "dataset_c499673bb982",
        ]
        assert "输入点图层 sample_populated_places" in plan_data["steps"][0]["description"]
        assert "掩膜面图层 dataset_c499673bb982" in plan_data["steps"][0]["description"]
        assert "within 或 intersects" in plan_data["steps"][1]["description"]
        assert "NAME、NAME_ZH、POP_MAX、POP2020、LATITUDE、LONGITUDE" in plan_data[
            "steps"
        ][2]["description"]
        assert "机场 50km 服务范围内的人口稠密地区" in plan_data["steps"][3][
            "description"
        ]
        completed_message = event_payloads(response.text, "message.completed")[0]["data"][
            "message"
        ]["content"]
        assert "输入点图层 sample_populated_places" in completed_message
        assert "掩膜面图层 dataset_c499673bb982" in completed_message
    finally:
        clear_overrides()


def test_ai_chat_spatial_filter_emits_completed_audit_and_result_layer(
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
    write_sichuan_polygon_dataset(storage)
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
            "/api/ai-chat/sessions/session_sichuan_execute/messages",
            headers=auth_headers(token),
            json={
                "message": (
                    "请只使用 dataset_f2838ae521d6 和 sample_airports，执行刚才的计划，"
                    "找出四川省范围内的所有机场，返回名称、IATA 代码、类型，"
                    "并说明调用了哪个确定性 GIS 工具。"
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
            "tool.started",
            "tool.completed",
            "layer.created",
            "map.command",
            "message.delta",
            "message.completed",
        ]

        started = event_payloads(response.text, "tool.started")[0]
        assert started["data"]["toolName"] == "spatial_filter"
        tool_input = started["data"]["input"]
        assert tool_input["inputDatasetId"] == "sample_airports"
        assert tool_input["maskDatasetId"] == "dataset_f2838ae521d6"
        assert tool_input["predicate"] == "within"
        assert tool_input["outputFields"] == ["name", "iata_code", "type"]

        completed = event_payloads(response.text, "tool.completed")[0]
        assert completed["data"]["toolName"] == "spatial_filter"
        assert completed["data"]["status"] == "completed"
        output = completed["data"]["output"]
        assert output["featureCount"] == len(output["rows"])
        assert output["featureCount"] >= 1
        assert output["resultDatasetId"].startswith("dataset_")
        assert output["summary"]["inputDatasetId"] == "sample_airports"
        assert output["summary"]["maskDatasetId"] == "dataset_f2838ae521d6"
        assert output["summary"]["predicate"] == "within"
        assert output["summary"]["outputFields"] == ["name", "iata_code", "type"]
        assert all(set(row) == {"name", "iata_code", "type"} for row in output["rows"])
        assert any(row["iata_code"] == "CTU" for row in output["rows"])

        layer = event_payloads(response.text, "layer.created")[0]["data"]
        assert layer["datasetId"] == output["resultDatasetId"]
        assert layer["metadata"]["operation"] == "spatial_filter"
        map_command = event_payloads(response.text, "map.command")[0]["data"]
        assert map_command["action"] == "layer.addDataset"
        assert map_command["datasetId"] == output["resultDatasetId"]

        assert event_payloads(response.text, "tool.failed") == []
        completed_message = event_payloads(response.text, "message.completed")[0]["data"][
            "message"
        ]["content"]
        assert "已执行确定性 GIS 工具 spatial_filter" in completed_message
        assert "name=Chengdushuang Liu" in completed_message
        assert "iata_code=CTU" in completed_message
        assert "type=major" in completed_message
        assert "spatial_filter 尚未实现/未执行" not in completed_message
        assert "featureCount=16" not in completed_message
        assert "status=success" not in completed_message
        assert model_client.tool_results == []
    finally:
        clear_overrides()


def test_ai_chat_executes_population_points_within_existing_buffer_with_spatial_filter(
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
    buffer_summary = write_airport_buffer_result(
        storage,
        dataset_id="dataset_bb1fc4102e6d",
        tool_call_id="tool_visible_buffer",
    )
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
            "/api/ai-chat/sessions/session_population_buffer_execute/messages",
            headers=auth_headers(token),
            json={
                "message": (
                    "请执行刚才的查询缓冲区内人口稠密地区计划，只使用 "
                    "sample_populated_places 作为输入点图层，dataset_bb1fc4102e6d "
                    "作为掩膜面图层，空间关系使用 within，生成结果图层，并返回 "
                    "resultDatasetId、图层名称、几何类型、bbox、要素数量、输出字段，"
                    "以及调用的确定性 GIS 工具。"
                ),
                "selectedDatasetIds": [
                    "sample_airports",
                    "sample_ports",
                    "sample_populated_places",
                    "dataset_f2838ae521d6",
                    "dataset_00eb6853cff3",
                    "dataset_bae7bf3355a7",
                    "dataset_bb1fc4102e6d",
                ],
                "metadata": {
                    "layers": [
                        {
                            "id": "layer_sample_populated_places",
                            "layerId": "layer_sample_populated_places",
                            "datasetId": "sample_populated_places",
                            "name": "人口稠密地区",
                            "visible": True,
                            "geometryType": "Point",
                        },
                        {
                            "id": "layer_4AOG4vg_N9bOy_DF",
                            "layerId": "layer_4AOG4vg_N9bOy_DF",
                            "datasetId": "dataset_bb1fc4102e6d",
                            "name": "机场 空间筛选 缓冲区",
                            "visible": True,
                            "geometryType": "Polygon",
                            "bbox": buffer_summary.bbox,
                        },
                    ],
                    "activeDatasetIds": [
                        "sample_airports",
                        "sample_ports",
                        "sample_populated_places",
                        "dataset_f2838ae521d6",
                        "dataset_00eb6853cff3",
                        "dataset_bae7bf3355a7",
                        "dataset_bb1fc4102e6d",
                    ],
                },
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
            "message.completed",
        ]
        assert '"toolName": "attribute_summary"' not in response.text
        assert '"toolName": "geoprocess"' not in response.text
        assert event_payloads(response.text, "tool.failed") == []

        summary_event = event_payloads(response.text, "data.summary")[0]["data"]
        assert summary_event["effectiveDatasetIds"] == [
            "sample_populated_places",
            "dataset_bb1fc4102e6d",
        ]

        started = event_payloads(response.text, "tool.started")[0]["data"]
        assert started["toolName"] == "spatial_filter"
        tool_input = started["input"]
        assert tool_input["inputDatasetId"] == "sample_populated_places"
        assert tool_input["maskDatasetId"] == "dataset_bb1fc4102e6d"
        assert tool_input["predicate"] == "within"
        assert "NAME" in tool_input["outputFields"]
        assert "POP_MAX" in tool_input["outputFields"]
        assert "POP2020" in tool_input["outputFields"]

        completed = event_payloads(response.text, "tool.completed")[0]["data"]
        assert completed["toolName"] == "spatial_filter"
        assert completed["status"] == "completed"
        output = completed["output"]
        assert output["resultDatasetId"].startswith("dataset_")
        assert output["featureCount"] == len(output["rows"])
        assert output["summary"]["inputDatasetId"] == "sample_populated_places"
        assert output["summary"]["maskDatasetId"] == "dataset_bb1fc4102e6d"
        assert output["summary"]["predicate"] == "within"

        layer = event_payloads(response.text, "layer.created")[0]["data"]
        assert layer["datasetId"] == output["resultDatasetId"]
        assert layer["geometryType"] == "Point"
        assert layer["metadata"]["operation"] == "spatial_filter"
        map_command = event_payloads(response.text, "map.command")[0]["data"]
        assert map_command["datasetId"] == output["resultDatasetId"]
        completed_message = event_payloads(response.text, "message.completed")[0]["data"][
            "message"
        ]["content"]
        assert "已执行确定性 GIS 工具 spatial_filter" in completed_message
        assert f"resultDatasetId={output['resultDatasetId']}" in completed_message
        assert model_client.tool_results == []
    finally:
        clear_overrides()


def test_ai_chat_map_display_existing_population_result_does_not_run_analysis(
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
    write_population_spatial_filter_result(storage)
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
            "/api/ai-chat/sessions/session_8091e24d-3369-4c3c-83ca-d78d8ab59d7b/messages",
            headers=auth_headers(token),
            json={
                "message": (
                    "请将地图定位到“人口稠密地区 空间筛选”结果图层，并高亮显示该点。"
                    "只执行地图展示动作，不重新执行任何数据分析工具。"
                ),
                "selectedDatasetIds": [
                    "sample_airports",
                    "sample_ports",
                    "sample_populated_places",
                    "dataset_f2838ae521d6",
                    "dataset_00eb6853cff3",
                    "dataset_bae7bf3355a7",
                    "dataset_bb1fc4102e6d",
                    "dataset_16fb343ba5e6",
                    "dataset_fb534d9ba83d",
                ],
                "metadata": {
                    "mapView": {
                        "center": [104.068074, 30.671946],
                        "height": 1,
                        "bbox": [104.068068, 30.671942, 104.06808, 30.67195],
                        "crs": "EPSG:4326",
                    },
                    "layers": [
                        {
                            "id": "layer_WWHuPgqiEQf4cxBU",
                            "layerId": "layer_WWHuPgqiEQf4cxBU",
                            "datasetId": "dataset_16fb343ba5e6",
                            "name": "人口稠密地区 空间筛选",
                            "visible": True,
                            "opacity": 1,
                            "geometryType": "Point",
                            "bbox": [
                                104.0680736,
                                30.6719459,
                                104.0680736,
                                30.6719459,
                            ],
                            "dataRef": "storage://normalized/dataset_16fb343ba5e6/data.geojson",
                        },
                        {
                            "id": "layer_QD1-65dLmANAqT6n",
                            "layerId": "layer_QD1-65dLmANAqT6n",
                            "datasetId": "dataset_fb534d9ba83d",
                            "name": "人口稠密地区 空间筛选 空间筛选",
                            "visible": True,
                            "opacity": 1,
                            "geometryType": "Point",
                            "bbox": [
                                104.0680736,
                                30.6719459,
                                104.0680736,
                                30.6719459,
                            ],
                            "dataRef": "storage://normalized/dataset_fb534d9ba83d/data.geojson",
                        },
                    ],
                    "activeDatasetIds": [
                        "sample_airports",
                        "sample_ports",
                        "sample_populated_places",
                        "dataset_f2838ae521d6",
                        "dataset_00eb6853cff3",
                        "dataset_bae7bf3355a7",
                        "dataset_bb1fc4102e6d",
                        "dataset_16fb343ba5e6",
                        "dataset_fb534d9ba83d",
                    ],
                    "clientCapabilities": {
                        "mapCommands": [
                            "camera.flyTo",
                            "layer.addDataset",
                            "layer.setVisible",
                            "layer.setOpacity",
                            "overlay.addMarker",
                            "map.clearTemporary",
                        ]
                    },
                },
            },
        )

        assert response.status_code == 200
        assert event_names(response.text) == [
            "data.summary",
            "map.command",
            "map.command",
            "message.delta",
            "message.completed",
        ]
        assert event_payloads(response.text, "tool.started") == []
        assert event_payloads(response.text, "tool.completed") == []
        assert event_payloads(response.text, "layer.created") == []
        assert '"toolName": "spatial_filter"' not in response.text

        summary_event = event_payloads(response.text, "data.summary")[0]["data"]
        assert summary_event["effectiveDatasetIds"] == ["dataset_16fb343ba5e6"]

        commands = [event["data"] for event in event_payloads(response.text, "map.command")]
        assert commands == [
            {
                "action": "camera.flyTo",
                "target": {
                    "kind": "coordinate",
                    "lon": 104.0680736,
                    "lat": 30.6719459,
                },
                "durationMs": 1200,
                "datasetId": "dataset_16fb343ba5e6",
                "layerId": "layer_WWHuPgqiEQf4cxBU",
            },
            {
                "action": "overlay.addMarker",
                "id": "dataset_16fb343ba5e6-highlight",
                "position": [104.0680736, 30.6719459],
                "label": "Chengdu / 成都",
                "datasetId": "dataset_16fb343ba5e6",
                "layerId": "layer_WWHuPgqiEQf4cxBU",
            },
        ]
        completed_message = event_payloads(response.text, "message.completed")[0]["data"][
            "message"
        ]["content"]
        assert "camera.flyTo" in completed_message
        assert "overlay.addMarker" in completed_message
        assert "dataset_16fb343ba5e6" in completed_message
        assert "layer_WWHuPgqiEQf4cxBU" in completed_message
        assert "未调用任何数据分析工具" not in completed_message
        assert model_client.messages == []
        assert model_client.tool_results == []
    finally:
        clear_overrides()


def test_ai_chat_map_display_accepts_fly_to_layer_extent_wording(
    tmp_path: Path,
) -> None:
    configure_app(tmp_path)
    try:
        client = TestClient(app)
        token = login(client)
        response = client.post(
            "/api/ai-chat/sessions/session_map_display_sichuan/messages",
            headers=auth_headers(token),
            json={
                "message": "飞行到四川省这个图层范围",
                "metadata": {
                    "layers": [
                        {
                            "layerId": "layer_sichuan",
                            "datasetId": "dataset_sichuan",
                            "name": "四川省",
                            "geometryType": "MultiPolygon",
                            "bbox": [97.35, 26.05, 108.55, 34.32],
                        }
                    ],
                    "clientCapabilities": {
                        "mapCommands": ["camera.flyTo", "overlay.addMarker"]
                    },
                },
            },
        )

        assert response.status_code == 200
        assert event_names(response.text) == [
            "data.summary",
            "map.command",
            "message.delta",
            "message.completed",
        ]
        assert event_payloads(response.text, "map.command")[0]["data"] == {
            "action": "camera.flyTo",
            "target": {"kind": "bbox", "bbox": [97.35, 26.05, 108.55, 34.32]},
            "durationMs": 1200,
            "datasetId": "dataset_sichuan",
            "layerId": "layer_sichuan",
        }
    finally:
        clear_overrides()


def test_ai_chat_map_display_resolves_pronoun_to_unique_generated_layer(
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
    result_summary = write_population_spatial_filter_result(storage)

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
            "/api/ai-chat/sessions/session_map_display_pronoun/messages",
            headers=auth_headers(token),
            json={
                "message": "定位到该图层",
                "selectedDatasetIds": ["sample_airports", result_summary.dataset_id],
                "metadata": {
                    "layers": [
                        {
                            "layerId": "layer_sample_airports",
                            "datasetId": "sample_airports",
                            "name": "机场",
                            "geometryType": "Point",
                            "bbox": [-175.14, -53.01, 178.56, 71.29],
                        },
                        {
                            "layerId": "layer_generated_result",
                            "datasetId": result_summary.dataset_id,
                            "name": result_summary.name,
                            "geometryType": "Point",
                            "bbox": [104.0680736, 30.6719459, 104.0680736, 30.6719459],
                        },
                    ],
                    "activeDatasetIds": ["sample_airports", result_summary.dataset_id],
                },
            },
        )

        assert response.status_code == 200
        assert event_names(response.text) == [
            "data.summary",
            "map.command",
            "message.delta",
            "message.completed",
        ]
        assert event_payloads(response.text, "map.command")[0]["data"] == {
            "action": "camera.flyTo",
            "target": {
                "kind": "coordinate",
                "lon": 104.0680736,
                "lat": 30.6719459,
            },
            "durationMs": 1200,
            "datasetId": result_summary.dataset_id,
            "layerId": "layer_generated_result",
        }
    finally:
        clear_overrides()


def test_ai_chat_map_display_recovers_explicit_registered_dataset_not_in_layer_context(
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
    result_summary = write_population_spatial_filter_result(
        storage,
        dataset_id="dataset_explicit_result",
    )

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
            "/api/ai-chat/sessions/session_map_display_explicit_dataset/messages",
            headers=auth_headers(token),
            json={
                "message": f"定位到{result_summary.dataset_id}图层",
                "selectedDatasetIds": ["sample_airports"],
                "metadata": {
                    "layers": [
                        {
                            "layerId": "layer_sample_airports",
                            "datasetId": "sample_airports",
                            "name": "机场",
                            "geometryType": "Point",
                            "bbox": [-175.14, -53.01, 178.56, 71.29],
                        }
                    ],
                    "activeDatasetIds": ["sample_airports"],
                    "clientCapabilities": {"mapCommands": ["camera.flyTo"]},
                },
            },
        )

        assert response.status_code == 200
        assert event_names(response.text) == [
            "data.summary",
            "map.command",
            "message.delta",
            "message.completed",
        ]
        assert event_payloads(response.text, "tool.started") == []
        assert event_payloads(response.text, "layer.created") == []
        assert event_payloads(response.text, "map.command")[0]["data"] == {
            "action": "camera.flyTo",
            "target": {
                "kind": "coordinate",
                "lon": 104.0680736,
                "lat": 30.6719459,
            },
            "durationMs": 1200,
            "datasetId": result_summary.dataset_id,
            "layerId": f"layer_{result_summary.dataset_id}",
        }
    finally:
        clear_overrides()


def test_ai_chat_map_display_without_command_uses_fallback_message(
    tmp_path: Path,
) -> None:
    configure_app(tmp_path)
    try:
        client = TestClient(app)
        token = login(client)
        response = client.post(
            "/api/ai-chat/sessions/session_map_display_missing_target/messages",
            headers=auth_headers(token),
            json={"message": "飞行到四川省这个图层范围", "metadata": {"layers": []}},
        )

        assert response.status_code == 200
        assert event_payloads(response.text, "map.command") == []
        completed_message = event_payloads(response.text, "message.completed")[0]["data"][
            "message"
        ]["content"]
        assert completed_message == "我未生成地图指令，无法执行定位。"
    finally:
        clear_overrides()


def test_ai_chat_result_layer_inspection_is_read_only_and_uses_lineage(
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
    write_sichuan_polygon_dataset(storage)
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

        execute_response = client.post(
            "/api/ai-chat/sessions/session_sichuan_inspection/messages",
            headers=auth_headers(token),
            json={
                "message": (
                    "请只使用 dataset_f2838ae521d6 和 sample_airports，"
                    "找出四川省范围内的所有机场，返回名称、IATA 代码、类型。"
                ),
                "selectedDatasetIds": [
                    "sample_airports",
                    "dataset_f2838ae521d6",
                ],
                "metadata": {
                    "activeDatasetIds": [
                        "sample_airports",
                        "dataset_f2838ae521d6",
                    ],
                    "layers": [
                        {
                            "id": "layer_sample_airports",
                            "layerId": "layer_sample_airports",
                            "datasetId": "sample_airports",
                            "name": "机场",
                        },
                        {
                            "id": "layer_sichuan",
                            "layerId": "layer_sichuan",
                            "datasetId": "dataset_f2838ae521d6",
                            "name": "四川省",
                        },
                    ],
                },
            },
        )
        assert execute_response.status_code == 200
        result_dataset_id = event_payloads(execute_response.text, "tool.completed")[0][
            "data"
        ]["output"]["resultDatasetId"]
        result_summary = GisDatasetService(
            storage=storage,
            repository=DatasetRepository(storage.metadata_path()),
        ).get_dataset(result_dataset_id)
        assert result_summary.lineage == {
            "inputDatasetId": "sample_airports",
            "maskDatasetId": "dataset_f2838ae521d6",
            "predicate": "within",
            "outputFields": ["name", "iata_code", "type"],
            "operation": "spatial_filter",
            "toolCallId": event_payloads(execute_response.text, "tool.started")[0]["toolCallId"],
        }
        repository = DatasetRepository(storage.metadata_path())
        result_record = repository.get(result_dataset_id)
        assert result_record is not None
        repository.save(
            result_record.model_copy(
                update={"summary": result_record.summary.model_copy(update={"lineage": None})}
            )
        )
        stale_summary = GisDatasetService(
            storage=storage,
            repository=repository,
        ).register_generated_dataset(
            name="机场 空间筛选",
            geodata=gpd.GeoDataFrame(
                {"name": ["stale"]},
                geometry=[Point(103.9, 30.5)],
                crs="EPSG:4326",
            ),
            source_tool_call_id="tool_stale",
            metadata={
                "operation": "spatial_filter",
                "inputDatasetId": "sample_airports",
                "maskDatasetId": "dataset_f2838ae521d6",
                "predicate": "within",
                "outputFields": ["name"],
            },
        )

        inspect_response = client.post(
            "/api/ai-chat/sessions/session_sichuan_inspection/messages",
            headers=auth_headers(token),
            json={
                "message": (
                    "请说明刚才生成的“机场 空间筛选”结果图层的信息，包括图层 ID、"
                    "数据集 ID、几何类型、bbox、来源输入图层、空间关系、要素数量，"
                    "并判断这个结果图层是否可以继续用于后续分析。"
                ),
                "selectedDatasetIds": [
                    "sample_airports",
                    "dataset_f2838ae521d6",
                    result_dataset_id,
                    stale_summary.dataset_id,
                ],
                "metadata": {
                    "activeDatasetIds": [
                        "sample_airports",
                        "dataset_f2838ae521d6",
                        result_dataset_id,
                        stale_summary.dataset_id,
                    ],
                    "layers": [
                        {
                            "id": "layer_sample_airports",
                            "layerId": "layer_sample_airports",
                            "datasetId": "sample_airports",
                            "name": "机场",
                            "geometryType": "Point",
                        },
                        {
                            "id": "layer_sichuan",
                            "layerId": "layer_sichuan",
                            "datasetId": "dataset_f2838ae521d6",
                            "name": "四川省",
                            "geometryType": "Polygon",
                        },
                        {
                            "id": "layer_58Xhznd_LAd_zfaP",
                            "layerId": "layer_58Xhznd_LAd_zfaP",
                            "datasetId": result_dataset_id,
                            "name": "机场 空间筛选",
                            "geometryType": "Point",
                            "bbox": result_summary.bbox,
                            "dataRef": result_summary.data_ref,
                        },
                        {
                            "id": f"layer_{stale_summary.dataset_id}",
                            "layerId": f"layer_{stale_summary.dataset_id}",
                            "datasetId": stale_summary.dataset_id,
                            "name": "机场 空间筛选",
                            "visible": True,
                            "geometryType": "Point",
                            "bbox": stale_summary.bbox,
                            "dataRef": stale_summary.data_ref,
                        },
                    ],
                },
            },
        )

        assert inspect_response.status_code == 200
        assert event_names(inspect_response.text) == [
            "data.summary",
            "message.delta",
            "message.completed",
        ]
        assert event_payloads(inspect_response.text, "tool.started") == []
        assert event_payloads(inspect_response.text, "tool.completed") == []
        assert event_payloads(inspect_response.text, "layer.created") == []
        assert event_payloads(inspect_response.text, "map.command") == []
        assert result_dataset_id in inspect_response.text
        summary_event = event_payloads(inspect_response.text, "data.summary")[0]["data"]
        assert summary_event["effectiveDatasetIds"] == [result_dataset_id]
        summary_datasets = summary_event["datasets"]
        assert [dataset["datasetId"] for dataset in summary_datasets] == [result_dataset_id]
        assert summary_datasets[0]["lineage"] == {
            "operation": "spatial_filter",
            "inputDatasetId": "sample_airports",
            "maskDatasetId": "dataset_f2838ae521d6",
            "predicate": "within",
            "outputFields": ["name", "iata_code", "type"],
            "toolCallId": event_payloads(execute_response.text, "tool.started")[0]["toolCallId"],
        }
        persisted_result = DatasetRepository(storage.metadata_path()).get(result_dataset_id)
        assert persisted_result is not None
        assert persisted_result.summary.lineage == summary_datasets[0]["lineage"]
        completed_message = event_payloads(inspect_response.text, "message.completed")[0][
            "data"
        ]["message"]["content"]
        assert "当前结果图层信息如下" in completed_message
        assert "图层ID=layer_58Xhznd_LAd_zfaP" in completed_message
        assert f"数据集ID={result_dataset_id}" in completed_message
        assert "来源输入图层=sample_airports" in completed_message
        assert "掩膜图层=dataset_f2838ae521d6" in completed_message
        assert "空间关系=within" in completed_message
        assert "可继续作为后续分析输入" in completed_message
        assert stale_summary.dataset_id not in completed_message
        assert model_client.messages == []
        assert model_client.tool_results == []
    finally:
        clear_overrides()


def test_ai_chat_named_population_result_inspection_is_read_only(
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
    target_summary = write_population_spatial_filter_result(
        storage,
        dataset_id="dataset_06ba53bf3caa",
    )
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
            "/api/ai-chat/sessions/session_named_population_result/messages",
            headers=auth_headers(token),
            json={
                "message": (
                    "当前 selectedDatasetIds 中包含多个历史结果图层和同名图层。"
                    "请只使用明确指定的“人口稠密地区 空间筛选”结果图层，说明其信息。"
                    "不要使用同名旧图层，不要使用当前地图中心点附近图层，不要重新执行工具。"
                ),
                "selectedDatasetIds": [
                    "sample_airports",
                    "sample_ports",
                    "sample_populated_places",
                    "dataset_f2838ae521d6",
                    "dataset_d5e0e9290f86",
                    "dataset_d54e236999e1",
                    target_summary.dataset_id,
                ],
                "metadata": {
                    "mapView": {
                        "center": [104.068074, 30.671946],
                        "height": 1800,
                        "bbox": [104.057228, 30.665413, 104.07892, 30.678478],
                        "crs": "EPSG:4326",
                    },
                    "layers": [
                        {
                            "id": "layer_sample_airports",
                            "datasetId": "sample_airports",
                            "name": "机场",
                            "visible": True,
                            "geometryType": "Point",
                        },
                        {
                            "id": "layer_sample_ports",
                            "datasetId": "sample_ports",
                            "name": "港口",
                            "visible": True,
                            "geometryType": "Point",
                        },
                        {
                            "id": "layer_population_result",
                            "layerId": "layer_6fh6eq8Jbe-fLwBu",
                            "datasetId": target_summary.dataset_id,
                            "name": "人口稠密地区 空间筛选",
                            "visible": True,
                            "geometryType": "Point",
                            "bbox": target_summary.bbox,
                            "dataRef": target_summary.data_ref,
                        },
                    ],
                    "activeDatasetIds": [
                        "sample_airports",
                        "sample_ports",
                        "sample_populated_places",
                        "dataset_f2838ae521d6",
                        "dataset_d5e0e9290f86",
                        "dataset_d54e236999e1",
                        target_summary.dataset_id,
                    ],
                },
            },
        )

        assert response.status_code == 200
        assert event_payloads(response.text, "tool.started") == []
        assert event_payloads(response.text, "tool.completed") == []
        assert event_payloads(response.text, "layer.created") == []
        assert event_payloads(response.text, "map.command") == []
        summary_event = event_payloads(response.text, "data.summary")[0]["data"]
        assert summary_event["effectiveDatasetIds"] == [target_summary.dataset_id]
        assert [dataset["datasetId"] for dataset in summary_event["datasets"]] == [
            target_summary.dataset_id
        ]
    finally:
        clear_overrides()


def test_ai_chat_buffer_result_metadata_query_is_read_only_and_targets_visible_polygon(
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
    model_client = FakeModelClient()
    filtered_summary = write_airport_spatial_filter_result(storage)
    duplicate_filtered_summary = write_airport_spatial_filter_result(storage).model_copy(
        update={"dataset_id": "dataset_bae7bf3355a7"}
    )
    DatasetRepository(storage.metadata_path()).save(
        DatasetRecord(
            summary=duplicate_filtered_summary,
            rawUri=storage.upload_uri(duplicate_filtered_summary.dataset_id),
            normalizedUri=storage.normalized_uri(duplicate_filtered_summary.dataset_id),
        )
    )
    hidden_buffer = write_airport_buffer_result(
        storage,
        dataset_id="dataset_edccca572501",
        tool_call_id="tool_hidden_buffer",
    )
    visible_buffer = write_airport_buffer_result(
        storage,
        dataset_id="dataset_bb1fc4102e6d",
        tool_call_id="tool_visible_buffer",
    )

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
            "/api/ai-chat/sessions/session_buffer_inspection/messages",
            headers=auth_headers(token),
            json={
                "message": (
                    "请说明刚才生成的“机场 空间筛选 缓冲区”结果图层信息，包括图层 ID、"
                    "数据集 ID、几何类型、CRS、processingCRS、bbox、面积、来源输入图层、"
                    "缓冲距离、单位、工具调用 ID，并判断是否可以继续用于后续叠加分析。"
                    "不要重新执行任何工具。"
                ),
                "selectedDatasetIds": [
                    "sample_airports",
                    "sample_ports",
                    "sample_populated_places",
                    "dataset_f2838ae521d6",
                    filtered_summary.dataset_id,
                    duplicate_filtered_summary.dataset_id,
                    visible_buffer.dataset_id,
                ],
                "metadata": {
                    "layers": [
                        {
                            "id": "layer_58Xhznd_LAd_zfaP",
                            "layerId": "layer_58Xhznd_LAd_zfaP",
                            "datasetId": filtered_summary.dataset_id,
                            "name": "机场 空间筛选",
                            "visible": True,
                            "geometryType": "Point",
                        },
                        {
                            "id": "layer_gncN-KJUye1bKtPn",
                            "layerId": "layer_gncN-KJUye1bKtPn",
                            "datasetId": duplicate_filtered_summary.dataset_id,
                            "name": "机场 空间筛选",
                            "visible": True,
                            "geometryType": "Point",
                        },
                        {
                            "id": "layer_nH3sURCF6U1qimOc",
                            "layerId": "layer_nH3sURCF6U1qimOc",
                            "datasetId": hidden_buffer.dataset_id,
                            "name": "机场 空间筛选 缓冲区",
                            "visible": False,
                            "geometryType": "Polygon",
                            "bbox": hidden_buffer.bbox,
                        },
                        {
                            "id": "layer_4AOG4vg_N9bOy_DF",
                            "layerId": "layer_4AOG4vg_N9bOy_DF",
                            "datasetId": visible_buffer.dataset_id,
                            "name": "机场 空间筛选 缓冲区",
                            "visible": True,
                            "geometryType": "Polygon",
                            "bbox": visible_buffer.bbox,
                        },
                    ],
                    "activeDatasetIds": [
                        "sample_airports",
                        "sample_ports",
                        "sample_populated_places",
                        "dataset_f2838ae521d6",
                        filtered_summary.dataset_id,
                        duplicate_filtered_summary.dataset_id,
                        visible_buffer.dataset_id,
                    ],
                },
            },
        )

        assert response.status_code == 200
        assert event_names(response.text) == [
            "data.summary",
            "message.delta",
            "message.completed",
        ]
        assert event_payloads(response.text, "tool.started") == []
        assert event_payloads(response.text, "tool.failed") == []
        assert event_payloads(response.text, "layer.created") == []
        assert event_payloads(response.text, "map.command") == []
        summary_event = event_payloads(response.text, "data.summary")[0]["data"]
        assert summary_event["effectiveDatasetIds"] == ["dataset_bb1fc4102e6d"]
        assert [dataset["datasetId"] for dataset in summary_event["datasets"]] == [
            "dataset_bb1fc4102e6d"
        ]
        dataset = summary_event["datasets"][0]
        assert dataset["crs"] == "EPSG:4326"
        assert dataset["processingCRS"] == "EPSG:32648"
        assert dataset["bbox"] == [
            103.43480642640635,
            30.12994002309082,
            104.47757568081893,
            31.032170519011135,
        ]
        assert dataset["area"]["unit"] == "square_meters"
        assert dataset["area"]["processingCRS"] == "EPSG:32648"
        assert dataset["area"]["value"] > 0
        assert dataset["sourceDatasetId"] == "dataset_00eb6853cff3"
        assert dataset["distance"] == 50000
        assert dataset["unit"] == "meters"
        assert dataset["toolCallId"] == "tool_visible_buffer"
        assert dataset["lineage"]["toolCallId"] == "tool_visible_buffer"
        completed_message = event_payloads(response.text, "message.completed")[0]["data"][
            "message"
        ]["content"]
        assert "当前结果图层信息如下" in completed_message
        assert "图层ID=layer_4AOG4vg_N9bOy_DF" in completed_message
        assert "来源输入图层=dataset_00eb6853cff3" in completed_message
        assert "processingCRS=EPSG:32648" in completed_message
        assert "缓冲距离=50000" in completed_message
        assert "工具调用ID=tool_visible_buffer" in completed_message
        assert "tool_failed" not in completed_message
        assert model_client.messages == []
        assert model_client.tool_results == []
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
        completed_message = event_payloads(response.text, "message.completed")[0]["data"][
            "message"
        ]["content"]
        assert "结果几何已回写为 EPSG:4326 GeoJSON" in completed_message
        assert "processingCRS=" in completed_message
    finally:
        clear_overrides()


def test_ai_chat_executes_prior_50km_buffer_plan_with_real_tool_events(
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
    write_sichuan_polygon_dataset(storage)
    write_airport_spatial_filter_result(storage)
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
            "/api/ai-chat/sessions/session_8091e24d-3369-4c3c-83ca-d78d8ab59d7b/messages",
            headers=auth_headers(token),
            json={
                "message": (
                    "请执行刚才的 50 公里缓冲区分析计划，只使用 dataset_00eb6853cff3 "
                    "作为输入，生成缓冲区结果图层，并返回 resultDatasetId、图层名称、"
                    "几何类型、bbox、面积估算、使用的坐标系/距离单位处理方式。"
                ),
                "selectedDatasetIds": [
                    "sample_airports",
                    "sample_ports",
                    "sample_populated_places",
                    "dataset_f2838ae521d6",
                    "dataset_00eb6853cff3",
                    "dataset_bae7bf3355a7",
                ],
                "metadata": {
                    "mapView": {
                        "center": [103.954935, 30.58352],
                        "height": 4403.56,
                        "bbox": [103.92842, 30.567549, 103.98145, 30.599486],
                        "crs": "EPSG:4326",
                    },
                    "layers": [
                        {
                            "id": "layer_58Xhznd_LAd_zfaP",
                            "layerId": "layer_58Xhznd_LAd_zfaP",
                            "datasetId": "dataset_00eb6853cff3",
                            "name": "机场 空间筛选",
                            "visible": True,
                            "opacity": 1,
                            "geometryType": "Point",
                            "bbox": [
                                103.95613648169501,
                                30.581071264746427,
                                103.95613648169501,
                                30.581071264746427,
                            ],
                            "dataRef": "storage://normalized/dataset_00eb6853cff3/data.geojson",
                        }
                    ],
                    "activeDatasetIds": [
                        "sample_airports",
                        "sample_ports",
                        "sample_populated_places",
                        "dataset_f2838ae521d6",
                        "dataset_00eb6853cff3",
                        "dataset_bae7bf3355a7",
                    ],
                    "clientCapabilities": {
                        "mapCommands": [
                            "camera.flyTo",
                            "layer.addDataset",
                            "layer.setVisible",
                            "layer.setOpacity",
                            "overlay.addMarker",
                            "map.clearTemporary",
                        ]
                    },
                },
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
            "message.completed",
        ]

        started = event_payloads(response.text, "tool.started")[0]
        assert started["data"]["toolName"] == "geoprocess"
        tool_input = started["data"]["input"]
        assert tool_input["inputDatasetId"] == "dataset_00eb6853cff3"
        assert tool_input["operation"] == "buffer"
        assert tool_input["distance"] == 50000
        assert tool_input["unit"] == "meters"
        assert tool_input["processingCRS"] == "EPSG:32648"

        completed = event_payloads(response.text, "tool.completed")[0]
        output = completed["data"]["output"]
        assert output["resultDatasetId"].startswith("dataset_")
        assert output["featureCount"] == 1
        assert output["geometryType"] == "Polygon"
        assert len(output["bbox"]) == 4
        assert output["area"]["unit"] == "square_meters"
        assert output["area"]["value"] > 7_800_000_000
        assert output["dataRef"].startswith("storage://normalized/")
        assert output["lineage"]["operation"] == "buffer"
        assert output["lineage"]["inputDatasetId"] == "dataset_00eb6853cff3"
        assert output["lineage"]["distance"] == 50000
        assert output["lineage"]["unit"] == "meters"
        assert output["lineage"]["processingCRS"] == "EPSG:32648"
        assert output["result"]["crs"] == "EPSG:4326"
        assert "outputCRS" not in output
        assert "outputCRS" not in output["lineage"]

        layer = event_payloads(response.text, "layer.created")[0]["data"]
        assert layer["datasetId"] == output["resultDatasetId"]
        assert layer["geometryType"] == "Polygon"
        assert layer["dataRef"] == output["dataRef"]
        map_command = event_payloads(response.text, "map.command")[0]["data"]
        assert map_command["action"] == "layer.addDataset"
        assert map_command["datasetId"] == output["resultDatasetId"]
        assert model_client.tool_results == []
        completed_message = event_payloads(response.text, "message.completed")[0]["data"][
            "message"
        ]["content"]
        assert (
            "输入点先临时重投影到 EPSG:32648 进行 50000 米缓冲计算；"
            "结果几何已回写为 EPSG:4326 GeoJSON，bbox 使用 WGS84 经纬度，"
            "面积按 processingCRS=EPSG:32648 计算。"
        ) in completed_message
        assert "输出 geometry 以 EPSG:32648 存储" not in completed_message
        assert "EPSG:32648 GeoJSON" not in completed_message
    finally:
        clear_overrides()


def test_ai_chat_executes_recent_buffer_plan_target_from_session_history(
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
    write_sichuan_polygon_dataset(storage)
    target_summary = write_airport_spatial_filter_result(
        storage,
        dataset_id="dataset_87b7d6c8183c",
    )
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
        session_id = "session_26063812-dcbe-42b7-b398-afd63b7eb285"

        plan_response = client.post(
            f"/api/ai-chat/sessions/{session_id}/messages",
            headers=auth_headers(token),
            json={
                "message": (
                    "基于刚才生成的机场 空间筛选结果图层，生成 50 公里缓冲区分析计划。"
                    "只生成计划，不要执行任何工具，不重新执行机场筛选，不生成新图层。"
                ),
                "selectedDatasetIds": [
                    "sample_airports",
                    "sample_ports",
                    "sample_populated_places",
                    "dataset_f2838ae521d6",
                    "dataset_87b7d6c8183c",
                ],
                "metadata": {
                    "mapView": {
                        "center": [103.956136, 30.581071],
                        "bbox": [103.95613, 30.581068, 103.956143, 30.581075],
                        "crs": "EPSG:4326",
                    },
                    "layers": [
                        {
                            "id": "layer_sample_airports",
                            "layerId": "layer_sample_airports",
                            "datasetId": "sample_airports",
                            "name": "机场",
                            "visible": True,
                            "geometryType": "Point",
                        },
                        {
                            "id": "layer_to_Z_GF51SOUbjjU",
                            "layerId": "layer_to_Z_GF51SOUbjjU",
                            "datasetId": "dataset_f2838ae521d6",
                            "name": "四川省",
                            "visible": True,
                            "geometryType": "MultiPolygon",
                        },
                        {
                            "id": "layer_filter_result",
                            "layerId": "layer_filter_result",
                            "datasetId": "dataset_87b7d6c8183c",
                            "name": "机场 空间筛选",
                            "visible": True,
                            "geometryType": "Point",
                            "bbox": target_summary.bbox,
                        },
                    ],
                    "activeDatasetIds": [
                        "sample_airports",
                        "sample_ports",
                        "sample_populated_places",
                        "dataset_f2838ae521d6",
                        "dataset_87b7d6c8183c",
                    ],
                },
            },
        )

        assert plan_response.status_code == 200
        plan_data = event_payloads(plan_response.text, "plan.created")[0]["data"]
        assert plan_data["targetDatasetId"] == "dataset_87b7d6c8183c"

        execute_response = client.post(
            f"/api/ai-chat/sessions/{session_id}/messages",
            headers=auth_headers(token),
            json={
                "message": (
                    "请执行刚才的 50 公里缓冲区分析计划，只使用“机场 空间筛选”"
                    "结果图层作为输入，生成缓冲区结果图层，并返回 resultDatasetId、"
                    "图层名称、几何类型、bbox、面积估算、使用的坐标系/距离单位处理方式。"
                ),
                "selectedDatasetIds": [
                    "sample_airports",
                    "sample_ports",
                    "sample_populated_places",
                    "dataset_f2838ae521d6",
                ],
                "metadata": {
                    "mapView": {
                        "center": [103.956136, 30.581071],
                        "height": 1,
                        "bbox": [103.95613, 30.581068, 103.956143, 30.581075],
                        "crs": "EPSG:4326",
                    },
                    "layers": [
                        {
                            "id": "layer_sample_airports",
                            "layerId": "layer_sample_airports",
                            "datasetId": "sample_airports",
                            "name": "机场",
                            "visible": True,
                            "geometryType": "Point",
                        },
                        {
                            "id": "layer_sample_ports",
                            "layerId": "layer_sample_ports",
                            "datasetId": "sample_ports",
                            "name": "港口",
                            "visible": True,
                            "geometryType": "Point",
                        },
                        {
                            "id": "layer_sample_populated_places",
                            "layerId": "layer_sample_populated_places",
                            "datasetId": "sample_populated_places",
                            "name": "人口稠密地区",
                            "visible": True,
                            "geometryType": "Point",
                        },
                        {
                            "id": "layer_to_Z_GF51SOUbjjU",
                            "layerId": "layer_to_Z_GF51SOUbjjU",
                            "datasetId": "dataset_f2838ae521d6",
                            "name": "四川省",
                            "visible": True,
                            "geometryType": "MultiPolygon",
                        },
                    ],
                    "activeDatasetIds": [
                        "sample_airports",
                        "sample_ports",
                        "sample_populated_places",
                        "dataset_f2838ae521d6",
                    ],
                },
            },
        )

        assert execute_response.status_code == 200
        assert event_names(execute_response.text) == [
            "data.summary",
            "tool.started",
            "tool.completed",
            "layer.created",
            "map.command",
            "message.delta",
            "message.completed",
        ]
        started = event_payloads(execute_response.text, "tool.started")[0]["data"]
        assert started["toolName"] == "geoprocess"
        assert started["input"]["operation"] == "buffer"
        assert started["input"]["inputDatasetId"] == "dataset_87b7d6c8183c"
        assert started["input"]["distance"] == 50000
        assert event_payloads(execute_response.text, "data.summary")[0]["data"][
            "effectiveDatasetIds"
        ] == ["dataset_87b7d6c8183c"]
        completed = event_payloads(execute_response.text, "tool.completed")[0]["data"]
        assert completed["toolName"] == "geoprocess"
        assert completed["output"]["geometryType"] == "Polygon"
        assert completed["output"]["lineage"]["inputDatasetId"] == "dataset_87b7d6c8183c"
        assert '"toolName": "spatial_filter"' not in execute_response.text
    finally:
        clear_overrides()


def test_ai_chat_executes_buffer_plan_prefers_run_event_target_over_bad_history(
    tmp_path: Path,
) -> None:
    settings.auth_storage_root = str(tmp_path / "auth")
    settings.gis_storage_root = str(tmp_path / "gis")
    settings.ai_chat_storage_root = str(tmp_path / "ai-chat")
    settings.auth_username = "admin"
    settings.auth_password = "secret"
    settings.auth_token_secret = "test-secret"
    settings.auth_token_expire_minutes = 60
    settings.database_url = f"sqlite:///{tmp_path / 'agent-runs.sqlite'}"
    storage = GisDataStorage(settings.gis_storage_root)
    write_sichuan_polygon_dataset(storage)
    target_summary = write_airport_spatial_filter_result(
        storage,
        dataset_id="dataset_87b7d6c8183c",
    )
    bad_summary = write_invalid_airport_self_filter_result(storage)
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
            run_repository=AgentRunRepository(create_engine(settings.database_url)),
            model_client=model_client,
        )

    app.dependency_overrides[get_ai_chat_service] = fake_service
    try:
        client = TestClient(app)
        token = login(client)
        session_id = "session_bad_history_buffer_plan"

        plan_response = client.post(
            f"/api/ai-chat/sessions/{session_id}/messages",
            headers=auth_headers(token),
            json={
                "message": (
                    "基于刚才生成的机场 空间筛选结果图层，生成 50 公里缓冲区分析计划。"
                    "只生成计划，不要执行任何工具，不重新执行机场筛选，不生成新图层。"
                ),
                "selectedDatasetIds": [
                    "sample_airports",
                    "sample_ports",
                    "sample_populated_places",
                    "dataset_f2838ae521d6",
                    bad_summary.dataset_id,
                    target_summary.dataset_id,
                ],
                "metadata": {
                    "mapView": {
                        "center": [103.956136, 30.581071],
                        "bbox": [103.95613, 30.581068, 103.956143, 30.581075],
                        "crs": "EPSG:4326",
                    },
                    "layers": [
                        {
                            "id": "layer_bad_filter",
                            "layerId": "layer_bad_filter",
                            "datasetId": bad_summary.dataset_id,
                            "name": "机场 空间筛选",
                            "visible": True,
                            "geometryType": "Point",
                            "bbox": bad_summary.bbox,
                        },
                        {
                            "id": "layer_target_filter",
                            "layerId": "layer_target_filter",
                            "datasetId": target_summary.dataset_id,
                            "name": "机场 空间筛选",
                            "visible": True,
                            "geometryType": "Point",
                            "bbox": target_summary.bbox,
                        },
                    ],
                    "activeDatasetIds": [
                        "sample_airports",
                        "sample_ports",
                        "sample_populated_places",
                        "dataset_f2838ae521d6",
                        bad_summary.dataset_id,
                        target_summary.dataset_id,
                    ],
                },
            },
        )

        assert plan_response.status_code == 200
        plan_data = event_payloads(plan_response.text, "plan.created")[0]["data"]
        assert plan_data["targetDatasetId"] == target_summary.dataset_id

        repository = AiChatRepository(settings.ai_chat_storage_root)
        session = repository.get("default", session_id)
        assert session is not None
        repository.save("default", session.model_copy(update={"plan_payloads": []}))

        execute_response = client.post(
            f"/api/ai-chat/sessions/{session_id}/messages",
            headers=auth_headers(token),
            json={
                "message": (
                    "请执行刚才的 50 公里缓冲区分析计划，只使用“机场 空间筛选”"
                    "结果图层作为输入，生成缓冲区结果图层，并返回 resultDatasetId、"
                    "图层名称、几何类型、bbox、面积估算、使用的坐标系/距离单位处理方式。"
                ),
                "selectedDatasetIds": [
                    "sample_airports",
                    "sample_ports",
                    "sample_populated_places",
                    "dataset_f2838ae521d6",
                    bad_summary.dataset_id,
                ],
                "metadata": {
                    "mapView": {
                        "center": [103.956136, 30.581071],
                        "height": 1,
                        "bbox": [103.95613, 30.581068, 103.956143, 30.581075],
                        "crs": "EPSG:4326",
                    },
                    "layers": [
                        {
                            "id": "layer_bad_filter",
                            "layerId": "layer_bad_filter",
                            "datasetId": bad_summary.dataset_id,
                            "name": "机场 空间筛选",
                            "visible": True,
                            "geometryType": "Point",
                            "bbox": bad_summary.bbox,
                        }
                    ],
                    "activeDatasetIds": [
                        "sample_airports",
                        "sample_ports",
                        "sample_populated_places",
                        "dataset_f2838ae521d6",
                        bad_summary.dataset_id,
                    ],
                },
            },
        )

        assert execute_response.status_code == 200
        started = event_payloads(execute_response.text, "tool.started")[0]["data"]
        assert started["toolName"] == "geoprocess"
        assert started["input"]["operation"] == "buffer"
        assert started["input"]["inputDatasetId"] == target_summary.dataset_id
        assert started["input"]["inputDatasetId"] != bad_summary.dataset_id
        summary_event = event_payloads(execute_response.text, "data.summary")[0]["data"]
        assert summary_event["effectiveDatasetIds"] == [target_summary.dataset_id]
        completed = event_payloads(execute_response.text, "tool.completed")[0]["data"]
        assert completed["output"]["lineage"]["inputDatasetId"] == target_summary.dataset_id
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
                "message": "schools 图层里面名称为 A School 的要素帮我单独提取出来创建为一个图层",
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
        completed = event_payloads(response.text, "tool.completed")[0]["data"]
        output = completed["output"]
        assert completed["toolName"] == "geoprocess"
        assert completed["status"] == "completed"
        assert output["summary"]["operation"] == "attribute_filter"
        assert output["summary"]["filter"] == {
            "field": "name",
            "operator": "eq",
            "value": "A School",
        }

        layer = event_payloads(response.text, "layer.created")[0]["data"]
        assert layer["datasetId"] == output["resultDatasetId"]
        assert layer["name"] == output["summary"]["result"]["name"]
        assert layer["metadata"] == {
            "sourceDatasetId": source_summary.dataset_id,
            "operation": "attribute_filter",
        }

        assert event_payloads(response.text, "map.command")[0]["data"] == {
            "action": "layer.addDataset",
            "datasetId": output["resultDatasetId"],
            "name": layer["name"],
            "visible": True,
            "flyTo": True,
        }

        session = client.get(
            "/api/ai-chat/sessions/session_filter",
            headers=auth_headers(token),
        ).json()["session"]
        result_dataset_id = session["toolCalls"][0]["output"]["summary"]["resultDatasetId"]
        preview_response = client.get(f"/api/datasets/{result_dataset_id}/preview")
        assert preview_response.status_code == 200
        features = preview_response.json()["data"]["features"]
        assert len(features) == 1
        assert features[0]["properties"]["name"] == "A School"
    finally:
        clear_overrides()


def test_ai_chat_attribute_filter_uses_named_layer_as_input_dataset(tmp_path: Path) -> None:
    settings.auth_storage_root = str(tmp_path / "auth")
    settings.gis_storage_root = str(tmp_path / "gis")
    settings.ai_chat_storage_root = str(tmp_path / "ai-chat")
    settings.auth_username = "admin"
    settings.auth_password = "secret"
    settings.auth_token_secret = "test-secret"
    settings.auth_token_expire_minutes = 60
    storage = GisDataStorage(settings.gis_storage_root)
    sichuan_summary = write_sichuan_city_dataset(storage)

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
            "/api/ai-chat/sessions/session_sichuan_attribute_filter/messages",
            headers=auth_headers(token),
            json={
                "message": "四川省这个图层里面的名称为德阳的要素帮我单独提取出来创建为一个图层",
                "selectedDatasetIds": [
                    "sample_airports",
                    "sample_ports",
                    "sample_populated_places",
                    sichuan_summary.dataset_id,
                ],
                "metadata": {
                    "layers": [
                        {
                            "layerId": "layer_sample_airports",
                            "datasetId": "sample_airports",
                            "name": "机场",
                        },
                        {
                            "layerId": "layer_sichuan",
                            "datasetId": sichuan_summary.dataset_id,
                            "name": "四川省",
                        },
                    ]
                },
            },
        )

        assert response.status_code == 200
        assert event_names(response.text)[:5] == [
            "data.summary",
            "tool.started",
            "tool.completed",
            "layer.created",
            "map.command",
        ]
        started = event_payloads(response.text, "tool.started")[0]["data"]
        assert started["toolName"] == "geoprocess"
        assert started["input"]["operation"] == "attribute_filter"
        assert started["input"]["inputDatasetId"] == sichuan_summary.dataset_id
        assert started["input"]["field"] == "name"

        completed = event_payloads(response.text, "tool.completed")[0]["data"]
        output = completed["output"]
        assert output["summary"]["sourceDatasetId"] == sichuan_summary.dataset_id
        assert output["featureCount"] == 1
        layer = event_payloads(response.text, "layer.created")[0]["data"]
        assert layer["name"] == "四川省 属性筛选"
        assert event_payloads(response.text, "map.command")[0]["data"]["datasetId"] == output[
            "resultDatasetId"
        ]
    finally:
        clear_overrides()


def test_ai_chat_updates_point_style_without_running_tools(tmp_path: Path) -> None:
    configure_app(tmp_path)
    try:
        client = TestClient(app)
        token = login(client)
        response = client.post(
            "/api/ai-chat/sessions/session_style_point/messages",
            headers=auth_headers(token),
            json={
                "message": "把 schools 图层改成半透明红色，点大小设为 12",
                "selectedDatasetIds": ["dataset_schools"],
                "metadata": {
                    "layers": [
                        {
                            "layerId": "layer_schools",
                            "datasetId": "dataset_schools",
                            "name": "schools",
                            "geometryType": "Point",
                            "editable": {"style": True},
                        }
                    ],
                    "clientCapabilities": {"mapCommands": ["layer.updateStyle"]},
                },
            },
        )

        assert response.status_code == 200
        assert event_names(response.text) == [
            "data.summary",
            "map.command",
            "message.delta",
            "message.completed",
        ]
        assert event_payloads(response.text, "tool.started") == []
        assert event_payloads(response.text, "layer.created") == []
        assert event_payloads(response.text, "map.command")[0]["data"] == {
            "action": "layer.updateStyle",
            "layerId": "layer_schools",
            "style": {
                "point": {"color": "rgba(255, 0, 0, 0.5)", "pixelSize": 12}
            },
        }
    finally:
        clear_overrides()


def test_ai_chat_updates_line_and_polygon_style_contracts(tmp_path: Path) -> None:
    configure_app(tmp_path)
    try:
        client = TestClient(app)
        token = login(client)
        base_metadata = {
            "clientCapabilities": {"mapCommands": ["layer.updateStyle"]},
        }
        line_response = client.post(
            "/api/ai-chat/sessions/session_style_line/messages",
            headers=auth_headers(token),
            json={
                "message": "把港口线改成蓝色，线宽到 4",
                "metadata": {
                    **base_metadata,
                    "layers": [
                        {
                            "layerId": "layer_ports",
                            "datasetId": "sample_ports",
                            "name": "港口线",
                            "geometryType": "LineString",
                            "editable": {"style": True},
                        }
                    ],
                },
            },
        )
        polygon_response = client.post(
            "/api/ai-chat/sessions/session_style_polygon/messages",
            headers=auth_headers(token),
            json={
                "message": "把人口区填充为橙色",
                "metadata": {
                    **base_metadata,
                    "layers": [
                        {
                            "layerId": "layer_population",
                            "datasetId": "dataset_population",
                            "name": "人口区",
                            "geometryType": "Polygon",
                            "editable": {"style": True},
                        }
                    ],
                },
            },
        )

        assert event_payloads(line_response.text, "map.command")[0]["data"]["style"] == {
            "line": {"color": "#0000FF", "width": 4}
        }
        assert event_payloads(polygon_response.text, "map.command")[0]["data"]["style"] == {
            "polygon": {"fillColor": "#FFA500"}
        }
    finally:
        clear_overrides()


def test_ai_chat_beautifies_named_polygon_layer_without_running_tools(tmp_path: Path) -> None:
    configure_app(tmp_path)
    try:
        client = TestClient(app)
        token = login(client)
        response = client.post(
            "/api/ai-chat/sessions/session_style_beautify/messages",
            headers=auth_headers(token),
            json={
                "message": "四川省这个图层换一个好看点的样式",
                "metadata": {
                    "layers": [
                        {
                            "layerId": "layer_sample_airports",
                            "datasetId": "sample_airports",
                            "name": "机场",
                            "geometryType": "Point",
                            "editable": {"style": True},
                        },
                        {
                            "layerId": "layer_to_Z_GF51SOUbjjU",
                            "datasetId": "dataset_f2838ae521d6",
                            "name": "四川省",
                            "geometryType": "MultiPolygon",
                            "editable": {"style": True},
                        },
                    ],
                    "clientCapabilities": {"mapCommands": ["layer.updateStyle"]},
                },
            },
        )

        assert response.status_code == 200
        assert event_payloads(response.text, "tool.started") == []
        assert event_payloads(response.text, "map.command")[0]["data"] == {
            "action": "layer.updateStyle",
            "layerId": "layer_to_Z_GF51SOUbjjU",
            "style": {
                "polygon": {
                    "fillColor": "#E6F2FF",
                    "outlineColor": "#0047AB",
                    "outlineWidth": 2,
                }
            },
        }
    finally:
        clear_overrides()


def test_ai_chat_style_ambiguity_and_missing_capability_do_not_send_commands(
    tmp_path: Path,
) -> None:
    configure_app(tmp_path)
    try:
        client = TestClient(app)
        token = login(client)
        ambiguous_response = client.post(
            "/api/ai-chat/sessions/session_style_ambiguous/messages",
            headers=auth_headers(token),
            json={
                "message": "把机场图层改成红色",
                "metadata": {
                    "layers": [
                        {
                            "layerId": "layer_airports_a",
                            "datasetId": "sample_airports",
                            "name": "机场",
                            "geometryType": "Point",
                            "editable": {"style": True},
                        },
                        {
                            "layerId": "layer_airports_b",
                            "datasetId": "dataset_airports_copy",
                            "name": "机场",
                            "geometryType": "Point",
                            "editable": {"style": True},
                        },
                    ],
                    "clientCapabilities": {"mapCommands": ["layer.updateStyle"]},
                },
            },
        )
        unsupported_response = client.post(
            "/api/ai-chat/sessions/session_style_no_capability/messages",
            headers=auth_headers(token),
            json={
                "message": "把当前图层改成红色",
                "metadata": {
                    "activeLayerId": "layer_airports",
                    "layers": [
                        {
                            "layerId": "layer_airports",
                            "datasetId": "sample_airports",
                            "name": "机场",
                            "geometryType": "Point",
                            "editable": {"style": True},
                        }
                    ],
                    "clientCapabilities": {"mapCommands": []},
                },
            },
        )

        assert event_payloads(ambiguous_response.text, "map.command") == []
        clarification = event_payloads(ambiguous_response.text, "clarification")[0]["data"]
        assert clarification["reason"] == "ambiguous_target"
        assert len(clarification["candidates"]) == 2
        assert event_payloads(unsupported_response.text, "map.command") == []
        assert event_payloads(unsupported_response.text, "clarification") == []
        completed = event_payloads(unsupported_response.text, "message.completed")[0]["data"]
        assert "layer.updateStyle" in completed["message"]["content"]
    finally:
        clear_overrides()


def test_ai_chat_emits_style_before_map_display_commands(tmp_path: Path) -> None:
    configure_app(tmp_path)
    try:
        client = TestClient(app)
        token = login(client)
        response = client.post(
            "/api/ai-chat/sessions/session_style_display/messages",
            headers=auth_headers(token),
            json={
                "message": "把 schools 图层改成红色后定位并高亮显示",
                "metadata": {
                    "layers": [
                        {
                            "layerId": "layer_schools",
                            "datasetId": "dataset_schools",
                            "name": "schools",
                            "geometryType": "Point",
                            "bbox": [116.1, 39.7, 116.3, 39.9],
                            "editable": {"style": True},
                        }
                    ],
                    "clientCapabilities": {
                        "mapCommands": [
                            "layer.updateStyle",
                            "camera.flyTo",
                            "overlay.addMarker",
                        ]
                    },
                },
            },
        )

        commands = [event["data"] for event in event_payloads(response.text, "map.command")]
        assert [command["action"] for command in commands] == [
            "layer.updateStyle",
            "camera.flyTo",
            "overlay.addMarker",
        ]
        assert commands[0]["style"] == {"point": {"color": "#FF0000"}}
        assert event_payloads(response.text, "tool.started") == []
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
