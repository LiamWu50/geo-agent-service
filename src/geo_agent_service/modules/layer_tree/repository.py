import json
from pathlib import Path
from typing import Any

from geo_agent_service.modules.layer_tree.schemas import LayerTreeNode


class LayerTreeRepository:
    def __init__(self, storage_root: str) -> None:
        self.storage_root = Path(storage_root)

    def get(self, user_id: str) -> list[LayerTreeNode] | None:
        path = self._user_tree_path(user_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        raw_nodes = data.get("nodes", []) if isinstance(data, dict) else []
        if not isinstance(raw_nodes, list):
            raw_nodes = []
        return [LayerTreeNode.model_validate(item) for item in raw_nodes]

    def save(self, user_id: str, nodes: list[LayerTreeNode]) -> None:
        path = self._user_tree_path(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_suffix(".tmp")
        data: dict[str, Any] = {
            "userId": user_id,
            "nodes": [node.model_dump(mode="json", by_alias=True) for node in nodes],
        }
        temporary_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(path)

    def _user_tree_path(self, user_id: str) -> Path:
        safe_user_id = "".join(
            char if char.isalnum() or char in {"-", "_"} else "_" for char in user_id
        )
        return self.storage_root / f"{safe_user_id}.json"
