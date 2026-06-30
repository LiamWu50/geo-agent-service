# AI 聊天 GIS 工具链前端对接文档

本文档面向前端对接 `geo-agent-service` 的 AI 聊天与 GIS 工具链。当前后端通过一个 SSE 聊天接口完成：数据摘要注入、元数据查询、属性统计、空间处理、属性筛选、生成结果图层和地图命令。

## 1. 基础信息

- Base URL: `/api`
- AI 聊天认证方式: `Authorization: Bearer <accessToken>`
- 聊天接口: `POST /api/ai-chat/sessions/{sessionId}/messages`
- 响应格式: `text/event-stream`
- 会话详情: `GET /api/ai-chat/sessions/{sessionId}`
- 数据集预览: `GET /api/datasets/{datasetId}/preview?limit=100`

未配置 `QWEN_API_KEY` 时，接口仍会返回流式消息，便于前端联调认证、SSE 解析和工具事件。

## 2. 请求格式

```http
POST /api/ai-chat/sessions/{sessionId}/messages
Content-Type: application/json
Accept: text/event-stream
Authorization: Bearer <accessToken>
```

```json
{
  "message": "筛选 type 等于 school 的要素并显示",
  "selectedDatasetIds": ["dataset_abc123"],
  "selectedServiceIds": [],
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

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `message` | string | 是 | 用户自然语言消息，不能为空 |
| `selectedDatasetIds` | string[] | 否 | 当前参与分析的数据集 ID，工具主要读取这里的数据 |
| `selectedServiceIds` | string[] | 否 | 当前选中的地图服务 ID，现阶段主要透传 |
| `metadata.layers` | object[] | 否 | 当前地图图层上下文，用于模型理解用户指代 |
| `metadata.mapView` | object | 否 | 当前地图视图；`bbox_clip` 可直接使用 `mapView.bbox` |

前端注意：

- `selectedDatasetIds` 至少传一个，属性统计、空间处理、属性筛选才会执行。
- 当前 AI 聊天接口需要登录 token；数据集接口现阶段未强制鉴权，前端统一带 token 也可以。
- `dataRef` 是后端内部引用，不要暴露成下载链接。
- 原生 `EventSource` 不能方便携带 `Authorization` header，建议用 `fetch` + `ReadableStream` 解析 SSE。

## 3. SSE 事件

每个事件由 `event:` 和 `data:` 组成，`data` 是 JSON：

```ts
type StreamEvent = {
  type:
    | 'data.summary'
    | 'tool.started'
    | 'tool.completed'
    | 'tool.failed'
    | 'layer.created'
    | 'map.command'
    | 'message.delta'
    | 'message.completed'
    | 'error'
    | 'plan.created'
    | 'chart.created'
    | 'clarification'
    | 'done'
  sessionId: string
  messageId?: string | null
  toolCallId?: string | null
  data: Record<string, unknown>
}
```

典型顺序：

```text
data.summary
tool.started
tool.completed
layer.created      # 仅 geoprocess 生成结果图层时出现
map.command        # 仅 geoprocess 生成地图命令时出现
message.delta
message.completed
```

工具失败但请求仍可恢复时：

```text
data.summary
tool.started
tool.failed
message.delta
message.completed
```

### data.summary

后端已加载本轮可用数据摘要。前端可用它更新“本轮使用的数据”状态，并提示找不到的数据集。

```json
{
  "type": "data.summary",
  "sessionId": "session_demo",
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
            "sampleValues": ["school", "hospital"],
            "nullRatio": 0,
            "uniqueCount": 2
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

### tool.started

工具开始执行。前端建议显示轻量状态，例如“正在筛选要素”“正在生成缓冲区”。

```json
{
  "type": "tool.started",
  "sessionId": "session_demo",
  "toolCallId": "tool_x",
  "data": {
    "toolName": "geoprocess",
    "input": {
      "message": "筛选 type 等于 school 的要素并显示",
      "selectedDatasetIds": ["dataset_abc123"],
      "datasetIds": ["dataset_abc123"],
      "operation": "attribute_filter",
      "field": "type"
    }
  }
}
```

### tool.completed

工具成功返回。不同工具的 `output.summary` 结构不同，但外层一致：

```json
{
  "dataRef": "storage://normalized/dataset_result/data.geojson",
  "summary": {},
  "layer": null,
  "mapCommand": null
}
```

### layer.created

`geoprocess` 成功生成新 dataset 时发送。前端可先记录结果图层元信息。

```json
{
  "type": "layer.created",
  "sessionId": "session_demo",
  "toolCallId": "tool_x",
  "data": {
    "id": "layer_dataset_result",
    "datasetId": "dataset_result",
    "name": "学校点位 属性筛选",
    "geometryType": "Point",
    "dataRef": "storage://normalized/dataset_result/data.geojson",
    "bbox": [116.1, 39.7, 116.3, 39.9],
    "source": {
      "type": "dataset",
      "datasetId": "dataset_result"
    },
    "metadata": {
      "sourceDatasetId": "dataset_abc123",
      "operation": "attribute_filter"
    }
  }
}
```

### map.command

前端应执行的地图动作。当前 `geoprocess` 返回 `layer.addDataset`：

```json
{
  "type": "map.command",
  "sessionId": "session_demo",
  "toolCallId": "tool_x",
  "data": {
    "action": "layer.addDataset",
    "datasetId": "dataset_result",
    "name": "学校点位 属性筛选",
    "visible": true,
    "flyTo": true
  }
}
```

建议前端处理：

1. 收到 `layer.addDataset` 后，用 `datasetId` 调用 `/api/datasets/{datasetId}/preview` 获取 GeoJSON。
2. 把 preview 返回的 `data` 加到地图图层。
3. 使用 `name` 作为图层名。
4. `visible: true` 时默认显示。
5. `flyTo: true` 时按 preview 或 layer 的 `bbox` 定位地图。

### tool.failed

工具失败不会中断 SSE。前端展示失败状态，然后继续等待助手消息。

```json
{
  "type": "tool.failed",
  "sessionId": "session_demo",
  "toolCallId": "tool_x",
  "data": {
    "toolName": "geoprocess",
    "error": {
      "code": "TOOL_FAILED",
      "message": "attribute_filter operation requires a field.",
      "recoverable": true,
      "details": null
    }
  }
}
```

## 4. 当前工具能力和触发词

后端当前使用关键词规则选择工具，前端不需要指定工具名。

| 用户消息关键词 | 工具 | 作用 |
| --- | --- | --- |
| `字段`、`图层`、`数据`、`属性`、`有哪些`、`是什么`、`field` | `metadata_search` | 查询选中数据集摘要、字段和图层信息 |
| `统计`、`数量`、`分类`、`占比`、`平均`、`总和`、`求和`、`汇总`、`summary`、`count` | `attribute_summary` | 读取完整 GeoJSON 并做字段统计 |
| `缓冲`、`buffer`、`附近` | `geoprocess(buffer)` | 生成缓冲区结果 dataset |
| `中心点`、`质心`、`centroid` | `geoprocess(centroid)` | 生成中心点结果 dataset |
| `裁剪`、`范围`、`bbox`、`当前视图` | `geoprocess(bbox_clip)` | 按 bbox 裁剪生成结果 dataset |
| `筛选`、`过滤`、`filter`、`等于`、`不等于`、`大于`、`小于`、`超过`、`低于`、`包含` | `geoprocess(attribute_filter)` | 按属性条件筛选要素并生成结果 dataset |

注意：

- 多个关键词可能触发多个工具，例如“有哪些字段并按 type 统计数量”会依次执行 `metadata_search` 和 `attribute_summary`。
- `geoprocess` 操作会生成新 dataset，并返回 `layer.created` 和 `map.command`。
- `buffer` 要求源数据有 CRS；如果数据缺 CRS，会返回 `tool.failed`。

## 5. 工具输出示例

### metadata_search

```json
{
  "toolName": "metadata_search",
  "output": {
    "dataRef": null,
    "summary": {
      "query": "当前数据有哪些字段",
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
```

### attribute_summary

```json
{
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
          "type": "school",
          "count": 120,
          "student_count_sum": 98000
        }
      ]
    },
    "layer": null,
    "mapCommand": null
  }
}
```

### geoprocess: buffer

触发消息示例：

```text
给学校点位做 500 米缓冲区并显示
```

```json
{
  "toolName": "geoprocess",
  "output": {
    "dataRef": "storage://normalized/dataset_result/data.geojson",
    "summary": {
      "sourceDatasetId": "dataset_abc123",
      "resultDatasetId": "dataset_result",
      "operation": "buffer",
      "featureCount": 328,
      "bbox": [116.09, 39.69, 116.71, 40.11],
      "result": {
        "datasetId": "dataset_result",
        "name": "学校点位 缓冲区",
        "sourceType": "generated",
        "geometryType": "Polygon",
        "featureCount": 328,
        "dataRef": "storage://normalized/dataset_result/data.geojson"
      }
    },
    "layer": {
      "id": "layer_dataset_result",
      "datasetId": "dataset_result",
      "name": "学校点位 缓冲区",
      "geometryType": "Polygon"
    },
    "mapCommand": {
      "action": "layer.addDataset",
      "datasetId": "dataset_result",
      "name": "学校点位 缓冲区",
      "visible": true,
      "flyTo": true
    }
  }
}
```

### geoprocess: attribute_filter

触发消息示例：

```text
筛选 type 等于 school 的要素并显示
筛选 population 大于 10000 的城市
筛选 name 包含 Beijing 的要素
```

支持的操作符：

| 自然语言/符号 | 后端 operator | 说明 |
| --- | --- | --- |
| `等于`、`=`、`==` | `eq` | 等于 |
| `不等于`、`!=`、`<>` | `ne` | 不等于 |
| `大于`、`超过`、`>` | `gt` | 大于 |
| `大于等于`、`不小于`、`>=` | `gte` | 大于等于 |
| `小于`、`低于`、`<` | `lt` | 小于 |
| `小于等于`、`不大于`、`<=` | `lte` | 小于等于 |
| `包含`、`含有` | `contains` | 字符串包含，忽略大小写 |
| 显式 payload `operator: "in"` | `in` | 多值匹配 |

```json
{
  "toolName": "geoprocess",
  "output": {
    "dataRef": "storage://normalized/dataset_result/data.geojson",
    "summary": {
      "sourceDatasetId": "dataset_abc123",
      "resultDatasetId": "dataset_result",
      "operation": "attribute_filter",
      "featureCount": 120,
      "bbox": [116.1, 39.7, 116.5, 40.0],
      "filter": {
        "field": "type",
        "operator": "eq",
        "value": "school"
      },
      "result": {
        "datasetId": "dataset_result",
        "name": "学校点位 属性筛选",
        "sourceType": "generated",
        "geometryType": "Point",
        "featureCount": 120,
        "dataRef": "storage://normalized/dataset_result/data.geojson"
      }
    },
    "layer": {
      "id": "layer_dataset_result",
      "datasetId": "dataset_result",
      "name": "学校点位 属性筛选",
      "geometryType": "Point"
    },
    "mapCommand": {
      "action": "layer.addDataset",
      "datasetId": "dataset_result",
      "name": "学校点位 属性筛选",
      "visible": true,
      "flyTo": true
    }
  }
}
```

## 6. 前端 SSE 解析参考

```ts
async function streamAiChat(params: {
  baseUrl: string
  accessToken: string
  sessionId: string
  message: string
  selectedDatasetIds: string[]
  metadata?: Record<string, unknown>
  onEvent: (event: StreamEvent) => void
}) {
  const response = await fetch(
    `${params.baseUrl}/api/ai-chat/sessions/${params.sessionId}/messages`,
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${params.accessToken}`,
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
      },
      body: JSON.stringify({
        message: params.message,
        selectedDatasetIds: params.selectedDatasetIds,
        selectedServiceIds: [],
        metadata: params.metadata ?? {},
      }),
    },
  )

  if (!response.ok || !response.body) {
    throw new Error(`AI chat request failed: ${response.status}`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    const chunks = buffer.split('\n\n')
    buffer = chunks.pop() ?? ''

    for (const chunk of chunks) {
      const dataLine = chunk.split('\n').find((line) => line.startsWith('data: '))
      if (!dataLine) continue
      params.onEvent(JSON.parse(dataLine.slice(6)) as StreamEvent)
    }
  }
}
```

地图命令处理参考：

```ts
async function handleMapCommand(event: StreamEvent, accessToken: string) {
  if (event.type !== 'map.command') return
  const command = event.data as {
    action?: string
    datasetId?: string
    name?: string
    visible?: boolean
    flyTo?: boolean
  }

  if (command.action !== 'layer.addDataset' || !command.datasetId) return

  const response = await fetch(`/api/datasets/${command.datasetId}/preview?limit=1000`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  })
  const preview = await response.json()

  // TODO: 按前端地图引擎实现：
  // addGeoJsonLayer({
  //   id: `layer_${command.datasetId}`,
  //   name: command.name,
  //   data: preview.data,
  //   visible: command.visible ?? true,
  // })
  // if (command.flyTo && preview.bbox) flyToBbox(preview.bbox)
}
```

## 7. 联调测试流程

### 7.1 启动后端

```bash
uvicorn geo_agent_service.main:app --reload --host 0.0.0.0 --port 8000
```

如果使用项目虚拟环境：

```bash
.venv/bin/python -m uvicorn geo_agent_service.main:app --reload --host 0.0.0.0 --port 8000
```

### 7.2 登录获取 token

```bash
curl -s -X POST "http://localhost:8000/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"secret"}'
```

保存返回的 `accessToken`。

### 7.3 上传测试 GeoJSON

准备 `schools.geojson`：

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "properties": {"name": "A School", "type": "school", "student_count": 100},
      "geometry": {"type": "Point", "coordinates": [116.1, 39.7]}
    },
    {
      "type": "Feature",
      "properties": {"name": "B Hospital", "type": "hospital", "student_count": 0},
      "geometry": {"type": "Point", "coordinates": [116.2, 39.8]}
    },
    {
      "type": "Feature",
      "properties": {"name": "C School", "type": "school", "student_count": 150},
      "geometry": {"type": "Point", "coordinates": [116.3, 39.9]}
    }
  ]
}
```

