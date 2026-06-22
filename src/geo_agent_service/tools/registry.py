from geo_agent_service.modules.gis_data.repository import DatasetRepository
from geo_agent_service.modules.gis_data.storage import GisDataStorage
from geo_agent_service.tools.attribute_summary import AttributeSummaryTool
from geo_agent_service.tools.base import GisTool
from geo_agent_service.tools.metadata_search import MetadataSearchTool


class GisToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, GisTool] = {}

    def register(self, tool: GisTool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> GisTool:
        return self._tools[name]

    def list_names(self) -> list[str]:
        return sorted(self._tools)


def create_default_tool_registry(
    *,
    dataset_repository: DatasetRepository,
    storage: GisDataStorage,
) -> GisToolRegistry:
    registry = GisToolRegistry()
    registry.register(MetadataSearchTool(dataset_repository=dataset_repository))
    registry.register(
        AttributeSummaryTool(
            dataset_repository=dataset_repository,
            storage=storage,
        )
    )
    return registry
