from typing import Any

from geo_agent_service.modules.gis_data.repository import DatasetRepository
from geo_agent_service.modules.gis_data.schemas import InputDataSummary
from geo_agent_service.tools.base import GisTool, GisToolResult


class MetadataSearchTool(GisTool):
    name = "metadata_search"
    description = "Search selected GIS dataset metadata by name, geometry, fields, and samples."

    def __init__(self, dataset_repository: DatasetRepository) -> None:
        self.dataset_repository = dataset_repository

    async def run(self, payload: dict[str, Any]) -> GisToolResult:
        query = str(payload.get("query") or payload.get("message") or "").strip()
        dataset_ids = self._dataset_ids(payload)
        summaries = self._summaries(dataset_ids)
        matches = [self._match(summary, query) for summary in summaries]
        matches = [match for match in matches if match["score"] > 0 or not query]
        matches.sort(key=lambda item: item["score"], reverse=True)

        return GisToolResult(
            summary={
                "query": query,
                "matches": matches,
                "datasets": [
                    summary.model_dump(mode="json", by_alias=True) for summary in summaries
                ],
            }
        )

    def _dataset_ids(self, payload: dict[str, Any]) -> list[str]:
        raw_ids = payload.get("datasetIds") or payload.get("selectedDatasetIds") or []
        return [str(dataset_id) for dataset_id in raw_ids if str(dataset_id).strip()]

    def _summaries(self, dataset_ids: list[str]) -> list[InputDataSummary]:
        summaries: list[InputDataSummary] = []
        for dataset_id in dataset_ids:
            record = self.dataset_repository.get(dataset_id)
            if record is not None:
                summaries.append(record.summary)
        return summaries

    def _match(self, summary: InputDataSummary, query: str) -> dict[str, Any]:
        terms = [term.lower() for term in query.split() if term.strip()]
        haystack_parts = [
            summary.dataset_id,
            summary.name,
            summary.geometry_type or "",
            summary.crs or "",
            *(field.name for field in summary.fields),
            *(field.type for field in summary.fields),
            *(sample for field in summary.fields for sample in field.sample_values),
        ]
        haystack = " ".join(haystack_parts).lower()
        hits = [term for term in terms if term in haystack]
        score = 1.0 if not terms else len(hits) / len(terms)

        reasons: list[str] = []
        if not terms:
            reasons.append("selected dataset")
        if summary.name.lower() in query.lower() or any(
            term in summary.name.lower() for term in terms
        ):
            reasons.append("name matched")
        field_hits = [
            field.name
            for field in summary.fields
            if any(term in field.name.lower() for term in terms)
        ]
        if field_hits:
            reasons.append(f"fields matched: {', '.join(field_hits[:5])}")
        if not reasons and hits:
            reasons.append("metadata matched")

        return {
            "datasetId": summary.dataset_id,
            "name": summary.name,
            "geometryType": summary.geometry_type,
            "featureCount": summary.feature_count,
            "bbox": summary.bbox,
            "fields": [field.model_dump(mode="json", by_alias=True) for field in summary.fields],
            "score": round(score, 4),
            "reason": "; ".join(reasons) if reasons else "no metadata match",
        }
