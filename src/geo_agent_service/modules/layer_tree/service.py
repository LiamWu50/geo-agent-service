import secrets
from datetime import UTC, datetime

from geo_agent_service.modules.gis_data.repository import DatasetRepository
from geo_agent_service.modules.gis_data.schemas import InputDataSummary
from geo_agent_service.modules.layer_tree.defaults import (
    DEFAULT_USER_LAYERS_FOLDER_ID,
    default_layer_tree,
)
from geo_agent_service.modules.layer_tree.repository import LayerTreeRepository
from geo_agent_service.modules.layer_tree.schemas import (
    AddDatasetLayerRequest,
    LayerTreeNode,
    MoveLayerNodeRequest,
    UpdateLayerNodeRequest,
)


class LayerTreeError(ValueError):
    """Raised when a layer tree operation cannot be completed."""


class LayerNodeNotFoundError(LayerTreeError):
    """Raised when a tree node does not exist."""


class LayerNodeProtectedError(LayerTreeError):
    """Raised when attempting to mutate a protected default node."""


class LayerParentInvalidError(LayerTreeError):
    """Raised when a requested parent is not a folder."""


class LayerTreeService:
    def __init__(
        self,
        repository: LayerTreeRepository,
        dataset_repository: DatasetRepository,
    ) -> None:
        self.repository = repository
        self.dataset_repository = dataset_repository

    def get_tree(self, user_id: str) -> list[LayerTreeNode]:
        return self._load_tree(user_id)

    def add_dataset_layer(self, user_id: str, payload: AddDatasetLayerRequest) -> LayerTreeNode:
        dataset = self.dataset_repository.get(payload.dataset_id)
        if dataset is None:
            raise LayerNodeNotFoundError("Dataset not found.")

        nodes = self._load_tree(user_id)
        parent_id = payload.parent_id or DEFAULT_USER_LAYERS_FOLDER_ID
        parent = self._find_node(nodes, parent_id)
        if parent is None or parent.type != "folder":
            raise LayerParentInvalidError("Parent layer node is not a folder.")

        now = datetime.now(UTC)
        node = self._node_from_dataset(
            dataset=dataset.summary,
            name=payload.name,
            parent_id=parent_id,
            visible=payload.visible,
            opacity=payload.opacity,
            created_at=now,
        )
        self._insert_child(parent, node, payload.position)
        self._touch(parent, now)
        self.repository.save(user_id, nodes)
        return node

    def update_node(
        self,
        user_id: str,
        node_id: str,
        payload: UpdateLayerNodeRequest,
    ) -> LayerTreeNode:
        nodes = self._load_tree(user_id)
        node = self._require_existing_node(nodes, node_id)
        update_data = payload.model_dump(exclude_unset=True)
        if "name" in update_data and payload.name is not None:
            if not node.user_managed:
                raise LayerNodeProtectedError("Default layer nodes cannot be renamed.")
            node.name = payload.name
        if "visible" in update_data and payload.visible is not None:
            node.visible = payload.visible
        if "opacity" in update_data and payload.opacity is not None:
            node.opacity = payload.opacity
        node.updated_at = datetime.now(UTC)
        self.repository.save(user_id, nodes)
        return node

    def move_node(
        self,
        user_id: str,
        node_id: str,
        payload: MoveLayerNodeRequest,
    ) -> LayerTreeNode:
        nodes = self._load_tree(user_id)
        node = self._require_user_managed_node(nodes, node_id)
        parent_id = payload.parent_id or DEFAULT_USER_LAYERS_FOLDER_ID
        target_parent = self._find_node(nodes, parent_id)
        if target_parent is None or target_parent.type != "folder":
            raise LayerParentInvalidError("Parent layer node is not a folder.")
        if node_id == parent_id or self._contains_node(node.children, parent_id):
            raise LayerParentInvalidError("Cannot move a node into itself or its descendants.")

        removed = self._remove_node(nodes, node_id)
        if removed is None:
            raise LayerNodeNotFoundError("Layer node not found.")

        target_parent = self._find_node(nodes, parent_id)
        if target_parent is None or target_parent.type != "folder":
            raise LayerParentInvalidError("Parent layer node is not a folder.")

        now = datetime.now(UTC)
        removed.parent_id = parent_id
        removed.updated_at = now
        self._insert_child(target_parent, removed, payload.position)
        self._touch(target_parent, now)
        self.repository.save(user_id, nodes)
        return removed

    def delete_node(self, user_id: str, node_id: str) -> None:
        nodes = self._load_tree(user_id)
        self._require_user_managed_node(nodes, node_id)
        if self._remove_node(nodes, node_id) is None:
            raise LayerNodeNotFoundError("Layer node not found.")
        self.repository.save(user_id, nodes)

    def _load_tree(self, user_id: str) -> list[LayerTreeNode]:
        nodes = self.repository.get(user_id)
        if nodes is not None:
            return nodes
        nodes = default_layer_tree()
        self._normalize_parent_ids(nodes)
        self.repository.save(user_id, nodes)
        return nodes

    def _node_from_dataset(
        self,
        dataset: InputDataSummary,
        name: str | None,
        parent_id: str,
        visible: bool,
        opacity: float,
        created_at: datetime,
    ) -> LayerTreeNode:
        return LayerTreeNode(
            id=f"layer_{secrets.token_urlsafe(12)}",
            name=name or dataset.name,
            type="layer",
            parentId=parent_id,
            datasetId=dataset.dataset_id,
            sourceType=dataset.source_type,
            geometryType=dataset.geometry_type,
            bbox=dataset.bbox,
            iconKey=self._icon_key_for_geometry(dataset.geometry_type),
            visible=visible,
            opacity=opacity,
            userManaged=True,
            createdAt=created_at,
            updatedAt=created_at,
        )

    def _icon_key_for_geometry(self, geometry_type: str | None) -> str:
        if geometry_type in {"Point", "MultiPoint"}:
            return "map-pinned"
        if geometry_type in {"LineString", "MultiLineString"}:
            return "route"
        if geometry_type in {"Polygon", "MultiPolygon"}:
            return "square-dashed-mouse-pointer"
        if geometry_type == "Raster":
            return "image"
        return "layers"

    def _require_user_managed_node(
        self,
        nodes: list[LayerTreeNode],
        node_id: str,
    ) -> LayerTreeNode:
        node = self._find_node(nodes, node_id)
        if node is None:
            raise LayerNodeNotFoundError("Layer node not found.")
        if not node.user_managed:
            raise LayerNodeProtectedError("Default layer nodes cannot be modified.")
        return node

    def _require_existing_node(
        self,
        nodes: list[LayerTreeNode],
        node_id: str,
    ) -> LayerTreeNode:
        node = self._find_node(nodes, node_id)
        if node is None:
            raise LayerNodeNotFoundError("Layer node not found.")
        return node

    def _find_node(self, nodes: list[LayerTreeNode], node_id: str) -> LayerTreeNode | None:
        for node in nodes:
            if node.id == node_id:
                return node
            child = self._find_node(node.children, node_id)
            if child is not None:
                return child
        return None

    def _contains_node(self, nodes: list[LayerTreeNode], node_id: str) -> bool:
        return self._find_node(nodes, node_id) is not None

    def _remove_node(self, nodes: list[LayerTreeNode], node_id: str) -> LayerTreeNode | None:
        for index, node in enumerate(nodes):
            if node.id == node_id:
                return nodes.pop(index)
            child = self._remove_node(node.children, node_id)
            if child is not None:
                return child
        return None

    def _insert_child(
        self,
        parent: LayerTreeNode,
        child: LayerTreeNode,
        position: int | None,
    ) -> None:
        index = len(parent.children) if position is None else min(position, len(parent.children))
        parent.children.insert(index, child)

    def _normalize_parent_ids(
        self,
        nodes: list[LayerTreeNode],
        parent_id: str | None = None,
    ) -> None:
        for node in nodes:
            node.parent_id = parent_id
            self._normalize_parent_ids(node.children, node.id)

    def _touch(self, node: LayerTreeNode, updated_at: datetime) -> None:
        node.updated_at = updated_at
