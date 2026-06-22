import json
from collections.abc import Hashable
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlparse

import geopandas as gpd  # type: ignore[import-untyped]
import httpx
import pandas as pd  # type: ignore[import-untyped]
from fastapi import UploadFile
from pandas.api import types as pandas_types  # type: ignore[import-untyped]

from geo_agent_service.modules.gis_data.repository import DatasetRepository
from geo_agent_service.modules.gis_data.sample_datasets import (
    SAMPLE_DATASET_BY_ID,
    SAMPLE_DATASETS,
    SampleDatasetDefinition,
)
from geo_agent_service.modules.gis_data.schemas import (
    DatasetPreviewResponse,
    DatasetRecord,
    FieldSummary,
    InputDataSummary,
)
from geo_agent_service.modules.gis_data.storage import GisDataStorage

GeometryType = Literal[
    "Point",
    "LineString",
    "Polygon",
    "MultiPoint",
    "MultiLineString",
    "MultiPolygon",
    "Mixed",
    "Raster",
]
FieldType = Literal["string", "number", "boolean", "date", "unknown"]
DatasetSourceType = Literal["upload", "url", "sample"]
MAX_URL_DOWNLOAD_BYTES = 25 * 1024 * 1024


class InvalidDatasetInputError(ValueError):
    """Raised when a dataset input cannot be accepted."""


class InvalidDatasetUploadError(InvalidDatasetInputError):
    """Raised when an uploaded dataset cannot be accepted."""


class InvalidDatasetUrlError(InvalidDatasetInputError):
    """Raised when a URL dataset cannot be accepted."""


class DatasetNotFoundError(LookupError):
    """Raised when a dataset id does not exist."""


