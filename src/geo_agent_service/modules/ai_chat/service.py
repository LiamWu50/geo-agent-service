import json
import re
import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from geo_agent_service.modules.ai_chat.model_client import ChatModelClient
from geo_agent_service.modules.ai_chat.repository import AiChatRepository
from geo_agent_service.modules.ai_chat.schemas import (
    ChatMessageRequest,
    StreamEvent,
    new_agent_message,
)
from geo_agent_service.modules.gis_data.repository import DatasetRepository
from geo_agent_service.modules.gis_data.schemas import InputDataSummary
from geo_agent_service.schemas.agent import AgentError, ToolCallRecord
from geo_agent_service.schemas.session import AgentMessage, AgentSession
from geo_agent_service.tools.registry import GisToolRegistry


class AiChatService:
    def __init__(
        self,
        *,
        repository: AiChatRepository,
        dataset_repository: DatasetRepository,
        tool_registry: GisToolRegistry,
        model_client: ChatModelClient,
    ) -> None:
        self.repository = repository
        self.dataset_repository = dataset_repository
        self.tool_registry = tool_registry
        self.model_client = model_client

    async def stream_message(
        self,
        *,
        user_id: str,
        session_id: str,
        payload: ChatMessageRequest,
    ) -> AsyncIterator[str]:
        try:
            session = self._get_or_create_session(
                user_id=user_id,
                session_id=session_id,
                payload=payload,
            )
            user_message = new_agent_message(
                message_id=f"msg_{secrets.token_urlsafe(12)}",
                role="user",
                content=payload.message.strip(),
                status="completed",
            )
            assistant_message = new_agent_message(
                message_id=f"msg_{secrets.token_urlsafe(12)}",
                role="assistant",
                content="",
                status="streaming",
            )
            session.messages.extend([user_message, assistant_message])
            session.status = "running"
            session.selected_dataset_ids = payload.selected_dataset_ids
            session.selected_service_ids = payload.selected_service_ids
            session.data_summaries = self._load_data_summaries(payload.selected_dataset_ids)
            session.updated_at = datetime.now(UTC).isoformat()
            self.repository.save(user_id, session)

            yield self._encode_event(
                StreamEvent(
                    type="data.summary",
                    sessionId=session.id,
                    data={
                        "datasets": [
                            summary.model_dump(mode="json", by_alias=True)
                            for summary in session.data_summaries
                        ],
                        "selectedDatasetIds": payload.selected_dataset_ids,
                        "missingDatasetIds": self._missing_dataset_ids(
                            payload.selected_dataset_ids,
                            session.data_summaries,
                        ),
                    },
                )
            )

            tool_results: list[dict[str, Any]] = []
            async for event in self._run_tools(session, payload):
                if event.type in {"tool.completed", "tool.failed"}:
                    tool_results.append(event.data)
                yield self._encode_event(event)

            chunks: list[str] = []
            async for chunk in self.model_client.stream_response(
                messages=self._model_messages(session.messages, session.data_summaries, payload),
                tool_results=tool_results,
            ):
                chunks.append(chunk)
                yield self._encode_event(
                    StreamEvent(
                        type="message.delta",
                        sessionId=session.id,
                        messageId=assistant_message.id,
                        data={"delta": chunk},
                    )
                )

            assistant_message.content = "".join(chunks)
            assistant_message.status = "completed"
            session.status = "completed"
            session.updated_at = datetime.now(UTC).isoformat()
            self.repository.save(user_id, session)
            yield self._encode_event(
                StreamEvent(
                    type="message.completed",
                    sessionId=session.id,
                    messageId=assistant_message.id,
                    data={
                        "message": assistant_message.model_dump(mode="json", by_alias=True),
                    },
                )
            )
        except Exception as exc:
            yield self._encode_event(
                StreamEvent(
                    type="error",
                    sessionId=session_id,
                    data={"message": str(exc)},
                )
            )

    def get_session(self, *, user_id: str, session_id: str) -> AgentSession | None:
        return self.repository.get(user_id, session_id)

    async def _run_tools(
        self,
        session: AgentSession,
        payload: ChatMessageRequest,
    ) -> AsyncIterator[StreamEvent]:
        for tool_name, tool_input in self._select_tool_calls(session, payload):
            tool = self.tool_registry.get(tool_name)
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
            try:
                result = await tool.run(tool_input)
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
                        "error": error.model_dump(mode="json", by_alias=True),
                    },
                )
                continue

            now = datetime.now(UTC)
            tool_call.status = "completed"
            tool_call.output = result.model_dump(mode="json", by_alias=True)
            tool_call.finished_at = now.isoformat()
            tool_call.duration_ms = self._duration_ms(started_at, now)
            yield StreamEvent(
                type="tool.completed",
                sessionId=session.id,
                toolCallId=tool_call.id,
                data={
                    "toolName": tool_name,
                    "output": tool_call.output,
                },
            )
            if result.layer:
                yield StreamEvent(
                    type="layer.created",
                    sessionId=session.id,
                    toolCallId=tool_call.id,
                    data=result.layer,
                )
            if result.map_command:
                yield StreamEvent(
                    type="map.command",
                    sessionId=session.id,
                    toolCallId=tool_call.id,
                    data=result.map_command,
                )

    def _load_data_summaries(self, dataset_ids: list[str]) -> list[InputDataSummary]:
        summaries: list[InputDataSummary] = []
        for dataset_id in dataset_ids:
            record = self.dataset_repository.get(dataset_id)
            if record is not None:
                summaries.append(record.summary)
        return summaries

    def _missing_dataset_ids(
        self,
        selected_dataset_ids: list[str],
        data_summaries: list[InputDataSummary],
    ) -> list[str]:
        loaded_ids = {summary.dataset_id for summary in data_summaries}
        return [dataset_id for dataset_id in selected_dataset_ids if dataset_id not in loaded_ids]

    def _select_tool_calls(
        self,
        session: AgentSession,
        payload: ChatMessageRequest,
    ) -> list[tuple[str, dict[str, Any]]]:
        message = payload.message.lower()
        base_input = {
            "message": payload.message,
            "query": payload.message,
            "selectedDatasetIds": payload.selected_dataset_ids,
            "datasetIds": payload.selected_dataset_ids,
            "selectedServiceIds": payload.selected_service_ids,
            "metadata": payload.metadata,
            "dataSummaries": [
                summary.model_dump(mode="json", by_alias=True)
                for summary in session.data_summaries
            ],
        }

        calls: list[tuple[str, dict[str, Any]]] = []
        if self._has_any(message, ["字段", "图层", "数据", "属性", "有哪些", "是什么", "field"]):
            if "metadata_search" in self.tool_registry.list_names():
                calls.append(("metadata_search", base_input))
        if self._has_any(
            message,
            ["统计", "数量", "分类", "占比", "平均", "总和", "求和", "汇总", "summary", "count"],
        ):
            if (
                payload.selected_dataset_ids
                and "attribute_summary" in self.tool_registry.list_names()
            ):
                calls.append(("attribute_summary", self._attribute_summary_input(base_input)))
        if self._has_any(message, ["缓冲", "buffer", "附近"]):
            if payload.selected_dataset_ids and "geoprocess" in self.tool_registry.list_names():
                calls.append(
                    ("geoprocess", self._geoprocess_input(base_input, operation="buffer"))
                )
        elif self._has_any(message, ["中心点", "质心", "centroid"]):
            if payload.selected_dataset_ids and "geoprocess" in self.tool_registry.list_names():
                calls.append(
                    ("geoprocess", self._geoprocess_input(base_input, operation="centroid"))
                )
        elif self._has_any(message, ["裁剪", "范围", "bbox", "当前视图"]):
            if payload.selected_dataset_ids and "geoprocess" in self.tool_registry.list_names():
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
            if payload.selected_dataset_ids and "geoprocess" in self.tool_registry.list_names():
                calls.append(
                    (
                        "geoprocess",
                        self._geoprocess_input(base_input, operation="attribute_filter"),
                    )
                )

        return calls

    def _attribute_summary_input(self, base_input: dict[str, Any]) -> dict[str, Any]:
        payload = dict(base_input)
        group_by = self._infer_group_by_field(
            str(base_input.get("message") or ""),
            base_input.get("dataSummaries") or [],
        )
        if group_by:
            payload["groupBy"] = group_by
        return payload

    def _geoprocess_input(
        self,
        base_input: dict[str, Any],
        *,
        operation: str,
    ) -> dict[str, Any]:
        payload = dict(base_input)
        payload["operation"] = operation
        if operation == "buffer":
            parsed_distance = self._infer_distance(str(base_input.get("message") or ""))
            if parsed_distance is not None:
                payload["distance"], payload["unit"] = parsed_distance
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

    def _has_any(self, value: str, keywords: list[str]) -> bool:
        return any(keyword in value for keyword in keywords)

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
        ]
        for index, summary in enumerate(data_summaries, start=1):
            fields = ", ".join(
                f"{field.name}({field.type})" for field in summary.fields
            ) or "无属性字段"
            lines.extend(
                [
                    f"{index}. {summary.dataset_id} - {summary.name}",
                    f"   geometryType: {summary.geometry_type}",
                    f"   featureCount: {summary.feature_count}",
                    f"   crs: {summary.crs}",
                    f"   bbox: {summary.bbox}",
                    f"   fields: {fields}",
                    f"   dataRef: {summary.data_ref}",
                ]
            )
            if summary.warnings:
                lines.append(f"   warnings: {'; '.join(summary.warnings)}")
        layers = payload.metadata.get("layers")
        map_view = payload.metadata.get("mapView")
        if layers:
            lines.append(f"前端图层上下文: {layers}")
        if map_view:
            lines.append(f"当前地图视角: {map_view}")
        lines.append(
            "规则：回答必须基于上面的真实摘要和工具结果；不要编造不存在的字段；"
            "需要空间计算、属性统计或生成图层时，以后端工具结果为准。"
        )
        return "\n".join(lines)

    def _duration_ms(self, started_at: datetime, finished_at: datetime) -> int:
        return int((finished_at - started_at).total_seconds() * 1000)

    def _encode_event(self, event: StreamEvent) -> str:
        data = json.dumps(event.model_dump(mode="json", by_alias=True), ensure_ascii=False)
        return f"event: {event.type}\ndata: {data}\n\n"
