import json
from pathlib import Path
from typing import Any

from geo_agent_service.schemas.session import AgentSession


class AiChatRepository:
    def __init__(self, storage_root: str) -> None:
        self.storage_root = Path(storage_root)

    def get(self, user_id: str, session_id: str) -> AgentSession | None:
        path = self._session_path(user_id, session_id)
        if not path.exists():
            return None
        data = self._read_json(path)
        if not data:
            return None
        return AgentSession.model_validate(data)

    def save(self, user_id: str, session: AgentSession) -> None:
        path = self._session_path(user_id, session.id)
        self._write_json(path, session.model_dump(mode="json", by_alias=True))

    def _session_path(self, user_id: str, session_id: str) -> Path:
        safe_user_id = self._safe_segment(user_id)
        safe_session_id = self._safe_segment(session_id)
        return self.storage_root / safe_user_id / f"{safe_session_id}.json"

    def _safe_segment(self, value: str) -> str:
        return "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value)

    def _read_json(self, path: Path) -> dict[str, Any]:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return data

    def _write_json(self, path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(path)
