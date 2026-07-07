from pathlib import Path

from geo_agent_service.modules.gis_data.repository import DatasetRepository
from geo_agent_service.modules.gis_data.schemas import DatasetRecord, FieldSummary, InputDataSummary
from geo_agent_service.modules.gis_data.storage import GisDataStorage
from geo_agent_service.tools.attribute_summary import AttributeSummaryTool


def write_dataset_with_array_properties(storage: GisDataStorage) -> str:
    dataset_id = "dataset_arrays"
    path = storage.normalized_path(dataset_id)
    path.write_text(
        """
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "properties": {"name": "A", "center": [104.1, 30.7], "childrenNum": 3},
      "geometry": {"type": "Point", "coordinates": [104.1, 30.7]}
    },
    {
      "type": "Feature",
      "properties": {"name": "B", "center": [105.2, 31.8], "childrenNum": 11},
      "geometry": {"type": "Point", "coordinates": [105.2, 31.8]}
    },
    {
      "type": "Feature",
      "properties": {"name": "C", "center": [102.3, 29.9], "childrenNum": 7},
      "geometry": {"type": "Point", "coordinates": [102.3, 29.9]}
    }
  ]
}
        """.strip(),
        encoding="utf-8",
    )
    summary = InputDataSummary(
        datasetId=dataset_id,
        name="array properties",
        sourceType="upload",
        geometryType="Point",
        crs="EPSG:4326",
        featureCount=3,
        bbox=(102.3, 29.9, 105.2, 31.8),
        fields=[
            FieldSummary(name="name", type="string"),
            FieldSummary(name="center", type="unknown"),
            FieldSummary(name="childrenNum", type="number"),
        ],
        dataRef=storage.normalized_uri(dataset_id),
    )
    DatasetRepository(storage.metadata_path()).save(
        DatasetRecord(
            summary=summary,
            rawUri=storage.upload_uri(dataset_id),
            normalizedUri=storage.normalized_uri(dataset_id),
        )
    )
    return dataset_id


async def test_attribute_summary_sorts_rows_with_array_properties(tmp_path: Path) -> None:
    storage = GisDataStorage(tmp_path / "gis")
    repository = DatasetRepository(storage.metadata_path())
    dataset_id = write_dataset_with_array_properties(storage)

    result = await AttributeSummaryTool(
        dataset_repository=repository,
        storage=storage,
    ).run(
        {
            "datasetId": dataset_id,
            "sortBy": "childrenNum",
            "sortOrder": "desc",
            "includeRows": True,
        }
    )

    rows = result.summary["rows"]
    assert [row["childrenNum"] for row in rows] == [11, 7, 3]
    assert rows[0]["center"] == [105.2, 31.8]
