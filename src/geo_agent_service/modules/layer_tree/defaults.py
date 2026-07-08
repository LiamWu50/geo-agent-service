from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

import geopandas as gpd  # type: ignore[import-untyped]

from geo_agent_service.modules.gis_data.sample_datasets import (
    SAMPLE_DATASETS,
    SampleDatasetDefinition,
)
from geo_agent_service.modules.layer_tree.schemas import LayerTreeNode

DEFAULT_USER_LAYERS_FOLDER_ID = "user-layers"


class SampleLayerMetadata(TypedDict):
    geometryType: str
    crs: str | None
    bbox: tuple[float, float, float, float] | None


def _sample_layer_metadata(path: Path) -> SampleLayerMetadata:
    geodata = gpd.read_file(path)
    geometry_types = sorted(
        {
            geometry_type
            for geometry_type in geodata.geometry.geom_type.dropna().unique().tolist()
            if geometry_type
        }
    )
    geometry_type = geometry_types[0] if len(geometry_types) == 1 else "Mixed"
    total_bounds = geodata.total_bounds
    return {
        "geometryType": geometry_type,
        "crs": geodata.crs.to_string() if geodata.crs is not None else None,
        "bbox": (
            float(total_bounds[0]),
            float(total_bounds[1]),
            float(total_bounds[2]),
            float(total_bounds[3]),
        )
        if len(total_bounds) == 4
        else None,
    }


def _sample_layer_node(
    dataset: SampleDatasetDefinition,
    created_at: datetime,
) -> LayerTreeNode:
    metadata = _sample_layer_metadata(dataset.path)
    return LayerTreeNode(
        id=f"layer_{dataset.dataset_id}",
        name=dataset.name,
        parentId="business-layers",
        datasetId=dataset.dataset_id,
        sourceType="sample",
        geometryType=metadata["geometryType"],
        crs=metadata["crs"],
        bbox=metadata["bbox"],
        iconKey=dataset.icon_key,
        userManaged=False,
        createdAt=created_at,
        updatedAt=created_at,
    )


def default_layer_tree() -> list[LayerTreeNode]:
    created_at = datetime.now(UTC)
    return [
        LayerTreeNode(
            id="basemap",
            name="底图",
            type="folder",
            iconKey="map",
            userManaged=False,
            createdAt=created_at,
            updatedAt=created_at,
            children=[
                LayerTreeNode(
                    id="basemap-imagery",
                    name="谷歌影像",
                    iconKey="satellite",
                    userManaged=False,
                    createdAt=created_at,
                    updatedAt=created_at,
                ),
                LayerTreeNode(
                    id="basemap-annotation",
                    name="影像注记",
                    iconKey="tags",
                    userManaged=False,
                    createdAt=created_at,
                    updatedAt=created_at,
                ),
            ],
        ),
        LayerTreeNode(
            id="business-layers",
            name="业务图层",
            type="folder",
            iconKey="layers",
            userManaged=False,
            createdAt=created_at,
            updatedAt=created_at,
            children=[
                _sample_layer_node(dataset, created_at) for dataset in SAMPLE_DATASETS
            ],
        ),
        LayerTreeNode(
            id=DEFAULT_USER_LAYERS_FOLDER_ID,
            name="用户图层",
            type="folder",
            iconKey="user-round",
            userManaged=False,
            createdAt=created_at,
            updatedAt=created_at,
            children=[],
        ),
        LayerTreeNode(
            id="analysis-layers",
            name="分析结果",
            type="folder",
            iconKey="square-dashed-mouse-pointer",
            userManaged=False,
            createdAt=created_at,
            updatedAt=created_at,
            children=[],
        ),
    ]
