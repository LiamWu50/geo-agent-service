from fastapi import FastAPI

from geo_agent_service.api.router import api_router
from geo_agent_service.core.config import settings


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name)
    app.include_router(api_router, prefix=settings.api_prefix)
    return app


app = create_app()
