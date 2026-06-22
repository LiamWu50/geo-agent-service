from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FieldSummary(BaseModel):
    name: str
    type: Literal["string", "number", "boolean", "date", "unknown"]
    sample_values: list[str] = Field(default_factory=list)
    null_ratio: float | None = None


class InputDataSummary(BaseModel):
    dataset_id: str
    name: str
    source_type: Literal["upload", "sample", "map_service"]
    geometry_type: Literal["Point", "LineString", "Polygon", "Mixed"] | None = None
    crs: str | None = None
    feature_count: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    fields: list[FieldSummary] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PlanStep(BaseModel):
    id: str
    title: str
    type: Literal["intent", "data", "analysis", "visualization", "report"]
    status: Literal["pending", "running", "completed", "failed", "skipped"]


class AgentError(BaseModel):
    code: str
    message: str
    recoverable: bool
    details: Any | None = None


class MapLayerResult(BaseModel):
    id: str
    name: str
    geometry_type: Literal["Point", "LineString", "Polygon", "Raster", "Mixed"]
    data_ref: str
    bbox: tuple[float, float, float, float] | None = None
    style: dict[str, Any] | None = None
    legend: list[dict[str, Any]] = Field(default_factory=list)
    source: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChartResult(BaseModel):
    id: str
    title: str
    chart_type: Literal["table", "bar", "pie", "metric"]
    data: Any
    source_tool_call_id: str | None = None
    source_layer_id: str | None = None


class ThreeSceneAction(BaseModel):
    id: str
    action_type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class AnalysisReport(BaseModel):
    id: str
    title: str
    content: str
    referenced_dataset_ids: list[str] = Field(default_factory=list)
    referenced_service_ids: list[str] = Field(default_factory=list)
    referenced_layer_ids: list[str] = Field(default_factory=list)
    referenced_tool_call_ids: list[str] = Field(default_factory=list)


class ToolCallRecord(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    tool_name: str = Field(alias="toolName")
    status: Literal["running", "completed", "failed"]
    input: Any
    output: Any | None = None
    error: AgentError | None = None
    started_at: str = Field(alias="startedAt")
    finished_at: str | None = Field(default=None, alias="finishedAt")
    duration_ms: int | None = Field(default=None, alias="durationMs")
