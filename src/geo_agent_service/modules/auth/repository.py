import json
from pathlib import Path
from typing import Any

from geo_agent_service.modules.auth.schemas import AuthSession, UserProfile


class AuthRepository:
    def __init__(self, storage_root: str) -> None:
        self.storage_root = Path(storage_root)
        self.profile_path = self.storage_root / "profile.json"
        self.session_path = self.storage_root / "session.json"

    def get_or_create_profile(self, username: str) -> UserProfile:
        if self.profile_path.exists():
            return UserProfile.model_validate(self._read_json(self.profile_path))

        profile = UserProfile(
            id="default",
            username=username,
            nickname=username,
            email=None,
            avatarUrl=None,
        )
        self.save_profile(profile)
        return profile

    def save_profile(self, profile: UserProfile) -> None:
        self._write_json(self.profile_path, profile.model_dump(mode="json", by_alias=True))

    def get_session(self) -> AuthSession:
        if not self.session_path.exists():
            return AuthSession()
        return AuthSession.model_validate(self._read_json(self.session_path))

    def save_session(self, session: AuthSession) -> None:
        self._write_json(self.session_path, session.model_dump(mode="json", by_alias=True))

    def clear_session(self) -> None:
        self.save_session(AuthSession())

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