上传：

```bash
curl -s -X POST "http://localhost:8000/api/datasets" \
  -H "Authorization: Bearer <accessToken>" \
  -F "name=schools" \
  -F "file=@schools.geojson;type=application/geo+json"
```

记录返回的 `datasetId`。

### 7.4 测试字段查询

```bash
curl -N -X POST "http://localhost:8000/api/ai-chat/sessions/session_fields/messages" \
  -H "Authorization: Bearer <accessToken>" \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '{
    "message": "当前数据有哪些字段",
    "selectedDatasetIds": ["<datasetId>"],
    "metadata": {}
  }'
```

预期：

- 收到 `data.summary`
- 收到 `tool.started` / `tool.completed`
- `toolName` 为 `metadata_search`

### 7.5 测试属性统计

```bash
curl -N -X POST "http://localhost:8000/api/ai-chat/sessions/session_stats/messages" \
  -H "Authorization: Bearer <accessToken>" \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '{
    "message": "按 type 统计数量和 student_count 总和",
    "selectedDatasetIds": ["<datasetId>"],
    "metadata": {}
  }'
```

预期：

- `toolName` 为 `attribute_summary`
- `summary.groupBy` 为 `type`
- `summary.rows` 中有 `school`、`hospital`

### 7.6 测试属性筛选并显示

