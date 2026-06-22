from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from geo_agent_service.schemas.session import AgentMessage, AgentSession


class ChatMessageRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    message: str = Field(min_length=1)
    selected_dataset_ids: list[str] = Field(default_factory=list, alias="selectedDatasetIds")
    selected_service_ids: list[str] = Field(default_factory=list, alias="selectedServiceIds")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message must not be empty.")
        return value


class ChatSessionResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    session: AgentSession


class StreamEvent(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type: Literal[
        "data.summary",
        "plan.created",
        "message.delta",
        "tool.started",
        "tool.completed",
        "tool.failed",
        "layer.created",
        "map.command",
        "chart.created",
        "clarification",
        "message.completed",
        "error",
        "done",
    ]
    session_id: str = Field(alias="sessionId")
    message_id: str | None = Field(default=None, alias="messageId")
    tool_call_id: str | None = Field(default=None, alias="toolCallId")
    data: dict[str, Any] = Field(default_factory=dict)


def new_agent_message(
    *,
    message_id: str,
    role: Literal["user", "assistant", "system"],
    content: str,
    status: Literal["streaming", "completed", "failed"] | None = None,
) -> AgentMessage:
    return AgentMessage(
        id=message_id,
        role=role,
        content=content,
        status=status,
        createdAt=datetime.now(UTC).isoformat(),
    )
