from pathlib import Path

import geopandas as gpd  # type: ignore[import-untyped]
import pytest
from shapely.geometry import LineString, Point, Polygon

from geo_agent_service.modules.gis_data.repository import DatasetRepository
from geo_agent_service.modules.gis_data.service import GisDatasetService
from geo_agent_service.modules.gis_data.storage import GisDataStorage
from geo_agent_service.tools.spatial_filter import SpatialFilterTool


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


async def test_spatial_filter_within_returns_rows_and_result_dataset(
    tmp_path: Path,
) -> None:
    service = make_service(tmp_path)
    input_dataset_id = register_source(
        service,
        gpd.GeoDataFrame(
            {
                "name": ["Inside Airport", "Outside Airport"],
                "iata_code": ["INA", "OUT"],
                "type": ["major", "mid"],
            },
            geometry=[Point(0.5, 0.5), Point(3, 3)],
            crs="EPSG:4326",
        ),
        name="airports",
    )
    mask_dataset_id = register_source(
        service,
        gpd.GeoDataFrame(
            {"name": ["mask"]},
            geometry=[
                Polygon(
                    [
                        (0, 0),
                        (1, 0),
                        (1, 1),
                        (0, 1),
                        (0, 0),
                    ]
                )
            ],
            crs="EPSG:4326",
        ),
        name="area",
    )

    result = await SpatialFilterTool(service).run(
        {
            "inputDatasetId": input_dataset_id,
            "maskDatasetId": mask_dataset_id,
            "predicate": "within",
            "outputFields": ["name", "iata_code", "type"],
            "toolCallId": "tool_spatial",
        }
    )

    assert result.summary["featureCount"] == 1
    assert result.summary["rows"] == [
        {"name": "Inside Airport", "iata_code": "INA", "type": "major"}
    ]
    result_dataset_id = result.summary["resultDatasetId"]
    assert result_dataset_id.startswith("dataset_")
    assert result.layer is not None
    assert result.layer["datasetId"] == result_dataset_id
    assert result.map_command == {
        "action": "layer.addDataset",
        "datasetId": result_dataset_id,
        "name": "airports 空间筛选",
        "visible": True,
        "flyTo": True,
    }
    preview = service.preview_dataset(result_dataset_id)
    assert preview.feature_count == 1
    assert preview.data["features"][0]["properties"] == {
        "name": "Inside Airport",
        "iata_code": "INA",
        "type": "major",
    }


async def test_spatial_filter_intersects_returns_crossing_feature(
    tmp_path: Path,
) -> None:
    service = make_service(tmp_path)
    input_dataset_id = register_source(
        service,
        gpd.GeoDataFrame(
            {"name": ["crossing", "outside"]},
            geometry=[
                LineString([(-1, 0.5), (2, 0.5)]),
                LineString([(2, 2), (3, 3)]),
            ],
            crs="EPSG:4326",
        ),
    )
    mask_dataset_id = register_source(
        service,
        gpd.GeoDataFrame(
            {"name": ["mask"]},
            geometry=[
                Polygon(
                    [
                        (0, 0),
                        (1, 0),
                        (1, 1),
                        (0, 1),
                        (0, 0),
                    ]
                )
            ],
            crs="EPSG:4326",
        ),
    )

    result = await SpatialFilterTool(service).run(
        {
            "inputDatasetId": input_dataset_id,
            "maskDatasetId": mask_dataset_id,
            "predicate": "intersects",
            "outputFields": ["name"],
        }
    )

    assert result.summary["featureCount"] == 1
    assert result.summary["rows"] == [{"name": "crossing"}]


async def test_spatial_filter_repairs_invalid_mask_geometry(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    input_dataset_id = register_source(
        service,
        gpd.GeoDataFrame(
            {"name": ["inside", "outside"]},
            geometry=[Point(0.5, 0.25), Point(3, 3)],
            crs="EPSG:4326",
        ),
    )
    mask_dataset_id = register_source(
        service,
        gpd.GeoDataFrame(
            {"name": ["invalid-mask"]},
            geometry=[
                Polygon(
                    [
                        (0, 0),
                        (2, 2),
                        (0, 2),
                        (2, 0),
                        (0, 0),
                    ]
                )
            ],
            crs="EPSG:4326",
        ),
    )

    result = await SpatialFilterTool(service).run(
        {
            "inputDatasetId": input_dataset_id,
            "maskDatasetId": mask_dataset_id,
            "predicate": "within",
            "outputFields": ["name"],
        }
    )

    assert result.summary["featureCount"] == 1
    assert result.summary["rows"] == [{"name": "inside"}]


async def test_spatial_filter_reports_invalid_input(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    input_dataset_id = register_source(
        service,
        gpd.GeoDataFrame({"name": ["A"]}, geometry=[Point(0, 0)], crs="EPSG:4326"),
    )
    mask_dataset_id = register_source(
        service,
        gpd.GeoDataFrame(
            {"name": ["mask"]},
            geometry=[
                Polygon(
                    [
                        (-1, -1),
                        (1, -1),
                        (1, 1),
                        (-1, 1),
                        (-1, -1),
                    ]
                )
            ],
            crs="EPSG:4326",
        ),
    )
    tool = SpatialFilterTool(service)

    with pytest.raises(ValueError, match="requires inputDatasetId"):
        await tool.run(
            {
                "maskDatasetId": mask_dataset_id,
                "predicate": "within",
                "outputFields": ["name"],
            }
        )
    with pytest.raises(ValueError, match="requires maskDatasetId"):
        await tool.run(
            {
                "inputDatasetId": input_dataset_id,
                "predicate": "within",
                "outputFields": ["name"],
            }
        )
    with pytest.raises(ValueError, match="requires predicate"):
        await tool.run(
            {
                "inputDatasetId": input_dataset_id,
                "maskDatasetId": mask_dataset_id,
                "outputFields": ["name"],
            }
        )
    with pytest.raises(ValueError, match="requires outputFields"):
        await tool.run(
            {
                "inputDatasetId": input_dataset_id,
                "maskDatasetId": mask_dataset_id,
                "predicate": "within",
            }
        )
    with pytest.raises(ValueError, match="Unsupported spatial_filter predicate"):
        await tool.run(
            {
                "inputDatasetId": input_dataset_id,
                "maskDatasetId": mask_dataset_id,
                "predicate": "contains",
                "outputFields": ["name"],
            }
        )
    with pytest.raises(ValueError, match="outputFields not found"):
        await tool.run(
            {
                "inputDatasetId": input_dataset_id,
                "maskDatasetId": mask_dataset_id,
                "predicate": "within",
                "outputFields": ["missing"],
            }
        )


async def test_spatial_filter_rejects_one_sided_missing_crs(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    input_dataset_id = register_source(
        service,
        gpd.GeoDataFrame({"name": ["A"]}, geometry=[Point(0, 0)], crs="EPSG:4326"),
    )
    mask_dataset_id = register_source(
        service,
        gpd.GeoDataFrame(
            {"name": ["mask"]},
            geometry=[
                Polygon(
                    [
                        (-1, -1),
                        (1, -1),
                        (1, 1),
                        (-1, 1),
                        (-1, -1),
                    ]
                )
            ],
        ),
    )

    with pytest.raises(ValueError, match="one dataset is missing CRS"):
        await SpatialFilterTool(service).run(
            {
                "inputDatasetId": input_dataset_id,
                "maskDatasetId": mask_dataset_id,
                "predicate": "within",
                "outputFields": ["name"],
            }
        )
