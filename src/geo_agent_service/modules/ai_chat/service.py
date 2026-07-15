import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from geo_agent_service.modules.ai_chat.model_client import ChatModelClient
from geo_agent_service.modules.ai_chat.repository import AiChatRepository
from geo_agent_service.modules.ai_chat.run_repository import AgentRunRepository, RunStatus
from geo_agent_service.modules.ai_chat.schemas import (
    AgentRun,
    ChatMessageRequest,
    StreamEvent,
    new_agent_message,
)
from geo_agent_service.modules.ai_chat.service_helpers import (
    AiChatIntentAndPlanMixin,
    AiChatMessagingMixin,
    AiChatSessionDataMixin,
    AiChatStylePlanningMixin,
    AiChatToolExecutionMixin,
)
from geo_agent_service.modules.gis_data.repository import DatasetRepository
from geo_agent_service.modules.gis_data.service import GisDatasetService
from geo_agent_service.schemas.session import AgentMessage, AgentSession
from geo_agent_service.tools.registry import GisToolRegistry
from geo_agent_service.workflows.geo_agent import (
    DataReadinessResult,
    GeoAgentState,
    IntentResult,
    ToolPlan,
    create_geo_agent_graph,
)


class AiChatService(
    AiChatSessionDataMixin,
    AiChatIntentAndPlanMixin,
    AiChatToolExecutionMixin,
    AiChatMessagingMixin,
    AiChatStylePlanningMixin,
):
    def __init__(
        self,
        *,
        repository: AiChatRepository,
        dataset_repository: DatasetRepository,
        dataset_service: GisDatasetService | None = None,
        tool_registry: GisToolRegistry,
        model_client: ChatModelClient,
        run_repository: AgentRunRepository | None = None,
    ) -> None:
        self.repository = repository
        self.dataset_repository = dataset_repository
        self.dataset_service = dataset_service
        self.tool_registry = tool_registry
        self.model_client = model_client
        self.run_repository = run_repository
        if self.run_repository is not None:
            self.run_repository.ensure_schema()
        self._graph = create_geo_agent_graph(
            prepare_context=self._prepare_context_node,
            intent_parse=self._intent_parse_node,
            data_readiness=self._data_readiness_node,
            planning=self._planning_node,
            human_confirmation=self._human_confirmation_node,
            tool_execution=self._tool_execution_node,
            visualization_build=self._visualization_build_node,
            report_generation=self._report_generation_node,
            error_handler=self._error_handler_node,
        )

    async def stream_message(
        self,
        *,
        user_id: str,
        session_id: str,
        payload: ChatMessageRequest,
    ) -> AsyncIterator[str]:
        run_id = f"run_{secrets.token_urlsafe(12)}"
        persisted_events = 0
        if self.run_repository is not None:
            self.run_repository.create_run(
                run_id=run_id,
                session_id=session_id,
                user_id=user_id,
                query=payload.message.strip(),
                selected_dataset_ids=payload.selected_dataset_ids,
                selected_service_ids=payload.selected_service_ids,
            )
        try:
            state = GeoAgentState(
                session_id=session_id,
                run_id=run_id,
                user_id=user_id,
                user_query=payload.message,
                payload=payload,
            )
            emitted_events = 0
            last_state: GeoAgentState | None = None
            async for update in self._graph.astream(state, stream_mode="values"):
                current_state = GeoAgentState.model_validate(update)
                last_state = current_state
                self._persist_run_state(current_state)
                for event in current_state.stream_events[emitted_events:]:
                    event_with_run = self._with_run_id(event, run_id)
                    persisted_events += 1
                    self._append_run_event(
                        run_id=run_id,
                        session_id=session_id,
                        sequence=persisted_events,
                        event=event_with_run,
                    )
                    yield self._encode_event(event_with_run)
                emitted_events = len(current_state.stream_events)
            if last_state is not None:
                self._finish_run(last_state)
        except Exception as exc:
            error_event = StreamEvent(
                type="error",
                sessionId=session_id,
                runId=run_id,
                data={"message": str(exc)},
            )
            self._append_run_event(
                run_id=run_id,
                session_id=session_id,
                sequence=persisted_events + 1,
                event=error_event,
            )
            self._fail_run(run_id, error_event.data)
            yield self._encode_event(error_event)

    async def _prepare_context_node(self, state: GeoAgentState) -> GeoAgentState:
        payload = self._require_payload(state)
        session = self._get_or_create_session(
            user_id=state.user_id,
            session_id=state.session_id,
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
        self._hydrate_session_plan_payloads_from_runs(
            user_id=state.user_id,
            session=session,
        )
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
        self.repository.save(state.user_id, session)

        state.session = session
        state.user_message = user_message
        state.assistant_message = assistant_message
        state.messages = session.messages
        state.selected_dataset_ids = session.selected_dataset_ids
        state.selected_service_ids = session.selected_service_ids
        state.available_dataset_ids = available_dataset_ids
        state.effective_dataset_ids = effective_dataset_ids
        state.data_summaries = session.data_summaries
        state.stream_events.append(
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
        return state

    async def _intent_parse_node(self, state: GeoAgentState) -> GeoAgentState:
        if state.is_done:
            return state
        payload = self._require_payload(state)
        message = payload.message.lower()
        style_plan = self._style_command_plan(payload)
        map_display_payload = self._map_display_payload(
            self._require_session(state),
            payload,
        )
        state.intent = IntentResult(
            task_type="layer_style" if style_plan is not None else self._task_type(message),
            requires_plan_only=self._is_plan_only_request(payload.message),
            requires_map_display=bool(map_display_payload),
            requires_tool_execution=(
                style_plan is None and not self._user_forbids_tools(message)
            ),
            requires_confirmation=False,
        )
        state.style_plan = style_plan
        return state

    async def _data_readiness_node(self, state: GeoAgentState) -> GeoAgentState:
        if state.is_done:
            return state
        session = self._require_session(state)
        payload = self._require_payload(state)
        missing_dataset_ids = self._missing_dataset_ids(
            state.effective_dataset_ids,
            session.data_summaries,
        )
        warnings = [
            warning
            for summary in session.data_summaries
            for warning in summary.warnings
        ]
        state.data_readiness = DataReadinessResult(
            status="ready" if not missing_dataset_ids else "partial",
            available_dataset_ids=state.available_dataset_ids,
            effective_dataset_ids=state.effective_dataset_ids,
            missing_dataset_ids=missing_dataset_ids,
            warnings=warnings,
        )
        payload.metadata.setdefault(
            "dataReadiness",
            state.data_readiness.model_dump(mode="json"),
        )
        return state

    async def _planning_node(self, state: GeoAgentState) -> GeoAgentState:
        if state.is_done:
            return state
        payload = self._require_payload(state)
        session = self._require_session(state)
        assistant_message = self._require_assistant_message(state)
        intent = state.intent
        if intent is None or not intent.requires_plan_only:
            state.tool_plan = ToolPlan(execute=True)
            return state

        plan_payload = self._plan_created_payload(session, payload)
        session.plan_payloads.append(plan_payload)
        session.updated_at = datetime.now(UTC).isoformat()
        self.repository.save(state.user_id, session)
        state.tool_plan = ToolPlan(
            execute=False,
            reason="plan_only_request",
            plan_payload=plan_payload,
        )
        state.stream_events.append(
            StreamEvent(
                type="plan.created",
                sessionId=session.id,
                messageId=assistant_message.id,
                data=plan_payload,
            )
        )
        assistant_message.content = self._plan_message(plan_payload)
        state.assistant_chunks = [assistant_message.content]
        state.stream_events.append(
            self._finalize_assistant_message(
                user_id=state.user_id,
                session=session,
                assistant_message=assistant_message,
                chunks=state.assistant_chunks,
            )
        )
        state.is_done = True
        return state

    async def _human_confirmation_node(self, state: GeoAgentState) -> GeoAgentState:
        if state.is_done:
            return state
        if state.intent is not None and state.intent.requires_confirmation:
            # The current MVP keeps confirmation detection conservative. This node
            # makes the pause point explicit for the next iteration.
            state.tool_plan = ToolPlan(execute=False, reason="waiting_confirmation")
        return state

    async def _tool_execution_node(self, state: GeoAgentState) -> GeoAgentState:
        if state.is_done:
            return state
        if (
            (state.tool_plan is not None and not state.tool_plan.execute)
            or state.style_plan is not None
        ):
            return state
        async for event in self._run_tools(
            self._require_session(state),
            self._require_payload(state),
        ):
            if event.type in {"tool.completed", "tool.failed"}:
                state.tool_result_payloads.append(event.data)
            state.stream_events.append(event)
        state.tool_calls = self._require_session(state).tool_calls
        return state

    async def _visualization_build_node(self, state: GeoAgentState) -> GeoAgentState:
        if state.is_done:
            return state
        payload = self._require_payload(state)
        session = self._require_session(state)
        assistant_message = self._require_assistant_message(state)
        style_plan = state.style_plan
        if style_plan is not None:
            outcome = str(style_plan["outcome"])
            if outcome == "clarification":
                state.stream_events.append(
                    StreamEvent(
                        type="clarification",
                        sessionId=session.id,
                        messageId=assistant_message.id,
                        data=style_plan["clarification"],
                    )
                )
                return self._finalize_text_response_node(
                    state, str(style_plan["message"])
                )
            if outcome == "unsupported":
                return self._finalize_text_response_node(
                    state, str(style_plan["message"])
                )

            for command in style_plan["commands"]:
                state.map_commands.append(
                    {
                        **command,
                        "datasetId": str(style_plan["datasetId"]),
                        "reason": str(style_plan["reason"]),
                    }
                )
                state.stream_events.append(
                    StreamEvent(
                        type="map.command",
                        sessionId=session.id,
                        messageId=assistant_message.id,
                        data=command,
                    )
                )
            style_message = str(style_plan["message"])
            state.assistant_chunks.append(style_message)
            state.stream_events.append(
                StreamEvent(
                    type="message.delta",
                    sessionId=session.id,
                    messageId=assistant_message.id,
                    data={"delta": style_message},
                )
            )
        map_display_payload = self._map_display_payload(session, payload)
        if not map_display_payload:
            if style_plan is not None:
                state.stream_events.append(
                    self._finalize_assistant_message(
                        user_id=state.user_id,
                        session=session,
                        assistant_message=assistant_message,
                        chunks=state.assistant_chunks,
                    )
                )
                state.is_done = True
            elif self._is_map_display_request(payload.message.lower()):
                return self._finalize_text_response_node(
                    state,
                    "我未生成地图指令，无法执行定位。",
                )
            return state

        commands = map_display_payload["commands"]
        for command in commands:
            state.map_commands.append(command)
            state.stream_events.append(
                StreamEvent(
                    type="map.command",
                    sessionId=session.id,
                    messageId=assistant_message.id,
                    data=command,
                )
            )
        display_message = self._map_display_message(map_display_payload)
        state.assistant_chunks.append(display_message)
        state.stream_events.append(
            StreamEvent(
                type="message.delta",
                sessionId=session.id,
                messageId=assistant_message.id,
                data={"delta": display_message},
            )
        )
        state.stream_events.append(
            self._finalize_assistant_message(
                user_id=state.user_id,
                session=session,
                assistant_message=assistant_message,
                chunks=state.assistant_chunks,
            )
        )
        state.is_done = True
        return state

    async def _report_generation_node(self, state: GeoAgentState) -> GeoAgentState:
        if state.is_done:
            return state
        payload = self._require_payload(state)
        session = self._require_session(state)
        assistant_message = self._require_assistant_message(state)
        tool_results = state.tool_result_payloads

        blocked_spatial_filter_message = self._blocked_spatial_filter_message(tool_results)
        if blocked_spatial_filter_message:
            return self._finalize_text_response_node(state, blocked_spatial_filter_message)

        failure_message = self._tool_failure_message(tool_results)
        if failure_message:
            return self._finalize_text_response_node(state, failure_message)

        deterministic_message = self._deterministic_attribute_summary_message(
            tool_results
        ) or self._deterministic_spatial_filter_message(
            tool_results
        ) or self._deterministic_geoprocess_buffer_message(
            tool_results
        ) or self._deterministic_data_readiness_message(
            session.data_summaries,
            payload,
        ) or self._deterministic_selected_layer_metadata_message(
            session.data_summaries,
            payload,
        )
        if deterministic_message:
            return self._finalize_text_response_node(state, deterministic_message)

        async for chunk in self.model_client.stream_response(
            messages=self._model_messages(session.messages, session.data_summaries, payload),
            tool_results=tool_results,
        ):
            state.assistant_chunks.append(chunk)
            state.stream_events.append(
                StreamEvent(
                    type="message.delta",
                    sessionId=session.id,
                    messageId=assistant_message.id,
                    data={"delta": chunk},
                )
            )

        state.stream_events.append(
            self._finalize_assistant_message(
                user_id=state.user_id,
                session=session,
                assistant_message=assistant_message,
                chunks=state.assistant_chunks,
            )
        )
        state.is_done = True
        return state

    async def _error_handler_node(self, state: GeoAgentState) -> GeoAgentState:
        return state

    def _finalize_text_response_node(
        self,
        state: GeoAgentState,
        message: str,
    ) -> GeoAgentState:
        session = self._require_session(state)
        assistant_message = self._require_assistant_message(state)
        state.assistant_chunks.append(message)
        state.stream_events.append(
            StreamEvent(
                type="message.delta",
                sessionId=session.id,
                messageId=assistant_message.id,
                data={"delta": message},
            )
        )
        state.stream_events.append(
            self._finalize_assistant_message(
                user_id=state.user_id,
                session=session,
                assistant_message=assistant_message,
                chunks=state.assistant_chunks,
            )
        )
        state.is_done = True
        return state

    def _require_payload(self, state: GeoAgentState) -> ChatMessageRequest:
        if state.payload is None:
            raise ValueError("GeoAgentState.payload is required.")
        return state.payload

    def _require_session(self, state: GeoAgentState) -> AgentSession:
        if state.session is None:
            raise ValueError("GeoAgentState.session is required.")
        return state.session

    def _require_assistant_message(self, state: GeoAgentState) -> AgentMessage:
        if state.assistant_message is None:
            raise ValueError("GeoAgentState.assistant_message is required.")
        return state.assistant_message

    def _with_run_id(self, event: StreamEvent, run_id: str) -> StreamEvent:
        return event.model_copy(update={"run_id": run_id})

    def _persist_run_state(self, state: GeoAgentState) -> None:
        if self.run_repository is None:
            return
        status: RunStatus = "running"
        if state.tool_plan is not None and state.tool_plan.reason == "waiting_confirmation":
            status = "waiting_confirmation"
        self.run_repository.update_run_state(
            run_id=state.run_id,
            status=status,
            intent=(
                state.intent.model_dump(mode="json")
                if state.intent is not None
                else None
            ),
            data_readiness=(
                state.data_readiness.model_dump(mode="json")
                if state.data_readiness is not None
                else None
            ),
            tool_plan=(
                state.tool_plan.model_dump(mode="json")
                if state.tool_plan is not None
                else None
            ),
            tool_results=state.tool_result_payloads,
            errors=[
                error.model_dump(mode="json", by_alias=True)
                for error in state.errors
            ],
        )

    def _append_run_event(
        self,
        *,
        run_id: str,
        session_id: str,
        sequence: int,
        event: StreamEvent,
    ) -> None:
        if self.run_repository is None:
            return
        self.run_repository.append_event(
            run_id=run_id,
            session_id=session_id,
            sequence=sequence,
            event_type=event.type,
            payload=event.model_dump(mode="json", by_alias=True),
        )

    def _finish_run(self, state: GeoAgentState) -> None:
        if self.run_repository is None:
            return
        status: RunStatus = "completed"
        if any(result.get("status") == "failed" for result in state.tool_result_payloads):
            status = "failed"
        if state.tool_plan is not None and state.tool_plan.reason == "waiting_confirmation":
            status = "waiting_confirmation"
        self.run_repository.finish_run(run_id=state.run_id, status=status)

    def _fail_run(self, run_id: str, error: dict[str, object]) -> None:
        if self.run_repository is None:
            return
        self.run_repository.fail_run(run_id=run_id, errors=[error])

    def _hydrate_session_plan_payloads_from_runs(
        self,
        *,
        user_id: str,
        session: AgentSession,
    ) -> None:
        if self.run_repository is None:
            return
        seen = {
            self._stable_plan_payload_key(plan_payload)
            for plan_payload in session.plan_payloads
            if isinstance(plan_payload, dict)
        }
        recovered: list[dict[str, object]] = []
        for run in self.run_repository.list_session_runs(
            user_id=user_id,
            session_id=session.id,
        ):
            run_with_events = self.run_repository.get_run(run_id=run.id)
            if run_with_events is None:
                continue
            for event in run_with_events.events:
                if event.type != "plan.created":
                    continue
                payload = event.payload.get("data")
                if not isinstance(payload, dict):
                    continue
                key = self._stable_plan_payload_key(payload)
                if key in seen:
                    continue
                recovered.append(payload)
                seen.add(key)
        if recovered:
            session.plan_payloads.extend(recovered)

    def _stable_plan_payload_key(self, plan_payload: dict[str, object]) -> str:
        return "|".join(
            [
                str(plan_payload.get("type") or ""),
                str(plan_payload.get("planType") or ""),
                str(plan_payload.get("targetDatasetId") or ""),
                str(plan_payload.get("distance") or ""),
                str(plan_payload.get("unit") or ""),
            ]
        )

    def get_session(self, *, user_id: str, session_id: str) -> AgentSession | None:
        return self.repository.get(user_id, session_id)

    def list_session_runs(self, *, user_id: str, session_id: str) -> list[AgentRun]:
        if self.run_repository is None:
            return []
        return self.run_repository.list_session_runs(user_id=user_id, session_id=session_id)

    def get_run(self, *, user_id: str, session_id: str, run_id: str) -> AgentRun | None:
        if self.run_repository is None:
            return None
        run = self.run_repository.get_run(run_id=run_id)
        if run is None:
            return None
        if run.user_id != user_id or run.session_id != session_id:
            return None
        return run