class GisDatasetService:
    def __init__(self, storage: GisDataStorage, repository: DatasetRepository) -> None:
        self.storage = storage
        self.repository = repository

    async def upload_dataset(self, file: UploadFile, name: str | None = None) -> InputDataSummary:
        self._validate_upload_filename(file.filename)

        dataset_id = self.storage.new_dataset_id()
        raw_path = await self.storage.save_upload(dataset_id, file)
        if raw_path.stat().st_size == 0:
            raise InvalidDatasetUploadError("Uploaded GeoJSON file is empty.")

        return self._register_dataset_from_path(
            dataset_id=dataset_id,
            raw_path=raw_path,
            name=name or Path(file.filename or dataset_id).stem,
            source_type="upload",
        )

    async def register_dataset_from_url(
        self,
        url: str,
        name: str | None = None,
    ) -> InputDataSummary:
        dataset_id = self.storage.new_dataset_id()
        content = await self._download_geojson_url(url)
        raw_path = self.storage.save_source_bytes(dataset_id, content)

        return self._register_dataset_from_path(
            dataset_id=dataset_id,
            raw_path=raw_path,
            name=name or self._name_from_url(url) or dataset_id,
            source_type="url",
            source_url=url,
        )

    def list_datasets(self) -> list[InputDataSummary]:
        sample_ids = {sample.dataset_id for sample in SAMPLE_DATASETS}
        user_datasets = [
            record.summary
            for record in self.repository.list()
            if record.summary.dataset_id not in sample_ids
        ]
        return [self._sample_summary(sample) for sample in SAMPLE_DATASETS] + user_datasets

    def get_dataset(self, dataset_id: str) -> InputDataSummary:
        sample = SAMPLE_DATASET_BY_ID.get(dataset_id)
        if sample is not None:
            return self._sample_summary(sample)

        record = self.repository.get(dataset_id)
        if record is None:
            raise DatasetNotFoundError(dataset_id)
        return record.summary

    def preview_dataset(self, dataset_id: str, limit: int = 100) -> DatasetPreviewResponse:
        sample = SAMPLE_DATASET_BY_ID.get(dataset_id)
        if sample is not None:
            return self._preview_geojson(
                dataset_id=dataset_id,
                path=sample.path,
                summary=self._sample_summary(sample),
                limit=limit,
            )

        record = self.repository.get(dataset_id)
        if record is None:
            raise DatasetNotFoundError(dataset_id)

        path = self.storage.resolve_data_ref(record.summary.data_ref)
        return self._preview_geojson(
            dataset_id=dataset_id,
            path=path,
            summary=record.summary,
            limit=limit,
        )

    def _preview_geojson(
        self,
        dataset_id: str,
        path: Path,
        summary: InputDataSummary,
        limit: int,
    ) -> DatasetPreviewResponse:
        feature_collection = json.loads(path.read_text(encoding="utf-8"))
        features = feature_collection.get("features", [])
        if isinstance(features, list):
            feature_collection["features"] = features[:limit]

        return DatasetPreviewResponse(
            datasetId=dataset_id,
            bbox=summary.bbox,
            featureCount=summary.feature_count,
            returnedFeatureCount=len(feature_collection.get("features", [])),
            data=feature_collection,
        )

    def resolve_data_ref(self, data_ref: str) -> Path:
        return self.storage.resolve_data_ref(data_ref)

    def _register_dataset_from_path(
        self,
        dataset_id: str,
        raw_path: Path,
        name: str,
        source_type: DatasetSourceType,
        source_url: str | None = None,
    ) -> InputDataSummary:
        normalized_path = self.storage.normalized_path(dataset_id)
        geodata = self._read_geodata(raw_path)
        geodata.to_file(normalized_path, driver="GeoJSON")

        summary = self._summarize_geodata(
            geodata=geodata,
            dataset_id=dataset_id,
            name=name,
            data_ref=self.storage.normalized_uri(dataset_id),
            source_type=source_type,
            has_declared_crs=self._has_declared_crs(raw_path),
        )
        self.repository.save(
            DatasetRecord(
                summary=summary,
                rawUri=self.storage.upload_uri(dataset_id),
                normalizedUri=self.storage.normalized_uri(dataset_id),
                sourceUrl=source_url,
            )
        )
        return summary

    def _sample_summary(self, sample: SampleDatasetDefinition) -> InputDataSummary:
        geodata = self._read_geodata(sample.path)
        return self._summarize_geodata(
            geodata=geodata,
            dataset_id=sample.dataset_id,
            name=sample.name,
            data_ref=sample.data_ref,
            source_type="sample",
            has_declared_crs=self._has_declared_crs(sample.path),
        )

    def _validate_upload_filename(self, filename: str | None) -> None:
        suffix = Path(filename or "").suffix.lower()
        if suffix not in {".geojson", ".json"}:
            raise InvalidDatasetUploadError("Only .geojson and .json uploads are supported.")

    def _read_geodata(self, path: Path) -> gpd.GeoDataFrame:
        try:
            geodata = gpd.read_file(path)
        except Exception as exc:
            raise InvalidDatasetInputError("Input is not a readable GeoJSON dataset.") from exc

        if geodata.empty and "geometry" not in geodata:
            raise InvalidDatasetInputError("Input does not contain GIS features.")
        return geodata

    def _summarize_geodata(
        self,
        geodata: gpd.GeoDataFrame,
        dataset_id: str,
        name: str,
        data_ref: str,
        source_type: DatasetSourceType,
        has_declared_crs: bool,
    ) -> InputDataSummary:
        warnings: list[str] = []
        crs = geodata.crs.to_string() if geodata.crs is not None else None
        if crs is None or not has_declared_crs:
            warnings.append(
                "CRS is missing; spatial distance and area calculations need confirmation."
            )

        total_bounds = geodata.total_bounds
        bbox: tuple[float, float, float, float] | None = None
        if len(total_bounds) == 4:
            bbox = (
                float(total_bounds[0]),
                float(total_bounds[1]),
                float(total_bounds[2]),
                float(total_bounds[3]),
            )

        return InputDataSummary(
            datasetId=dataset_id,
            name=name,
            sourceType=source_type,
            geometryType=self._geometry_type(geodata),
            crs=crs,
            featureCount=len(geodata),
            bbox=bbox,
            fields=self._field_summaries(geodata),
            warnings=warnings,
            dataRef=data_ref,
        )

    def _geometry_type(self, geodata: gpd.GeoDataFrame) -> GeometryType | None:
        geometry_types = sorted(
            {
                geometry_type
                for geometry_type in geodata.geometry.geom_type.dropna().unique().tolist()
                if geometry_type
            }
        )
        if not geometry_types:
            return None
        if len(geometry_types) == 1:
            return cast(GeometryType, geometry_types[0])
        return "Mixed"

    def _field_summaries(self, geodata: gpd.GeoDataFrame) -> list[FieldSummary]:
        fields: list[FieldSummary] = []
        for column in geodata.columns:
            if column == geodata.geometry.name:
                continue
            series = geodata[column]
            fields.append(
                FieldSummary(
                    name=column,
                    type=self._field_type(series),
                    sampleValues=self._sample_values(series),
                    nullRatio=float(series.isna().mean()) if len(series) else None,
                    uniqueCount=self._unique_count(series),
                )
            )
        return fields

    def _field_type(self, series: pd.Series) -> FieldType:
        if pandas_types.is_bool_dtype(series):
            return "boolean"
        if pandas_types.is_numeric_dtype(series):
            return "number"
        if pandas_types.is_datetime64_any_dtype(series):
            return "date"
        if pandas_types.is_string_dtype(series) or pandas_types.is_object_dtype(series):
            return "string"
        return "unknown"

    def _sample_values(self, series: pd.Series, limit: int = 5) -> list[str]:
        values = series.dropna().map(self._summarizable_value).astype(str).unique().tolist()
        return [str(value) for value in values[:limit]]

    def _unique_count(self, series: pd.Series) -> int:
        values = series.dropna().map(self._summarizable_value)
        return int(values.nunique(dropna=True))

    def _summarizable_value(self, value: object) -> object:
        if isinstance(value, Hashable):
            return value
        return json.dumps(value, ensure_ascii=False, default=str)

    def _has_declared_crs(self, path: Path) -> bool:
        try:
            content = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return False
        return "crs" in content

    async def _download_geojson_url(self, url: str) -> bytes:
        self._validate_geojson_url(url)
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise InvalidDatasetUrlError("Unable to download GeoJSON from URL.") from exc

        content = response.content
        if not content:
            raise InvalidDatasetUrlError("Downloaded GeoJSON file is empty.")
        if len(content) > MAX_URL_DOWNLOAD_BYTES:
            raise InvalidDatasetUrlError("Downloaded GeoJSON file is too large.")

        content_type = response.headers.get("content-type", "").lower()
        if not self._looks_like_geojson_response(url, content_type):
            raise InvalidDatasetUrlError("URL must return a GeoJSON or JSON response.")
        return content

    def _validate_geojson_url(self, url: str) -> None:
        parsed_url = urlparse(url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise InvalidDatasetUrlError("Only http and https GeoJSON URLs are supported.")

    def _looks_like_geojson_response(self, url: str, content_type: str) -> bool:
        path = urlparse(url).path.lower()
        if path.endswith((".geojson", ".json")):
            return True
        return "json" in content_type or "geo+json" in content_type

    def _name_from_url(self, url: str) -> str | None:
        stem = Path(urlparse(url).path).stem
        return stem or None
