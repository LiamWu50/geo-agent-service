from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UserProfile(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    username: str
    nickname: str
    email: str | None = None
    avatar_url: str | None = Field(default=None, alias="avatarUrl")


class UserProfileUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    nickname: str | None = None
    email: str | None = None
    avatar_url: str | None = Field(default=None, alias="avatarUrl")


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    access_token: str = Field(alias="accessToken")
    token_type: str = Field(default="bearer", alias="tokenType")
    expires_in: int = Field(alias="expiresIn")
    user: UserProfile


class AuthSession(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    token_hash: str | None = Field(default=None, alias="tokenHash")
    expires_at: datetime | None = Field(default=None, alias="expiresAt")
