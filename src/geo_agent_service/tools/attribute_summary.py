from typing import Any

import geopandas as gpd  # type: ignore[import-untyped]
import pandas as pd  # type: ignore[import-untyped]
from pandas.api import types as pandas_types  # type: ignore[import-untyped]

from geo_agent_service.modules.gis_data.repository import DatasetRepository
from geo_agent_service.modules.gis_data.storage import GisDataStorage
from geo_agent_service.tools.base import GisTool, GisToolResult


class AttributeSummaryTool(GisTool):
    name = "attribute_summary"
    description = "Read a full GeoJSON dataset and compute attribute statistics."

    def __init__(
        self,
        *,
        dataset_repository: DatasetRepository,
        storage: GisDataStorage,
    ) -> None:
        self.dataset_repository = dataset_repository
        self.storage = storage

    async def run(self, payload: dict[str, Any]) -> GisToolResult:
        dataset_id = self._dataset_id(payload)
        record = self.dataset_repository.get(dataset_id)
        if record is None:
            raise ValueError(f"Dataset not found: {dataset_id}")

        path = self.storage.resolve_data_ref(record.summary.data_ref)
        geodata = gpd.read_file(path)
        group_by = self._optional_field(payload.get("groupBy"), geodata)
        requested_metrics = payload.get("metrics")

        summary: dict[str, Any] = {
            "datasetId": dataset_id,
            "name": record.summary.name,
            "featureCount": int(len(geodata)),
            "fields": self._field_statistics(geodata),
        }
        if group_by:
            summary["groupBy"] = group_by
            summary["rows"] = self._group_rows(geodata, group_by, requested_metrics)

        return GisToolResult(data_ref=record.summary.data_ref, summary=summary)

    def _dataset_id(self, payload: dict[str, Any]) -> str:
        dataset_id = payload.get("datasetId")
        if not dataset_id:
            dataset_ids = payload.get("datasetIds") or payload.get("selectedDatasetIds") or []
            dataset_id = dataset_ids[0] if dataset_ids else None
        if not dataset_id:
            raise ValueError("attribute_summary requires a datasetId.")
        return str(dataset_id)

    def _optional_field(self, value: Any, geodata: gpd.GeoDataFrame) -> str | None:
        if not value:
            return None
        field = str(value)
        if field not in geodata.columns or field == geodata.geometry.name:
            raise ValueError(f"Field not found: {field}")
        return field

    def _field_statistics(self, geodata: gpd.GeoDataFrame) -> list[dict[str, Any]]:
        fields: list[dict[str, Any]] = []
        for column in geodata.columns:
            if column == geodata.geometry.name:
                continue
            series = geodata[column]
            stats: dict[str, Any] = {
                "name": column,
                "count": int(series.notna().sum()),
                "nullCount": int(series.isna().sum()),
                "nullRatio": float(series.isna().mean()) if len(series) else None,
                "uniqueCount": int(series.dropna().nunique()),
            }
            if pandas_types.is_numeric_dtype(series):
                numeric = pd.to_numeric(series, errors="coerce")
                stats.update(
                    {
                        "type": "number",
                        "min": self._json_number(numeric.min()),
                        "max": self._json_number(numeric.max()),
                        "mean": self._json_number(numeric.mean()),
                        "sum": self._json_number(numeric.sum()),
                    }
                )
            else:
                counts = series.dropna().astype(str).value_counts().head(20)
                stats.update(
                    {
                        "type": "category",
                        "topValues": [
                            {"value": value, "count": int(count)}
                            for value, count in counts.items()
                        ],
                    }
                )
            fields.append(stats)
        return fields

    def _group_rows(
        self,
        geodata: gpd.GeoDataFrame,
        group_by: str,
        requested_metrics: Any,
    ) -> list[dict[str, Any]]:
        metric_specs = self._metric_specs(geodata, requested_metrics)
        rows: list[dict[str, Any]] = []
        grouped = geodata.groupby(group_by, dropna=False)
        for group_value, frame in grouped:
            row: dict[str, Any] = {
                group_by: None if pd.isna(group_value) else str(group_value),
                "count": int(len(frame)),
            }
            for field, op in metric_specs:
                numeric = pd.to_numeric(frame[field], errors="coerce")
                key = f"{field}_{op}"
                if op == "sum":
                    row[key] = self._json_number(numeric.sum())
                elif op == "mean":
                    row[key] = self._json_number(numeric.mean())
                elif op == "min":
                    row[key] = self._json_number(numeric.min())
                elif op == "max":
                    row[key] = self._json_number(numeric.max())
            rows.append(row)
        return rows

    def _metric_specs(
        self,
        geodata: gpd.GeoDataFrame,
        requested_metrics: Any,
    ) -> list[tuple[str, str]]:
        allowed_ops = {"sum", "mean", "min", "max"}
        if isinstance(requested_metrics, list) and requested_metrics:
            specs: list[tuple[str, str]] = []
            for item in requested_metrics:
                if not isinstance(item, dict):
                    continue
                field = str(item.get("field") or "")
                op = str(item.get("op") or "sum")
                if field in geodata.columns and op in allowed_ops:
                    specs.append((field, op))
            return specs

        return [
            (column, "sum")
            for column in geodata.columns
            if column != geodata.geometry.name and pandas_types.is_numeric_dtype(geodata[column])
        ]

    def _json_number(self, value: Any) -> float | int | None:
        if pd.isna(value):
            return None
        number = float(value)
        return int(number) if number.is_integer() else number
