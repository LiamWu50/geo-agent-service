from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path


@dataclass(frozen=True)
class SampleDatasetDefinition:
    dataset_id: str
    name: str
    filename: str
    icon_key: str

    @property
    def data_ref(self) -> str:
        return f"sample://{self.dataset_id}"

    @property
    def path(self) -> Path:
        return Path(str(files("geo_agent_service.sample_data").joinpath(self.filename)))


SAMPLE_DATASETS: tuple[SampleDatasetDefinition, ...] = (
    SampleDatasetDefinition(
        dataset_id="sample_airports",
        name="机场",
        filename="airports.geojson",
        icon_key="plane",
    ),
    SampleDatasetDefinition(
        dataset_id="sample_ports",
        name="港口",
        filename="ports.geojson",
        icon_key="anchor",
    ),
    SampleDatasetDefinition(
        dataset_id="sample_populated_places",
        name="人口稠密地区",
        filename="populated_places.geojson",
        icon_key="building-2",
    ),
)

SAMPLE_DATASET_BY_ID = {dataset.dataset_id: dataset for dataset in SAMPLE_DATASETS}
