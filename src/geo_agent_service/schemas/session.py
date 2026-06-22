from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from geo_agent_service.modules.gis_data.schemas import InputDataSummary
from geo_agent_service.schemas.agent import (
    AnalysisReport,
    ChartResult,
    MapLayerResult,
    PlanStep,
    ThreeSceneAction,
    ToolCallRecord,
)


class AgentMessage(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    role: Literal["user", "assistant", "system"]
    content: str
    created_at: str = Field(alias="createdAt")
    status: Literal["streaming", "completed", "failed"] | None = None


class AgentSession(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    title: str
    status: Literal[
        "idle",
        "running",
        "waiting_confirmation",
        "waiting_clarification",
        "completed",
        "failed",
    ]
    messages: list[AgentMessage] = Field(default_factory=list)
    selected_dataset_ids: list[str] = Field(default_factory=list, alias="selectedDatasetIds")
    selected_service_ids: list[str] = Field(default_factory=list, alias="selectedServiceIds")
    data_summaries: list[InputDataSummary] = Field(default_factory=list, alias="dataSummaries")
    plan: list[PlanStep] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list, alias="toolCalls")
    layers: list[MapLayerResult] = Field(default_factory=list)
    charts: list[ChartResult] = Field(default_factory=list)
    scene_actions: list[ThreeSceneAction] = Field(default_factory=list, alias="sceneActions")
    report: AnalysisReport | None = None
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")
