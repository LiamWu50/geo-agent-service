import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from geo_agent_service.modules.auth.repository import AuthRepository
from geo_agent_service.modules.auth.schemas import (
    AuthSession,
    LoginResponse,
    UserProfile,
    UserProfileUpdate,
)


class AuthenticationError(PermissionError):
    """Raised when credentials are invalid."""


class InvalidTokenError(PermissionError):
    """Raised when a bearer token is missing, invalid, expired, or inactive."""


class AuthService:
    def __init__(
        self,
        repository: AuthRepository,
        username: str,
        password: str,
        token_secret: str,
        token_expire_minutes: int,
    ) -> None:
        self.repository = repository
        self.username = username
        self.password = password
        self.token_secret = token_secret
        self.token_expire_minutes = token_expire_minutes

    def login(self, username: str, password: str) -> LoginResponse:
        if not (
            secrets.compare_digest(username, self.username)
            and secrets.compare_digest(password, self.password)
        ):
            raise AuthenticationError("Invalid username or password.")

        profile = self.repository.get_or_create_profile(self.username)
        token = self._create_token()
        expires_at = datetime.now(UTC) + timedelta(minutes=self.token_expire_minutes)
        self.repository.save_session(
            AuthSession(tokenHash=self._hash_token(token), expiresAt=expires_at)
        )
        return LoginResponse(
            accessToken=token,
            expiresIn=self.token_expire_minutes * 60,
            user=profile,
        )

    def get_current_user(self, token: str) -> UserProfile:
        self._require_valid_token(token)
        return self.repository.get_or_create_profile(self.username)

    def update_current_user(self, token: str, update: UserProfileUpdate) -> UserProfile:
        profile = self.get_current_user(token)
        updated = profile.model_copy(
            update={
                "nickname": update.nickname if update.nickname is not None else profile.nickname,
                "email": update.email if update.email is not None else profile.email,
                "avatar_url": (
                    update.avatar_url if update.avatar_url is not None else profile.avatar_url
                ),
            }
        )
        self.repository.save_profile(updated)
        return updated

    def logout(self, token: str) -> None:
        self._require_valid_token(token)
        self.repository.clear_session()

    def _require_valid_token(self, token: str) -> None:
        if not self._has_valid_signature(token):
            raise InvalidTokenError("Invalid token.")

        session = self.repository.get_session()
        token_hash = self._hash_token(token)
        now = datetime.now(UTC)
        if (
            session.token_hash is None
            or session.expires_at is None
            or not secrets.compare_digest(session.token_hash, token_hash)
            or session.expires_at <= now
        ):
            raise InvalidTokenError("Invalid token.")

    def _create_token(self) -> str:
        token_id = secrets.token_urlsafe(32)
        signature = self._sign(token_id)
        return f"{token_id}.{signature}"

    def _has_valid_signature(self, token: str) -> bool:
        token_id, separator, signature = token.partition(".")
        if not separator or not token_id or not signature:
            return False
        expected_signature = self._sign(token_id)
        return secrets.compare_digest(signature, expected_signature)

    def _sign(self, token_id: str) -> str:
        return hmac.new(
            self.token_secret.encode("utf-8"),
            token_id.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _hash_token(self, token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()
