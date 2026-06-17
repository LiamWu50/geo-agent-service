from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field


class FieldSummary(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    type: Literal["string", "number", "boolean", "date", "unknown"]
    sample_values: list[str] = Field(default_factory=list, alias="sampleValues")
    null_ratio: float | None = Field(default=None, alias="nullRatio")
    unique_count: int | None = Field(default=None, alias="uniqueCount")


class InputDataSummary(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    dataset_id: str = Field(alias="datasetId")
    name: str
    source_type: Literal["upload", "url", "database", "sample", "map_service"] = Field(
        alias="sourceType"
    )
    geometry_type: Literal[
        "Point",
        "LineString",
        "Polygon",
        "MultiPoint",
        "MultiLineString",
        "MultiPolygon",
        "Mixed",
        "Raster",
    ] | None = Field(default=None, alias="geometryType")
    crs: str | None = None
    feature_count: int | None = Field(default=None, alias="featureCount")
    bbox: tuple[float, float, float, float] | None = None
    fields: list[FieldSummary] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    data_ref: str = Field(alias="dataRef")


class DatasetRecord(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    summary: InputDataSummary
    raw_uri: str = Field(alias="rawUri")
    normalized_uri: str = Field(alias="normalizedUri")
    source_url: str | None = Field(default=None, alias="sourceUrl")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="createdAt")


class DatasetFromUrlRequest(BaseModel):
    name: str | None = None
    url: AnyHttpUrl


class DatasetListResponse(BaseModel):
    datasets: list[InputDataSummary]


class DatasetPreviewResponse(BaseModel):
    dataset_id: str = Field(alias="datasetId")
    bbox: tuple[float, float, float, float] | None = None
    feature_count: int | None = Field(default=None, alias="featureCount")
    returned_feature_count: int = Field(alias="returnedFeatureCount")
    data: dict[str, Any]
