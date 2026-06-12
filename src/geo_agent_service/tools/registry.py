from geo_agent_service.tools.base import GisTool


class GisToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, GisTool] = {}

    def register(self, tool: GisTool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> GisTool:
        return self._tools[name]

    def list_names(self) -> list[str]:
        return sorted(self._tools)
