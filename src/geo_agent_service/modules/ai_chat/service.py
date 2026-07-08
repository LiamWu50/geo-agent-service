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
from geo_agent_service.modules.gis_data.service import GisDatasetService
from geo_agent_service.schemas.agent import AgentError, ToolCallRecord
from geo_agent_service.schemas.session import AgentMessage, AgentSession
from geo_agent_service.tools.registry import GisToolRegistry


class AiChatService:
    def __init__(
        self,
        *,
        repository: AiChatRepository,
        dataset_repository: DatasetRepository,
        dataset_service: GisDatasetService | None = None,
        tool_registry: GisToolRegistry,
        model_client: ChatModelClient,
    ) -> None:
        self.repository = repository
        self.dataset_repository = dataset_repository
        self.dataset_service = dataset_service
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
            effective_dataset_ids = self._effective_dataset_ids(payload, session)
            available_dataset_ids = self._available_dataset_ids(payload)
            session.selected_dataset_ids = effective_dataset_ids
            session.selected_service_ids = payload.selected_service_ids
            session.data_summaries = self._with_recovered_lineage(
                self._load_data_summaries(
                    effective_dataset_ids,
                    existing_summaries=session.data_summaries,
                ),
                session,
            )
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
                            if summary.dataset_id in effective_dataset_ids
                        ],
                        "availableDatasetIds": available_dataset_ids,
                        "selectedDatasetIds": payload.selected_dataset_ids,
                        "effectiveDatasetIds": effective_dataset_ids,
                        "missingDatasetIds": self._missing_dataset_ids(
                            effective_dataset_ids,
                            session.data_summaries,
                        ),
                    },
                )
            )

            if self._is_plan_only_request(payload.message):
                plan_payload = self._plan_created_payload(session, payload)
                yield self._encode_event(
                    StreamEvent(
                        type="plan.created",
                        sessionId=session.id,
                        messageId=assistant_message.id,
                        data=plan_payload,
                    )
                )
                assistant_message.content = self._plan_message(plan_payload)
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
                            "message": assistant_message.model_dump(
                                mode="json",
                                by_alias=True,
                            ),
                        },
                    )
                )
                return

            tool_results: list[dict[str, Any]] = []
            async for event in self._run_tools(session, payload):
                if event.type in {"tool.completed", "tool.failed"}:
                    tool_results.append(event.data)
                yield self._encode_event(event)

            chunks: list[str] = []
            blocked_spatial_filter_message = self._blocked_spatial_filter_message(tool_results)
            if blocked_spatial_filter_message:
                chunks.append(blocked_spatial_filter_message)
                yield self._encode_event(
                    StreamEvent(
                        type="message.delta",
                        sessionId=session.id,
                        messageId=assistant_message.id,
                        data={"delta": blocked_spatial_filter_message},
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
                            "message": assistant_message.model_dump(
                                mode="json",
                                by_alias=True,
                            ),
                        },
                    )
                )
                return

            failure_notice = self._tool_failure_notice(tool_results)
            if failure_notice:
                chunks.append(failure_notice)
                yield self._encode_event(
                    StreamEvent(
                        type="message.delta",
                        sessionId=session.id,
                        messageId=assistant_message.id,
                        data={"delta": failure_notice},
                    )
                )

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

    def _tool_output(self, tool_name: str, result: Any) -> dict[str, Any]:
        output = result.model_dump(mode="json", by_alias=True)
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

    def _load_data_summaries(
        self,
        dataset_ids: list[str],
        *,
        existing_summaries: list[InputDataSummary] | None = None,
    ) -> list[InputDataSummary]:
        summaries: list[InputDataSummary] = []
        existing_by_id = {
            summary.dataset_id: summary for summary in (existing_summaries or [])
        }
        for dataset_id in dataset_ids:
            if self.dataset_service is not None:
                try:
                    summaries.append(self.dataset_service.get_dataset(dataset_id))
                    continue
                except LookupError:
                    pass

            record = self.dataset_repository.get(dataset_id)
            if record is not None:
                summaries.append(record.summary)
                continue
            if dataset_id in existing_by_id:
                summaries.append(existing_by_id[dataset_id])
        return summaries

    def _with_recovered_lineage(
        self,
        summaries: list[InputDataSummary],
        session: AgentSession,
    ) -> list[InputDataSummary]:
        enriched: list[InputDataSummary] = []
        for summary in summaries:
            if summary.lineage is not None:
                enriched.append(summary)
                continue

            lineage = self._lineage_from_tool_calls(summary.dataset_id, session.tool_calls)
            if lineage is None:
                enriched.append(summary)
                continue

            recovered = summary.model_copy(update={"lineage": lineage})
            self._persist_recovered_lineage(recovered)
            enriched.append(recovered)
        return enriched

    def _lineage_from_tool_calls(
        self,
        dataset_id: str,
        tool_calls: list[ToolCallRecord],
    ) -> dict[str, Any] | None:
        for tool_call in reversed(tool_calls):
            if tool_call.status != "completed" or tool_call.tool_name != "spatial_filter":
                continue
            output = tool_call.output if isinstance(tool_call.output, dict) else {}
            summary = output.get("summary") if isinstance(output.get("summary"), dict) else output
            if not isinstance(summary, dict):
                continue
            if str(summary.get("resultDatasetId") or "") != dataset_id:
                continue
            tool_input = tool_call.input if isinstance(tool_call.input, dict) else {}
            return {
                "operation": "spatial_filter",
                "inputDatasetId": str(
                    summary.get("inputDatasetId")
                    or tool_input.get("inputDatasetId")
                    or ""
                ),
                "maskDatasetId": str(
                    summary.get("maskDatasetId")
                    or tool_input.get("maskDatasetId")
                    or ""
                ),
                "predicate": str(summary.get("predicate") or tool_input.get("predicate") or ""),
                "outputFields": list(
                    summary.get("outputFields")
                    or tool_input.get("outputFields")
                    or []
                ),
                "toolCallId": tool_call.id,
            }
        return None

    def _persist_recovered_lineage(self, summary: InputDataSummary) -> None:
        record = self.dataset_repository.get(summary.dataset_id)
        if record is None:
            return
        self.dataset_repository.save(record.model_copy(update={"summary": summary}))

    def _missing_dataset_ids(
        self,
        selected_dataset_ids: list[str],
        data_summaries: list[InputDataSummary],
    ) -> list[str]:
        loaded_ids = {summary.dataset_id for summary in data_summaries}
        return [dataset_id for dataset_id in selected_dataset_ids if dataset_id not in loaded_ids]

    def _effective_dataset_ids(
        self,
        payload: ChatMessageRequest,
        session: AgentSession,
    ) -> list[str]:
        selected_ids = self._dedupe_dataset_ids(payload.selected_dataset_ids)
        available_ids = self._available_dataset_ids(payload)
        if not selected_ids and not available_ids:
            return []

        mentioned_ids = self._explicit_dataset_ids(payload.message, available_ids or selected_ids)
        if mentioned_ids:
            return mentioned_ids

        if self._is_result_layer_inspection_request(payload.message.lower()):
            layer_dataset_ids = self._layer_inspection_dataset_ids(payload, session)
            if layer_dataset_ids:
                return layer_dataset_ids

        return selected_ids

    def _layer_inspection_dataset_ids(
        self,
        payload: ChatMessageRequest,
        session: AgentSession,
    ) -> list[str]:
        layers = payload.metadata.get("layers")
        if not isinstance(layers, list):
            return []

        message = payload.message.lower()
        selected_positions = {
            dataset_id: index for index, dataset_id in enumerate(payload.selected_dataset_ids)
        }
        candidates: list[tuple[float, str]] = []
        for layer in layers:
            if not isinstance(layer, dict):
                continue
            dataset_id = str(layer.get("datasetId") or "").strip()
            if not dataset_id:
                continue
            name = str(layer.get("name") or "").strip()
            layer_id = str(layer.get("layerId") or layer.get("id") or "").strip()
            if not self._is_layer_inspection_candidate(message, name, layer_id, dataset_id):
                continue
            candidates.append(
                (
                    self._layer_inspection_score(
                        message=message,
                        layer=layer,
                        dataset_id=dataset_id,
                        name=name,
                        layer_id=layer_id,
                        selected_positions=selected_positions,
                        session=session,
                    ),
                    dataset_id,
                )
            )

        if not candidates:
            return []

        candidates.sort(key=lambda item: item[0], reverse=True)
        dataset_ids = self._dedupe_dataset_ids([dataset_id for _, dataset_id in candidates])
        if self._asks_for_same_name_layers(message):
            return dataset_ids
        return dataset_ids[:1]

    def _is_layer_inspection_candidate(
        self,
        message: str,
        name: str,
        layer_id: str,
        dataset_id: str,
    ) -> bool:
        if dataset_id.lower() in message or (layer_id and layer_id.lower() in message):
            return True
        if name and name.lower() in message:
            return True
        if ("结果图层" in message or "刚才生成" in message) and (
            dataset_id.startswith("dataset_") or "空间筛选" in name
        ):
            return True
        return False

    def _layer_inspection_score(
        self,
        *,
        message: str,
        layer: dict[str, Any],
        dataset_id: str,
        name: str,
        layer_id: str,
        selected_positions: dict[str, int],
        session: AgentSession,
    ) -> float:
        summary = self._summary_for_ranking(dataset_id)
        lineage = None if summary is None else summary.lineage
        lineage = lineage or self._lineage_from_tool_calls(dataset_id, session.tool_calls)
        field_names = {
            field.name.lower()
            for field in (summary.fields if summary is not None else [])
        }

        score = 0.0
        if dataset_id.lower() in message:
            score += 10000
        if layer_id and layer_id.lower() in message:
            score += 9000
        if name and name.lower() in message:
            score += 500
        if bool(layer.get("visible")):
            score += 100
        if layer_id and layer_id not in {f"layer_{dataset_id}", f"layer_{dataset_id.lower()}"}:
            score += 300
        if layer_id.startswith("layer_dataset_"):
            score -= 200
        if dataset_id in selected_positions:
            score += 50
            score -= selected_positions[dataset_id] * 0.001
        if summary is not None and summary.source_type == "generated":
            score += 70
        if "空间筛选" in name:
            score += 70
        if isinstance(lineage, dict) and lineage.get("operation") == "spatial_filter":
            score += 200
        if {"name", "iata_code", "type"}.issubset(field_names):
            score += 150
        if "刚才生成" in message:
            created_at = self._record_created_at_timestamp(dataset_id)
            score += created_at / 10_000_000_000_000
        return score

    def _summary_for_ranking(self, dataset_id: str) -> InputDataSummary | None:
        if self.dataset_service is not None:
            try:
                return self.dataset_service.get_dataset(dataset_id)
            except LookupError:
                pass
        record = self.dataset_repository.get(dataset_id)
        return record.summary if record is not None else None

    def _record_created_at_timestamp(self, dataset_id: str) -> float:
        record = self.dataset_repository.get(dataset_id)
        if record is None:
            return 0
        return record.created_at.timestamp()

    def _asks_for_same_name_layers(self, message: str) -> bool:
        return self._has_any(message, ["同名图层", "重名图层"]) and self._has_any(
            message,
            ["哪些", "所有", "有哪些", "列出", "列表"],
        )

    def _available_dataset_ids(self, payload: ChatMessageRequest) -> list[str]:
        ids = list(payload.selected_dataset_ids)
        active_ids = payload.metadata.get("activeDatasetIds")
        if isinstance(active_ids, list):
            ids.extend(str(dataset_id) for dataset_id in active_ids)
        layers = payload.metadata.get("layers")
        if isinstance(layers, list):
            for layer in layers:
                if isinstance(layer, dict):
                    dataset_id = layer.get("datasetId")
                    if dataset_id:
                        ids.append(str(dataset_id))
        return self._dedupe_dataset_ids(ids)

    def _explicit_dataset_ids(self, message: str, available_ids: list[str]) -> list[str]:
        normalized_message = message.lower()
        positions: list[tuple[int, str]] = []
        for dataset_id in available_ids:
            normalized_id = dataset_id.lower()
            index = normalized_message.find(normalized_id)
            if index < 0:
                continue
            if self._is_positive_dataset_mention(dataset_id, normalized_message):
                positions.append((index, dataset_id))
        positions.sort(key=lambda item: item[0])
        return [dataset_id for _, dataset_id in positions]

    def _is_positive_dataset_mention(self, dataset_id: str, normalized_message: str) -> bool:
        normalized_id = dataset_id.lower()
        start = 0
        while True:
            index = normalized_message.find(normalized_id, start)
            if index < 0:
                return False
            prefix = normalized_message[:index]
            clause_start = max(
                prefix.rfind(separator)
                for separator in ["，", "。", "；", ";", "\n"]
            )
            clause_prefix = prefix[clause_start + 1 :]
            if not self._has_any(clause_prefix, ["不要", "不得", "禁止", "任何", "别"]):
                return True
            start = index + len(normalized_id)

    def _dedupe_dataset_ids(self, dataset_ids: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for dataset_id in dataset_ids:
            value = str(dataset_id).strip()
            if value and value not in seen:
                deduped.append(value)
                seen.add(value)
        return deduped

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
        if task_type == "result_layer_inspection":
            return calls

        if task_type == "data_readiness":
            if "metadata_search" in self.tool_registry.list_names():
                calls.append(("metadata_search", base_input))
            return calls

        if self._is_metadata_query(message) and not analysis_execution:
            if "metadata_search" in self.tool_registry.list_names():
                calls.append(("metadata_search", base_input))
        if self._has_any(
            message,
            ["统计", "数量", "分类", "占比", "平均", "总和", "求和", "汇总", "summary", "count"],
        ):
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
        elif self._is_spatial_filter_request(message):
            if effective_dataset_ids:
                calls.append(("spatial_filter", self._spatial_filter_input(base_input)))
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

    def _task_type(self, message: str) -> str:
        if self._is_analysis_execution_request(message):
            return "spatial_analysis"
        if self._is_result_layer_inspection_request(message):
            return "result_layer_inspection"
        if re.search(
            r"是否适合|能否|判断.*适合|只判断|前提条件|数据准备|不要执行|不要调用",
            message,
        ):
            return "data_readiness"
        if self._has_any(message, ["统计", "数量", "分类", "汇总", "summary", "count"]):
            return "attribute_summary"
        if self._has_any(
            message,
            [
                "缓冲",
                "buffer",
                "附近",
                "裁剪",
                "clip",
                "筛选",
                "过滤",
                "filter",
                "范围内",
                "省内",
                "within",
                "intersect",
            ],
        ):
            return "spatial_analysis"
        if self._has_any(message, ["显示", "可视化", "地图", "图层"]):
            return "visualization"
        return "report"

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

    def _is_plan_only_request(self, message: str) -> bool:
        normalized = message.lower()
        wants_plan = self._has_any(
            normalized,
            ["计划", "plan", "步骤", "分步骤", "执行前先", "只生成计划"],
        )
        defers_execution = self._has_any(
            normalized,
            [
                "不要立即执行",
                "不要执行工具",
                "不执行工具",
                "先不要执行",
                "暂不执行",
                "do not execute",
                "don't execute",
                "plan first",
            ],
        )
        explicitly_forbids_tools = bool(
            re.search(
                r"(只生成|仅生成)?\s*(计划|plan)?.{0,12}"
                r"(不要|不|无需|禁止|别)\s*(执行|调用|使用).{0,8}(任何)?\s*(工具|tool)",
                normalized,
            )
        )
        if explicitly_forbids_tools:
            return True
        return wants_plan and defers_execution

    def _plan_created_payload(
        self,
        session: AgentSession,
        payload: ChatMessageRequest,
    ) -> dict[str, Any]:
        input_ids = session.selected_dataset_ids
        message = payload.message.lower()
        data_prep_description = (
            "确认本轮只使用用户明确限定的数据集，"
            "并读取其几何类型、范围、字段和可用性摘要。"
        )
        if input_ids:
            data_prep_description = (
                "确认本轮只使用 "
                + "、".join(input_ids)
                + "，并读取其几何类型、范围、字段和可用性摘要。"
            )

        if self._has_any(message, ["缓冲", "buffer"]):
            distance = self._infer_distance(payload.message)
            plan_distance = (
                self._plan_distance_value(distance[0])
                if distance is not None
                else None
            )
            plan_unit = (
                self._plan_distance_unit(distance[1])
                if distance is not None
                else None
            )
            distance_text = (
                f"{distance[0]:g} {distance[1]}"
                if distance is not None
                else "用户指定距离"
            )
            return {
                "type": "plan.created",
                "planType": "buffer_analysis",
                "targetDatasetId": input_ids[0] if input_ids else None,
                "distance": plan_distance,
                "unit": plan_unit,
                "execute": False,
                "steps": [
                    {
                        "id": "data-prep",
                        "title": "数据准备",
                        "kind": "data_preparation",
                        "description": (
                            data_prep_description
                            + "本计划不重新执行机场筛选，只沿用现有结果图层。"
                        ),
                        "expectedInputs": input_ids,
                    },
                    {
                        "id": "crs-distance-units",
                        "title": "坐标系与距离单位处理",
                        "kind": "crs_distance_units",
                        "description": (
                            "源数据若为 EPSG:4326，经纬度单位不能直接用于米级缓冲。"
                            "可临时转换到 EPSG:32648 这类本地米制投影，适合局部距离计算；"
                            "若要求更高精度，可使用以点为中心的方位等距投影或 geodesic buffer。"
                        ),
                    },
                    {
                        "id": "buffer-calculation",
                        "title": "缓冲区计算",
                        "kind": "deterministic_gis",
                        "description": (
                            f"计划阶段仅说明后续可按 {distance_text} 距离对输入点要素生成缓冲区，"
                            "本轮不调用 geoprocess，不生成缓冲结果。"
                        ),
                        "toolCandidates": ["buffer"],
                        "parameters": {
                            "distance": plan_distance,
                            "unit": plan_unit,
                        },
                    },
                    {
                        "id": "result-output",
                        "title": "结果输出",
                        "kind": "visualization_or_report",
                        "description": (
                            "本轮只输出分析计划文本和结构化步骤；不生成新图层，"
                            "不发送地图命令，也不产生结果数据集。"
                        ),
                        "expectedOutputs": ["分析计划", "结构化步骤"],
                    },
                ],
                "constraints": [
                    "只生成计划，不执行任何工具",
                    "不重新执行机场筛选",
                    "不生成新图层",
                ],
            }

        return {
            "type": "plan.created",
            "steps": [
                {
                    "id": "data-prep",
                    "title": "数据准备",
                    "kind": "data_preparation",
                    "description": data_prep_description,
                    "expectedInputs": input_ids,
                },
                {
                    "id": "spatial-calc",
                    "title": "空间计算",
                    "kind": "deterministic_gis",
                    "description": (
                        "使用确定性的空间关系计算找出目标范围内的要素，"
                        "不在计划阶段执行工具。"
                    ),
                    "toolCandidates": ["within", "intersects", "spatial_join"],
                },
                {
                    "id": "result-output",
                    "title": "结果输出",
                    "kind": "visualization_or_report",
                    "description": (
                        "输出筛选后的 GeoJSON、结果摘要和可核验的样本记录，"
                        "供前端展示或继续执行。"
                    ),
                    "expectedOutputs": [
                        "GeoJSON FeatureCollection",
                        "结果摘要",
                        "样本记录",
                    ],
                },
            ],
        }

    def _plan_message(self, plan_payload: dict[str, Any]) -> str:
        step_titles = [
            str(step.get("title") or step.get("id") or "")
            for step in plan_payload.get("steps", [])
            if isinstance(step, dict)
        ]
        if not step_titles:
            return "已生成执行计划，尚未调用工具。"
        constraints = [
            str(item)
            for item in plan_payload.get("constraints", [])
            if isinstance(item, str)
        ]
        suffix = ""
        if constraints:
            suffix = "；" + "，".join(constraints)
        expected_inputs = []
        for step in plan_payload.get("steps", []):
            if isinstance(step, dict) and step.get("id") == "data-prep":
                expected_inputs = [
                    str(dataset_id)
                    for dataset_id in step.get("expectedInputs", [])
                ]
                break
        input_text = ""
        if expected_inputs:
            input_text = "只使用 " + "、".join(expected_inputs) + "；"
        return (
            "已生成执行计划，尚未调用工具："
            + input_text
            + "、".join(step_titles)
            + suffix
            + "。"
        )

    def _plan_distance_value(self, distance: float) -> int | float:
        if distance.is_integer():
            return int(distance)
        return distance

    def _plan_distance_unit(self, unit: str) -> str:
        normalized = unit.lower()
        if normalized in {"公里", "千米", "kilometer", "kilometers", "km"}:
            return "km"
        if normalized in {"米", "meter", "meters", "m"}:
            return "m"
        return normalized

    def _tool_failure_notice(self, tool_results: list[dict[str, Any]]) -> str:
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
            "本轮曾发生工具调用失败（"
            + "；".join(parts)
            + "）。以下回答仅基于已成功返回的工具结果和 data.summary。\n"
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

    def _attribute_summary_input(self, base_input: dict[str, Any]) -> dict[str, Any]:
        payload = dict(base_input)
        group_by = self._infer_group_by_field(
            str(base_input.get("message") or ""),
            base_input.get("dataSummaries") or [],
        )
        sort_by = self._infer_sort_by_field(
            str(base_input.get("message") or ""),
            base_input.get("dataSummaries") or [],
        )
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

    def _is_bbox_clip_request(self, message: str) -> bool:
        if not self._has_any(message, ["裁剪", "clip", "截取"]):
            return False
        return self._has_any(
            message,
            ["范围", "bbox", "当前视图", "视图", "地图", "图层", "数据", "要素"],
        )

    def _is_spatial_filter_request(self, message: str) -> bool:
        if not self._has_any(
            message,
            [
                "范围内",
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
            ["找出", "返回", "筛选", "过滤", "所有", "哪些", "机场"],
        )

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

    def _is_result_layer_inspection_request(self, message: str) -> bool:
        result_layer_terms = [
            "刚才生成",
            "刚生成",
            "生成的",
            "结果图层",
            "图层信息",
            "查看结果",
            "查看图层",
            "layer info",
            "layer metadata",
            "result layer",
        ]
        inspection_terms = [
            "图层 id",
            "图层id",
            "数据集 id",
            "数据集id",
            "dataset id",
            "几何类型",
            "geometry",
            "bbox",
            "来源",
            "输入图层",
            "空间关系",
            "要素数量",
            "featurecount",
            "feature count",
            "是否可以继续",
            "能否继续",
            "后续分析",
            "继续用于",
        ]
        return self._has_any(message, result_layer_terms) and self._has_any(
            message,
            inspection_terms,
        )

    def _is_analysis_execution_request(self, message: str) -> bool:
        strong_execution_terms = [
            "执行",
            "运行",
            "调用",
            "run",
            "execute",
        ]
        create_terms = [
            "生成",
            "创建",
            "create",
        ]
        deterministic_operation_terms = [
            "缓冲",
            "buffer",
            "裁剪",
            "clip",
            "空间筛选",
            "空间过滤",
        ]
        analysis_terms = [
            *deterministic_operation_terms,
            "分析计划",
            "结果图层",
            "resultdatasetid",
        ]
        if self._has_any(message, strong_execution_terms):
            return self._has_any(message, analysis_terms)
        if self._is_result_layer_inspection_request(message):
            return False
        return self._has_any(message, create_terms) and self._has_any(
            message, deterministic_operation_terms
        )

    def _duration_ms(self, started_at: datetime, finished_at: datetime) -> int:
        return int((finished_at - started_at).total_seconds() * 1000)

    def _encode_event(self, event: StreamEvent) -> str:
        data = json.dumps(event.model_dump(mode="json", by_alias=True), ensure_ascii=False)
        return f"event: {event.type}\ndata: {data}\n\n"
