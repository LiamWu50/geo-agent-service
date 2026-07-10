from __future__ import annotations

import json
import re
import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from geo_agent_service.modules.ai_chat.schemas import ChatMessageRequest, StreamEvent
from geo_agent_service.schemas.agent import AgentError, ToolCallRecord
from geo_agent_service.schemas.session import AgentSession

if TYPE_CHECKING:
    from geo_agent_service.tools.registry import GisToolRegistry


class AiChatToolExecutionMixin:
    if TYPE_CHECKING:
        tool_registry: GisToolRegistry

        def _available_dataset_ids(self, payload: ChatMessageRequest) -> list[str]: ...
        def _duration_ms(self, started_at: datetime, finished_at: datetime) -> int: ...
        def _task_type(self, message: str) -> str: ...
        def _is_analysis_execution_request(self, message: str) -> bool: ...
        def _is_points_in_existing_buffer_plan_request(self, message: str) -> bool: ...
        def _is_existing_result_buffer_request(self, message: str) -> bool: ...

    async def _run_tools(
        self,
        session: AgentSession,
        payload: ChatMessageRequest,
    ) -> AsyncIterator[StreamEvent]:
        for tool_name, tool_input in self._select_tool_calls(session, payload):
            blocked_reason = self._blocked_tool_reason(
                tool_name=tool_name,
                tool_input=tool_input,
                payload=payload,
            )
            if blocked_reason:
                continue
            started_at = datetime.now(UTC)
            tool_call_id = f"tool_{secrets.token_urlsafe(12)}"
            tool_input = {**tool_input, "toolCallId": tool_call_id}
            tool_call = ToolCallRecord(
                id=tool_call_id,
                toolName=tool_name,
                status="running",
                input=tool_input,
                startedAt=started_at.isoformat(),
            )
            session.tool_calls.append(tool_call)
            yield StreamEvent(
                type="tool.started",
                sessionId=session.id,
                toolCallId=tool_call.id,
                data={"toolName": tool_name, "input": tool_call.input},
            )
            if tool_name not in self.tool_registry.list_names():
                now = datetime.now(UTC)
                error = AgentError(
                    code="TOOL_NOT_IMPLEMENTED",
                    message=f"{tool_name} 尚未实现，未执行确定性 GIS 计算。",
                    recoverable=True,
                )
                tool_call.status = "failed"
                tool_call.error = error
                tool_call.finished_at = now.isoformat()
                tool_call.duration_ms = self._duration_ms(started_at, now)
                yield StreamEvent(
                    type="tool.failed",
                    sessionId=session.id,
                    toolCallId=tool_call.id,
                    data={
                        "toolName": tool_name,
                        "status": "failed",
                        "input": tool_call.input,
                        "error": error.model_dump(mode="json", by_alias=True),
                        "durationMs": tool_call.duration_ms,
                    },
                )
                continue

            tool = self.tool_registry.get(tool_name)
            try:
                result = await tool.run(tool_input)
                tool_output = self._tool_output(tool_name, result)
            except Exception as exc:
                now = datetime.now(UTC)
                error = AgentError(
                    code="TOOL_FAILED",
                    message=str(exc),
                    recoverable=True,
                )
                tool_call.status = "failed"
                tool_call.error = error
                tool_call.finished_at = now.isoformat()
                tool_call.duration_ms = self._duration_ms(started_at, now)
                yield StreamEvent(
                    type="tool.failed",
                    sessionId=session.id,
                    toolCallId=tool_call.id,
                    data={
                        "toolName": tool_name,
                        "status": "failed",
                        "input": tool_call.input,
                        "error": error.model_dump(mode="json", by_alias=True),
                        "durationMs": tool_call.duration_ms,
                    },
                )
                continue

            now = datetime.now(UTC)
            tool_call.status = "completed"
            tool_call.output = tool_output
            tool_call.finished_at = now.isoformat()
            tool_call.duration_ms = self._duration_ms(started_at, now)
            yield StreamEvent(
                type="tool.completed",
                sessionId=session.id,
                toolCallId=tool_call.id,
                data={
                    "toolName": tool_name,
                    "status": "completed",
                    "input": tool_call.input,
                    "output": tool_call.output,
                    "durationMs": tool_call.duration_ms,
                },
            )
            result_layer = result.layer
            can_emit_result_layer = self._can_emit_result_layer(
                tool_name,
                tool_call.output,
                result_layer,
            )
            if can_emit_result_layer:
                yield StreamEvent(
                    type="layer.created",
                    sessionId=session.id,
                    toolCallId=tool_call.id,
                    data=result_layer or {},
                )
            if can_emit_result_layer and result.map_command:
                yield StreamEvent(
                    type="map.command",
                    sessionId=session.id,
                    toolCallId=tool_call.id,
                    data=result.map_command,
                )

    def _tool_output(self, tool_name: str, result: Any) -> dict[str, Any]:
        output = cast(dict[str, Any], result.model_dump(mode="json", by_alias=True))
        if tool_name == "geoprocess":
            summary = output.get("summary")
            if isinstance(summary, dict):
                for key in [
                    "resultDatasetId",
                    "featureCount",
                    "geometryType",
                    "bbox",
                    "area",
                    "dataRef",
                    "lineage",
                    "result",
                    "processingCRS",
                ]:
                    if key in summary and key not in output:
                        output[key] = summary[key]
            return output
        if tool_name != "spatial_filter":
            return output

        summary = output.get("summary")
        if isinstance(summary, dict):
            for key in ["featureCount", "rows", "resultDatasetId"]:
                if key in summary and key not in output:
                    output[key] = summary[key]

        has_feature_count = "featureCount" in output
        has_result_payload = "rows" in output or "resultDatasetId" in output
        if not has_feature_count or not has_result_payload:
            raise ValueError(
                "spatial_filter output requires featureCount and rows or resultDatasetId."
            )
        return output

    def _can_emit_result_layer(
        self,
        tool_name: str,
        output: dict[str, Any] | None,
        layer: dict[str, Any] | None,
    ) -> bool:
        if not layer or not isinstance(output, dict):
            return False
        if tool_name != "spatial_filter":
            return True
        result_dataset_id = str(output.get("resultDatasetId") or "").strip()
        return bool(result_dataset_id) and str(layer.get("datasetId") or "") == result_dataset_id

    def _select_tool_calls(
        self,
        session: AgentSession,
        payload: ChatMessageRequest,
    ) -> list[tuple[str, dict[str, Any]]]:
        message = payload.message.lower()
        effective_dataset_ids = session.selected_dataset_ids
        available_dataset_ids = self._available_dataset_ids(payload)
        task_type = self._task_type(message)
        analysis_execution = self._is_analysis_execution_request(message)
        base_input = {
            "message": payload.message,
            "query": payload.message,
            "selectedDatasetIds": effective_dataset_ids,
            "datasetIds": effective_dataset_ids,
            "availableDatasetIds": available_dataset_ids,
            "frontendSelectedDatasetIds": payload.selected_dataset_ids,
            "effectiveDatasetIds": effective_dataset_ids,
            "taskType": task_type,
            "selectedServiceIds": payload.selected_service_ids,
            "metadata": payload.metadata,
            "dataSummaries": [
                summary.model_dump(mode="json", by_alias=True)
                for summary in session.data_summaries
            ],
        }

        calls: list[tuple[str, dict[str, Any]]] = []
        if task_type == "map_display":
            return calls

        if task_type == "result_layer_inspection":
            return calls

        if task_type == "data_readiness":
            if "metadata_search" in self.tool_registry.list_names():
                calls.append(("metadata_search", base_input))
            return calls

        if self._is_attribute_summary_only_request(message):
            if (
                effective_dataset_ids
                and "attribute_summary" in self.tool_registry.list_names()
            ):
                calls.append(("attribute_summary", self._attribute_summary_input(base_input)))
            return calls

        if self._is_existing_result_buffer_request(message):
            if effective_dataset_ids and "geoprocess" in self.tool_registry.list_names():
                calls.append(
                    ("geoprocess", self._geoprocess_input(base_input, operation="buffer"))
                )
            return calls

        if self._is_spatial_filter_request(message):
            if effective_dataset_ids:
                calls.append(("spatial_filter", self._spatial_filter_input(base_input)))
            return calls

        if self._is_metadata_query(message) and not analysis_execution:
            if "metadata_search" in self.tool_registry.list_names():
                calls.append(("metadata_search", base_input))
        if self._has_any(
            message,
            ["统计", "数量", "分类", "占比", "平均", "总和", "求和", "汇总", "summary", "count"],
        ) and not self._is_read_only_metadata_count_query(message):
            if (
                effective_dataset_ids
                and "attribute_summary" in self.tool_registry.list_names()
            ):
                calls.append(("attribute_summary", self._attribute_summary_input(base_input)))
        if self._has_any(message, ["缓冲", "buffer", "附近"]):
            if effective_dataset_ids and "geoprocess" in self.tool_registry.list_names():
                calls.append(
                    ("geoprocess", self._geoprocess_input(base_input, operation="buffer"))
                )
        elif self._has_any(message, ["中心点", "质心", "centroid"]):
            if effective_dataset_ids and "geoprocess" in self.tool_registry.list_names():
                calls.append(
                    ("geoprocess", self._geoprocess_input(base_input, operation="centroid"))
                )
        elif self._is_bbox_clip_request(message):
            if effective_dataset_ids and "geoprocess" in self.tool_registry.list_names():
                calls.append(
                    ("geoprocess", self._geoprocess_input(base_input, operation="bbox_clip"))
                )
        elif self._has_any(
            message,
            [
                "筛选",
                "过滤",
                "filter",
                "等于",
                "不等于",
                "大于",
                "小于",
                "超过",
                "低于",
                "包含",
            ],
        ):
            if effective_dataset_ids and "geoprocess" in self.tool_registry.list_names():
                calls.append(
                    (
                        "geoprocess",
                        self._geoprocess_input(base_input, operation="attribute_filter"),
                    )
                )

        return calls

    def _blocked_tool_reason(
        self,
        *,
        tool_name: str,
        tool_input: dict[str, Any],
        payload: ChatMessageRequest,
    ) -> str | None:
        message = payload.message.lower()
        task_type = str(tool_input.get("taskType") or self._task_type(message))
        operation = str(tool_input.get("operation") or "")
        if task_type == "result_layer_inspection" and tool_name in {
            "spatial_filter",
            "attribute_filter",
            "attribute_summary",
            "geoprocess",
        }:
            return f"Tool {tool_name} is not allowed for result_layer_inspection"
        if task_type == "data_readiness" and tool_name != "metadata_search":
            return f"Tool {tool_name} is not allowed for data_readiness"
        if self._user_forbids_attribute_filter(message) and (
            operation == "attribute_filter"
            or (tool_name == "geoprocess" and operation == "attribute_filter")
        ):
            return "User explicitly forbids attribute_filter"
        if tool_name == "geoprocess" and operation in {
            "attribute_filter",
            "spatial_filter",
            "intersect",
            "within",
            "buffer",
        }:
            if task_type == "data_readiness":
                return f"Operation {operation} is not allowed for data_readiness"
        return None

    def _user_forbids_attribute_filter(self, message: str) -> bool:
        return bool(
            re.search(
                r"(不要|不得|禁止|别|不允许)\s*(调用|执行|使用)?\s*attribute_filter",
                message,
            )
        )

    def _attribute_summary_input(self, base_input: dict[str, Any]) -> dict[str, Any]:
        payload = dict(base_input)
        message = str(base_input.get("message") or "")
        dataset_ids = [str(dataset_id) for dataset_id in payload.get("datasetIds") or []]
        if dataset_ids:
            payload["datasetId"] = dataset_ids[0]

        group_by = self._infer_group_by_field(message, base_input.get("dataSummaries") or [])
        sort_by = self._infer_sort_by_field(message, base_input.get("dataSummaries") or [])
        field = self._infer_requested_field(message, base_input.get("dataSummaries") or [])
        statistics = self._infer_requested_statistics(message)
        if field and statistics:
            payload["field"] = field
            payload["statistics"] = statistics
            payload["metrics"] = [{"field": field, "op": statistic} for statistic in statistics]
            if not self._is_explicit_group_by_request(message):
                return payload
        if group_by and group_by != sort_by:
            payload["groupBy"] = group_by
        if sort_by:
            payload["sortBy"] = sort_by
            payload["sortOrder"] = "desc" if self._is_descending_request(
                str(base_input.get("message") or "")
            ) else "asc"
            if not group_by or group_by == sort_by:
                payload["includeRows"] = True
        return payload

    def _geoprocess_input(
        self,
        base_input: dict[str, Any],
        *,
        operation: str,
    ) -> dict[str, Any]:
        payload = dict(base_input)
        payload["operation"] = operation
        dataset_ids = [str(dataset_id) for dataset_id in payload.get("datasetIds") or []]
        if dataset_ids:
            payload["inputDatasetId"] = dataset_ids[0]
            payload["datasetId"] = dataset_ids[0]
        if operation == "buffer":
            parsed_distance = self._infer_distance(str(base_input.get("message") or ""))
            if parsed_distance is not None:
                distance, unit = parsed_distance
                distance_meters = self._distance_to_meters(distance, unit)
                payload["distance"] = self._clean_number(distance_meters)
                payload["unit"] = "meters"
            processing_crs = self._infer_processing_crs(payload)
            if processing_crs:
                payload["processingCRS"] = processing_crs
        elif operation == "bbox_clip":
            metadata = base_input.get("metadata")
            if isinstance(metadata, dict):
                map_view = metadata.get("mapView")
                if isinstance(map_view, dict) and "bbox" in map_view:
                    payload["bbox"] = map_view["bbox"]
        elif operation == "attribute_filter":
            field = self._infer_filter_field(
                str(base_input.get("message") or ""),
                base_input.get("dataSummaries") or [],
            )
            if field:
                payload["field"] = field
        return payload

    def _distance_to_meters(self, distance: float, unit: str) -> float:
        normalized = unit.lower()
        if normalized in {"公里", "千米", "kilometer", "kilometers", "km"}:
            return distance * 1000
        return distance

    def _clean_number(self, value: float) -> int | float:
        return int(value) if value.is_integer() else value

    def _infer_processing_crs(self, payload: dict[str, Any]) -> str | None:
        metadata = payload.get("metadata")
        if isinstance(metadata, dict):
            map_view = metadata.get("mapView")
            if isinstance(map_view, dict):
                center = map_view.get("center")
                if isinstance(center, list | tuple) and len(center) >= 2:
                    crs = self._utm_crs_for_lon_lat(center[0], center[1])
                    if crs:
                        return crs

        summaries = payload.get("dataSummaries")
        if isinstance(summaries, list):
            dataset_id = str(payload.get("inputDatasetId") or payload.get("datasetId") or "")
            for summary in summaries:
                if not isinstance(summary, dict):
                    continue
                if dataset_id and str(summary.get("datasetId") or "") != dataset_id:
                    continue
                bbox = summary.get("bbox")
                if isinstance(bbox, list | tuple) and len(bbox) == 4:
                    lon = (float(bbox[0]) + float(bbox[2])) / 2
                    lat = (float(bbox[1]) + float(bbox[3])) / 2
                    return self._utm_crs_for_lon_lat(lon, lat)
        return None

    def _utm_crs_for_lon_lat(self, lon: Any, lat: Any) -> str | None:
        try:
            longitude = float(lon)
            latitude = float(lat)
        except (TypeError, ValueError):
            return None
        if not -180 <= longitude <= 180 or not -80 <= latitude <= 84:
            return None
        zone = int((longitude + 180) // 6) + 1
        epsg_prefix = 326 if latitude >= 0 else 327
        return f"EPSG:{epsg_prefix}{zone:02d}"

    def _spatial_filter_input(self, base_input: dict[str, Any]) -> dict[str, Any]:
        payload = dict(base_input)
        message = str(base_input.get("message") or "")
        input_dataset_id, mask_dataset_id = self._infer_spatial_filter_datasets(
            message,
            base_input.get("dataSummaries") or [],
            [str(dataset_id) for dataset_id in base_input.get("datasetIds") or []],
        )
        payload.update(
            {
                "inputDatasetId": input_dataset_id,
                "maskDatasetId": mask_dataset_id,
                "predicate": self._infer_spatial_predicate(message),
                "outputFields": self._infer_spatial_output_fields(
                    message,
                    base_input.get("dataSummaries") or [],
                    input_dataset_id,
                ),
            }
        )
        return payload

    def _infer_spatial_filter_datasets(
        self,
        message: str,
        summaries: list[Any],
        dataset_ids: list[str],
    ) -> tuple[str | None, str | None]:
        normalized = message.lower()
        summaries_by_id = {
            str(summary.get("datasetId") or ""): summary
            for summary in summaries
            if isinstance(summary, dict)
        }
        point_ids = [
            dataset_id
            for dataset_id in dataset_ids
            if str(summaries_by_id.get(dataset_id, {}).get("geometryType") or "").lower()
            in {"point", "multipoint"}
        ]
        polygon_ids = [
            dataset_id
            for dataset_id in dataset_ids
            if "polygon"
            in str(summaries_by_id.get(dataset_id, {}).get("geometryType") or "").lower()
        ]
        input_dataset_id = point_ids[0] if point_ids else (
            dataset_ids[1] if len(dataset_ids) > 1 else None
        )
        mask_dataset_id = polygon_ids[0] if polygon_ids else (
            dataset_ids[0] if dataset_ids else None
        )

        if "机场" in normalized or "airport" in normalized:
            for dataset_id in dataset_ids:
                summary = summaries_by_id.get(dataset_id, {})
                label = (
                    dataset_id
                    + " "
                    + str(summary.get("name") or "")
                    + " "
                    + json.dumps(summary.get("fields") or [], ensure_ascii=False)
                ).lower()
                if "airport" in label or "机场" in label:
                    input_dataset_id = dataset_id
                    break
        if "四川" in normalized:
            for dataset_id in dataset_ids:
                summary = summaries_by_id.get(dataset_id, {})
                label = (dataset_id + " " + str(summary.get("name") or "")).lower()
                if "四川" in label or "sichuan" in label:
                    mask_dataset_id = dataset_id
                    break

        if input_dataset_id == mask_dataset_id and len(dataset_ids) > 1:
            for dataset_id in dataset_ids:
                if dataset_id != mask_dataset_id:
                    input_dataset_id = dataset_id
                    break
        return input_dataset_id, mask_dataset_id

    def _infer_spatial_predicate(self, message: str) -> str:
        normalized = message.lower()
        if self._has_any(normalized, ["相交", "intersect", "intersects"]):
            return "intersects"
        return "within"

    def _infer_spatial_output_fields(
        self,
        message: str,
        summaries: list[Any],
        input_dataset_id: str | None,
    ) -> list[str]:
        normalized = message.lower()
        fields: list[str] = []
        for summary in summaries:
            if not isinstance(summary, dict):
                continue
            if input_dataset_id and summary.get("datasetId") != input_dataset_id:
                continue
            for field in summary.get("fields", []) or []:
                if isinstance(field, dict) and field.get("name"):
                    fields.append(str(field["name"]))

        if (
            input_dataset_id == "sample_populated_places"
            and self._is_points_in_existing_buffer_plan_request(normalized)
        ):
            preferred_fields = [
                "NAME",
                "NAME_ZH",
                "POP_MAX",
                "POP2020",
                "LATITUDE",
                "LONGITUDE",
            ]
            available = set(fields)
            matched_fields = [field for field in preferred_fields if field in available]
            if matched_fields:
                return matched_fields

        requested: list[str] = []
        for canonical, keywords in {
            "name": ["名称", "名字", "name"],
            "iata": ["iata", "iata 代码", "iata代码"],
            "type": ["类型", "type"],
        }.items():
            if not self._has_any(normalized, keywords):
                continue
            match = self._match_field(canonical, keywords, fields)
            if match and match not in requested:
                requested.append(match)
        return requested or fields

    def _match_field(
        self,
        canonical: str,
        keywords: list[str],
        fields: list[str],
    ) -> str | None:
        lower_fields = {field.lower(): field for field in fields}
        if canonical in lower_fields:
            return lower_fields[canonical]
        for field in fields:
            field_lower = field.lower()
            if any(keyword.replace(" ", "") in field_lower for keyword in keywords):
                return field
        return None

    def _infer_filter_field(self, message: str, summaries: list[Any]) -> str | None:
        normalized = message.lower()
        fields: list[str] = []
        for summary in summaries:
            summary_fields = summary.get("fields", []) if isinstance(summary, dict) else []
            for field in summary_fields:
                if isinstance(field, dict):
                    name = str(field.get("name") or "")
                    if name:
                        fields.append(name)
        for name in sorted(fields, key=len, reverse=True):
            if name.lower() in normalized:
                return name
        return None

    def _infer_distance(self, message: str) -> tuple[float, str] | None:
        match = re.search(
            r"(\d+(?:\.\d+)?)\s*(公里|千米|米|kilometers?|km|meters?|m)",
            message,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        return float(match.group(1)), match.group(2).lower()

    def _infer_group_by_field(self, message: str, summaries: list[Any]) -> str | None:
        normalized = message.lower()
        for summary in summaries:
            fields = summary.get("fields", []) if isinstance(summary, dict) else []
            for field in fields:
                if not isinstance(field, dict):
                    continue
                name = str(field.get("name") or "")
                if name and name.lower() in normalized:
                    return name
        for keyword in ["type", "category", "类别", "类型"]:
            for summary in summaries:
                fields = summary.get("fields", []) if isinstance(summary, dict) else []
                for field in fields:
                    if not isinstance(field, dict):
                        continue
                    name = str(field.get("name") or "")
                    if keyword in name.lower():
                        return name
        return None

    def _is_explicit_group_by_request(self, message: str) -> bool:
        normalized = message.lower()
        return self._has_any(
            normalized,
            ["按", "分组", "分类", "各", "每个", "group by", "by "],
        )

    def _infer_sort_by_field(self, message: str, summaries: list[Any]) -> str | None:
        normalized = message.lower()
        fields: list[str] = []
        for summary in summaries:
            summary_fields = summary.get("fields", []) if isinstance(summary, dict) else []
            for field in summary_fields:
                if not isinstance(field, dict):
                    continue
                name = str(field.get("name") or "")
                if name:
                    fields.append(name)
        for name in sorted(fields, key=len, reverse=True):
            if name.lower() in normalized and self._has_any(
                normalized,
                ["排序", "从高到低", "从低到高", "降序", "升序", "最高", "最低", "top", "order"],
            ):
                return name
        return None

    def _is_descending_request(self, message: str) -> bool:
        normalized = message.lower()
        if self._has_any(normalized, ["从低到高", "升序", "最低", "asc", "ascending"]):
            return False
        return True

    def _has_any(self, value: str, keywords: list[str]) -> bool:
        return any(keyword in value for keyword in keywords)

    def _is_metadata_query(self, message: str) -> bool:
        return self._has_any(
            message,
            [
                "字段",
                "图层",
                "数据",
                "属性",
                "有哪些",
                "是什么",
                "field",
                "schema",
                "元数据",
                "摘要",
                "坐标系",
                "crs",
                "几何类型",
                "geometry",
                "空间范围",
                "bbox",
            ],
        )

    def _is_read_only_metadata_count_query(self, message: str) -> bool:
        if not self._is_metadata_query(message):
            return False
        if not self._has_any(message, ["要素数量", "featurecount", "feature count"]):
            return False
        return not self._has_any(
            message,
            [
                "按",
                "统计",
                "分类",
                "占比",
                "平均",
                "总和",
                "求和",
                "汇总",
                "group by",
                "summary by",
            ],
        )

    def _is_bbox_clip_request(self, message: str) -> bool:
        if not self._has_any(message, ["裁剪", "clip", "截取"]):
            return False
        return self._has_any(
            message,
            ["范围", "bbox", "当前视图", "视图", "地图", "图层", "数据", "要素"],
        )

    def _is_spatial_filter_request(self, message: str) -> bool:
        if self._user_forbids_tools(message):
            return False
        if self._is_attribute_summary_request(message):
            return False
        if not self._has_any(
            message,
            [
                "范围内",
                "缓冲区内",
                "缓冲范围内",
                "省内",
                "市内",
                "区内",
                "县内",
                "within",
                "intersects",
                "相交",
                "空间筛选",
                "空间过滤",
            ],
        ):
            return False
        return self._has_any(
            message,
            [
                "执行",
                "查询",
                "找出",
                "返回",
                "生成",
                "筛选",
                "过滤",
                "所有",
                "哪些",
                "机场",
                "人口稠密",
            ],
        )

    def _is_attribute_summary_request(self, message: str) -> bool:
        if not self._has_any(
            message,
            ["统计", "数量", "分类", "占比", "平均", "总和", "求和", "汇总", "summary", "count"],
        ):
            return False
        if self._has_any(message, ["不要重新执行空间筛选", "不要执行空间筛选", "不要空间筛选"]):
            return True
        return not self._has_any(
            message,
            ["生成结果图层", "生成新图层", "执行空间筛选", "空间过滤", "范围内", "缓冲区内"],
        )

    def _is_attribute_summary_only_request(self, message: str) -> bool:
        if not self._is_attribute_summary_request(message):
            return False
        if self._has_any(message, ["有哪些字段", "字段列表", "schema", "元数据"]):
            return False
        return self._has_any(
            message,
            ["不要重新执行空间筛选", "不要执行空间筛选", "不要空间筛选"],
        ) or bool(self._infer_requested_statistics(message))

    def _infer_requested_field(self, message: str, summaries: list[Any]) -> str | None:
        normalized = message.lower()
        fields: list[str] = []
        for summary in summaries:
            summary_fields = summary.get("fields", []) if isinstance(summary, dict) else []
            for field in summary_fields:
                if not isinstance(field, dict):
                    continue
                name = str(field.get("name") or "")
                if name:
                    fields.append(name)
        for name in sorted(fields, key=len, reverse=True):
            if name.lower() in normalized:
                return name
        return None

    def _infer_requested_statistics(self, message: str) -> list[str]:
        normalized = message.lower()
        requested: list[str] = []
        for statistic, keywords in [
            ("sum", ["总人口", "总和", "求和", "合计", "sum"]),
            ("mean", ["平均人口", "平均值", "平均", "mean", "avg", "average"]),
            ("max", ["最大值", "最高", "最大", "max"]),
            ("min", ["最小值", "最低", "最小", "min"]),
        ]:
            if self._has_any(normalized, keywords):
                requested.append(statistic)
        return requested
