import json
from pathlib import Path
from typing import Any

from geo_agent_service.modules.gis_data.schemas import DatasetRecord


class DatasetRepository:
    def __init__(self, metadata_path: Path) -> None:
        self.metadata_path = metadata_path

    def list(self) -> list[DatasetRecord]:
        data = self._read()
        raw_records = data.get("datasets", [])
        if not isinstance(raw_records, list):
            raw_records = []
        records = [DatasetRecord.model_validate(item) for item in raw_records]
        return sorted(records, key=lambda record: record.created_at, reverse=True)

    def get(self, dataset_id: str) -> DatasetRecord | None:
        return next(
            (
                record
                for record in self.list()
                if record.summary.dataset_id == dataset_id
            ),
            None,
        )

    def save(self, record: DatasetRecord) -> None:
        records = [
            item for item in self.list() if item.summary.dataset_id != record.summary.dataset_id
        ]
        records.append(record)
        self._write({"datasets": [item.model_dump(mode="json", by_alias=True) for item in records]})

    def _read(self) -> dict[str, Any]:
        if not self.metadata_path.exists():
            return {"datasets": []}
        data = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"datasets": []}
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.metadata_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.metadata_path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(self.metadata_path)
