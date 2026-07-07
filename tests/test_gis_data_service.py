from pathlib import Path

from geo_agent_service.modules.gis_data.repository import DatasetRepository
from geo_agent_service.modules.gis_data.schemas import DatasetRecord, InputDataSummary
from geo_agent_service.modules.gis_data.service import GisDatasetService
from geo_agent_service.modules.gis_data.storage import GisDataStorage


def test_storage_resolves_data_ref_inside_storage_root(tmp_path: Path) -> None:
    storage = GisDataStorage(tmp_path / "gis")
    path = storage.resolve_data_ref("storage://normalized/dataset_001/data.geojson")

    assert path == (tmp_path / "gis" / "normalized" / "dataset_001" / "data.geojson").resolve()


def test_dataset_service_resolves_sample_data_ref(tmp_path: Path) -> None:
    storage = GisDataStorage(tmp_path / "gis")
    service = GisDatasetService(
        storage=storage,
        repository=DatasetRepository(storage.metadata_path()),
    )

    path = service.resolve_data_ref("sample://sample_airports")

    assert path.name == "airports.geojson"
    assert path.exists()


def test_repository_persists_and_reads_dataset_records(tmp_path: Path) -> None:
    storage = GisDataStorage(tmp_path / "gis")
    repository = DatasetRepository(storage.metadata_path())
    summary = InputDataSummary(
        datasetId="dataset_001",
        name="schools",
        sourceType="upload",
        geometryType="Point",
        featureCount=1,
        bbox=(116.1, 39.7, 116.1, 39.7),
        fields=[],
        warnings=["CRS is missing; spatial distance and area calculations need confirmation."],
        dataRef="storage://normalized/dataset_001/data.geojson",
    )

    repository.save(
        DatasetRecord(
            summary=summary,
            rawUri="storage://uploads/dataset_001/source.geojson",
            normalizedUri="storage://normalized/dataset_001/data.geojson",
        )
    )

    reloaded = DatasetRepository(storage.metadata_path()).get("dataset_001")
    assert reloaded is not None
    assert reloaded.summary.dataset_id == "dataset_001"
    assert reloaded.summary.data_ref == "storage://normalized/dataset_001/data.geojson"
    assert reloaded.summary.warnings == [
        "CRS is missing; spatial distance and area calculations need confirmation."
    ]
