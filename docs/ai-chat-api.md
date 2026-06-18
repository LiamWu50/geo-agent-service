# AI 聊天接口文档

本文档描述 Geo Agent Service 的 AI 聊天模块，覆盖登录保护、会话消息发送、流式事件和 qwen-plus 配置。

## 基础信息

- Base URL: `/api`
- 认证方式: `Authorization: Bearer <accessToken>`
- 流式格式: `text/event-stream`
- 当前模型: `qwen-plus`
- 当前传输: HTTP SSE-style streaming

## 环境变量

```bash
AI_CHAT_STORAGE_ROOT="data/ai-chat"
QWEN_API_KEY=""
QWEN_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
QWEN_MODEL_NAME="qwen-plus"
QWEN_TIMEOUT_SECONDS=60
QWEN_MAX_OUTPUT_TOKENS=2048
```

未配置 `QWEN_API_KEY` 时，接口仍可流式返回一条模型未配置提示，便于本地联调认证、会话和事件格式。

## 1. 发送会话消息

```http
POST /api/ai-chat/sessions/{sessionId}/messages
Content-Type: application/json
Authorization: Bearer <accessToken>
Accept: text/event-stream
```

### 请求体

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `message` | string | 是 | 用户自然语言消息，不能为空 |
| `selectedDatasetIds` | string[] | 否 | 当前选中的数据集 ID |
| `selectedServiceIds` | string[] | 否 | 当前选中的地图服务 ID |
| `metadata` | object | 否 | 前端透传上下文 |

### curl 示例

```bash
curl -N -X POST "http://localhost:8000/api/ai-chat/sessions/session_demo/messages" \
  -H "Authorization: Bearer <accessToken>" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "分析当前学校点位数据的分布",
    "selectedDatasetIds": ["dataset_abc123"],
    "selectedServiceIds": []
  }'
```

### 流式事件

每个事件由 `event:` 和 `data:` 两行组成，事件之间以空行分隔。`data` 是 JSON，包含 `type`、`sessionId`、可选 `messageId`、可选 `toolCallId` 和 `data`。

支持事件类型：

- `tool.started`：后端工具开始执行
- `tool.completed`：后端工具成功返回
- `tool.failed`：后端工具失败但可恢复
- `message.delta`：助手消息增量文本
- `message.completed`：助手消息已完成并持久化
- `error`：请求级错误

### 示例事件

```text
event: message.delta
data: {"type":"message.delta","sessionId":"session_demo","messageId":"msg_x","toolCallId":null,"data":{"delta":"当前数据"}}

event: message.completed
data: {"type":"message.completed","sessionId":"session_demo","messageId":"msg_x","toolCallId":null,"data":{"message":{"id":"msg_x","role":"assistant","content":"当前数据...","created_at":"...","status":"completed"}}}
```

### 错误响应

未登录、token 无效或 token 过期：

```json
{
  "detail": "Unauthorized."
}
```

HTTP 状态码为 `401`。

消息为空时返回 `422` 请求体校验错误。

## 2. 获取会话详情

```http
GET /api/ai-chat/sessions/{sessionId}
Authorization: Bearer <accessToken>
```

### 成功响应

```json
{
  "session": {
    "id": "session_demo",
    "title": "分析当前学校点位数据的分布",
    "status": "completed",
    "messages": [],
    "selectedDatasetIds": [],
    "selectedServiceIds": [],
    "toolCalls": [],
    "createdAt": "...",
    "updatedAt": "..."
  }
}
```

### 错误响应

会话不存在时返回：

```json
{
  "detail": "Chat session not found."
}
```

HTTP 状态码为 `404`。
