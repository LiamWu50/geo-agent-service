from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from geo_agent_service.core.config import settings
from geo_agent_service.modules.gis_data.repository import DatasetRepository
from geo_agent_service.modules.gis_data.schemas import (
    DatasetFromUrlRequest,
    DatasetListResponse,
    DatasetPreviewResponse,
    InputDataSummary,
)
from geo_agent_service.modules.gis_data.service import (
    DatasetNotFoundError,
    GisDatasetService,
    InvalidDatasetInputError,
    InvalidDatasetUploadError,
    InvalidDatasetUrlError,
)
from geo_agent_service.modules.gis_data.storage import GisDataStorage

router = APIRouter(prefix="/datasets", tags=["datasets"])


def get_dataset_service() -> GisDatasetService:
    storage = GisDataStorage(settings.gis_storage_root)
    repository = DatasetRepository(storage.metadata_path())
    return GisDatasetService(storage=storage, repository=repository)


DatasetServiceDependency = Annotated[GisDatasetService, Depends(get_dataset_service)]


@router.post("", response_model=InputDataSummary)
async def upload_dataset(
    file: Annotated[UploadFile, File()],
    service: DatasetServiceDependency,
    name: Annotated[str | None, Form()] = None,
) -> InputDataSummary:
    try:
        return await service.upload_dataset(file=file, name=name)
    except (InvalidDatasetUploadError, InvalidDatasetInputError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/from-url", response_model=InputDataSummary)
async def register_dataset_from_url(
    payload: DatasetFromUrlRequest,
    service: DatasetServiceDependency,
) -> InputDataSummary:
    try:
        return await service.register_dataset_from_url(
            url=str(payload.url),
            name=payload.name,
        )
    except (InvalidDatasetUrlError, InvalidDatasetInputError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("", response_model=DatasetListResponse)
async def list_datasets(
    service: DatasetServiceDependency,
) -> DatasetListResponse:
    return DatasetListResponse(datasets=service.list_datasets())


@router.get("/{dataset_id}", response_model=InputDataSummary)
async def get_dataset(
    dataset_id: str,
    service: DatasetServiceDependency,
) -> InputDataSummary:
    try:
        return service.get_dataset(dataset_id)
    except DatasetNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Dataset not found.") from exc


@router.get("/{dataset_id}/preview", response_model=DatasetPreviewResponse)
async def preview_dataset(
    dataset_id: str,
    service: DatasetServiceDependency,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> DatasetPreviewResponse:
    try:
        return service.preview_dataset(dataset_id=dataset_id, limit=limit)
    except DatasetNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Dataset not found.") from exc
