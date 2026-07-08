from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from geo_agent_service.modules.ai_chat.schemas import ChatMessageRequest
from geo_agent_service.modules.gis_data.repository import DatasetRepository
from geo_agent_service.modules.gis_data.schemas import InputDataSummary
from geo_agent_service.modules.gis_data.service import GisDatasetService
from geo_agent_service.schemas.agent import ToolCallRecord
from geo_agent_service.schemas.session import AgentSession


class AiChatSessionDataMixin:
    if TYPE_CHECKING:
        dataset_repository: DatasetRepository
        dataset_service: GisDatasetService | None

        def _is_points_in_existing_buffer_plan_request(self, message: str) -> bool: ...
        def _population_point_dataset_ids(
            self,
            payload: ChatMessageRequest,
            available_ids: list[str],
        ) -> list[str]: ...
        def _is_map_display_request(self, message: str) -> bool: ...
        def _is_result_layer_inspection_request(self, message: str) -> bool: ...
        def _has_any(self, text: str, needles: list[str]) -> bool: ...

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
        if self._is_points_in_existing_buffer_plan_request(payload.message.lower()):
            point_ids = self._population_point_dataset_ids(payload, available_ids or selected_ids)
            if point_ids or mentioned_ids:
                return self._dedupe_dataset_ids([*point_ids, *mentioned_ids])
        if mentioned_ids:
            return mentioned_ids

        if self._is_map_display_request(payload.message.lower()):
            layer_dataset_ids = self._layer_inspection_dataset_ids(payload, session)
            if layer_dataset_ids:
                return layer_dataset_ids

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
        if isinstance(lineage, dict) and lineage.get("operation") == "buffer":
            score += 500
        if "缓冲" in message or "buffer" in message:
            if name and ("缓冲" in name or "buffer" in name.lower()):
                score += 300
            geometry_type = str(layer.get("geometryType") or "").lower()
            if geometry_type in {"polygon", "multipolygon"}:
                score += 250
            if isinstance(lineage, dict) and lineage.get("operation") == "spatial_filter":
                score -= 250
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
        return float(record.created_at.timestamp())

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

    def _dedupe_dataset_ids(self, dataset_ids: Sequence[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for dataset_id in dataset_ids:
            value = str(dataset_id).strip()
            if value and value not in seen:
                deduped.append(value)
                seen.add(value)
        return deduped
