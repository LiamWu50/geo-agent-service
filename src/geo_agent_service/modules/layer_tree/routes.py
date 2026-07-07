from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status

from geo_agent_service.core.config import settings
from geo_agent_service.modules.auth.routes import (
    AuthServiceDependency,
    BearerTokenDependency,
    unauthorized_error,
)
from geo_agent_service.modules.auth.service import InvalidTokenError
from geo_agent_service.modules.gis_data.repository import DatasetRepository
from geo_agent_service.modules.gis_data.service import GisDatasetService
from geo_agent_service.modules.gis_data.storage import GisDataStorage
from geo_agent_service.modules.layer_tree.repository import LayerTreeRepository
from geo_agent_service.modules.layer_tree.schemas import (
    AddDatasetLayerRequest,
    LayerTreeNode,
    LayerTreeResponse,
    MoveLayerNodeRequest,
    UpdateLayerNodeRequest,
)
from geo_agent_service.modules.layer_tree.service import (
    LayerNodeNotFoundError,
    LayerNodeProtectedError,
    LayerParentInvalidError,
    LayerTreeService,
)

router = APIRouter(prefix="/layer-tree", tags=["layer-tree"])


def get_layer_tree_service() -> LayerTreeService:
    gis_storage = GisDataStorage(settings.gis_storage_root)
    dataset_repository = DatasetRepository(gis_storage.metadata_path())
    repository = LayerTreeRepository(settings.layer_tree_storage_root)
    return LayerTreeService(
        repository=repository,
        dataset_repository=dataset_repository,
        dataset_service=GisDatasetService(storage=gis_storage, repository=dataset_repository),
    )


LayerTreeServiceDependency = Annotated[LayerTreeService, Depends(get_layer_tree_service)]


def current_user_id(token: BearerTokenDependency, auth_service: AuthServiceDependency) -> str:
    try:
        return auth_service.get_current_user(token).id
    except InvalidTokenError as exc:
        raise unauthorized_error() from exc


CurrentUserIdDependency = Annotated[str, Depends(current_user_id)]


@router.get("", response_model=LayerTreeResponse)
async def get_layer_tree(
    user_id: CurrentUserIdDependency,
    service: LayerTreeServiceDependency,
) -> LayerTreeResponse:
    return LayerTreeResponse(userId=user_id, nodes=service.get_tree(user_id))


@router.post("/dataset-layers", response_model=LayerTreeNode)
async def add_dataset_layer(
    payload: AddDatasetLayerRequest,
    user_id: CurrentUserIdDependency,
    service: LayerTreeServiceDependency,
) -> LayerTreeNode:
    try:
        return service.add_dataset_layer(user_id=user_id, payload=payload)
    except LayerNodeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LayerParentInvalidError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/nodes/{node_id}", response_model=LayerTreeNode)
async def update_layer_node(
    node_id: str,
    payload: UpdateLayerNodeRequest,
    user_id: CurrentUserIdDependency,
    service: LayerTreeServiceDependency,
) -> LayerTreeNode:
    try:
        return service.update_node(user_id=user_id, node_id=node_id, payload=payload)
    except LayerNodeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LayerNodeProtectedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/nodes/{node_id}/move", response_model=LayerTreeNode)
async def move_layer_node(
    node_id: str,
    payload: MoveLayerNodeRequest,
    user_id: CurrentUserIdDependency,
    service: LayerTreeServiceDependency,
) -> LayerTreeNode:
    try:
        return service.move_node(user_id=user_id, node_id=node_id, payload=payload)
    except LayerNodeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LayerNodeProtectedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except LayerParentInvalidError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/nodes/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_layer_node(
    node_id: str,
    user_id: CurrentUserIdDependency,
    service: LayerTreeServiceDependency,
) -> Response:
    try:
        service.delete_node(user_id=user_id, node_id=node_id)
    except LayerNodeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LayerNodeProtectedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
