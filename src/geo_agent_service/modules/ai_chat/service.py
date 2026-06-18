import json
import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from geo_agent_service.modules.ai_chat.model_client import ChatModelClient
from geo_agent_service.modules.ai_chat.repository import AiChatRepository
from geo_agent_service.modules.ai_chat.schemas import (
    ChatMessageRequest,
    StreamEvent,
    new_agent_message,
)
from geo_agent_service.modules.gis_data.repository import DatasetRepository
from geo_agent_service.schemas.agent import AgentError, ToolCallRecord
from geo_agent_service.schemas.session import AgentMessage, AgentSession
from geo_agent_service.tools.registry import GisToolRegistry


class AiChatService:
    def __init__(
        self,
        *,
        repository: AiChatRepository,
        dataset_repository: DatasetRepository,
        tool_registry: GisToolRegistry,
        model_client: ChatModelClient,
    ) -> None:
        self.repository = repository
        self.dataset_repository = dataset_repository
        self.tool_registry = tool_registry
        self.model_client = model_client

    async def stream_message(
        self,
        *,
        user_id: str,
        session_id: str,
        payload: ChatMessageRequest,
    ) -> AsyncIterator[str]:
        try:
            session = self._get_or_create_session(
                user_id=user_id,
                session_id=session_id,
                payload=payload,
            )
            user_message = new_agent_message(
                message_id=f"msg_{secrets.token_urlsafe(12)}",
                role="user",
                content=payload.message.strip(),
                status="completed",
            )
            assistant_message = new_agent_message(
                message_id=f"msg_{secrets.token_urlsafe(12)}",
                role="assistant",
                content="",
                status="streaming",
            )
            session.messages.extend([user_message, assistant_message])
            session.status = "running"
            session.selected_dataset_ids = payload.selected_dataset_ids
            session.selected_service_ids = payload.selected_service_ids
            session.updated_at = datetime.now(UTC).isoformat()
            self.repository.save(user_id, session)

            tool_results: list[dict[str, Any]] = []
            async for event in self._run_tools(session, payload):
                if event.type in {"tool.completed", "tool.failed"}:
                    tool_results.append(event.data)
                yield self._encode_event(event)

            chunks: list[str] = []
            async for chunk in self.model_client.stream_response(
                messages=self._model_messages(session.messages),
                tool_results=tool_results,
            ):
                chunks.append(chunk)
                yield self._encode_event(
                    StreamEvent(
                        type="message.delta",
                        sessionId=session.id,
                        messageId=assistant_message.id,
                        data={"delta": chunk},
                    )
                )

            assistant_message.content = "".join(chunks)
            assistant_message.status = "completed"
            session.status = "completed"
            session.updated_at = datetime.now(UTC).isoformat()
            self.repository.save(user_id, session)
            yield self._encode_event(
                StreamEvent(
                    type="message.completed",
                    sessionId=session.id,
                    messageId=assistant_message.id,
                    data={
                        "message": assistant_message.model_dump(mode="json", by_alias=True),
                    },
                )
            )
        except Exception as exc:
            yield self._encode_event(
                StreamEvent(
                    type="error",
                    sessionId=session_id,
                    data={"message": str(exc)},
                )
            )

    def get_session(self, *, user_id: str, session_id: str) -> AgentSession | None:
        return self.repository.get(user_id, session_id)

    async def _run_tools(
        self,
        session: AgentSession,
        payload: ChatMessageRequest,
    ) -> AsyncIterator[StreamEvent]:
        for tool_name in self.tool_registry.list_names():
            tool = self.tool_registry.get(tool_name)
            started_at = datetime.now(UTC)
            tool_call = ToolCallRecord(
                id=f"tool_{secrets.token_urlsafe(12)}",
                tool_name=tool_name,
                status="running",
                input={
                    "message": payload.message,
                    "selectedDatasetIds": payload.selected_dataset_ids,
                    "selectedServiceIds": payload.selected_service_ids,
                    "metadata": payload.metadata,
                },
                started_at=started_at.isoformat(),
            )
            session.tool_calls.append(tool_call)
            yield StreamEvent(
                type="tool.started",
                sessionId=session.id,
                toolCallId=tool_call.id,
                data={"toolName": tool_name, "input": tool_call.input},
            )
            try:
                result = await tool.run(tool_call.input)
            except Exception as exc:
                now = datetime.now(UTC)
                error = AgentError(
                    code="TOOL_FAILED",
                    message=str(exc),
                    recoverable=True,
                )
                tool_call.status = "failed"
                tool_call.error = error
                tool_call.finished_at = now.isoformat()
                tool_call.duration_ms = self._duration_ms(started_at, now)
                yield StreamEvent(
                    type="tool.failed",
                    sessionId=session.id,
                    toolCallId=tool_call.id,
                    data={
                        "toolName": tool_name,
                        "error": error.model_dump(mode="json", by_alias=True),
                    },
                )
                continue

            now = datetime.now(UTC)
            tool_call.status = "completed"
            tool_call.output = result.model_dump(mode="json", by_alias=True)
            tool_call.finished_at = now.isoformat()
            tool_call.duration_ms = self._duration_ms(started_at, now)
            yield StreamEvent(
                type="tool.completed",
                sessionId=session.id,
                toolCallId=tool_call.id,
                data={
                    "toolName": tool_name,
                    "output": tool_call.output,
                },
            )

    def _get_or_create_session(
        self,
        *,
        user_id: str,
        session_id: str,
        payload: ChatMessageRequest,
    ) -> AgentSession:
        session = self.repository.get(user_id, session_id)
        if session is not None:
            return session

        now = datetime.now(UTC).isoformat()
        title = payload.message.strip()[:40] or "Untitled chat"
        return AgentSession(
            id=session_id,
            title=title,
            status="idle",
            selectedDatasetIds=payload.selected_dataset_ids,
            selectedServiceIds=payload.selected_service_ids,
            createdAt=now,
            updatedAt=now,
        )

    def _model_messages(self, messages: list[AgentMessage]) -> list[dict[str, str]]:
        return [
            {"role": message.role, "content": message.content}
            for message in messages
            if message.role in {"user", "assistant"} and message.content
        ]

    def _duration_ms(self, started_at: datetime, finished_at: datetime) -> int:
        return int((finished_at - started_at).total_seconds() * 1000)

    def _encode_event(self, event: StreamEvent) -> str:
        data = json.dumps(event.model_dump(mode="json", by_alias=True), ensure_ascii=False)
        return f"event: {event.type}\ndata: {data}\n\n"
