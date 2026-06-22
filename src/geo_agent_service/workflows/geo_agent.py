from typing import Any

from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from geo_agent_service.modules.gis_data.schemas import InputDataSummary
from geo_agent_service.schemas.agent import (
    AgentError,
    AnalysisReport,
    ChartResult,
    MapLayerResult,
    PlanStep,
    ThreeSceneAction,
    ToolCallRecord,
)


class GeoAgentState(BaseModel):
    session_id: str
    user_id: str = ""
    user_query: str
    messages: list[Any] = Field(default_factory=list)
    selected_dataset_ids: list[str] = Field(default_factory=list)
    selected_service_ids: list[str] = Field(default_factory=list)
    data_summaries: list[InputDataSummary] = Field(default_factory=list)
    layer_context: list[dict[str, Any]] = Field(default_factory=list)
    map_context: dict[str, Any] = Field(default_factory=dict)
    plan: list[PlanStep] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    tool_results: list[ToolCallRecord] = Field(default_factory=list)
    layers: list[MapLayerResult] = Field(default_factory=list)
    charts: list[ChartResult] = Field(default_factory=list)
    scene_actions: list[ThreeSceneAction] = Field(default_factory=list)
    map_commands: list[dict[str, Any]] = Field(default_factory=list)
    report: AnalysisReport | None = None
    errors: list[AgentError] = Field(default_factory=list)


def create_geo_agent_graph() -> Any:
    """Create the minimal Geo Agent graph used by the chat orchestration layer."""
    graph = StateGraph(GeoAgentState)

    async def load_context(state: GeoAgentState) -> GeoAgentState:
        return state

    async def run_tools(state: GeoAgentState) -> GeoAgentState:
        return state

    async def generate_response(state: GeoAgentState) -> GeoAgentState:
        return state

    graph.add_node("load_context", load_context)
    graph.add_node("run_tools", run_tools)
    graph.add_node("generate_response", generate_response)
    graph.set_entry_point("load_context")
    graph.add_edge("load_context", "run_tools")
    graph.add_edge("run_tools", "generate_response")
    graph.add_edge("generate_response", END)
    return graph.compile()