```bash
curl -N -X POST "http://localhost:8000/api/ai-chat/sessions/session_filter/messages" \
  -H "Authorization: Bearer <accessToken>" \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '{
    "message": "筛选 type 等于 school 的要素并显示",
    "selectedDatasetIds": ["<datasetId>"],
    "metadata": {}
  }'
```

预期事件顺序：

```text
data.summary
tool.started
tool.completed
layer.created
map.command
message.delta
message.completed
```

检查点：

- `toolName` 为 `geoprocess`
- `output.summary.operation` 为 `attribute_filter`
- `output.summary.filter.field` 为 `type`
- `output.summary.filter.operator` 为 `eq`
- `output.summary.filter.value` 为 `school`
- `output.summary.resultDatasetId` 是新生成的数据集 ID
- `map.command.data.action` 为 `layer.addDataset`

然后预览结果：

```bash
curl -s "http://localhost:8000/api/datasets/<resultDatasetId>/preview?limit=1000" \
  -H "Authorization: Bearer <accessToken>"
```

预期：

- `featureCount` 为 2
- 返回的 GeoJSON 只包含 `type = school` 的要素

### 7.7 测试 bbox 裁剪

```bash
curl -N -X POST "http://localhost:8000/api/ai-chat/sessions/session_clip/messages" \
  -H "Authorization: Bearer <accessToken>" \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '{
    "message": "按当前视图范围裁剪并显示",
    "selectedDatasetIds": ["<datasetId>"],
    "metadata": {
      "mapView": {
        "bbox": [116.05, 39.65, 116.25, 39.85],
        "crs": "EPSG:4326"
      }
    }
  }'
```

