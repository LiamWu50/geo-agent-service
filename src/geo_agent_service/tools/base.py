from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel


class GisToolResult(BaseModel):
    data_ref: str | None = None
    summary: dict[str, Any] = {}


class GisTool(ABC):
    name: str
    description: str

    @abstractmethod
    async def run(self, payload: dict[str, Any]) -> GisToolResult:
        raise NotImplementedError
