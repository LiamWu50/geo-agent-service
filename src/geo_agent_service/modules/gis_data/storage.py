from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile


class GisStorageError(ValueError):
    """Raised when a storage reference cannot be resolved safely."""


class GisDataStorage:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.uploads_root = self.root / "uploads"
        self.normalized_root = self.root / "normalized"
        self.metadata_root = self.root / "metadata"

    def ensure_ready(self) -> None:
        self.uploads_root.mkdir(parents=True, exist_ok=True)
        self.normalized_root.mkdir(parents=True, exist_ok=True)
        self.metadata_root.mkdir(parents=True, exist_ok=True)

    def new_dataset_id(self) -> str:
        return f"dataset_{uuid4().hex[:12]}"

    async def save_upload(self, dataset_id: str, file: UploadFile) -> Path:
        self.ensure_ready()
        dataset_dir = self.uploads_root / dataset_id
        dataset_dir.mkdir(parents=True, exist_ok=True)
        target = dataset_dir / "source.geojson"
        content = await file.read()
        target.write_bytes(content)
        return target

    def save_source_bytes(self, dataset_id: str, content: bytes) -> Path:
        self.ensure_ready()
        dataset_dir = self.uploads_root / dataset_id
        dataset_dir.mkdir(parents=True, exist_ok=True)
        target = dataset_dir / "source.geojson"
        target.write_bytes(content)
        return target

    def normalized_path(self, dataset_id: str) -> Path:
        self.ensure_ready()
        dataset_dir = self.normalized_root / dataset_id
        dataset_dir.mkdir(parents=True, exist_ok=True)
        return dataset_dir / "data.geojson"

    def metadata_path(self) -> Path:
        self.ensure_ready()
        return self.metadata_root / "datasets.json"

    def upload_uri(self, dataset_id: str) -> str:
        return f"storage://uploads/{dataset_id}/source.geojson"

    def normalized_uri(self, dataset_id: str) -> str:
        return f"storage://normalized/{dataset_id}/data.geojson"

    def resolve_data_ref(self, data_ref: str) -> Path:
        prefix = "storage://"
        if not data_ref.startswith(prefix):
            raise GisStorageError(f"Unsupported dataRef: {data_ref}")

        relative = data_ref.removeprefix(prefix)
        path = (self.root / relative).resolve()
        root = self.root.resolve()

        if path != root and root not in path.parents:
            raise GisStorageError(f"dataRef escapes storage root: {data_ref}")
        return path
