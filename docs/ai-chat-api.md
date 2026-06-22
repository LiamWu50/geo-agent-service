# AI 聊天接口文档

本文档描述 Geo Agent Service 的 AI 聊天模块，覆盖登录保护、会话消息发送、流式事件、GIS 数据摘要注入和第一批 GIS 工具事件。

## 基础信息

- Base URL: `/api`
- 认证方式: `Authorization: Bearer <accessToken>`
- 流式格式: `text/event-stream`
- 当前模型: `qwen-plus`
- 当前传输: HTTP SSE-style streaming
- 当前已支持 GIS 能力: 数据摘要注入、元数据搜索、属性统计

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
| `metadata` | object | 否 | 前端透传上下文，建议包含 `layers` 和 `mapView` |

### metadata 建议结构

后端当前不会强校验 `metadata` 的结构，会将其中的 `layers`、`mapView` 注入模型上下文。前端可按下面格式传递：

```json
{
  "metadata": {
    "layers": [
      {
        "id": "layer_dataset_abc123",
        "datasetId": "dataset_abc123",
        "name": "学校点位",
        "visible": true,
        "opacity": 0.8
      }
    ],
    "mapView": {
      "center": [116.391, 39.907],
      "zoom": 11,
      "bbox": [116.1, 39.7, 116.7, 40.1],
      "crs": "EPSG:4326"
    }
  }
}
```

### curl 示例

```bash
curl -N -X POST "http://localhost:8000/api/ai-chat/sessions/session_demo/messages" \
  -H "Authorization: Bearer <accessToken>" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "当前学校点位数据有哪些字段，按 type 统计数量",
    "selectedDatasetIds": ["dataset_abc123"],
    "selectedServiceIds": [],
    "metadata": {
      "layers": [
        {
          "id": "layer_dataset_abc123",
          "datasetId": "dataset_abc123",
          "name": "学校点位",
          "visible": true
        }
      ],
      "mapView": {
        "center": [116.391, 39.907],
        "zoom": 11
      }
    }
  }'
```

### 流式事件

每个事件由 `event:` 和 `data:` 两行组成，事件之间以空行分隔。`data` 是 JSON，包含 `type`、`sessionId`、可选 `messageId`、可选 `toolCallId` 和 `data`。

支持事件类型：

- `data.summary`：后端已加载本轮选中数据集摘要
- `plan.created`：工具计划已创建，预留事件，当前暂不主动发送
- `tool.started`：后端工具开始执行
- `tool.completed`：后端工具成功返回
- `tool.failed`：后端工具失败但可恢复
- `layer.created`：工具生成结果图层，预留事件，空间处理工具接入后发送
- `map.command`：地图命令，预留事件，地图命令工具接入后发送
- `chart.created`：图表结果，预留事件
- `clarification`：澄清问题，预留事件
- `message.delta`：助手消息增量文本
- `message.completed`：助手消息已完成并持久化
- `error`：请求级错误
- `done`：流结束标记，预留事件

当前一次典型 GIS 问答事件顺序：

```text
data.summary
tool.started
tool.completed
message.delta
message.delta
message.completed
```

如果用户问题不需要工具，事件顺序通常是：

```text
data.summary
message.delta
message.completed
```

如果工具失败但可恢复，后端仍会继续生成回答：

```text
data.summary
tool.started
tool.failed
message.delta
message.completed
```

### data.summary

`data.summary` 会在模型回答前发送。前端应使用该事件确认后端实际加载到了哪些数据集，以及哪些选中 ID 没有找到。

```text
event: data.summary
data: {
  "type": "data.summary",
  "sessionId": "session_demo",
  "messageId": null,
  "toolCallId": null,
  "data": {
    "datasets": [
      {
        "datasetId": "dataset_abc123",
        "name": "学校点位",
        "sourceType": "upload",
        "geometryType": "Point",
        "crs": "EPSG:4326",
        "featureCount": 328,
        "bbox": [116.1, 39.7, 116.7, 40.1],
        "fields": [
          {
            "name": "type",
            "type": "string",
            "sampleValues": ["小学", "中学"],
            "nullRatio": 0,
            "uniqueCount": 2
          },
          {
            "name": "student_count",
            "type": "number",
            "sampleValues": ["1200", "860"],
            "nullRatio": 0.03,
            "uniqueCount": 210
          }
        ],
        "warnings": [],
        "dataRef": "storage://normalized/dataset_abc123/data.geojson"
      }
    ],
    "selectedDatasetIds": ["dataset_abc123"],
    "missingDatasetIds": []
  }
}
```

前端建议处理：

- 用 `datasets` 更新聊天侧“本轮使用的数据”展示。
- 如果 `missingDatasetIds` 非空，提示这些数据集在后端不可用。
- 不要把 `dataRef` 暴露成可下载链接；它是后端内部工具读取完整数据的引用。

### tool.started

