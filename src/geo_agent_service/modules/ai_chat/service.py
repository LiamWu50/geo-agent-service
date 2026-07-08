import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from geo_agent_service.modules.ai_chat.model_client import ChatModelClient
from geo_agent_service.modules.ai_chat.repository import AiChatRepository
from geo_agent_service.modules.ai_chat.schemas import (
    ChatMessageRequest,
    StreamEvent,
    new_agent_message,
)
from geo_agent_service.modules.ai_chat.service_helpers import (
    AiChatIntentAndPlanMixin,
    AiChatMessagingMixin,
    AiChatSessionDataMixin,
    AiChatToolExecutionMixin,
)
from geo_agent_service.modules.gis_data.repository import DatasetRepository
from geo_agent_service.modules.gis_data.service import GisDatasetService
from geo_agent_service.schemas.session import AgentSession
from geo_agent_service.tools.registry import GisToolRegistry


class AiChatService(
    AiChatSessionDataMixin,
    AiChatIntentAndPlanMixin,
    AiChatToolExecutionMixin,
    AiChatMessagingMixin,
):
    def __init__(
        self,
        *,
        repository: AiChatRepository,
        dataset_repository: DatasetRepository,
        dataset_service: GisDatasetService | None = None,
        tool_registry: GisToolRegistry,
        model_client: ChatModelClient,
    ) -> None:
        self.repository = repository
        self.dataset_repository = dataset_repository
        self.dataset_service = dataset_service
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
            effective_dataset_ids = self._effective_dataset_ids(payload, session)
            available_dataset_ids = self._available_dataset_ids(payload)
            session.selected_dataset_ids = effective_dataset_ids
            session.selected_service_ids = payload.selected_service_ids
            session.data_summaries = self._with_recovered_lineage(
                self._load_data_summaries(
                    effective_dataset_ids,
                    existing_summaries=session.data_summaries,
                ),
                session,
            )
            session.updated_at = datetime.now(UTC).isoformat()
            self.repository.save(user_id, session)

            yield self._encode_event(
                StreamEvent(
                    type="data.summary",
                    sessionId=session.id,
                    data={
                        "datasets": [
                            self._data_summary_payload(summary, payload)
                            for summary in session.data_summaries
                            if summary.dataset_id in effective_dataset_ids
                        ],
                        "availableDatasetIds": available_dataset_ids,
                        "selectedDatasetIds": payload.selected_dataset_ids,
                        "effectiveDatasetIds": effective_dataset_ids,
                        "missingDatasetIds": self._missing_dataset_ids(
                            effective_dataset_ids,
                            session.data_summaries,
                        ),
                    },
                )
            )

            if self._is_plan_only_request(payload.message):
                plan_payload = self._plan_created_payload(session, payload)
                yield self._encode_event(
                    StreamEvent(
                        type="plan.created",
                        sessionId=session.id,
                        messageId=assistant_message.id,
                        data=plan_payload,
                    )
                )
                assistant_message.content = self._plan_message(plan_payload)
                yield self._encode_event(
                    self._finalize_assistant_message(
                        user_id=user_id,
                        session=session,
                        assistant_message=assistant_message,
                        chunks=[assistant_message.content],
                    )
                )
                return

            chunks: list[str] = []
            map_display_payload = self._map_display_payload(session, payload)
            if map_display_payload:
                commands = map_display_payload["commands"]
                for command in commands:
                    yield self._encode_event(
                        StreamEvent(
                            type="map.command",
                            sessionId=session.id,
                            messageId=assistant_message.id,
                            data=command,
                        )
                    )
                display_message = self._map_display_message(map_display_payload)
                chunks.append(display_message)
                yield self._encode_event(
                    StreamEvent(
                        type="message.delta",
                        sessionId=session.id,
                        messageId=assistant_message.id,
                        data={"delta": display_message},
                    )
                )
                yield self._encode_event(
                    self._finalize_assistant_message(
                        user_id=user_id,
                        session=session,
                        assistant_message=assistant_message,
                        chunks=chunks,
                    )
                )
                return

            tool_results: list[dict[str, object]] = []
            async for event in self._run_tools(session, payload):
                if event.type in {"tool.completed", "tool.failed"}:
                    tool_results.append(event.data)
                yield self._encode_event(event)

            blocked_spatial_filter_message = self._blocked_spatial_filter_message(tool_results)
            if blocked_spatial_filter_message:
                chunks.append(blocked_spatial_filter_message)
                yield self._encode_event(
                    StreamEvent(
                        type="message.delta",
                        sessionId=session.id,
                        messageId=assistant_message.id,
                        data={"delta": blocked_spatial_filter_message},
                    )
                )
                yield self._encode_event(
                    self._finalize_assistant_message(
                        user_id=user_id,
                        session=session,
                        assistant_message=assistant_message,
                        chunks=chunks,
                    )
                )
                return

            failure_message = self._tool_failure_message(tool_results)
            if failure_message:
                chunks.append(failure_message)
                yield self._encode_event(
                    StreamEvent(
                        type="message.delta",
                        sessionId=session.id,
                        messageId=assistant_message.id,
                        data={"delta": failure_message},
                    )
                )
                yield self._encode_event(
                    self._finalize_assistant_message(
                        user_id=user_id,
                        session=session,
                        assistant_message=assistant_message,
                        chunks=chunks,
                    )
                )
                return

            deterministic_message = self._deterministic_attribute_summary_message(
                tool_results
            ) or self._deterministic_geoprocess_buffer_message(tool_results)
            if deterministic_message:
                chunks.append(deterministic_message)
                yield self._encode_event(
                    StreamEvent(
                        type="message.delta",
                        sessionId=session.id,
                        messageId=assistant_message.id,
                        data={"delta": deterministic_message},
                    )
                )
                yield self._encode_event(
                    self._finalize_assistant_message(
                        user_id=user_id,
                        session=session,
                        assistant_message=assistant_message,
                        chunks=chunks,
                    )
                )
                return

            async for chunk in self.model_client.stream_response(
                messages=self._model_messages(session.messages, session.data_summaries, payload),
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

            yield self._encode_event(
                self._finalize_assistant_message(
                    user_id=user_id,
                    session=session,
                    assistant_message=assistant_message,
                    chunks=chunks,
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
