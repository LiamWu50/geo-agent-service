from fastapi import APIRouter

from geo_agent_service.api.routes import health
from geo_agent_service.modules.ai_chat import routes as ai_chat_routes
from geo_agent_service.modules.auth import routes as auth_routes
from geo_agent_service.modules.gis_data import routes as gis_data_routes
from geo_agent_service.modules.layer_tree import routes as layer_tree_routes

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth_routes.router)
api_router.include_router(ai_chat_routes.router)
api_router.include_router(gis_data_routes.router)
api_router.include_router(layer_tree_routes.router)
