from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from geo_agent_service.core.config import settings
from geo_agent_service.main import app


def configure_app(tmp_path: Path) -> None:
    settings.auth_storage_root = str(tmp_path / "auth")
    settings.gis_storage_root = str(tmp_path / "gis")
    settings.layer_tree_storage_root = str(tmp_path / "layer-trees")
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


def geojson_bytes(name: str, coordinates: tuple[float, float]) -> bytes:
    return f"""
    {{
      "type": "FeatureCollection",
      "features": [
        {{
          "type": "Feature",
          "properties": {{"name": "{name}"}},
          "geometry": {{"type": "Point", "coordinates": [{coordinates[0]}, {coordinates[1]}]}}
        }}
      ]
    }}
    """.encode()


def upload_dataset(client: TestClient, name: str) -> str:
    response = client.post(
        "/api/datasets",
        data={"name": name},
        files={"file": (f"{name}.geojson", geojson_bytes(name, (116.1, 39.7)), "application/json")},
    )
    assert response.status_code == 200
    return str(response.json()["datasetId"])


def user_layers(payload: dict[str, Any]) -> dict[str, Any]:
    return next(node for node in payload["nodes"] if node["id"] == "user-layers")


def business_layers(payload: dict[str, Any]) -> dict[str, Any]:
    return next(node for node in payload["nodes"] if node["id"] == "business-layers")


def analysis_layers(payload: dict[str, Any]) -> dict[str, Any]:
    return next(node for node in payload["nodes"] if node["id"] == "analysis-layers")


def test_layer_tree_requires_authentication(tmp_path: Path) -> None:
    configure_app(tmp_path)
    client = TestClient(app)

    response = client.get("/api/layer-tree")

    assert response.status_code == 401
    assert response.json() == {"detail": "Unauthorized."}


def test_first_read_returns_default_tree_with_empty_user_layers(tmp_path: Path) -> None:
    configure_app(tmp_path)
    client = TestClient(app)
    token = login(client)

    response = client.get("/api/layer-tree", headers=auth_headers(token))

    assert response.status_code == 200
    payload = response.json()
    assert payload["userId"] == "default"
    assert [node["id"] for node in payload["nodes"]] == [
        "basemap",
        "business-layers",
        "user-layers",
        "analysis-layers",
    ]
    business_children = business_layers(payload)["children"]
    assert [child["datasetId"] for child in business_children] == [
        "sample_airports",
        "sample_ports",
        "sample_populated_places",
    ]
    assert [child["name"] for child in business_children] == ["机场", "港口", "人口稠密地区"]
    assert {child["sourceType"] for child in business_children} == {"sample"}
    assert {child["userManaged"] for child in business_children} == {False}
    assert user_layers(payload)["children"] == []
    assert user_layers(payload)["userManaged"] is False
    assert analysis_layers(payload)["children"] == []


def test_add_dataset_layer_persists_under_user_layers(tmp_path: Path) -> None:
    configure_app(tmp_path)
    client = TestClient(app)
    token = login(client)
    dataset_id = upload_dataset(client, "schools")

    add_response = client.post(
        "/api/layer-tree/dataset-layers",
        headers=auth_headers(token),
        json={"datasetId": dataset_id},
    )
    get_response = client.get("/api/layer-tree", headers=auth_headers(token))

    assert add_response.status_code == 200
    added = add_response.json()
    assert added["name"] == "schools"
    assert added["datasetId"] == dataset_id
    assert added["sourceType"] == "upload"
    assert added["geometryType"] == "Point"
    assert added["parentId"] == "user-layers"
    assert added["userManaged"] is True

    assert get_response.status_code == 200
    children = user_layers(get_response.json())["children"]
    assert [child["id"] for child in children] == [added["id"]]
    assert children[0]["datasetId"] == dataset_id


def test_add_dataset_layer_rejects_missing_dataset(tmp_path: Path) -> None:
    configure_app(tmp_path)
    client = TestClient(app)
    token = login(client)

    response = client.post(
        "/api/layer-tree/dataset-layers",
        headers=auth_headers(token),
        json={"datasetId": "dataset_missing"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Dataset not found."}


def test_update_move_and_delete_user_layer(tmp_path: Path) -> None:
    configure_app(tmp_path)
    client = TestClient(app)
    token = login(client)
    first_dataset_id = upload_dataset(client, "schools")
    second_dataset_id = upload_dataset(client, "hospitals")

    first = client.post(
        "/api/layer-tree/dataset-layers",
        headers=auth_headers(token),
        json={"datasetId": first_dataset_id},
    ).json()
    second = client.post(
        "/api/layer-tree/dataset-layers",
        headers=auth_headers(token),
        json={"datasetId": second_dataset_id},
    ).json()

    update_response = client.patch(
        f"/api/layer-tree/nodes/{first['id']}",
        headers=auth_headers(token),
        json={"name": "学校点位", "visible": False, "opacity": 0.45},
    )
    move_response = client.post(
        f"/api/layer-tree/nodes/{second['id']}/move",
        headers=auth_headers(token),
        json={"parentId": "user-layers", "position": 0},
    )
    tree_after_move = client.get("/api/layer-tree", headers=auth_headers(token)).json()
    delete_response = client.delete(
        f"/api/layer-tree/nodes/{first['id']}",
        headers=auth_headers(token),
    )
    tree_after_delete = client.get("/api/layer-tree", headers=auth_headers(token)).json()

    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["name"] == "学校点位"
    assert updated["visible"] is False
    assert updated["opacity"] == 0.45

    assert move_response.status_code == 200
    assert [child["id"] for child in user_layers(tree_after_move)["children"]] == [
        second["id"],
        first["id"],
    ]

    assert delete_response.status_code == 204
    assert [child["id"] for child in user_layers(tree_after_delete)["children"]] == [second["id"]]


def test_default_nodes_are_protected(tmp_path: Path) -> None:
    configure_app(tmp_path)
    client = TestClient(app)
    token = login(client)

    update_response = client.patch(
        "/api/layer-tree/nodes/basemap",
        headers=auth_headers(token),
        json={"name": "custom"},
    )
    move_response = client.post(
        "/api/layer-tree/nodes/basemap/move",
        headers=auth_headers(token),
        json={"parentId": "user-layers", "position": 0},
    )
    delete_response = client.delete(
        "/api/layer-tree/nodes/basemap",
        headers=auth_headers(token),
    )

    assert update_response.status_code == 403
    assert move_response.status_code == 403
    assert delete_response.status_code == 403
