# AI WebGIS Agent Studio Geo Agent Service

Python backend scaffold for the AI WebGIS Agent Studio spatial analysis Agent.

## Tech Stack

- FastAPI
- Pydantic
- LangGraph
- GeoPandas
- Shapely
- PyProj
- SQLAlchemy / GeoAlchemy2
- PostGIS

## Commands

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn geo_agent_service.main:app --reload
pytest
ruff check .
mypy src
```

## 启动命令
```
cd /Users/admin/work-space/pratice/ai-webgis-agent-studio/geo-agent-service
source .venv/bin/activate
uvicorn geo_agent_service.main:app --reload
```