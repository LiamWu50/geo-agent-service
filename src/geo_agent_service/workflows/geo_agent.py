from pydantic import BaseModel, Field

from geo_agent_service.schemas.agent import (
    AgentError,
    AnalysisReport,
    ChartResult,
    InputDataSummary,
    MapLayerResult,
    PlanStep,
    ThreeSceneAction,
    ToolCallRecord,
)


class GeoAgentState(BaseModel):
    session_id: str
    user_query: str
    selected_dataset_ids: list[str] = Field(default_factory=list)
    selected_service_ids: list[str] = Field(default_factory=list)
    data_summaries: list[InputDataSummary] = Field(default_factory=list)
    plan: list[PlanStep] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    layers: list[MapLayerResult] = Field(default_factory=list)
    charts: list[ChartResult] = Field(default_factory=list)
    scene_actions: list[ThreeSceneAction] = Field(default_factory=list)
    report: AnalysisReport | None = None
    errors: list[AgentError] = Field(default_factory=list)


def create_geo_agent_graph() -> None:
    """Create the LangGraph workflow once concrete nodes are implemented."""
    return None
