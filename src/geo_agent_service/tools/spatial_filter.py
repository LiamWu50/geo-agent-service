from typing import Any, Literal, cast

import geopandas as gpd  # type: ignore[import-untyped]
import pandas as pd  # type: ignore[import-untyped]
from shapely import make_valid  # type: ignore[import-untyped]
from shapely.errors import GEOSException  # type: ignore[import-untyped]

from geo_agent_service.modules.gis_data.service import GisDatasetService
from geo_agent_service.tools.base import GisTool, GisToolResult

SpatialPredicate = Literal["within", "intersects"]


class SpatialFilterTool(GisTool):
    name = "spatial_filter"
    description = "Filter input features by a deterministic spatial relation to a mask dataset."

    def __init__(self, dataset_service: GisDatasetService) -> None:
        self.dataset_service = dataset_service

    async def run(self, payload: dict[str, Any]) -> GisToolResult:
        input_dataset_id = self._required_string(payload, "inputDatasetId")
        mask_dataset_id = self._required_string(payload, "maskDatasetId")
        predicate = self._predicate(payload)
        output_fields = self._output_fields(payload)

        input_summary = self.dataset_service.get_dataset(input_dataset_id)
        mask_summary = self.dataset_service.get_dataset(mask_dataset_id)
        input_geodata = gpd.read_file(self.dataset_service.resolve_data_ref(input_summary.data_ref))
        mask_geodata = gpd.read_file(self.dataset_service.resolve_data_ref(mask_summary.data_ref))

        input_geodata, mask_geodata = self._align_crs(
            input_geodata,
            mask_geodata,
            input_crs=input_summary.crs,
            mask_crs=mask_summary.crs,
        )
        self._validate_output_fields(input_geodata, output_fields)
        input_geodata = self._repair_geometries(input_geodata, "input")
        mask_geodata = self._repair_geometries(mask_geodata, "mask")

        try:
            mask_geometry = make_valid(mask_geodata.geometry.dropna().union_all())
        except GEOSException as exc:
            raise ValueError(
                "spatial_filter geometry validation failed for mask dataset."
            ) from exc
        if mask_geometry.is_empty:
            raise ValueError("spatial_filter mask dataset has no usable geometry.")

        try:
            if predicate == "within":
                mask = input_geodata.geometry.within(mask_geometry)
            else:
                mask = input_geodata.geometry.intersects(mask_geometry)
        except GEOSException as exc:
            raise ValueError(
                "spatial_filter geometry validation failed during spatial predicate."
            ) from exc

        result_columns = [*output_fields, input_geodata.geometry.name]
        result = input_geodata.loc[mask, result_columns].copy().reset_index(drop=True)
        rows = self._rows(result, output_fields)
        result_name = self._result_name(payload, input_summary.name)
        generated_summary = self.dataset_service.register_generated_dataset(
            name=result_name,
            geodata=result,
            source_tool_call_id=str(payload.get("toolCallId") or ""),
            metadata={
                "inputDatasetId": input_dataset_id,
                "maskDatasetId": mask_dataset_id,
                "predicate": predicate,
                "outputFields": output_fields,
                "operation": "spatial_filter",
            },
        )

        summary = {
            "inputDatasetId": input_dataset_id,
            "maskDatasetId": mask_dataset_id,
            "predicate": predicate,
            "outputFields": output_fields,
            "featureCount": generated_summary.feature_count,
            "rows": rows,
            "resultDatasetId": generated_summary.dataset_id,
            "bbox": generated_summary.bbox,
            "result": generated_summary.model_dump(mode="json", by_alias=True),
        }
        layer = {
            "id": f"layer_{generated_summary.dataset_id}",
            "datasetId": generated_summary.dataset_id,
            "name": generated_summary.name,
            "geometryType": generated_summary.geometry_type,
            "dataRef": generated_summary.data_ref,
            "bbox": generated_summary.bbox,
            "source": {
                "type": "dataset",
                "datasetId": generated_summary.dataset_id,
            },
            "metadata": {
                "inputDatasetId": input_dataset_id,
                "maskDatasetId": mask_dataset_id,
                "predicate": predicate,
                "operation": "spatial_filter",
            },
        }
        map_command = {
            "action": "layer.addDataset",
            "datasetId": generated_summary.dataset_id,
            "name": generated_summary.name,
            "visible": True,
            "flyTo": True,
        }
        return GisToolResult(
            data_ref=generated_summary.data_ref,
            summary=summary,
            layer=layer,
            map_command=map_command,
        )

    def _required_string(self, payload: dict[str, Any], key: str) -> str:
        value = str(payload.get(key) or "").strip()
        if not value:
            raise ValueError(f"spatial_filter requires {key}.")
        return value

    def _predicate(self, payload: dict[str, Any]) -> SpatialPredicate:
        predicate = str(payload.get("predicate") or "").strip().lower()
        if not predicate:
            raise ValueError("spatial_filter requires predicate.")
        if predicate in {"within", "intersects"}:
            return cast(SpatialPredicate, predicate)
        raise ValueError(f"Unsupported spatial_filter predicate: {predicate}")

    def _output_fields(self, payload: dict[str, Any]) -> list[str]:
        raw_fields = payload.get("outputFields")
        if not isinstance(raw_fields, list):
            raise ValueError("spatial_filter requires outputFields.")
        fields = [str(field).strip() for field in raw_fields if str(field).strip()]
        if not fields:
            raise ValueError("spatial_filter requires outputFields.")
        return fields

    def _align_crs(
        self,
        input_geodata: gpd.GeoDataFrame,
        mask_geodata: gpd.GeoDataFrame,
        *,
        input_crs: str | None,
        mask_crs: str | None,
    ) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
        if input_crs is None and mask_crs is None:
            return input_geodata, mask_geodata
        if input_crs is None or mask_crs is None:
            raise ValueError("spatial_filter requires matching CRS; one dataset is missing CRS.")
        if input_geodata.crs != mask_geodata.crs:
            mask_geodata = mask_geodata.to_crs(input_geodata.crs)
        return input_geodata, mask_geodata

    def _repair_geometries(
        self,
        geodata: gpd.GeoDataFrame,
        label: str,
    ) -> gpd.GeoDataFrame:
        repaired = geodata.copy()
        try:
            repaired.geometry = repaired.geometry.map(
                lambda geometry: make_valid(geometry) if geometry is not None else geometry
            )
        except GEOSException as exc:
            raise ValueError(
                f"spatial_filter geometry validation failed for {label} dataset."
            ) from exc
        repaired = repaired[
            repaired.geometry.notna() & ~repaired.geometry.is_empty
        ].reset_index(drop=True)
        if repaired.empty:
            raise ValueError(f"spatial_filter {label} dataset has no usable geometry.")
        return repaired

    def _validate_output_fields(
        self,
        geodata: gpd.GeoDataFrame,
        output_fields: list[str],
    ) -> None:
        missing_fields = [
            field
            for field in output_fields
            if field not in geodata.columns or field == geodata.geometry.name
        ]
        if missing_fields:
            raise ValueError(
                "spatial_filter outputFields not found: " + ", ".join(missing_fields)
            )

    def _rows(
        self,
        geodata: gpd.GeoDataFrame,
        output_fields: list[str],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for record in geodata[output_fields].to_dict(orient="records"):
            rows.append(
                {
                    key: None if pd.isna(value) else value
                    for key, value in record.items()
                }
            )
        return rows

    def _result_name(self, payload: dict[str, Any], input_name: str) -> str:
        result_name = str(payload.get("resultName") or "").strip()
        if result_name:
            return result_name
        return f"{input_name} 空间筛选"