当前后端按关键词规则选择工具，不再无条件执行所有工具。

```text
event: tool.started
data: {
  "type": "tool.started",
  "sessionId": "session_demo",
  "toolCallId": "tool_x",
  "data": {
    "toolName": "metadata_search",
    "input": {
      "message": "当前学校点位数据有哪些字段",
      "query": "当前学校点位数据有哪些字段",
      "selectedDatasetIds": ["dataset_abc123"],
      "datasetIds": ["dataset_abc123"],
      "selectedServiceIds": [],
      "metadata": {},
      "dataSummaries": []
    }
  }
}
```

前端建议处理：

- 可在聊天消息下展示“正在查询数据字段”“正在统计属性”等工具状态。
- `input` 主要用于调试，不建议面向普通用户完整展示。

### tool.completed

#### metadata_search 结果

触发意图示例：用户消息包含“字段、图层、数据、属性、有哪些、是什么、field”等关键词。

```text
event: tool.completed
data: {
  "type": "tool.completed",
  "sessionId": "session_demo",
  "toolCallId": "tool_x",
  "data": {
    "toolName": "metadata_search",
    "output": {
      "dataRef": null,
      "summary": {
        "query": "当前学校点位数据有哪些字段",
        "matches": [
          {
            "datasetId": "dataset_abc123",
            "name": "学校点位",
            "geometryType": "Point",
            "featureCount": 328,
            "bbox": [116.1, 39.7, 116.7, 40.1],
            "fields": [
              {"name": "type", "type": "string"},
              {"name": "student_count", "type": "number"}
            ],
            "score": 0.75,
            "reason": "fields matched: type"
          }
        ],
        "datasets": []
      },
      "layer": null,
      "mapCommand": null
    }
  }
}
```

#### attribute_summary 结果

触发意图示例：用户消息包含“统计、数量、分类、占比、平均、总和、求和、汇总、summary、count”等关键词。

如果用户消息中包含字段名，如 `type`，后端会尝试自动作为 `groupBy`。

```text
event: tool.completed
data: {
  "type": "tool.completed",
  "sessionId": "session_demo",
  "toolCallId": "tool_y",
  "data": {
    "toolName": "attribute_summary",
    "output": {
      "dataRef": "storage://normalized/dataset_abc123/data.geojson",
      "summary": {
        "datasetId": "dataset_abc123",
        "name": "学校点位",
        "featureCount": 328,
        "fields": [
          {
            "name": "student_count",
            "count": 318,
            "nullCount": 10,
            "nullRatio": 0.0304,
            "uniqueCount": 210,
            "type": "number",
            "min": 120,
            "max": 3600,
            "mean": 982.4,
            "sum": 312403
          }
        ],
        "groupBy": "type",
        "rows": [
          {
            "type": "小学",
            "count": 120,
            "student_count_sum": 98000
          },
          {
            "type": "中学",
            "count": 80,
            "student_count_sum": 76000
          }
        ]
      },
      "layer": null,
      "mapCommand": null
    }
  }
}
```

前端建议处理：

- `metadata_search` 可用于展示“匹配到的数据集/字段”。
- `attribute_summary.summary.fields` 可渲染字段统计面板。
- `attribute_summary.summary.rows` 可渲染表格、柱状图或饼图。
- `output.dataRef` 仍是后端内部引用，不直接请求。

### tool.failed

工具失败不会中断 SSE。后端会将失败信息传给模型，随后继续发送 `message.delta` 和 `message.completed`。

```text
event: tool.failed
data: {
  "type": "tool.failed",
  "sessionId": "session_demo",
  "toolCallId": "tool_x",
  "data": {
    "toolName": "attribute_summary",
    "error": {
      "code": "TOOL_FAILED",
      "message": "Dataset not found: dataset_missing",
      "recoverable": true,
      "details": null
    }
  }
}
```

前端建议处理：

- 展示轻量失败状态即可，不要关闭 SSE。
- 等待后续助手消息，最终解释应以 `message.completed` 为准。

### message.delta

助手回答增量文本。

```text
event: message.delta
data: {
  "type": "message.delta",
  "sessionId": "session_demo",
  "messageId": "msg_x",
  "toolCallId": null,
  "data": {
    "delta": "当前数据"
  }
}
```

### message.completed

助手消息完成并已持久化。前端应以该事件中的 `message.content` 作为最终文本。

```text
event: message.completed
data: {
  "type": "message.completed",
  "sessionId": "session_demo",
  "messageId": "msg_x",
  "toolCallId": null,
  "data": {
    "message": {
      "id": "msg_x",
      "role": "assistant",
      "content": "当前数据包含 type、student_count 等字段...",
      "createdAt": "2026-06-22T03:58:41.852502+00:00",
      "status": "completed"
    }
  }
}
```

## 2. 当前工具选择规则

后端第一阶段使用规则选择工具，前端不需要直接指定工具。

