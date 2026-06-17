from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

LayerNodeType = Literal["folder", "layer"]


class LayerTreeNode(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    type: LayerNodeType = "layer"
    parent_id: str | None = Field(default=None, alias="parentId")
    children: list["LayerTreeNode"] = Field(default_factory=list)
    dataset_id: str | None = Field(default=None, alias="datasetId")
    source_type: str | None = Field(default=None, alias="sourceType")
    geometry_type: str | None = Field(default=None, alias="geometryType")
    bbox: tuple[float, float, float, float] | None = None
    icon_key: str | None = Field(default=None, alias="iconKey")
    visible: bool = True
    opacity: float = Field(default=1.0, ge=0, le=1)
    user_managed: bool = Field(default=True, alias="userManaged")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="createdAt")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="updatedAt")


class LayerTreeResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_id: str = Field(alias="userId")
    nodes: list[LayerTreeNode]


class AddDatasetLayerRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    dataset_id: str = Field(alias="datasetId")
    name: str | None = None
    parent_id: str | None = Field(default=None, alias="parentId")
    position: int | None = Field(default=None, ge=0)
    visible: bool = True
    opacity: float = Field(default=1.0, ge=0, le=1)


class UpdateLayerNodeRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    visible: bool | None = None
    opacity: float | None = Field(default=None, ge=0, le=1)


class MoveLayerNodeRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    parent_id: str | None = Field(default=None, alias="parentId")
    position: int = Field(ge=0)
