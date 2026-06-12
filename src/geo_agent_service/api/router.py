from fastapi import APIRouter

from geo_agent_service.api.routes import health

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
