from datetime import UTC, datetime

from geo_agent_service.modules.gis_data.sample_datasets import SAMPLE_DATASETS
from geo_agent_service.modules.layer_tree.schemas import LayerTreeNode

DEFAULT_USER_LAYERS_FOLDER_ID = "user-layers"


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
                LayerTreeNode(
                    id=f"layer_{dataset.dataset_id}",
                    name=dataset.name,
                    parentId="business-layers",
                    datasetId=dataset.dataset_id,
                    sourceType="sample",
                    iconKey=dataset.icon_key,
                    userManaged=False,
                    createdAt=created_at,
                    updatedAt=created_at,
                )
                for dataset in SAMPLE_DATASETS
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
