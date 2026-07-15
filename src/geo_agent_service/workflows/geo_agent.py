from collections.abc import Awaitable, Callable
from typing import Any, cast

from langgraph.graph import END, StateGraph
from pydantic import BaseModel, ConfigDict, Field

from geo_agent_service.modules.ai_chat.schemas import ChatMessageRequest, StreamEvent
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
from geo_agent_service.schemas.session import AgentMessage, AgentSession

GeoAgentNode = Callable[["GeoAgentState"], Awaitable["GeoAgentState"]]


class IntentResult(BaseModel):
    task_type: str
    requires_plan_only: bool = False
    requires_map_display: bool = False
    requires_tool_execution: bool = True
    requires_confirmation: bool = False


class DataReadinessResult(BaseModel):
    status: str
    available_dataset_ids: list[str] = Field(default_factory=list)
    effective_dataset_ids: list[str] = Field(default_factory=list)
    missing_dataset_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ToolPlan(BaseModel):
    execute: bool = True
    reason: str | None = None
    plan_payload: dict[str, Any] | None = None


class GeoAgentState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    session_id: str
    run_id: str = ""
    user_id: str = ""
    user_query: str
    payload: ChatMessageRequest | None = None
    session: AgentSession | None = None
    user_message: AgentMessage | None = None
    assistant_message: AgentMessage | None = None
    messages: list[AgentMessage] = Field(default_factory=list)
    selected_dataset_ids: list[str] = Field(default_factory=list)
    selected_service_ids: list[str] = Field(default_factory=list)
    available_dataset_ids: list[str] = Field(default_factory=list)
    effective_dataset_ids: list[str] = Field(default_factory=list)
    data_summaries: list[InputDataSummary] = Field(default_factory=list)
    intent: IntentResult | None = None
    data_readiness: DataReadinessResult | None = None
    tool_plan: ToolPlan | None = None
    layer_context: list[dict[str, Any]] = Field(default_factory=list)
    map_context: dict[str, Any] = Field(default_factory=dict)
    style_plan: dict[str, Any] | None = None
    plan: list[PlanStep] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    tool_results: list[ToolCallRecord] = Field(default_factory=list)
    layers: list[MapLayerResult] = Field(default_factory=list)
    charts: list[ChartResult] = Field(default_factory=list)
    scene_actions: list[ThreeSceneAction] = Field(default_factory=list)
    map_commands: list[dict[str, Any]] = Field(default_factory=list)
    stream_events: list[StreamEvent] = Field(default_factory=list)
    tool_result_payloads: list[dict[str, Any]] = Field(default_factory=list)
    assistant_chunks: list[str] = Field(default_factory=list)
    report: AnalysisReport | None = None
    errors: list[AgentError] = Field(default_factory=list)
    is_done: bool = False


def create_geo_agent_graph(
    *,
    prepare_context: GeoAgentNode | None = None,
    intent_parse: GeoAgentNode | None = None,
    data_readiness: GeoAgentNode | None = None,
    planning: GeoAgentNode | None = None,
    human_confirmation: GeoAgentNode | None = None,
    tool_execution: GeoAgentNode | None = None,
    visualization_build: GeoAgentNode | None = None,
    report_generation: GeoAgentNode | None = None,
    error_handler: GeoAgentNode | None = None,
) -> Any:
    """Create the Geo Agent graph used by the chat orchestration layer.

    The graph keeps orchestration explicit while allowing the HTTP layer to
    keep owning authentication, persistence, and the public SSE contract.
    """
    graph = StateGraph(GeoAgentState)

    async def noop(state: GeoAgentState) -> GeoAgentState:
        return state

    graph.add_node("prepare_context", cast(Any, prepare_context or noop))
    graph.add_node("intent_parse", cast(Any, intent_parse or noop))
    graph.add_node("data_readiness", cast(Any, data_readiness or noop))
    graph.add_node("planning", cast(Any, planning or noop))
    graph.add_node("human_confirmation", cast(Any, human_confirmation or noop))
    graph.add_node("tool_execution", cast(Any, tool_execution or noop))
    graph.add_node("visualization_build", cast(Any, visualization_build or noop))
    graph.add_node("report_generation", cast(Any, report_generation or noop))
    graph.add_node("error_handler", cast(Any, error_handler or noop))
    graph.set_entry_point("prepare_context")
    graph.add_edge("prepare_context", "intent_parse")
    graph.add_edge("intent_parse", "data_readiness")
    graph.add_edge("data_readiness", "planning")
    graph.add_edge("planning", "human_confirmation")
    graph.add_edge("human_confirmation", "tool_execution")
    graph.add_edge("tool_execution", "visualization_build")
    graph.add_edge("visualization_build", "report_generation")
    graph.add_edge("report_generation", "error_handler")
    graph.add_edge("error_handler", END)
    return graph.compile()
