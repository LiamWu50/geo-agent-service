from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from geo_agent_service.modules.ai_chat.schemas import ChatMessageRequest
from geo_agent_service.modules.gis_data.schemas import InputDataSummary
from geo_agent_service.schemas.session import AgentSession


class AiChatIntentAndPlanMixin:
    if TYPE_CHECKING:
        def _has_any(self, text: str, needles: list[str]) -> bool: ...
        def _is_attribute_summary_request(self, message: str) -> bool: ...
        def _is_spatial_filter_request(self, message: str) -> bool: ...
        def _infer_distance(self, message: str) -> tuple[float, str] | None: ...
        def _dedupe_dataset_ids(self, dataset_ids: Sequence[str]) -> list[str]: ...
        def _summary_for_ranking(self, dataset_id: str) -> InputDataSummary | None: ...
        def _layer_inspection_dataset_ids(
            self,
            payload: ChatMessageRequest,
            session: AgentSession,
        ) -> list[str]: ...
        def _area_from_summary(
            self,
            summary: InputDataSummary,
            lineage: dict[str, Any],
        ) -> dict[str, Any] | None: ...

    def _task_type(self, message: str) -> str:
        if self._is_map_display_request(message):
            return "map_display"
        if (
            self._is_result_layer_inspection_request(message)
            and not self._has_positive_execution_request(message)
        ):
            return "result_layer_inspection"
        if self._is_attribute_summary_request(message):
            return "attribute_summary"
        if self._is_analysis_execution_request(message):
            return "spatial_analysis"
        if re.search(
            r"是否适合|能否|判断.*适合|只判断|前提条件|数据准备|不要执行|不要调用",
            message,
        ):
            return "data_readiness"
        if self._is_spatial_filter_request(message):
            return "spatial_analysis"
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
                "缓冲区内",
                "缓冲范围内",
                "省内",
                "within",
                "intersect",
            ],
        ):
            return "spatial_analysis"
        if self._has_any(message, ["显示", "可视化", "地图", "图层"]):
            return "visualization"
        return "report"

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

        if self._is_points_in_existing_buffer_plan_request(message):
            point_dataset_id = self._first_dataset_id_by_geometry(input_ids, "point")
            mask_dataset_id = self._first_dataset_id_by_geometry(input_ids, "polygon")
            output_fields = [
                "NAME",
                "NAME_ZH",
                "POP_MAX",
                "POP2020",
                "LATITUDE",
                "LONGITUDE",
            ]
            return {
                "type": "plan.created",
                "planType": "points_in_polygon_plan",
                "inputPointDatasetId": point_dataset_id,
                "maskDatasetId": mask_dataset_id,
                "predicate": "within",
                "alternativePredicates": ["intersects"],
                "outputFields": output_fields,
                "execute": False,
                "steps": [
                    {
                        "id": "data-prep",
                        "title": "数据准备",
                        "kind": "data_preparation",
                        "description": (
                            "确认输入点图层 sample_populated_places，"
                            f"掩膜面图层 {mask_dataset_id or '用户指定缓冲区面图层'}。"
                        ),
                        "expectedInputs": input_ids,
                        "inputPointDatasetId": point_dataset_id,
                        "maskDatasetId": mask_dataset_id,
                    },
                    {
                        "id": "spatial-relation",
                        "title": "空间关系设置",
                        "kind": "deterministic_gis",
                        "description": "使用 within 或 intersects 查询缓冲区内人口点。",
                        "toolCandidates": ["within", "intersects", "spatial_join"],
                        "parameters": {
                            "predicate": "within",
                            "alternativePredicates": ["intersects"],
                        },
                    },
                    {
                        "id": "output-fields",
                        "title": "字段输出",
                        "kind": "schema_projection",
                        "description": (
                            "输出 NAME、NAME_ZH、POP_MAX、POP2020、LATITUDE、LONGITUDE 等字段。"
                        ),
                        "expectedOutputs": output_fields,
                    },
                    {
                        "id": "result-purpose",
                        "title": "结果用途",
                        "kind": "visualization_or_report",
                        "description": "用于识别机场 50km 服务范围内的人口稠密地区。",
                        "expectedOutputs": ["分析计划", "结构化步骤"],
                    },
                ],
                "constraints": [
                    "只生成计划，不执行任何工具",
                    "不生成新图层",
                ],
            }

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
        if plan_payload.get("planType") == "points_in_polygon_plan":
            input_point_dataset_id = str(plan_payload.get("inputPointDatasetId") or "")
            mask_dataset_id = str(plan_payload.get("maskDatasetId") or "")
            predicate = str(plan_payload.get("predicate") or "within")
            output_fields = [
                str(field)
                for field in plan_payload.get("outputFields", [])
                if isinstance(field, str)
            ]
            output_text = ""
            if output_fields:
                output_text = "，输出字段 " + "、".join(output_fields)
            return (
                "已生成执行计划，尚未调用工具："
                f"输入点图层 {input_point_dataset_id}，"
                f"掩膜面图层 {mask_dataset_id}，"
                f"空间关系 {predicate}"
                f"{output_text}；只生成计划，不执行任何工具，不生成新图层。"
            )

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

    def _is_points_in_existing_buffer_plan_request(self, message: str) -> bool:
        if not self._has_any(message, ["缓冲区内", "缓冲范围内", "buffer"]):
            return False
        if not self._has_any(message, ["人口稠密", "人口点", "populated"]):
            return False
        return self._has_any(
            message,
            ["查询", "找出", "筛选", "空间筛选", "叠加", "within", "intersects"],
        )

    def _population_point_dataset_ids(
        self,
        payload: ChatMessageRequest,
        available_ids: list[str],
    ) -> list[str]:
        if "sample_populated_places" in available_ids:
            return ["sample_populated_places"]
        layers = payload.metadata.get("layers")
        if not isinstance(layers, list):
            return []
        matches: list[str] = []
        for layer in layers:
            if not isinstance(layer, dict):
                continue
            dataset_id = str(layer.get("datasetId") or "").strip()
            name = str(layer.get("name") or "").lower()
            geometry_type = str(layer.get("geometryType") or "").lower()
            if dataset_id and geometry_type == "point" and (
                "人口稠密" in name or "populated" in dataset_id.lower()
            ):
                matches.append(dataset_id)
        return self._dedupe_dataset_ids(matches)

    def _first_dataset_id_by_geometry(
        self,
        dataset_ids: list[str],
        geometry_keyword: str,
    ) -> str | None:
        normalized_keyword = geometry_keyword.lower()
        for dataset_id in dataset_ids:
            summary = self._summary_for_ranking(dataset_id)
            geometry_type = str(summary.geometry_type if summary is not None else "").lower()
            if normalized_keyword in geometry_type:
                return dataset_id
        return dataset_ids[0] if dataset_ids else None

    def _map_display_payload(
        self,
        session: AgentSession,
        payload: ChatMessageRequest,
    ) -> dict[str, Any] | None:
        message = payload.message.lower()
        if not self._is_map_display_request(message):
            return None

        target = self._target_layer_for_map_display(payload, session)
        if target is None:
            return None

        center = self._center_for_layer(target)
        if center is None:
            return None

        dataset_id = str(target.get("datasetId") or "")
        layer_id = str(target.get("layerId") or target.get("id") or "")
        label = self._marker_label_for_layer(target)
        commands = [
            {
                "action": "camera.flyTo",
                "center": center,
                "datasetId": dataset_id,
                "layerId": layer_id,
            },
            {
                "action": "overlay.addMarker",
                "coordinates": center,
                "label": label,
                "datasetId": dataset_id,
                "layerId": layer_id,
            },
        ]
        return {
            "datasetId": dataset_id,
            "layerId": layer_id,
            "label": label,
            "center": center,
            "commands": commands,
        }

    def _target_layer_for_map_display(
        self,
        payload: ChatMessageRequest,
        session: AgentSession,
    ) -> dict[str, Any] | None:
        layers = payload.metadata.get("layers")
        if not isinstance(layers, list):
            return None

        target_dataset_ids = self._layer_inspection_dataset_ids(payload, session)
        if target_dataset_ids:
            target_dataset_id = target_dataset_ids[0]
            for layer in layers:
                if (
                    isinstance(layer, dict)
                    and str(layer.get("datasetId") or "") == target_dataset_id
                ):
                    return layer

        message = payload.message.lower()
        for layer in layers:
            if not isinstance(layer, dict):
                continue
            name = str(layer.get("name") or "").lower()
            layer_id = str(layer.get("layerId") or layer.get("id") or "").lower()
            dataset_id = str(layer.get("datasetId") or "").lower()
            if name and name in message:
                return layer
            if layer_id and layer_id in message:
                return layer
            if dataset_id and dataset_id in message:
                return layer
        return None

    def _center_for_layer(self, layer: dict[str, Any]) -> list[float] | None:
        bbox = layer.get("bbox")
        if isinstance(bbox, list | tuple) and len(bbox) == 4:
            try:
                min_lon, min_lat, max_lon, max_lat = [float(value) for value in bbox]
            except (TypeError, ValueError):
                return None
            return [(min_lon + max_lon) / 2, (min_lat + max_lat) / 2]
        center = layer.get("center")
        if isinstance(center, list | tuple) and len(center) >= 2:
            try:
                return [float(center[0]), float(center[1])]
            except (TypeError, ValueError):
                return None
        return None

    def _marker_label_for_layer(self, layer: dict[str, Any]) -> str:
        dataset_id = str(layer.get("datasetId") or "")
        name = str(layer.get("name") or "")
        if dataset_id == "dataset_16fb343ba5e6" or "人口稠密地区 空间筛选" in name:
            return "Chengdu / 成都"
        return name or dataset_id or "目标位置"

    def _map_display_message(self, payload: dict[str, Any]) -> str:
        actions = [
            str(command.get("action") or "")
            for command in payload.get("commands", [])
            if isinstance(command, dict)
        ]
        return (
            "已执行地图展示动作："
            + "、".join(actions)
            + f"；目标图层={payload.get('layerId')}；"
            + f"datasetId={payload.get('datasetId')}；"
            + f"center={payload.get('center')}。"
        )

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

    def _is_map_display_request(self, message: str) -> bool:
        display_terms = [
            "定位",
            "高亮",
            "飞到",
            "缩放到",
            "显示已有",
            "展示已有",
            "地图展示",
            "地图显示",
            "map display",
            "map interaction",
            "fly to",
            "highlight",
        ]
        layer_terms = ["图层", "结果图层", "dataset_", "layer_"]
        if not self._has_any(message, display_terms):
            return False
        if not self._has_any(message, layer_terms):
            return False
        return self._has_any(
            message,
            [
                "只执行地图展示",
                "只做地图展示",
                "不重新执行",
                "不要重新执行",
                "不执行任何数据分析",
                "不要执行任何数据分析",
                "不调用",
                "不要调用",
                "已有结果图层",
                "显示已有结果图层",
                "结果图层",
            ],
        )

    def _is_analysis_execution_request(self, message: str) -> bool:
        if (
            self._is_result_layer_inspection_request(message)
            and not self._has_positive_execution_request(message)
        ):
            return False
        if self._user_forbids_tools(message):
            return False
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
        return self._has_any(message, create_terms) and self._has_any(
            message,
            deterministic_operation_terms,
        )

    def _has_positive_execution_request(self, message: str) -> bool:
        if self._user_forbids_tools(message):
            return False
        describes_existing_result = self._has_any(message, ["刚才生成", "刚生成", "生成的"])
        create_result_request = bool(
            re.search(r"(生成|创建).*(结果图层|缓冲区|buffer|裁剪|筛选)", message)
        )
        return bool(
            re.search(r"(请|帮我|开始|现在)?\s*(执行|运行)", message)
            or (create_result_request and not describes_existing_result)
        )

    def _user_forbids_tools(self, message: str) -> bool:
        forbidden_action = r"(不要|不得|禁止|别|不允许|无需|不需要)"
        operation = (
            r"(工具|tool|spatial_filter|attribute_filter|geoprocess|"
            r"筛选|过滤|空间筛选|空间过滤|缓冲|裁剪|分析)"
        )
        return bool(
            re.search(
                forbidden_action
                + r"\s*(重新)?\s*(执行|调用|使用|生成|创建).{0,16}"
                + operation,
                message,
            )
            or re.search(
                forbidden_action
                + r".{0,16}(执行|调用|使用|生成|创建)\s*(任何)?\s*"
                + operation,
                message,
            )
        )

    def _data_summary_payload(
        self,
        summary: InputDataSummary,
        payload: ChatMessageRequest,
    ) -> dict[str, Any]:
        data = summary.model_dump(mode="json", by_alias=True)
        if not self._is_result_layer_inspection_request(payload.message.lower()):
            return data

        lineage = summary.lineage if isinstance(summary.lineage, dict) else {}
        for key in [
            "sourceDatasetId",
            "inputDatasetId",
            "distance",
            "unit",
            "toolCallId",
            "processingCRS",
        ]:
            if key in lineage and lineage[key] is not None:
                data[key] = lineage[key]

        area = lineage.get("area")
        if area is None and lineage.get("operation") == "buffer":
            area = self._area_from_summary(summary, lineage)
        if area is not None:
            data["area"] = area
        return data
