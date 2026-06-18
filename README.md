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

## 用户图层树

提供当前登录用户私有地图图层树能力：

- `GET /api/layer-tree`：获取当前用户图层树；首次调用返回默认树
- `POST /api/layer-tree/dataset-layers`：把数据中心数据集加入用户图层
- `PATCH /api/layer-tree/nodes/{nodeId}`：更新用户图层节点名称、显隐、透明度
- `POST /api/layer-tree/nodes/{nodeId}/move`：移动用户图层节点
- `DELETE /api/layer-tree/nodes/{nodeId}`：删除用户图层节点

相关环境变量：

```bash
LAYER_TREE_STORAGE_ROOT="data/layer-trees"
```

接口对接文档见 [docs/layer-tree-api.md](docs/layer-tree-api.md)。

## AI 聊天

提供登录保护的会话式 AI 聊天流式接口：

- `POST /api/ai-chat/sessions/{sessionId}/messages`：发送用户消息并以 `text/event-stream` 返回工具调用和助手消息事件
- `GET /api/ai-chat/sessions/{sessionId}`：获取当前用户的会话详情

相关环境变量：

```bash
AI_CHAT_STORAGE_ROOT="data/ai-chat"
QWEN_API_KEY=""
QWEN_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
QWEN_MODEL_NAME="qwen-plus"
QWEN_TIMEOUT_SECONDS=60
QWEN_MAX_OUTPUT_TOKENS=2048
```

接口对接文档见 [docs/ai-chat-api.md](docs/ai-chat-api.md)。