| 用户消息关键词 | 后端工具 | 作用 |
| --- | --- | --- |
| `字段`、`图层`、`数据`、`属性`、`有哪些`、`是什么`、`field` | `metadata_search` | 查找选中数据集摘要、字段和图层信息 |
| `统计`、`数量`、`分类`、`占比`、`平均`、`总和`、`求和`、`汇总`、`summary`、`count` | `attribute_summary` | 读取完整 GeoJSON 并做属性统计 |

注意：

- 未选择数据集时，`attribute_summary` 不会执行。
- 普通闲聊或不命中关键词的问题，只会发送 `data.summary` 和模型消息事件。
- 如果同时命中元数据和统计意图，后端会依次发送两个工具调用事件。

## 3. 前端 EventSource/fetch 处理建议

如果需要携带 `Authorization` header，原生 `EventSource` 不适合直接使用，建议使用 `fetch` 读取 `ReadableStream` 并按 SSE 格式解析。

伪代码：

```ts
type StreamEvent = {
  type:
    | 'data.summary'
    | 'plan.created'
    | 'tool.started'
    | 'tool.completed'
    | 'tool.failed'
    | 'layer.created'
    | 'map.command'
    | 'chart.created'
    | 'clarification'
    | 'message.delta'
    | 'message.completed'
    | 'error'
    | 'done'
  sessionId: string
  messageId?: string | null
  toolCallId?: string | null
  data: Record<string, unknown>
}

const response = await fetch(`/api/ai-chat/sessions/${sessionId}/messages`, {
  method: 'POST',
  headers: {
    Authorization: `Bearer ${accessToken}`,
    'Content-Type': 'application/json',
    Accept: 'text/event-stream',
  },
  body: JSON.stringify({
    message,
    selectedDatasetIds,
    selectedServiceIds: [],
    metadata: { layers, mapView },
  }),
})

const reader = response.body?.getReader()
const decoder = new TextDecoder()
let buffer = ''

while (reader) {
  const { value, done } = await reader.read()
  if (done) break
  buffer += decoder.decode(value, { stream: true })

  const chunks = buffer.split('\n\n')
  buffer = chunks.pop() ?? ''

  for (const chunk of chunks) {
    const dataLine = chunk.split('\n').find((line) => line.startsWith('data: '))
    if (!dataLine) continue
    const event = JSON.parse(dataLine.slice(6)) as StreamEvent

    switch (event.type) {
      case 'data.summary':
        // 更新本轮数据摘要
        break
      case 'tool.started':
        // 展示工具运行状态
        break
      case 'tool.completed':
        // 渲染字段匹配、统计表格或图表
        break
      case 'tool.failed':
        // 展示可恢复失败状态，继续等待模型消息
        break
      case 'message.delta':
        // 追加 event.data.delta
        break
      case 'message.completed':
        // 使用 event.data.message 作为最终消息
        break
    }
  }
}
```

## 4. 获取会话详情

```http
GET /api/ai-chat/sessions/{sessionId}
Authorization: Bearer <accessToken>
```

### 成功响应

```json
{
  "session": {
    "id": "session_demo",
    "title": "当前学校点位数据有哪些字段",
    "status": "completed",
    "messages": [
      {
        "id": "msg_user",
        "role": "user",
        "content": "当前学校点位数据有哪些字段",
        "createdAt": "...",
        "status": "completed"
      },
      {
        "id": "msg_assistant",
        "role": "assistant",
        "content": "当前数据包含 type、student_count 等字段...",
        "createdAt": "...",
        "status": "completed"
      }
    ],
    "selectedDatasetIds": ["dataset_abc123"],
    "selectedServiceIds": [],
    "dataSummaries": [
      {
        "datasetId": "dataset_abc123",
        "name": "学校点位",
        "sourceType": "upload",
        "geometryType": "Point",
        "crs": "EPSG:4326",
        "featureCount": 328,
        "bbox": [116.1, 39.7, 116.7, 40.1],
        "fields": [],
        "warnings": [],
        "dataRef": "storage://normalized/dataset_abc123/data.geojson"
      }
    ],
    "toolCalls": [
      {
        "id": "tool_x",
        "toolName": "metadata_search",
        "status": "completed",
        "input": {},
        "output": {},
        "error": null,
        "startedAt": "...",
        "finishedAt": "...",
        "durationMs": 12
      }
    ],
    "layers": [],
    "charts": [],
    "sceneActions": [],
    "report": null,
    "createdAt": "...",
    "updatedAt": "..."
  }
}
```

## 5. 错误响应

### 发送消息错误

未登录、token 无效或 token 过期：

```json
{
  "detail": "Unauthorized."
}
```

HTTP 状态码为 `401`。

消息为空时返回 `422` 请求体校验错误。

### 获取会话错误

会话不存在时返回：

```json
{
  "detail": "Chat session not found."
}
```

HTTP 状态码为 `404`。
