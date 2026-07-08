from pathlib import Path

import geopandas as gpd  # type: ignore[import-untyped]
import pytest
from shapely.geometry import Point, Polygon  # type: ignore[import-untyped]

from geo_agent_service.modules.gis_data.repository import DatasetRepository
from geo_agent_service.modules.gis_data.service import GisDatasetService
from geo_agent_service.modules.gis_data.storage import GisDataStorage
from geo_agent_service.tools.geoprocess import GeoprocessTool


def make_service(tmp_path: Path) -> GisDatasetService:
    storage = GisDataStorage(tmp_path / "gis")
    return GisDatasetService(
        storage=storage,
        repository=DatasetRepository(storage.metadata_path()),
    )


def register_source(
    service: GisDatasetService,
    geodata: gpd.GeoDataFrame,
    name: str = "source",
) -> str:
    summary = service.register_generated_dataset(
        name=name,
        geodata=geodata,
        source_tool_call_id="test_setup",
    )
    return summary.dataset_id


async def test_geoprocess_buffer_registers_polygon_dataset(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    dataset_id = register_source(
        service,
        gpd.GeoDataFrame(
            {"name": ["A"]},
            geometry=[Point(116.1, 39.7)],
            crs="EPSG:4326",
        ),
        name="schools",
    )

    result = await GeoprocessTool(service).run(
        {
            "operation": "buffer",
            "datasetId": dataset_id,
            "distance": 100,
            "unit": "meter",
            "toolCallId": "tool_buffer",
        }
    )

    output = result.summary["result"]
    assert output["sourceType"] == "generated"
    assert output["geometryType"] in {"Polygon", "MultiPolygon"}
    assert result.layer is not None
    assert result.layer["datasetId"] == output["datasetId"]
    assert result.map_command == {
        "action": "layer.addDataset",
        "datasetId": output["datasetId"],
        "name": "schools 缓冲区",
        "visible": True,
        "flyTo": True,
    }
    generated = service.get_dataset(output["datasetId"])
    assert generated.feature_count == 1


async def test_geoprocess_centroid_registers_point_dataset(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    dataset_id = register_source(
        service,
        gpd.GeoDataFrame(
            {"name": ["Area"]},
            geometry=[
                Polygon(
                    [
                        (116.0, 39.0),
                        (117.0, 39.0),
                        (117.0, 40.0),
                        (116.0, 40.0),
                        (116.0, 39.0),
                    ]
                )
            ],
            crs="EPSG:4326",
        ),
        name="areas",
    )

    result = await GeoprocessTool(service).run(
        {"operation": "centroid", "datasetId": dataset_id}
    )

    output = result.summary["result"]
    assert output["geometryType"] == "Point"
    assert output["featureCount"] == 1


async def test_geoprocess_bbox_clip_keeps_features_inside_bbox(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    dataset_id = register_source(
        service,
        gpd.GeoDataFrame(
            {"name": ["inside", "outside"]},
            geometry=[Point(0, 0), Point(10, 10)],
            crs="EPSG:4326",
        ),
    )

    result = await GeoprocessTool(service).run(
        {"operation": "bbox_clip", "datasetId": dataset_id, "bbox": [-1, -1, 1, 1]}
    )

    output = result.summary["result"]
    assert output["featureCount"] == 1
    preview = service.preview_dataset(output["datasetId"])
    assert preview.data["features"][0]["properties"]["name"] == "inside"


async def test_geoprocess_attribute_filter_by_equal_value(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    dataset_id = register_source(
        service,
        gpd.GeoDataFrame(
            {"name": ["A", "B", "C"], "type": ["school", "hospital", "school"]},
            geometry=[Point(0, 0), Point(1, 1), Point(2, 2)],
            crs="EPSG:4326",
        ),
    )

    result = await GeoprocessTool(service).run(
        {
            "operation": "attribute_filter",
            "datasetId": dataset_id,
            "field": "type",
            "operator": "eq",
            "value": "school",
        }
    )

    output = result.summary["result"]
    assert output["featureCount"] == 2
    assert result.summary["filter"] == {
        "field": "type",
        "operator": "eq",
        "value": "school",
    }
    preview = service.preview_dataset(output["datasetId"])
    assert [feature["properties"]["name"] for feature in preview.data["features"]] == [
        "A",
        "C",
    ]


async def test_geoprocess_attribute_filter_by_numeric_comparison(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    dataset_id = register_source(
        service,
        gpd.GeoDataFrame(
            {"name": ["small", "large"], "population": [100, 1200]},
            geometry=[Point(0, 0), Point(1, 1)],
            crs="EPSG:4326",
        ),
    )

    result = await GeoprocessTool(service).run(
        {
            "operation": "attribute_filter",
            "datasetId": dataset_id,
            "field": "population",
            "operator": "gt",
            "value": 1000,
        }
    )

    output = result.summary["result"]
    preview = service.preview_dataset(output["datasetId"])
    assert output["featureCount"] == 1
    assert preview.data["features"][0]["properties"]["name"] == "large"


async def test_geoprocess_attribute_filter_infers_spec_from_message(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    dataset_id = register_source(
        service,
        gpd.GeoDataFrame(
            {"name": ["Beijing Port", "Airport"], "kind": ["port", "airport"]},
            geometry=[Point(0, 0), Point(1, 1)],
            crs="EPSG:4326",
        ),
    )

    result = await GeoprocessTool(service).run(
        {
            "operation": "attribute_filter",
            "datasetId": dataset_id,
            "message": "筛选 name 包含 Beijing 的要素",
        }
    )

    output = result.summary["result"]
    preview = service.preview_dataset(output["datasetId"])
    assert output["featureCount"] == 1
    assert preview.data["features"][0]["properties"]["name"] == "Beijing Port"


async def test_geoprocess_reports_invalid_input(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    dataset_id = register_source(
        service,
        gpd.GeoDataFrame({"name": ["A"]}, geometry=[Point(0, 0)], crs="EPSG:4326"),
    )
    tool = GeoprocessTool(service)

    with pytest.raises(ValueError, match="requires a datasetId"):
        await tool.run({"operation": "centroid"})
    with pytest.raises(ValueError, match="Unsupported geoprocess operation"):
        await tool.run({"operation": "union", "datasetId": dataset_id})
    with pytest.raises(ValueError, match="requires a distance"):
        await tool.run({"operation": "buffer", "datasetId": dataset_id})
    with pytest.raises(ValueError, match="bbox must satisfy"):
        await tool.run(
            {"operation": "bbox_clip", "datasetId": dataset_id, "bbox": [1, 1, 0, 0]}
        )
    with pytest.raises(ValueError, match="requires a field"):
        await tool.run({"operation": "attribute_filter", "datasetId": dataset_id})
    with pytest.raises(ValueError, match="requires a value"):
        await tool.run(
            {"operation": "attribute_filter", "datasetId": dataset_id, "field": "name"}
        )
