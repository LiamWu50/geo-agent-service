from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from geo_agent_service.core.config import settings
from geo_agent_service.main import app


def geojson_bytes() -> bytes:
    return b"""
    {
      "type": "FeatureCollection",
      "features": [
        {
          "type": "Feature",
          "properties": {"name": "School A", "students": 120, "active": true},
          "geometry": {"type": "Point", "coordinates": [116.1, 39.7]}
        },
        {
          "type": "Feature",
          "properties": {"name": "School B", "students": 80, "active": false},
          "geometry": {"type": "Point", "coordinates": [116.2, 39.8]}
        }
      ]
    }
    """


def geojson_with_array_property_bytes() -> bytes:
    return b"""
    {
      "type": "FeatureCollection",
      "features": [
        {
          "type": "Feature",
          "properties": {"name": "Area A", "center": [126.6, 45.7]},
          "geometry": {
            "type": "Polygon",
            "coordinates": [[
              [126.0, 45.0],
              [127.0, 45.0],
              [127.0, 46.0],
              [126.0, 46.0],
              [126.0, 45.0]
            ]]
          }
        }
      ]
    }
    """


def test_upload_geojson_returns_input_data_summary(tmp_path: Path) -> None:
    settings.gis_storage_root = str(tmp_path / "gis")
    client = TestClient(app)

    response = client.post(
        "/api/datasets",
        data={"name": "schools"},
        files={"file": ("schools.geojson", geojson_bytes(), "application/geo+json")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["datasetId"].startswith("dataset_")
    assert payload["name"] == "schools"
    assert payload["sourceType"] == "upload"
    assert payload["geometryType"] == "Point"
    assert payload["featureCount"] == 2
    assert payload["bbox"] == [116.1, 39.7, 116.2, 39.8]
    assert payload["dataRef"].startswith("storage://normalized/")
    assert "CRS is missing" in payload["warnings"][0]
    assert {field["name"] for field in payload["fields"]} == {"name", "students", "active"}


def test_upload_rejects_unsupported_file_extension(tmp_path: Path) -> None:
    settings.gis_storage_root = str(tmp_path / "gis")
    client = TestClient(app)

    response = client.post(
        "/api/datasets",
        files={"file": ("schools.txt", b"not geojson", "text/plain")},
    )

    assert response.status_code == 400


def test_upload_rejects_empty_geojson_file(tmp_path: Path) -> None:
    settings.gis_storage_root = str(tmp_path / "gis")
    client = TestClient(app)

    response = client.post(
        "/api/datasets",
        data={"name": "empty"},
        files={"file": ("empty.geojson", b"", "application/geo+json")},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Uploaded GeoJSON file is empty."}


def test_register_dataset_from_url_returns_input_data_summary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings.gis_storage_root = str(tmp_path / "gis")
    async_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://example.com/schools.geojson"
        return httpx.Response(
            status_code=200,
            content=geojson_bytes(),
            headers={"content-type": "application/geo+json"},
        )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: async_client(
            transport=httpx.MockTransport(handler),
            **kwargs,
        ),
    )
    client = TestClient(app)

    response = client.post(
        "/api/datasets/from-url",
        json={"name": "remote schools", "url": "https://example.com/schools.geojson"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "remote schools"
    assert payload["sourceType"] == "url"
    assert payload["geometryType"] == "Point"
    assert payload["featureCount"] == 2
    assert payload["dataRef"].startswith("storage://normalized/")

    list_response = client.get("/api/datasets")
    assert list_response.status_code == 200
    listed_ids = [item["datasetId"] for item in list_response.json()["datasets"]]
    assert listed_ids[:3] == ["sample_airports", "sample_ports", "sample_populated_places"]
    assert payload["datasetId"] in listed_ids


def test_register_dataset_from_url_rejects_non_json_response(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings.gis_storage_root = str(tmp_path / "gis")
    async_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            content=b"<html></html>",
            headers={"content-type": "text/html"},
        )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: async_client(
            transport=httpx.MockTransport(handler),
            **kwargs,
        ),
    )
    client = TestClient(app)

    response = client.post(
        "/api/datasets/from-url",
        json={"url": "https://example.com/page.html"},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "URL must return a GeoJSON or JSON response."}


def test_register_dataset_from_url_handles_array_properties(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings.gis_storage_root = str(tmp_path / "gis")
    async_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            content=geojson_with_array_property_bytes(),
            headers={"content-type": "application/json"},
        )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: async_client(
            transport=httpx.MockTransport(handler),
            **kwargs,
        ),
    )
    client = TestClient(app)

    response = client.post(
        "/api/datasets/from-url",
        json={"url": "https://geo.datav.aliyun.com/areas_v3/bound/geojson?code=230000_full"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["sourceType"] == "url"
    assert payload["geometryType"] == "Polygon"
    assert {field["name"] for field in payload["fields"]} == {"name", "center"}
    center_field = next(field for field in payload["fields"] if field["name"] == "center")
    assert center_field["uniqueCount"] == 1


def test_dataset_list_detail_and_preview(tmp_path: Path) -> None:
    settings.gis_storage_root = str(tmp_path / "gis")
    client = TestClient(app)

    upload_response = client.post(
        "/api/datasets",
        files={"file": ("schools.geojson", geojson_bytes(), "application/geo+json")},
    )
    dataset_id = upload_response.json()["datasetId"]

    list_response = client.get("/api/datasets")
    assert list_response.status_code == 200
    listed_ids = [item["datasetId"] for item in list_response.json()["datasets"]]
    assert listed_ids[:3] == ["sample_airports", "sample_ports", "sample_populated_places"]
    assert dataset_id in listed_ids

    detail_response = client.get(f"/api/datasets/{dataset_id}")
    assert detail_response.status_code == 200
    assert detail_response.json()["datasetId"] == dataset_id

    preview_response = client.get(f"/api/datasets/{dataset_id}/preview?limit=1")
    assert preview_response.status_code == 200
    preview = preview_response.json()
    assert preview["datasetId"] == dataset_id
    assert preview["featureCount"] == 2
    assert preview["returnedFeatureCount"] == 1
    assert preview["data"]["type"] == "FeatureCollection"
    assert len(preview["data"]["features"]) == 1


def test_sample_dataset_detail_and_preview(tmp_path: Path) -> None:
    settings.gis_storage_root = str(tmp_path / "gis")
    client = TestClient(app)

    detail_response = client.get("/api/datasets/sample_airports")
    preview_response = client.get("/api/datasets/sample_airports/preview?limit=2")

    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["datasetId"] == "sample_airports"
    assert detail["name"] == "机场"
    assert detail["sourceType"] == "sample"
    assert detail["geometryType"] == "Point"

    assert preview_response.status_code == 200
    preview = preview_response.json()
    assert preview["datasetId"] == "sample_airports"
    assert preview["returnedFeatureCount"] == 2
    assert preview["data"]["type"] == "FeatureCollection"


def test_missing_dataset_returns_404(tmp_path: Path) -> None:
    settings.gis_storage_root = str(tmp_path / "gis")
    client = TestClient(app)

    response = client.get("/api/datasets/dataset_missing")

    assert response.status_code == 404
