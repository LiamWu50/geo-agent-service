from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from geo_agent_service.core.config import settings
from geo_agent_service.modules.ai_chat.model_client import QwenPlusClient
from geo_agent_service.modules.ai_chat.repository import AiChatRepository
from geo_agent_service.modules.ai_chat.run_repository import AgentRunRepository
from geo_agent_service.modules.ai_chat.schemas import (
    AgentRunListResponse,
    AgentRunResponse,
    ChatMessageRequest,
    ChatSessionResponse,
)
from geo_agent_service.modules.ai_chat.service import AiChatService
from geo_agent_service.modules.auth.routes import (
    AuthServiceDependency,
    BearerTokenDependency,
    unauthorized_error,
)
from geo_agent_service.modules.auth.service import InvalidTokenError
from geo_agent_service.modules.gis_data.repository import DatasetRepository
from geo_agent_service.modules.gis_data.service import GisDatasetService
from geo_agent_service.modules.gis_data.storage import GisDataStorage
from geo_agent_service.persistence.database import create_database_engine
from geo_agent_service.tools.registry import GisToolRegistry, create_default_tool_registry

router = APIRouter(prefix="/ai-chat", tags=["ai-chat"])


def current_user_id(token: BearerTokenDependency, auth_service: AuthServiceDependency) -> str:
    try:
        return auth_service.get_current_user(token).id
    except InvalidTokenError as exc:
        raise unauthorized_error() from exc


CurrentUserIdDependency = Annotated[str, Depends(current_user_id)]


def get_tool_registry() -> GisToolRegistry:
    gis_storage = GisDataStorage(settings.gis_storage_root)
    dataset_repository = DatasetRepository(gis_storage.metadata_path())
    return create_default_tool_registry(
        dataset_repository=dataset_repository,
        storage=gis_storage,
    )


ToolRegistryDependency = Annotated[GisToolRegistry, Depends(get_tool_registry)]


def get_ai_chat_service(tool_registry: ToolRegistryDependency) -> AiChatService:
    gis_storage = GisDataStorage(settings.gis_storage_root)
    dataset_repository = DatasetRepository(gis_storage.metadata_path())
    run_repository = AgentRunRepository(create_database_engine())
    return AiChatService(
        repository=AiChatRepository(settings.ai_chat_storage_root),
        dataset_repository=dataset_repository,
        dataset_service=GisDatasetService(storage=gis_storage, repository=dataset_repository),
        tool_registry=tool_registry,
        run_repository=run_repository,
        model_client=QwenPlusClient(
            api_key=settings.qwen_api_key,
            base_url=settings.qwen_base_url,
            model_name=settings.qwen_model_name,
            timeout_seconds=settings.qwen_timeout_seconds,
            max_output_tokens=settings.qwen_max_output_tokens,
        ),
    )


AiChatServiceDependency = Annotated[AiChatService, Depends(get_ai_chat_service)]


@router.post("/sessions/{session_id}/messages")
async def stream_chat_message(
    session_id: str,
    payload: ChatMessageRequest,
    user_id: CurrentUserIdDependency,
    service: AiChatServiceDependency,
) -> StreamingResponse:
    return StreamingResponse(
        service.stream_message(user_id=user_id, session_id=session_id, payload=payload),
        media_type="text/event-stream",
    )


@router.get("/sessions/{session_id}", response_model=ChatSessionResponse)
async def get_chat_session(
    session_id: str,
    user_id: CurrentUserIdDependency,
    service: AiChatServiceDependency,
) -> ChatSessionResponse:
    session = service.get_session(user_id=user_id, session_id=session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Chat session not found.")
    return ChatSessionResponse(session=session)


@router.get("/sessions/{session_id}/runs", response_model=AgentRunListResponse)
async def list_chat_session_runs(
    session_id: str,
    user_id: CurrentUserIdDependency,
    service: AiChatServiceDependency,
) -> AgentRunListResponse:
    return AgentRunListResponse(
        runs=service.list_session_runs(user_id=user_id, session_id=session_id)
    )


@router.get("/sessions/{session_id}/runs/{run_id}", response_model=AgentRunResponse)
async def get_chat_session_run(
    session_id: str,
    run_id: str,
    user_id: CurrentUserIdDependency,
    service: AiChatServiceDependency,
) -> AgentRunResponse:
    run = service.get_run(user_id=user_id, session_id=session_id, run_id=run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found.")
    return AgentRunResponse(run=run)
