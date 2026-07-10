from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, cast

from geo_agent_service.modules.ai_chat.repository import AiChatRepository
from geo_agent_service.modules.ai_chat.schemas import ChatMessageRequest, StreamEvent
from geo_agent_service.modules.gis_data.schemas import InputDataSummary
from geo_agent_service.modules.gis_data.service import GisDatasetService
from geo_agent_service.schemas.session import AgentMessage, AgentSession

SessionStatus = Literal[
    "idle",
    "running",
    "waiting_confirmation",
    "waiting_clarification",
    "completed",
    "failed",
]
MessageStatus = Literal["streaming", "completed", "failed"]


class AiChatMessagingMixin:
    if TYPE_CHECKING:
        repository: AiChatRepository
        dataset_service: GisDatasetService | None

        def _task_type(self, message: str) -> str: ...
        def _data_summary_payload(
            self,
            summary: InputDataSummary,
            payload: ChatMessageRequest,
        ) -> dict[str, Any]: ...
        def _is_result_layer_inspection_request(self, message: str) -> bool: ...

    def _tool_failure_message(self, tool_results: list[dict[str, Any]]) -> str:
        failed_calls = [
            result for result in tool_results if result.get("status") == "failed"
        ]
        if not failed_calls:
            return ""
        parts: list[str] = []
        for call in failed_calls:
            tool_name = str(call.get("toolName") or "unknown")
            error = call.get("error")
            error_message = ""
            if isinstance(error, dict):
                error_message = str(error.get("message") or "")
            parts.append(f"{tool_name} 失败{f'：{error_message}' if error_message else ''}")
        return (
            "本轮工具调用失败（"
            + "；".join(parts)
            + "）。未生成结果图层，也不会返回 resultDatasetId、featureCount、bbox 或样本记录。"
            "建议检查输入数据集 ID、工具参数和空间关系后重试。"
        )

    def _blocked_spatial_filter_message(self, tool_results: list[dict[str, Any]]) -> str:
        spatial_calls = [
            result for result in tool_results if result.get("toolName") == "spatial_filter"
        ]
        if not spatial_calls:
            return ""
        if any(result.get("status") == "completed" for result in spatial_calls):
            return ""
        error = spatial_calls[-1].get("error")
        error_message = ""
        if isinstance(error, dict):
            error_message = str(error.get("message") or "")
        if not error_message:
            error_message = "spatial_filter 尚未实现或未成功执行。"
        return (
            "spatial_filter 尚未实现/未执行，不能返回四川省范围内机场的确定性筛选结果。"
            f"工具状态：failed；原因：{error_message}"
        )

    def _deterministic_geoprocess_buffer_message(
        self,
        tool_results: list[dict[str, Any]],
    ) -> str:
        for result in tool_results:
            if result.get("toolName") != "geoprocess" or result.get("status") != "completed":
                continue
            output = result.get("output")
            if not isinstance(output, dict):
                continue
            lineage = output.get("lineage")
            if not isinstance(lineage, dict) or lineage.get("operation") != "buffer":
                continue
            result_summary = output.get("result")
            if not isinstance(result_summary, dict):
                result_summary = {}
            area = output.get("area")
            if not isinstance(area, dict):
                area = {}
            bbox = output.get("bbox")
            output_crs = str(result_summary.get("crs") or "未知")
            processing_crs = str(output.get("processingCRS") or lineage.get("processingCRS") or "")
            distance = lineage.get("distance") or output.get("distance")
            result_dataset_id = str(output.get("resultDatasetId") or "")
            layer_name = str(result_summary.get("name") or "")
            geometry_type = str(
                output.get("geometryType") or result_summary.get("geometryType") or ""
            )
            area_value = area.get("value")
            area_unit = str(area.get("unit") or "square_meters")
            data_ref = str(output.get("dataRef") or result_summary.get("dataRef") or "")
            return (
                "缓冲区分析已执行完成。"
                f"resultDatasetId={result_dataset_id}；"
                f"图层名称={layer_name}；"
                f"几何类型={geometry_type}；"
                f"bbox={bbox}；"
                f"面积估算={area_value} {area_unit}；"
                f"dataRef={data_ref}。\n"
                f"输入点先临时重投影到 {processing_crs} 进行 {distance} 米缓冲计算；"
                f"结果几何已回写为 {output_crs} GeoJSON，bbox 使用 WGS84 经纬度，"
                f"面积按 processingCRS={processing_crs} 计算。"
            )
        return ""

    def _deterministic_attribute_summary_message(
        self,
        tool_results: list[dict[str, Any]],
    ) -> str:
        if len(tool_results) != 1:
            return ""
        completed = [
            result
            for result in tool_results
            if result.get("toolName") == "attribute_summary"
            and result.get("status") == "completed"
        ]
        if not completed:
            return ""
        result = completed[-1]
        raw_tool_input = result.get("input")
        tool_input: dict[str, Any] = raw_tool_input if isinstance(raw_tool_input, dict) else {}
        field = str(tool_input.get("field") or "")
        raw_statistics = tool_input.get("statistics")
        statistics_source = raw_statistics if isinstance(raw_statistics, list) else []
        statistics = [
            str(statistic)
            for statistic in statistics_source
            if str(statistic) in {"sum", "mean", "max", "min"}
        ]
        if not field or not statistics:
            return ""

        raw_output = result.get("output")
        output: dict[str, Any] = raw_output if isinstance(raw_output, dict) else {}
        raw_summary = output.get("summary")
        summary: dict[str, Any] = raw_summary if isinstance(raw_summary, dict) else {}
        raw_fields = summary.get("fields")
        fields = raw_fields if isinstance(raw_fields, list) else []
        field_summary = next(
            (
                item
                for item in fields
                if isinstance(item, dict) and str(item.get("name") or "") == field
            ),
            None,
        )
        dataset_id = str(
            summary.get("datasetId") or tool_input.get("datasetId") or ""
        )
        if not isinstance(field_summary, dict):
            return (
                "attribute_summary 已执行完成，但 tool.completed.output 未包含 "
                f"{field} 字段统计结果；datasetId={dataset_id}。"
            )

        labels = {
            "sum": "总人口",
            "mean": "平均人口",
            "max": "最大值",
            "min": "最小值",
        }
        parts = [
            f"{labels[statistic]}={field_summary.get(statistic)}"
            for statistic in statistics
            if statistic in field_summary
        ]
        if not parts:
            return (
                "attribute_summary 已执行完成，但 tool.completed.output 未包含请求的 "
                f"{field} 统计项；datasetId={dataset_id}。"
            )
        return (
            "attribute_summary 已执行完成。"
            f"datasetId={dataset_id}；field={field}；" + "；".join(parts) + "。"
        )

    def _deterministic_spatial_filter_message(
        self,
        tool_results: list[dict[str, Any]],
    ) -> str:
        completed = [
            result
            for result in tool_results
            if result.get("toolName") == "spatial_filter"
            and result.get("status") == "completed"
        ]
        if len(completed) != 1:
            return ""

        result = completed[0]
        raw_output = result.get("output")
        output: dict[str, Any] = raw_output if isinstance(raw_output, dict) else {}
        raw_summary = output.get("summary")
        summary: dict[str, Any] = raw_summary if isinstance(raw_summary, dict) else {}
        rows_source = output.get("rows")
        rows = rows_source if isinstance(rows_source, list) else summary.get("rows")
        if not isinstance(rows, list):
            rows = []

        result_dataset_id = str(
            output.get("resultDatasetId") or summary.get("resultDatasetId") or ""
        )
        feature_count = output.get("featureCount")
        if feature_count is None:
            feature_count = summary.get("featureCount")
        predicate = str(summary.get("predicate") or "")
        input_dataset_id = str(summary.get("inputDatasetId") or "")
        mask_dataset_id = str(summary.get("maskDatasetId") or "")
        output_fields_source = summary.get("outputFields")
        output_fields = (
            [str(field) for field in output_fields_source]
            if isinstance(output_fields_source, list)
            else []
        )

        lines = [
            "已执行确定性 GIS 工具 spatial_filter。",
            f"输入点图层={input_dataset_id or '未知'}；"
            f"掩膜面图层={mask_dataset_id or '未知'}；"
            f"空间关系={predicate or '未知'}；"
            f"结果要素数量={feature_count if feature_count is not None else '未知'}；"
            f"resultDatasetId={result_dataset_id or '未知'}。",
        ]
        if output_fields:
            lines.append("输出字段：" + "、".join(output_fields) + "。")

        if rows:
            lines.append("筛选结果：")
            for index, row in enumerate(rows, start=1):
                if not isinstance(row, dict):
                    continue
                rendered = []
                for field in output_fields or row.keys():
                    key = str(field)
                    rendered.append(f"{key}={row.get(key)}")
                lines.append(f"{index}. " + "；".join(rendered) + "。")
        else:
            lines.append("本次筛选未命中任何要素。")
        return "\n".join(lines)

    def _deterministic_selected_layer_metadata_message(
        self,
        data_summaries: list[InputDataSummary],
        payload: ChatMessageRequest,
    ) -> str:
        message = payload.message.lower()
        if not self._is_selected_layer_metadata_request(message):
            return ""

        layers = self._merged_layer_context(payload.metadata.get("layers"), data_summaries)
        if not layers:
            layers = [
                {
                    "id": None,
                    "layerId": None,
                    "datasetId": summary.dataset_id,
                    "name": summary.name,
                    "geometryType": summary.geometry_type,
                    "crs": summary.crs,
                    "bbox": summary.bbox,
                    "featureCount": summary.feature_count,
                }
                for summary in data_summaries
            ]
        if not layers:
            return ""

        summaries_by_id = {summary.dataset_id: summary for summary in data_summaries}
        result_inspection = self._is_result_layer_inspection_request(message)
        parts = [
            (
                "当前结果图层信息如下："
                if result_inspection
                else "当前已选图层信息如下："
            )
        ]
        for index, layer in enumerate(layers, start=1):
            dataset_id = str(layer.get("datasetId") or "")
            summary = summaries_by_id.get(dataset_id)
            field_names = [field.name for field in summary.fields] if summary else []
            fields = ", ".join(field_names) if field_names else "无属性字段"
            summary_payload = (
                self._data_summary_payload(summary, payload) if summary is not None else {}
            )
            lineage = summary.lineage if summary and isinstance(summary.lineage, dict) else {}
            source_dataset_id = summary_payload.get("sourceDatasetId") or summary_payload.get(
                "inputDatasetId"
            )
            mask_dataset_id = lineage.get("maskDatasetId")
            predicate = lineage.get("predicate")
            operation = lineage.get("operation")
            tool_call_id = summary_payload.get("toolCallId")
            extras: list[str] = []
            if source_dataset_id:
                extras.append(f"来源输入图层={source_dataset_id}")
            if mask_dataset_id:
                extras.append(f"掩膜图层={mask_dataset_id}")
            if operation:
                extras.append(f"来源操作={operation}")
            if predicate:
                extras.append(f"空间关系={predicate}")
            if summary_payload.get("processingCRS"):
                extras.append(f"processingCRS={summary_payload['processingCRS']}")
            if summary_payload.get("area") is not None:
                extras.append(f"面积={summary_payload['area']}")
            if summary_payload.get("distance") is not None:
                extras.append(f"缓冲距离={summary_payload['distance']}")
            if summary_payload.get("unit") is not None:
                extras.append(f"单位={summary_payload['unit']}")
            if tool_call_id:
                extras.append(f"工具调用ID={tool_call_id}")
            parts.append(
                f"{index}. 图层ID={layer.get('layerId') or layer.get('id') or '未知'}；"
                f"数据集ID={dataset_id or '未知'}；"
                f"名称={layer.get('name') or (summary.name if summary else '未知')}；"
                f"几何类型={layer.get('geometryType') or '未知'}；"
                f"CRS={layer.get('crs') or '未知'}；"
                f"bbox={layer.get('bbox') or '未知'}；"
                f"要素数量={layer.get('featureCount') if layer.get('featureCount') is not None else '未知'}；"
                f"字段={fields}"
                + (f"；{'；'.join(extras)}" if extras else "")
                + "。"
            )
        if result_inspection:
            parts.append(
                "判断：该结果图层已有数据集 ID、几何类型、bbox 和要素数量，"
                "可继续作为后续分析输入；若后续涉及距离、面积或叠加分析，"
                "应优先确认 CRS/processingCRS 与目标工具要求一致。"
            )
        return "\n".join(parts)

    def _deterministic_data_readiness_message(
        self,
        data_summaries: list[InputDataSummary],
        payload: ChatMessageRequest,
    ) -> str:
        if self._task_type(payload.message.lower()) != "data_readiness":
            return ""
        if not data_summaries:
            return "当前没有可读取的数据摘要，无法判断这些图层是否适合后续分析。"

        point_layers = [
            summary
            for summary in data_summaries
            if summary.geometry_type in {"Point", "MultiPoint"}
        ]
        polygon_layers = [
            summary
            for summary in data_summaries
            if summary.geometry_type in {"Polygon", "MultiPolygon", "Mixed"}
        ]
        crs_values = {summary.crs for summary in data_summaries if summary.crs}
        same_crs = len(crs_values) == 1

        parts = ["已按本轮指令只检查以下数据集，未执行筛选，也未生成新图层："]
        for index, summary in enumerate(data_summaries, start=1):
            field_names = ", ".join(field.name for field in summary.fields[:8])
            parts.append(
                f"{index}. {summary.dataset_id}（{summary.name}）："
                f"geometryType={summary.geometry_type}；CRS={summary.crs or '未知'}；"
                f"featureCount={summary.feature_count}；bbox={summary.bbox}；"
                f"字段={field_names or '无'}。"
            )

        if point_layers and polygon_layers and same_crs:
            parts.append(
                "判断：这两个图层适合用于“四川省内机场筛选”的准备条件检查。"
                f"{point_layers[0].dataset_id} 是点图层，"
                f"{polygon_layers[0].dataset_id} 是面图层，且 CRS 一致；"
                "后续如果用户明确要求执行筛选，可用面图层作为范围掩膜、点图层作为输入点。"
            )
        else:
            reasons: list[str] = []
            if not point_layers:
                reasons.append("缺少点图层")
            if not polygon_layers:
                reasons.append("缺少面图层")
            if not same_crs:
                reasons.append("CRS 不一致或缺失")
            parts.append(
                "判断：当前图层还不完全满足空间筛选准备条件；"
                + "；".join(reasons)
                + "。"
            )

        warnings = [
            warning
            for summary in data_summaries
            for warning in summary.warnings
            if warning
        ]
        if warnings:
            parts.append("注意：" + "；".join(dict.fromkeys(warnings)) + "。")
        return "\n".join(parts)

    def _is_selected_layer_metadata_request(self, message: str) -> bool:
        selected_terms = ["当前已选", "已选", "选中", "selected"]
        metadata_terms = [
            "数据集 id",
            "数据集id",
            "dataset id",
            "图层 id",
            "图层id",
            "layer id",
            "几何类型",
            "crs",
            "bbox",
            "字段",
            "要素数量",
            "来源输入图层",
            "空间关系",
            "是否可以继续",
            "继续用于",
            "后续分析",
            "featurecount",
            "feature count",
        ]
        if any(term in message for term in selected_terms) and any(
            term in message for term in metadata_terms
        ):
            return True
        return self._is_result_layer_inspection_request(message)

    def _get_or_create_session(
        self,
        *,
        user_id: str,
        session_id: str,
        payload: ChatMessageRequest,
    ) -> AgentSession:
        session = self.repository.get(user_id, session_id)
        if session is not None:
            return session

        now = datetime.now(UTC).isoformat()
        title = payload.message.strip()[:40] or "Untitled chat"
        return AgentSession(
            id=session_id,
            title=title,
            status="idle",
            selectedDatasetIds=payload.selected_dataset_ids,
            selectedServiceIds=payload.selected_service_ids,
            createdAt=now,
            updatedAt=now,
        )

    def _model_messages(
        self,
        messages: list[AgentMessage],
        data_summaries: list[InputDataSummary],
        payload: ChatMessageRequest,
    ) -> list[dict[str, str]]:
        model_messages = [
            {"role": message.role, "content": message.content}
            for message in messages
            if message.role in {"user", "assistant"} and message.content
        ]
        model_messages.insert(
            0,
            {
                "role": "system",
                "content": self._gis_context_prompt(data_summaries, payload),
            },
        )
        return model_messages

    def _gis_context_prompt(
        self,
        data_summaries: list[InputDataSummary],
        payload: ChatMessageRequest,
    ) -> str:
        if not data_summaries:
            return (
                "当前没有已选择且可读取的 GIS 数据集。不要编造图层、字段或统计结果；"
                "如果用户要求分析数据，请说明需要先选择或上传数据。"
            )

        lines = [
            "当前可用 GIS 数据集摘要如下。模型只能看到摘要；完整数据必须通过后端工具读取。",
            f"本轮意图: {self._task_type(payload.message.lower())}",
        ]
        for index, summary in enumerate(data_summaries, start=1):
            summary_payload = self._data_summary_payload(summary, payload)
            fields = ", ".join(
                f"{field.name}({field.type})" for field in summary.fields
            ) or "无属性字段"
            lines.extend(
                [
                    f"{index}. {summary.dataset_id} - {summary.name}",
                    f"   geometryType: {summary.geometry_type}",
                    f"   featureCount: {summary.feature_count}",
                    f"   crs: {summary.crs}",
                    f"   processingCRS: {summary_payload.get('processingCRS')}",
                    f"   bbox: {summary.bbox}",
                    f"   area: {summary_payload.get('area')}",
                    f"   fields: {fields}",
                    f"   dataRef: {summary.data_ref}",
                ]
            )
            for key in ["sourceDatasetId", "inputDatasetId", "distance", "unit", "toolCallId"]:
                if key in summary_payload:
                    lines.append(f"   {key}: {summary_payload[key]}")
            if summary.lineage:
                lines.append(
                    "   lineage: "
                    + json.dumps(summary.lineage, ensure_ascii=False, default=str)
                )
            if summary.warnings:
                lines.append(f"   warnings: {'; '.join(summary.warnings)}")
        layers = self._merged_layer_context(payload.metadata.get("layers"), data_summaries)
        map_view = payload.metadata.get("mapView")
        if layers:
            lines.append(
                "前端图层上下文（已按 datasetId 用后端 data.summary 补齐；"
                "若两者冲突，以 data.summary 为准）: "
                f"{json.dumps(layers, ensure_ascii=False)}"
            )
        if map_view:
            lines.append(f"当前地图视角: {map_view}")
        lines.append(
            "规则：回答必须基于上面的真实摘要和工具结果；不要编造不存在的字段；"
            "前端图层上下文只用于理解图层 ID、显隐和透明度，空间元数据以 data.summary 为准；"
            "询问刚才生成的图层、结果图层、图层信息、来源、bbox、要素数量或是否可继续分析时，"
            "只基于 data.summary 和 lineage 回答，不要假设本轮执行了新分析；"
            "若 lineage.predicate 已存在，必须直接陈述该空间关系，不要用“应为”“可能是”等推测语气；"
            "within 对点-面筛选表示点位严格位于多边形内部，"
            "边界包含语义应使用 intersects 或 covered_by；"
            "需要空间计算、属性统计或生成图层时，以后端工具结果为准。"
        )
        return "\n".join(lines)

    def _merged_layer_context(
        self,
        layers: Any,
        data_summaries: list[InputDataSummary],
    ) -> list[dict[str, Any]]:
        if not isinstance(layers, list):
            return []

        summaries_by_id = {summary.dataset_id: summary for summary in data_summaries}
        merged_layers: list[dict[str, Any]] = []
        for layer in layers:
            if not isinstance(layer, dict):
                continue
            merged = dict(layer)
            dataset_id = str(merged.get("datasetId") or "")
            summary = summaries_by_id.get(dataset_id)
            if summary is None:
                continue
            merged.update(
                {
                    "datasetId": summary.dataset_id,
                    "name": merged.get("name") or summary.name,
                    "sourceType": summary.source_type,
                    "geometryType": summary.geometry_type,
                    "crs": summary.crs,
                    "bbox": summary.bbox,
                    "featureCount": summary.feature_count,
                    "dataRef": summary.data_ref,
                    "lineage": summary.lineage,
                }
            )
            merged_layers.append(merged)
        return merged_layers

    def _area_from_summary(
        self,
        summary: InputDataSummary,
        lineage: dict[str, Any],
    ) -> dict[str, Any] | None:
        processing_crs = str(lineage.get("processingCRS") or "").strip()
        if not processing_crs:
            return None
        if summary.geometry_type not in {"Polygon", "MultiPolygon", "Mixed"}:
            return None
        if self.dataset_service is None:
            return None
        try:
            import geopandas as gpd  # type: ignore[import-untyped]

            path = self.dataset_service.resolve_data_ref(summary.data_ref)
            geodata = gpd.read_file(path)
            if geodata.empty:
                return None
            area = float(geodata.to_crs(processing_crs).geometry.area.sum())
        except Exception:
            return None
        return {
            "value": area,
            "unit": "square_meters",
            "processingCRS": processing_crs,
        }

    def _duration_ms(self, started_at: datetime, finished_at: datetime) -> int:
        return int((finished_at - started_at).total_seconds() * 1000)

    def _encode_event(self, event: StreamEvent) -> str:
        data = json.dumps(event.model_dump(mode="json", by_alias=True), ensure_ascii=False)
        return f"event: {event.type}\ndata: {data}\n\n"

    def _persist_session(
        self,
        user_id: str,
        session: AgentSession,
        *,
        status: SessionStatus,
    ) -> None:
        session.status = status
        session.updated_at = datetime.now(UTC).isoformat()
        self.repository.save(user_id, session)

    def _message_completed_event(
        self,
        session_id: str,
        message: AgentMessage,
    ) -> StreamEvent:
        return StreamEvent(
            type="message.completed",
            sessionId=session_id,
            messageId=message.id,
            data={
                "message": message.model_dump(
                    mode="json",
                    by_alias=True,
                ),
            },
        )

    def _finalize_assistant_message(
        self,
        *,
        user_id: str,
        session: AgentSession,
        assistant_message: AgentMessage,
        chunks: list[str],
        status: MessageStatus = "completed",
    ) -> StreamEvent:
        assistant_message.content = "".join(chunks)
        assistant_message.status = status
        self._persist_session(user_id, session, status=cast(SessionStatus, status))
        return self._message_completed_event(session.id, assistant_message)
