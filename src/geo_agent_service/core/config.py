from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "AI WebGIS Geo Agent Service"
    app_env: str = "development"
    api_prefix: str = "/api"
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/ai_webgis"
    gis_storage_root: str = "data/gis"
    auth_username: str = "admin"
    auth_password: str = "admin"
    auth_token_secret: str = "change-me-in-production"
    auth_token_expire_minutes: int = 1440
    auth_storage_root: str = "data/auth"
    layer_tree_storage_root: str = "data/layer-trees"
    ai_chat_storage_root: str = "data/ai-chat"
    qwen_api_key: str | None = None
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_model_name: str = "qwen-plus"
    qwen_timeout_seconds: float = 60.0
    qwen_max_output_tokens: int = 2048


settings = Settings()
