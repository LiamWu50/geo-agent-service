from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from geo_agent_service.core.config import settings


def create_database_engine() -> Engine:
    return create_engine(settings.database_url)