预期：

- `operation` 为 `bbox_clip`
- 返回 `layer.created` 和 `map.command`
- 预览结果只包含 bbox 内要素

### 7.8 测试缓冲区

注意：`buffer` 要求数据有 CRS。测试数据如果上传时没有 CRS，可能返回 `tool.failed`，这是预期保护。

```bash
curl -N -X POST "http://localhost:8000/api/ai-chat/sessions/session_buffer/messages" \
  -H "Authorization: Bearer <accessToken>" \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '{
    "message": "给 schools 做 500 米缓冲区并显示",
    "selectedDatasetIds": ["<datasetId>"],
    "metadata": {}
  }'
```

预期：

- 如果源数据有 CRS：生成 Polygon/MultiPolygon 结果图层
- 如果源数据缺 CRS：收到 `tool.failed`，错误信息包含 `buffer operation requires a dataset CRS`

## 8. 前端验收清单

- 能用 `fetch` 正确解析 SSE 多事件流。
- 能展示 `data.summary` 中的真实字段、bbox、featureCount。
- 能展示工具运行、成功、失败状态。
- 能处理 `attribute_summary` 的字段统计和 `rows`。
- 能在收到 `map.command: layer.addDataset` 后拉取 preview 并添加 GeoJSON 图层。
- 属性筛选后地图只显示筛选结果，不覆盖源图层，除非产品交互明确要求替换。
- `tool.failed` 不会中断聊天 UI，后续 `message.completed` 仍能正常展示。
- `missingDatasetIds` 非空时有清晰提示。

## 9. 后端测试命令

后端当前覆盖这些能力的测试：

```bash
.venv/bin/python -m pytest tests/test_geoprocess_tool.py tests/test_ai_chat_api.py
```

全量验证：

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy src/geo_agent_service
.venv/bin/python -m pytest
```

当前通过结果：

- `ruff check .`: pass
- `mypy src/geo_agent_service`: pass
- `pytest`: 42 passed，存在一个测试客户端 deprecation warning
