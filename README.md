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

## 轻量用户权限

默认提供单用户 Bearer Token 登录能力：

- `POST /api/auth/login`：提交 `username` 和 `password`，返回 `accessToken`
- `GET /api/auth/me`：通过 `Authorization: Bearer <accessToken>` 获取用户信息
- `PUT /api/auth/me`：更新 `nickname`、`email`、`avatarUrl`
- `POST /api/auth/logout`：退出登录并使当前 token 失效

相关环境变量：

```bash
AUTH_USERNAME="admin"
AUTH_PASSWORD="admin"
AUTH_TOKEN_SECRET="change-me-in-production"
AUTH_TOKEN_EXPIRE_MINUTES=1440
AUTH_STORAGE_ROOT="data/auth"
```

接口对接文档见 [docs/auth-api.md](docs/auth-api.md)。
