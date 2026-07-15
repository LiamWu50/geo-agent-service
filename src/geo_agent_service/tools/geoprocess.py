import re
from typing import Any, Literal, cast

import geopandas as gpd  # type: ignore[import-untyped]
from shapely.geometry import box  # type: ignore[import-untyped]

from geo_agent_service.modules.gis_data.service import GisDatasetService
from geo_agent_service.tools.base import GisTool, GisToolResult

GeoprocessOperation = Literal["buffer", "centroid", "bbox_clip", "attribute_filter"]
FilterOperator = Literal["eq", "ne", "gt", "gte", "lt", "lte", "contains", "in"]


class GeoprocessTool(GisTool):
    name = "geoprocess"
    description = "Run vector geoprocessing operations and register the result as a dataset."

    def __init__(self, dataset_service: GisDatasetService) -> None:
        self.dataset_service = dataset_service

    async def run(self, payload: dict[str, Any]) -> GisToolResult:
        operation = self._operation(payload)
        dataset_id = self._dataset_id(payload)
        source_summary = self.dataset_service.get_dataset(dataset_id)
        geodata = gpd.read_file(self.dataset_service.resolve_data_ref(source_summary.data_ref))
        processing_crs: str | None = None
        area: dict[str, Any] | None = None
        distance_meters: float | None = None

        if operation == "buffer":
            distance_meters = self._distance_meters(payload)
            result, processing_crs = self._buffer(geodata, distance_meters, payload)
            area = self._area_summary(result, processing_crs)
        elif operation == "centroid":
            result = self._centroid(geodata)
        elif operation == "bbox_clip":
            result = self._bbox_clip(geodata, self._bbox(payload))
        else:
            result = self._attribute_filter(geodata, payload)

        result_name = self._result_name(payload, source_summary.name, operation)
        lineage = self._lineage(
            payload=payload,
            source_dataset_id=dataset_id,
            operation=operation,
            processing_crs=processing_crs,
            distance_meters=distance_meters,
        )
        generated_summary = self.dataset_service.register_generated_dataset(
            name=result_name,
            geodata=result,
            source_tool_call_id=str(payload.get("toolCallId") or ""),
            metadata=lineage,
        )
        summary = {
            "sourceDatasetId": dataset_id,
            "inputDatasetId": dataset_id,
            "resultDatasetId": generated_summary.dataset_id,
            "operation": operation,
            "featureCount": generated_summary.feature_count,
            "geometryType": generated_summary.geometry_type,
            "bbox": generated_summary.bbox,
            "area": area,
            "dataRef": generated_summary.data_ref,
            "lineage": generated_summary.lineage,
            "result": generated_summary.model_dump(mode="json", by_alias=True),
        }
        if processing_crs:
            summary["processingCRS"] = processing_crs
        if operation == "attribute_filter":
            summary["filter"] = self._filter_spec(payload, geodata)

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
                "sourceDatasetId": dataset_id,
                "operation": operation,
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

    def _dataset_id(self, payload: dict[str, Any]) -> str:
        dataset_id = payload.get("inputDatasetId") or payload.get("datasetId")
        if not dataset_id:
            dataset_ids = payload.get("datasetIds") or payload.get("selectedDatasetIds") or []
            dataset_id = dataset_ids[0] if dataset_ids else None
        if not dataset_id:
            raise ValueError("geoprocess requires a datasetId.")
        return str(dataset_id)

    def _operation(self, payload: dict[str, Any]) -> GeoprocessOperation:
        operation = str(payload.get("operation") or "").strip().lower()
        if not operation:
            message = str(payload.get("message") or "").lower()
            if self._has_any(message, ["缓冲", "buffer", "附近"]):
                operation = "buffer"
            elif self._has_any(message, ["中心点", "质心", "centroid"]):
                operation = "centroid"
            elif self._has_any(message, ["裁剪", "范围", "bbox", "当前视图"]):
                operation = "bbox_clip"
            elif self._has_any(message, ["筛选", "过滤", "filter", "等于", "大于", "小于"]):
                operation = "attribute_filter"
        if operation in {"buffer", "centroid", "bbox_clip", "attribute_filter"}:
            return cast(GeoprocessOperation, operation)
        raise ValueError(f"Unsupported geoprocess operation: {operation or 'unknown'}")

    def _distance_meters(self, payload: dict[str, Any]) -> float:
        value = payload.get("distance")
        unit = str(payload.get("unit") or "meter").lower()
        if value is None:
            parsed = self._distance_from_message(str(payload.get("message") or ""))
            if parsed is not None:
                value, unit = parsed
        if value is None:
            raise ValueError("buffer operation requires a distance.")

        try:
            distance = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("buffer distance must be a number.") from exc
        if distance <= 0:
            raise ValueError("buffer distance must be greater than 0.")
        if unit in {"kilometer", "kilometers", "km", "公里", "千米"}:
            return distance * 1000
        if unit in {"meter", "meters", "m", "米"}:
            return distance
        raise ValueError(f"Unsupported buffer distance unit: {unit}")

    def _distance_from_message(self, message: str) -> tuple[float, str] | None:
        match = re.search(
            r"(\d+(?:\.\d+)?)\s*(公里|千米|米|kilometers?|km|meters?|m)",
            message,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        return float(match.group(1)), match.group(2).lower()

    def _bbox(self, payload: dict[str, Any]) -> tuple[float, float, float, float]:
        raw_bbox = payload.get("bbox")
        if raw_bbox is None:
            metadata = payload.get("metadata")
            if isinstance(metadata, dict):
                map_view = metadata.get("mapView")
                if isinstance(map_view, dict):
                    raw_bbox = map_view.get("bbox")
        if not isinstance(raw_bbox, list | tuple) or len(raw_bbox) != 4:
            raise ValueError("bbox_clip operation requires bbox [minX, minY, maxX, maxY].")
        try:
            min_x, min_y, max_x, max_y = [float(value) for value in raw_bbox]
        except (TypeError, ValueError) as exc:
            raise ValueError("bbox values must be numbers.") from exc
        if min_x >= max_x or min_y >= max_y:
            raise ValueError("bbox must satisfy minX < maxX and minY < maxY.")
        return min_x, min_y, max_x, max_y

    def _buffer(
        self,
        geodata: gpd.GeoDataFrame,
        distance_meters: float,
        payload: dict[str, Any],
    ) -> tuple[gpd.GeoDataFrame, str]:
        if geodata.crs is None:
            raise ValueError("buffer operation requires a dataset CRS.")
        working = self._project_for_metric_operation(geodata, payload)
        result = working.copy()
        result.geometry = working.geometry.buffer(distance_meters)
        if geodata.crs is not None and result.crs != geodata.crs:
            result = result.to_crs(geodata.crs)
        return result, working.crs.to_string()

    def _centroid(self, geodata: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        working = (
            self._project_for_metric_operation(geodata, {})
            if geodata.crs is not None
            else geodata
        )
        result = working.copy()
        result.geometry = working.geometry.centroid
        if geodata.crs is not None and result.crs != geodata.crs:
            result = result.to_crs(geodata.crs)
        return result

    def _bbox_clip(
        self,
        geodata: gpd.GeoDataFrame,
        bounds: tuple[float, float, float, float],
    ) -> gpd.GeoDataFrame:
        clipped = gpd.clip(geodata, box(*bounds))
        return clipped.reset_index(drop=True)

    def _attribute_filter(
        self,
        geodata: gpd.GeoDataFrame,
        payload: dict[str, Any],
    ) -> gpd.GeoDataFrame:
        spec = self._filter_spec(payload, geodata)
        field = spec["field"]
        operator = spec["operator"]
        value = spec["value"]
        series = geodata[field]

        if operator == "contains":
            mask = series.dropna().astype(str).str.contains(str(value), case=False, regex=False)
            mask = mask.reindex(series.index, fill_value=False)
        elif operator == "in":
            values = value if isinstance(value, list) else [value]
            comparable_values = [self._coerce_filter_value(item, series) for item in values]
            mask = series.isin(comparable_values)
        elif operator in {"gt", "gte", "lt", "lte"}:
            numeric_series = series.astype(float)
            numeric_value = float(value)
            if operator == "gt":
                mask = numeric_series > numeric_value
            elif operator == "gte":
                mask = numeric_series >= numeric_value
            elif operator == "lt":
                mask = numeric_series < numeric_value
            else:
                mask = numeric_series <= numeric_value
        else:
            comparable_value = self._coerce_filter_value(value, series)
            if operator == "eq" and isinstance(comparable_value, str):
                comparable_value = self._unique_prefixed_string_value(
                    comparable_value,
                    series,
                )
            mask = series != comparable_value if operator == "ne" else series == comparable_value

        return geodata.loc[mask].copy().reset_index(drop=True)

    def _unique_prefixed_string_value(self, value: str, series: Any) -> str:
        if bool(series.astype(str).eq(value).any()):
            return value
        matches = [
            candidate
            for candidate in series.dropna().astype(str).unique()
            if candidate.startswith(value)
        ]
        return matches[0] if len(matches) == 1 else value

    def _filter_spec(
        self,
        payload: dict[str, Any],
        geodata: gpd.GeoDataFrame,
    ) -> dict[str, Any]:
        field = self._filter_field(payload, geodata)
        operator = self._filter_operator(payload)
        value = self._filter_value(payload, field, operator)
        if field not in geodata.columns or field == geodata.geometry.name:
            raise ValueError(f"Filter field not found: {field}")
        return {"field": field, "operator": operator, "value": value}

    def _filter_field(self, payload: dict[str, Any], geodata: gpd.GeoDataFrame) -> str:
        field = str(payload.get("field") or "").strip()
        if field:
            return field

        message = str(payload.get("message") or "").lower()
        fields = sorted(
            [
                str(column)
                for column in geodata.columns
                if column != geodata.geometry.name
            ],
            key=len,
            reverse=True,
        )
        for candidate in fields:
            if candidate.lower() in message:
                return candidate
        raise ValueError("attribute_filter operation requires a field.")

    def _filter_operator(self, payload: dict[str, Any]) -> FilterOperator:
        raw_operator = str(payload.get("operator") or payload.get("op") or "").strip().lower()
        aliases: dict[str, FilterOperator] = {
            "=": "eq",
            "==": "eq",
            "eq": "eq",
            "equals": "eq",
            "!=": "ne",
            "<>": "ne",
            "ne": "ne",
            ">": "gt",
            "gt": "gt",
            ">=": "gte",
            "gte": "gte",
            "<": "lt",
            "lt": "lt",
            "<=": "lte",
            "lte": "lte",
            "contains": "contains",
            "in": "in",
        }
        if raw_operator:
            operator = aliases.get(raw_operator)
            if operator is None:
                raise ValueError(f"Unsupported filter operator: {raw_operator}")
            return operator

        message = str(payload.get("message") or "").lower()
        if self._has_any(message, ["包含", "含有", "contains"]):
            return "contains"
        if self._has_any(message, [">=", "大于等于", "不小于"]):
            return "gte"
        if self._has_any(message, ["<=", "小于等于", "不大于"]):
            return "lte"
        if self._has_any(message, ["!=", "<>", "不等于", "不是"]):
            return "ne"
        if self._has_any(message, [">", "大于", "超过"]):
            return "gt"
        if self._has_any(message, ["<", "小于", "低于"]):
            return "lt"
        return "eq"

    def _filter_value(
        self,
        payload: dict[str, Any],
        field: str,
        operator: FilterOperator,
    ) -> Any:
        if "value" in payload:
            return payload["value"]
        if "values" in payload:
            return payload["values"]

        message = str(payload.get("message") or "")
        field_terms = [re.escape(field)]
        if field.lower() == "name":
            field_terms.extend(["名称", "名字"])
        escaped_field = "(?:" + "|".join(field_terms) + ")"
        patterns = [
            rf"{escaped_field}\s*(?:==|=|!=|<>|>=|<=|>|<)\s*['\"]?(.+?)(?:['\"]?\s*(?:的?要素|并|，|,|。|$))",
            rf"{escaped_field}.*?(?:等于|为|是|包含|含有|大于等于|小于等于|大于|小于|超过|低于|不等于|不是|不小于|不大于)\s*['\"]?(.+?)(?:['\"]?\s*(?:的?要素|并|，|,|。|$))",
        ]
        for pattern in patterns:
            match = re.search(pattern, message, flags=re.IGNORECASE)
            if match:
                value = match.group(1).strip().strip("'\"")
                if operator == "in":
                    return [item.strip() for item in value.split("|") if item.strip()]
                return value
        raise ValueError("attribute_filter operation requires a value.")

    def _coerce_filter_value(self, value: Any, series: Any) -> Any:
        if str(value).lower() in {"true", "false"}:
            return str(value).lower() == "true"
        try:
            if hasattr(series, "dtype") and str(series.dtype).startswith(("int", "float")):
                number = float(value)
                return int(number) if number.is_integer() else number
        except (TypeError, ValueError):
            return value
        return value

    def _project_for_metric_operation(
        self,
        geodata: gpd.GeoDataFrame,
        payload: dict[str, Any],
    ) -> gpd.GeoDataFrame:
        requested_crs = str(payload.get("processingCRS") or "").strip()
        if requested_crs and requested_crs.lower() != "geodesic":
            return geodata.to_crs(requested_crs)
        if geodata.crs is None or not geodata.crs.is_geographic:
            return geodata
        target_crs = geodata.estimate_utm_crs()
        if target_crs is None:
            raise ValueError("Unable to estimate a projected CRS for this dataset.")
        return geodata.to_crs(target_crs)

    def _area_summary(
        self,
        geodata: gpd.GeoDataFrame,
        processing_crs: str | None,
    ) -> dict[str, Any] | None:
        if not processing_crs:
            return None
        working = geodata.to_crs(processing_crs)
        area = float(working.geometry.area.sum())
        return {
            "value": area,
            "unit": "square_meters",
            "processingCRS": processing_crs,
        }

    def _lineage(
        self,
        *,
        payload: dict[str, Any],
        source_dataset_id: str,
        operation: GeoprocessOperation,
        processing_crs: str | None,
        distance_meters: float | None,
    ) -> dict[str, Any]:
        lineage: dict[str, Any] = {
            "sourceDatasetId": source_dataset_id,
            "inputDatasetId": source_dataset_id,
            "operation": operation,
        }
        if operation == "buffer":
            lineage["distance"] = self._clean_number(distance_meters or 0)
            lineage["unit"] = "meters"
            if processing_crs:
                lineage["processingCRS"] = processing_crs
        tool_call_id = str(payload.get("toolCallId") or "")
        if tool_call_id:
            lineage["toolCallId"] = tool_call_id
        return lineage

    def _clean_number(self, value: float) -> int | float:
        return int(value) if value.is_integer() else value

    def _result_name(
        self,
        payload: dict[str, Any],
        source_name: str,
        operation: GeoprocessOperation,
    ) -> str:
        result_name = str(payload.get("resultName") or "").strip()
        if result_name:
            return result_name
        labels = {
            "buffer": "缓冲区",
            "centroid": "中心点",
            "bbox_clip": "范围裁剪",
            "attribute_filter": "属性筛选",
        }
        return f"{source_name} {labels[operation]}"

    def _has_any(self, value: str, keywords: list[str]) -> bool:
        return any(keyword in value for keyword in keywords)
