from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from geo_agent_service.core.config import settings
from geo_agent_service.modules.auth.repository import AuthRepository
from geo_agent_service.modules.auth.schemas import (
    LoginRequest,
    LoginResponse,
    UserProfile,
    UserProfileUpdate,
)
from geo_agent_service.modules.auth.service import (
    AuthenticationError,
    AuthService,
    InvalidTokenError,
)

router = APIRouter(prefix="/auth", tags=["auth"])
bearer_scheme = HTTPBearer(auto_error=False)


def get_auth_service() -> AuthService:
    repository = AuthRepository(settings.auth_storage_root)
    return AuthService(
        repository=repository,
        username=settings.auth_username,
        password=settings.auth_password,
        token_secret=settings.auth_token_secret,
        token_expire_minutes=settings.auth_token_expire_minutes,
    )


AuthServiceDependency = Annotated[AuthService, Depends(get_auth_service)]
BearerCredentialsDependency = Annotated[
    HTTPAuthorizationCredentials | None,
    Depends(bearer_scheme),
]


def bearer_token(credentials: BearerCredentialsDependency) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized_error()
    return credentials.credentials


BearerTokenDependency = Annotated[str, Depends(bearer_token)]


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest, service: AuthServiceDependency) -> LoginResponse:
    try:
        return service.login(username=payload.username, password=payload.password)
    except AuthenticationError as exc:
        raise unauthorized_error() from exc


@router.get("/me", response_model=UserProfile)
async def get_current_user(
    token: BearerTokenDependency,
    service: AuthServiceDependency,
) -> UserProfile:
    try:
        return service.get_current_user(token)
    except InvalidTokenError as exc:
        raise unauthorized_error() from exc


@router.put("/me", response_model=UserProfile)
async def update_current_user(
    payload: UserProfileUpdate,
    token: BearerTokenDependency,
    service: AuthServiceDependency,
) -> UserProfile:
    try:
        return service.update_current_user(token, payload)
    except InvalidTokenError as exc:
        raise unauthorized_error() from exc


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def logout(token: BearerTokenDependency, service: AuthServiceDependency) -> Response:
    try:
        service.logout(token)
    except InvalidTokenError as exc:
        raise unauthorized_error() from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def unauthorized_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized.",
        headers={"WWW-Authenticate": "Bearer"},
    )
