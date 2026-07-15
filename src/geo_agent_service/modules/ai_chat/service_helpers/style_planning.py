from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from geo_agent_service.modules.ai_chat.schemas import ChatMessageRequest


class AiChatStylePlanningMixin:
    """Build constrained map-style commands without invoking GIS tools."""

    if TYPE_CHECKING:
        def _has_any(self, value: str, keywords: list[str]) -> bool: ...

    _COLOR_NAMES = {
        "红": "#FF0000",
        "red": "#FF0000",
        "蓝": "#0000FF",
        "blue": "#0000FF",
        "绿": "#008000",
        "green": "#008000",
        "黄": "#FFFF00",
        "yellow": "#FFFF00",
        "橙": "#FFA500",
        "orange": "#FFA500",
        "紫": "#800080",
        "purple": "#800080",
        "黑": "#000000",
        "black": "#000000",
        "白": "#FFFFFF",
        "white": "#FFFFFF",
        "灰": "#808080",
        "gray": "#808080",
        "grey": "#808080",
    }
    _STYLE_TERMS = (
        "样式",
        "风格",
        "style",
        "颜色",
        "色",
        "color",
        "colour",
        "点大小",
        "大小",
        "size",
        "半径",
        "radius",
        "描边",
        "outline",
        "线宽",
        "加粗",
        "width",
        "填充",
        "fill",
        "边框",
        "border",
        "透明度",
        "透明",
        "opacity",
        "隐藏",
        "显示",
        "hide",
        "show",
    )
    _BEAUTIFY_TERMS = (
        "好看",
        "美观",
        "美化",
        "换个风格",
        "换一种风格",
        "换个样式",
        "换一种样式",
        "beautify",
    )
    _POLYGON_BEAUTIFY_PRESET = {
        "fillColor": "#E6F2FF",
        "outlineColor": "#0047AB",
        "outlineWidth": 2,
    }
    _STYLE_VERBS = (
        "改",
        "修改",
        "设置",
        "调整",
        "变",
        "加粗",
        "放大",
        "缩小",
        "填充",
        "set",
        "change",
        "make",
        "fill",
    )

    def _is_style_request(self, message: str) -> bool:
        normalized = message.lower()
        direct_request = self._has_any(normalized, ["隐藏", "hide"]) or bool(
            re.search(r"(?<!高亮)显示\s*(?:图层|layer)|show\s+(?:the\s+)?layer", normalized)
        )
        if self._has_any(normalized, list(self._BEAUTIFY_TERMS)):
            return True
        return self._has_any(normalized, list(self._STYLE_TERMS)) and (
            direct_request
            or
            self._has_any(normalized, list(self._STYLE_VERBS))
            or bool(re.search(r"#[0-9a-f]{3,8}\b|rgba?\(", normalized))
        )

    def _style_command_plan(self, payload: ChatMessageRequest) -> dict[str, Any] | None:
        message = payload.message.strip()
        normalized = message.lower()
        if not self._is_style_request(normalized):
            return None

        target_result = self._style_target_layer(payload, normalized)
        if target_result["outcome"] != "command":
            return target_result
        layer = target_result["layer"]
        layer_id = str(layer.get("layerId") or layer.get("id") or "").strip()
        dataset_id = str(layer.get("datasetId") or "").strip()
        geometry = self._style_geometry(layer, normalized)
        if geometry is None:
            return self._style_unsupported("该图层不是本期支持的矢量图层，无法修改样式。")
        editable = layer.get("editable")
        if not isinstance(editable, dict) or editable.get("style") is not True:
            return self._style_unsupported("该图层未声明 editable.style=true，无法修改样式。")

        visibility = self._visibility_value(normalized)
        opacity = self._opacity_value(normalized)
        try:
            patch = self._style_patch(normalized, geometry, opacity)
        except ValueError as exc:
            return self._style_unsupported(str(exc))

        commands: list[dict[str, Any]] = []
        capabilities = self._map_command_capabilities(payload)
        if patch:
            if "layer.updateStyle" not in capabilities:
                return self._style_unsupported("客户端未声明 layer.updateStyle，未修改图层样式。")
            commands.append(
                {
                    "action": "layer.updateStyle",
                    "layerId": layer_id,
                    "style": patch,
                }
            )
        elif opacity is not None:
            if "layer.setOpacity" not in capabilities:
                return self._style_unsupported("客户端未声明 layer.setOpacity，未修改图层透明度。")
            commands.append(
                {
                    "action": "layer.setOpacity",
                    "layerId": layer_id,
                    "datasetId": dataset_id,
                    "opacity": opacity,
                }
            )
        if visibility is not None:
            if "layer.setVisible" not in capabilities:
                return self._style_unsupported(
                    "客户端未声明 layer.setVisible，未修改图层显隐状态。"
                )
            commands.append(
                {
                    "action": "layer.setVisible",
                    "layerId": layer_id,
                    "datasetId": dataset_id,
                    "visible": visibility,
                }
            )
        if not commands:
            return self._style_unsupported(
                "未识别到可执行的样式值，请补充颜色、大小、宽度或透明度。"
            )

        return {
            "outcome": "command",
            "commands": commands,
            "datasetId": dataset_id,
            "reason": "deterministic_layer_style_request",
            "message": (
                f"已为图层 {layer_id} 生成 "
                f"{', '.join(command['action'] for command in commands)} 命令。"
            ),
        }

    def _style_target_layer(
        self,
        payload: ChatMessageRequest,
        message: str,
    ) -> dict[str, Any]:
        layers = payload.metadata.get("layers")
        if not isinstance(layers, list):
            return self._style_clarification(
                "no_layers", "未收到已加载图层上下文，请先选择要修改的图层。", []
            )
        candidates = [
            layer
            for layer in layers
            if isinstance(layer, dict)
            and str(layer.get("layerId") or layer.get("id") or "").strip()
        ]
        explicit = [layer for layer in candidates if self._layer_mentioned(layer, message)]
        if explicit:
            return self._unique_style_target(explicit, "ambiguous_target")

        if self._has_any(message, ["当前图层", "选中图层", "current layer", "selected layer"]):
            active_layer_id = str(payload.metadata.get("activeLayerId") or "").strip()
            active = [
                layer
                for layer in candidates
                if str(layer.get("layerId") or layer.get("id") or "") == active_layer_id
            ]
            if active:
                return self._unique_style_target(active, "ambiguous_active_layer")
            return self._style_clarification(
                "active_layer_missing", "当前没有可用于样式修改的活动图层。", []
            )
        return self._style_clarification(
            "target_not_found", "请提供要修改的图层名称、layerId，或选择当前图层。", []
        )

    def _unique_style_target(
        self,
        candidates: list[dict[str, Any]],
        reason: str,
    ) -> dict[str, Any]:
        unique_ids = {
            str(layer.get("layerId") or layer.get("id") or "").strip() for layer in candidates
        }
        if len(unique_ids) == 1:
            return {"outcome": "command", "layer": candidates[0]}
        return self._style_clarification(
            reason,
            "匹配到多个图层，请提供完整 layerId 以避免修改错误图层。",
            [
                {
                    "layerId": str(layer.get("layerId") or layer.get("id") or ""),
                    "datasetId": str(layer.get("datasetId") or ""),
                    "name": str(layer.get("name") or ""),
                }
                for layer in candidates
            ],
        )

    def _style_clarification(
        self,
        reason: str,
        message: str,
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "outcome": "clarification",
            "clarification": {
                "kind": "layer_style",
                "reason": reason,
                "candidates": candidates,
            },
            "message": message,
        }

    def _style_unsupported(self, message: str) -> dict[str, Any]:
        return {"outcome": "unsupported", "message": message}

    def _layer_mentioned(self, layer: dict[str, Any], message: str) -> bool:
        layer_id = str(layer.get("layerId") or layer.get("id") or "").strip().lower()
        name = self._normalize_layer_text(str(layer.get("name") or ""))
        normalized_message = self._normalize_layer_text(message)
        return bool(
            (layer_id and layer_id in message)
            or (name and len(name) > 1 and name in normalized_message)
        )

    def _normalize_layer_text(self, value: str) -> str:
        return re.sub(r"[\s\W_]+", "", value.lower(), flags=re.UNICODE)

    def _style_geometry(self, layer: dict[str, Any], message: str) -> str | None:
        geometry = str(layer.get("geometryType") or "").lower()
        if geometry in {"point", "multipoint"}:
            return "point"
        if geometry in {"linestring", "multilinestring", "line"}:
            return "line"
        if geometry in {"polygon", "multipolygon"}:
            return "polygon"
        if geometry == "mixed":
            requested_geometries: set[str] = set()
            if self._has_any(message, ["点大小", "点描边", "point"]):
                requested_geometries.add("point")
            if self._has_any(message, ["线宽", "线颜色", "line"]):
                requested_geometries.add("line")
            if self._has_any(message, ["面填充", "面边框", "多边形", "polygon"]):
                requested_geometries.add("polygon")
            return requested_geometries.pop() if len(requested_geometries) == 1 else None
        return None

    def _map_command_capabilities(self, payload: ChatMessageRequest) -> set[str]:
        client_capabilities = payload.metadata.get("clientCapabilities")
        if not isinstance(client_capabilities, dict):
            return set()
        commands = client_capabilities.get("mapCommands")
        return {str(command) for command in commands} if isinstance(commands, list) else set()

    def _style_patch(
        self,
        message: str,
        geometry: str,
        opacity: float | None,
    ) -> dict[str, Any]:
        if geometry == "point" and self._has_any(message, ["线宽", "line width", "线条"]):
            raise ValueError("点图层不支持线宽，请使用点大小或点描边。")
        if geometry == "line" and self._has_any(message, ["点大小", "point size", "点放大"]):
            raise ValueError("线图层不支持点大小，请使用线宽。")
        if geometry == "polygon" and self._has_any(message, ["线宽", "line width"]):
            raise ValueError("面图层不支持线宽，请使用描边宽度。")
        if geometry != "polygon" and self._has_any(message, ["填充", "fill"]):
            raise ValueError("只有面图层支持填充色。")

        color = self._color_value(message)
        color_requested = self._has_any(message, ["颜色", "色", "color", "colour"]) or bool(
            re.search(r"#[0-9a-f]{3,8}\b|rgba?\(", message)
        )
        if color_requested and color is None:
            raise ValueError("无法解析颜色，请使用颜色名称、十六进制或 rgb/rgba 值。")
        if color is not None and opacity is not None:
            color = self._with_alpha(color, opacity)

        patch: dict[str, Any] = {}
        if self._is_beautify_request(message) and not self._has_explicit_style_value(message):
            if geometry == "polygon":
                return {"polygon": dict(self._POLYGON_BEAUTIFY_PRESET)}
            raise ValueError("请补充颜色、大小、宽度或透明度以修改该图层样式。")

        if color is not None:
            if self._has_any(message, ["描边", "边框", "outline", "border"]):
                key = "outlineColor"
            elif geometry == "polygon":
                key = "fillColor"
            else:
                key = "color"
            patch[key] = color

        point_size = self._numeric_value(
            message, ["点大小", "大小", "size", "半径", "radius", "放大"]
        )
        width = self._numeric_value(message, ["线宽", "宽度", "width", "加粗", "描边宽度"])
        if geometry == "point" and point_size is not None:
            patch["pixelSize"] = self._bounded(point_size, 1, 128, "点大小")
        if geometry == "line" and width is not None:
            patch["width"] = self._bounded(width, 0, 32, "线宽")
        if geometry in {"point", "polygon"} and self._has_any(
            message, ["描边宽度", "outline width", "border width"]
        ):
            if width is None:
                raise ValueError("请提供描边宽度数值。")
            patch["outlineWidth"] = self._bounded(width, 0, 32, "描边宽度")

        return {geometry: patch} if patch else {}

    def _is_beautify_request(self, message: str) -> bool:
        return self._has_any(message, list(self._BEAUTIFY_TERMS))

    def _has_explicit_style_value(self, message: str) -> bool:
        return (
            self._color_value(message) is not None
            or bool(re.search(r"#[0-9a-f]{3,8}\b|rgba?\(", message))
            or self._numeric_value(
                message,
                [
                    "点大小",
                    "大小",
                    "size",
                    "半径",
                    "radius",
                    "放大",
                    "线宽",
                    "宽度",
                    "width",
                    "加粗",
                    "描边宽度",
                ],
            )
            is not None
            or self._opacity_value(message) is not None
        )

    def _color_value(self, message: str) -> str | None:
        hex_match = re.search(r"(?<![\w])#([0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})(?!\w)", message)
        if hex_match:
            raw = hex_match.group(1)
            if len(raw) == 3:
                raw = "".join(character * 2 for character in raw)
            return f"#{raw.upper()}"
        rgba_match = re.search(
            r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})(?:\s*,\s*(0(?:\.\d+)?|1(?:\.0+)?))?\s*\)",
            message,
        )
        if rgba_match:
            red, green, blue = (int(rgba_match.group(index)) for index in range(1, 4))
            if max(red, green, blue) > 255:
                return None
            alpha = rgba_match.group(4)
            return f"rgba({red}, {green}, {blue}, {alpha or '1'})"
        for name, value in self._COLOR_NAMES.items():
            if name in message:
                return value
        return None

    def _with_alpha(self, color: str, opacity: float) -> str:
        if color.startswith("#"):
            raw = color[1:7]
            return (
                f"rgba({int(raw[0:2], 16)}, {int(raw[2:4], 16)}, "
                f"{int(raw[4:6], 16)}, {opacity:g})"
            )
        return re.sub(r",\s*(0(?:\.\d+)?|1(?:\.0+)?)\)$", f", {opacity:g})", color)

    def _numeric_value(self, message: str, terms: list[str]) -> float | None:
        for term in terms:
            match = re.search(
                rf"{re.escape(term)}(?:\s*(?:为|到|设为|to))?\s*(-?\d+(?:\.\d+)?)",
                message,
            )
            if match:
                return float(match.group(1))
        return None

    def _bounded(self, value: float, lower: float, upper: float, label: str) -> int | float:
        if not lower <= value <= upper:
            raise ValueError(f"{label}必须在 {lower:g}-{upper:g} 之间。")
        return int(value) if value.is_integer() else value

    def _opacity_value(self, message: str) -> float | None:
        if "半透明" in message or "semi-transparent" in message:
            return 0.5
        match = re.search(
            r"(?:透明度|opacity)\s*(?:为|到|设为|to)?\s*(-?\d+(?:\.\d+)?)\s*(%)?",
            message,
        )
        if not match:
            return None
        value = float(match.group(1))
        opacity = value / 100 if match.group(2) else value
        if not 0 <= opacity <= 1:
            raise ValueError("透明度必须在 0-100% 或 0-1 之间。")
        return opacity

    def _visibility_value(self, message: str) -> bool | None:
        if self._has_any(message, ["隐藏", "hide"]):
            return False
        if re.search(r"(?<!高亮)显示\s*(?:图层|layer)|show\s+(?:the\s+)?layer", message):
            return True
        return None
