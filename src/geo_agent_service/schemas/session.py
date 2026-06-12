from typing import Literal

from pydantic import BaseModel, Field

from geo_agent_service.schemas.agent import (
    AnalysisReport,
    ChartResult,
    InputDataSummary,
    MapLayerResult,
    PlanStep,
    ThreeSceneAction,
    ToolCallRecord,
)


class AgentMessage(BaseModel):
    id: str
    role: Literal["user", "assistant", "system"]
    content: str
    created_at: str
    status: Literal["streaming", "completed", "failed"] | None = None


class AgentSession(BaseModel):
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
    selected_dataset_ids: list[str] = Field(default_factory=list)
    selected_service_ids: list[str] = Field(default_factory=list)
    data_summaries: list[InputDataSummary] = Field(default_factory=list)
    plan: list[PlanStep] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    layers: list[MapLayerResult] = Field(default_factory=list)
    charts: list[ChartResult] = Field(default_factory=list)
    scene_actions: list[ThreeSceneAction] = Field(default_factory=list)
    report: AnalysisReport | None = None
    created_at: str
    updated_at: str
