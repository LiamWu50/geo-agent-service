from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import JSON, Column, Integer, MetaData, String, Table, UniqueConstraint
from sqlalchemy.engine import Engine
from sqlalchemy.sql import select

from geo_agent_service.modules.ai_chat.schemas import AgentRun, AgentRunEvent

RunStatus = Literal[
    "running",
    "waiting_confirmation",
    "waiting_clarification",
    "completed",
    "failed",
]

metadata = MetaData()

agent_runs = Table(
    "agent_runs",
    metadata,
    Column("id", String(128), primary_key=True),
    Column("session_id", String(128), nullable=False, index=True),
    Column("user_id", String(128), nullable=False, index=True),
    Column("status", String(32), nullable=False),
    Column("query", String, nullable=False),
    Column("selected_dataset_ids", JSON, nullable=False),
    Column("selected_service_ids", JSON, nullable=False),
    Column("intent", JSON, nullable=True),
    Column("data_readiness", JSON, nullable=True),
    Column("tool_plan", JSON, nullable=True),
    Column("tool_results", JSON, nullable=False),
    Column("errors", JSON, nullable=False),
    Column("started_at", String(64), nullable=False),
    Column("finished_at", String(64), nullable=True),
    Column("duration_ms", Integer, nullable=True),
)

agent_run_events = Table(
    "agent_run_events",
    metadata,
    Column("id", String(160), primary_key=True),
    Column("run_id", String(128), nullable=False, index=True),
    Column("session_id", String(128), nullable=False, index=True),
    Column("sequence", Integer, nullable=False),
    Column("type", String(64), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("created_at", String(64), nullable=False),
    UniqueConstraint("run_id", "sequence", name="uq_agent_run_events_run_sequence"),
)


class AgentRunRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def ensure_schema(self) -> None:
        metadata.create_all(self.engine)

    def create_run(
        self,
        *,
        run_id: str,
        session_id: str,
        user_id: str,
        query: str,
        selected_dataset_ids: list[str],
        selected_service_ids: list[str],
    ) -> AgentRun:
        started_at = _now()
        with self.engine.begin() as connection:
            connection.execute(
                agent_runs.insert().values(
                    id=run_id,
                    session_id=session_id,
                    user_id=user_id,
                    status="running",
                    query=query,
                    selected_dataset_ids=selected_dataset_ids,
                    selected_service_ids=selected_service_ids,
                    intent=None,
                    data_readiness=None,
                    tool_plan=None,
                    tool_results=[],
                    errors=[],
                    started_at=started_at,
                    finished_at=None,
                    duration_ms=None,
                )
            )
        return AgentRun(
            id=run_id,
            runId=run_id,
            sessionId=session_id,
            userId=user_id,
            status="running",
            query=query,
            selectedDatasetIds=selected_dataset_ids,
            selectedServiceIds=selected_service_ids,
            startedAt=started_at,
        )

    def update_run_state(
        self,
        *,
        run_id: str,
        intent: dict[str, Any] | None = None,
        data_readiness: dict[str, Any] | None = None,
        tool_plan: dict[str, Any] | None = None,
        tool_results: list[dict[str, Any]] | None = None,
        errors: list[dict[str, Any]] | None = None,
        status: RunStatus | None = None,
    ) -> None:
        values: dict[str, Any] = {}
        if intent is not None:
            values["intent"] = intent
        if data_readiness is not None:
            values["data_readiness"] = data_readiness
        if tool_plan is not None:
            values["tool_plan"] = tool_plan
        if tool_results is not None:
            values["tool_results"] = tool_results
        if errors is not None:
            values["errors"] = errors
        if status is not None:
            values["status"] = status
        if not values:
            return
        with self.engine.begin() as connection:
            connection.execute(
                agent_runs.update().where(agent_runs.c.id == run_id).values(**values)
            )

    def append_event(
        self,
        *,
        run_id: str,
        session_id: str,
        sequence: int,
        event_type: str,
        payload: dict[str, Any],
    ) -> AgentRunEvent:
        created_at = _now()
        with self.engine.begin() as connection:
            connection.execute(
                agent_run_events.insert().values(
                    id=f"{run_id}:{sequence}",
                    run_id=run_id,
                    session_id=session_id,
                    sequence=sequence,
                    type=event_type,
                    payload=payload,
                    created_at=created_at,
                )
            )
        return AgentRunEvent(
            sequence=sequence,
            type=event_type,
            payload=payload,
            createdAt=created_at,
        )

    def finish_run(self, *, run_id: str, status: RunStatus = "completed") -> None:
        run = self.get_run(run_id=run_id)
        finished_at = _now()
        duration_ms = _duration_ms(run.started_at, finished_at) if run is not None else None
        with self.engine.begin() as connection:
            connection.execute(
                agent_runs.update()
                .where(agent_runs.c.id == run_id)
                .values(status=status, finished_at=finished_at, duration_ms=duration_ms)
            )

    def fail_run(self, *, run_id: str, errors: list[dict[str, Any]]) -> None:
        run = self.get_run(run_id=run_id)
        finished_at = _now()
        duration_ms = _duration_ms(run.started_at, finished_at) if run is not None else None
        with self.engine.begin() as connection:
            connection.execute(
                agent_runs.update()
                .where(agent_runs.c.id == run_id)
                .values(
                    status="failed",
                    errors=errors,
                    finished_at=finished_at,
                    duration_ms=duration_ms,
                )
            )

    def get_run(self, *, run_id: str) -> AgentRun | None:
        with self.engine.begin() as connection:
            row = connection.execute(
                select(agent_runs).where(agent_runs.c.id == run_id)
            ).mappings().first()
            if row is None:
                return None
            event_rows = connection.execute(
                select(agent_run_events)
                .where(agent_run_events.c.run_id == run_id)
                .order_by(agent_run_events.c.sequence)
            ).mappings().all()
        return _agent_run_from_row(dict(row), [dict(event_row) for event_row in event_rows])

    def list_session_runs(self, *, user_id: str, session_id: str) -> list[AgentRun]:
        with self.engine.begin() as connection:
            rows = connection.execute(
                select(agent_runs)
                .where(
                    agent_runs.c.user_id == user_id,
                    agent_runs.c.session_id == session_id,
                )
                .order_by(agent_runs.c.started_at)
            ).mappings().all()
        return [_agent_run_from_row(dict(row), []) for row in rows]


def _agent_run_from_row(
    row: dict[str, Any],
    event_rows: list[dict[str, Any]],
) -> AgentRun:
    events = [
        AgentRunEvent(
            sequence=int(event_row["sequence"]),
            type=str(event_row["type"]),
            payload=dict(event_row["payload"] or {}),
            createdAt=str(event_row["created_at"]),
        )
        for event_row in event_rows
    ]
    return AgentRun(
        id=str(row["id"]),
        runId=str(row["id"]),
        sessionId=str(row["session_id"]),
        userId=str(row["user_id"]),
        status=row["status"],
        query=str(row["query"]),
        selectedDatasetIds=list(row["selected_dataset_ids"] or []),
        selectedServiceIds=list(row["selected_service_ids"] or []),
        intent=row["intent"],
        dataReadiness=row["data_readiness"],
        toolPlan=row["tool_plan"],
        toolResults=list(row["tool_results"] or []),
        errors=list(row["errors"] or []),
        events=events,
        startedAt=str(row["started_at"]),
        finishedAt=row["finished_at"],
        durationMs=row["duration_ms"],
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _duration_ms(started_at: str, finished_at: str) -> int:
    started = datetime.fromisoformat(started_at)
    finished = datetime.fromisoformat(finished_at)
    return int((finished - started).total_seconds() * 1000)
