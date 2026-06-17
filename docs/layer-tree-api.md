# 用户图层树接口文档

本文档描述 Geo Agent Service 用户私有地图图层树接口，覆盖默认图层树读取、把数据中心数据集加入用户图层、更新节点、移动排序和删除节点。

## 基础信息

- Base URL: `/api`
- 数据格式: JSON
- 认证方式: `Authorization: Bearer <accessToken>`
- 图层树归属: 当前登录用户，用户 ID 来自 `GET /api/auth/me` 返回的 `id`
- 当前默认新增分组: `user-layers`（用户图层）
- 当前不支持: 图层样式编辑、图层共享、回收站、版本历史、直接注册 WMS/WFS/WMTS/XYZ 服务

## 通用模型

### LayerTreeNode

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | 图层树节点 ID |
| `name` | string | 节点展示名称 |
| `type` | string | `folder` 或 `layer` |
| `parentId` | string/null | 父节点 ID；根节点为 `null` |
| `children` | LayerTreeNode[] | 子节点列表；普通图层通常为空数组 |
| `datasetId` | string/null | 数据中心数据集 ID；用户数据图层有值 |
| `sourceType` | string/null | 数据来源，例如 `upload`、`url` |
| `geometryType` | string/null | 几何类型，例如 `Point`、`LineString`、`Polygon`、`Mixed`、`Raster` |
| `bbox` | number[4]/null | 数据范围 `[minX, minY, maxX, maxY]` |
| `iconKey` | string/null | 前端图标映射 key，不是 React icon 组件 |
| `visible` | boolean | 是否默认显示 |
| `opacity` | number | 透明度，范围 `0-1` |
| `userManaged` | boolean | 是否允许当前用户更新、移动、删除 |
| `createdAt` | string | ISO 8601 创建时间 |
| `updatedAt` | string | ISO 8601 更新时间 |

### LayerTreeResponse

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `userId` | string | 当前图层树所属用户 ID |
| `nodes` | LayerTreeNode[] | 根节点列表 |

### 通用未认证响应

未登录、token 缺失、token 无效、token 过期，统一返回：

```http
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Bearer
```

```json
{
  "detail": "Unauthorized."
}
```

## 1. 获取当前用户图层树

首次调用时，如果当前用户还没有保存过图层树，后端会返回并持久化默认图层树。

```http
GET /api/layer-tree
Authorization: Bearer <accessToken>
```

### curl 示例

```bash
curl "http://localhost:8000/api/layer-tree" \
  -H "Authorization: Bearer token-id.signature"
```

### 成功响应

```json
{
  "userId": "default",
  "nodes": [
    {
      "id": "basemap",
      "name": "底图",
      "type": "folder",
      "parentId": null,
      "children": [
        {
          "id": "basemap-imagery",
          "name": "谷歌影像",
          "type": "layer",
          "parentId": "basemap",
          "children": [],
          "datasetId": null,
          "sourceType": null,
          "geometryType": null,
          "bbox": null,
          "iconKey": "satellite",
          "visible": true,
          "opacity": 1,
          "userManaged": false,
          "createdAt": "2026-06-17T10:00:00Z",
          "updatedAt": "2026-06-17T10:00:00Z"
        }
      ],
      "datasetId": null,
      "sourceType": null,
      "geometryType": null,
      "bbox": null,
      "iconKey": "map",
      "visible": true,
      "opacity": 1,
      "userManaged": false,
      "createdAt": "2026-06-17T10:00:00Z",
      "updatedAt": "2026-06-17T10:00:00Z"
    },
    {
      "id": "user-layers",
      "name": "用户图层",
      "type": "folder",
      "parentId": null,
      "children": [],
      "datasetId": null,
      "sourceType": null,
      "geometryType": null,
      "bbox": null,
      "iconKey": "user-round",
      "visible": true,
      "opacity": 1,
      "userManaged": false,
      "createdAt": "2026-06-17T10:00:00Z",
      "updatedAt": "2026-06-17T10:00:00Z"
    }
  ]
}
```

> 示例省略了部分默认分组；实际响应还包含“业务图层”和“分析结果”。

## 2. 添加数据集为用户图层

把数据中心已有数据集加入当前用户图层树。默认追加到 `user-layers` 分组末尾。

```http
POST /api/layer-tree/dataset-layers
Content-Type: application/json
Authorization: Bearer <accessToken>
```

### 请求体

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `datasetId` | string | 是 | 数据中心数据集 ID，来自上传或在线数据注册接口 |
| `name` | string | 否 | 图层展示名称；不传时使用数据集名称 |
| `parentId` | string/null | 否 | 目标父分组 ID；不传时为 `user-layers` |
| `position` | number/null | 否 | 插入位置，从 `0` 开始；不传时追加到末尾 |
| `visible` | boolean | 否 | 是否默认显示，默认 `true` |
| `opacity` | number | 否 | 透明度，范围 `0-1`，默认 `1` |

### curl 示例

```bash
curl -X POST "http://localhost:8000/api/layer-tree/dataset-layers" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer token-id.signature" \
  -d '{
    "datasetId": "dataset_abc123def456",
    "name": "学校点位",
    "visible": true,
    "opacity": 0.8
  }'
```

### 成功响应

返回新创建的 `LayerTreeNode`：

```json
{
  "id": "layer_Nh4yk1a9Tj3upQ9Y",
  "name": "学校点位",
  "type": "layer",
  "parentId": "user-layers",
  "children": [],
  "datasetId": "dataset_abc123def456",
  "sourceType": "upload",
  "geometryType": "Point",
  "bbox": [116.1, 39.7, 116.2, 39.8],
  "iconKey": "map-pinned",
  "visible": true,
  "opacity": 0.8,
  "userManaged": true,
  "createdAt": "2026-06-17T10:00:00Z",
  "updatedAt": "2026-06-17T10:00:00Z"
}
```

### 错误响应

数据集不存在：

```json
{
  "detail": "Dataset not found."
}
```

HTTP 状态码为 `404`。

目标父节点不存在或不是文件夹：

```json
{
  "detail": "Parent layer node is not a folder."
}
```

HTTP 状态码为 `400`。

## 3. 更新用户图层节点

更新用户可管理节点的展示属性。默认系统节点 `userManaged=false`，不能通过该接口更新。

```http
PATCH /api/layer-tree/nodes/{nodeId}
Content-Type: application/json
Authorization: Bearer <accessToken>
```

### Path 参数

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `nodeId` | string | 是 | 图层树节点 ID |

### 请求体

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `name` | string | 否 | 新的展示名称 |
| `visible` | boolean | 否 | 是否显示 |
| `opacity` | number | 否 | 透明度，范围 `0-1` |

### curl 示例

```bash
curl -X PATCH "http://localhost:8000/api/layer-tree/nodes/layer_Nh4yk1a9Tj3upQ9Y" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer token-id.signature" \
  -d '{
    "name": "学校点位",
    "visible": false,
    "opacity": 0.45
  }'
```

### 成功响应

返回更新后的 `LayerTreeNode`。

### 错误响应

节点不存在：

```json
{
  "detail": "Layer node not found."
}
```

HTTP 状态码为 `404`。

默认节点不允许修改：

```json
{
  "detail": "Default layer nodes cannot be modified."
}
```

HTTP 状态码为 `403`。

## 4. 移动用户图层节点

移动用户可管理节点到指定父分组和指定顺序。可用于前端拖拽排序。

```http
POST /api/layer-tree/nodes/{nodeId}/move
Content-Type: application/json
Authorization: Bearer <accessToken>
```

### Path 参数

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `nodeId` | string | 是 | 要移动的节点 ID |

### 请求体

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `parentId` | string/null | 否 | 目标父分组 ID；不传时为 `user-layers` |
| `position` | number | 是 | 插入位置，从 `0` 开始 |

### curl 示例

```bash
curl -X POST "http://localhost:8000/api/layer-tree/nodes/layer_Nh4yk1a9Tj3upQ9Y/move" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer token-id.signature" \
  -d '{
    "parentId": "user-layers",
    "position": 0
  }'
```

### 成功响应

返回移动后的 `LayerTreeNode`。

### 错误响应

节点不存在：

```json
{
  "detail": "Layer node not found."
}
```

HTTP 状态码为 `404`。

默认节点不允许移动：

```json
{
  "detail": "Default layer nodes cannot be modified."
}
```

HTTP 状态码为 `403`。

目标父节点非法：

```json
{
  "detail": "Parent layer node is not a folder."
}
```

HTTP 状态码为 `400`。

移动到自身或自己的子节点：

```json
{
  "detail": "Cannot move a node into itself or its descendants."
}
```

HTTP 状态码为 `400`。

## 5. 删除用户图层节点

删除用户可管理节点。默认系统节点 `userManaged=false`，不能删除。

```http
DELETE /api/layer-tree/nodes/{nodeId}
Authorization: Bearer <accessToken>
```

### Path 参数

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `nodeId` | string | 是 | 要删除的节点 ID |

### curl 示例

```bash
curl -X DELETE "http://localhost:8000/api/layer-tree/nodes/layer_Nh4yk1a9Tj3upQ9Y" \
  -H "Authorization: Bearer token-id.signature"
```

### 成功响应

```http
HTTP/1.1 204 No Content
```

响应体为空。

### 错误响应

节点不存在：

```json
{
  "detail": "Layer node not found."
}
```

HTTP 状态码为 `404`。

默认节点不允许删除：

```json
{
  "detail": "Default layer nodes cannot be modified."
}
```

HTTP 状态码为 `403`。

## 前端对接流程建议

1. 登录后保存 `accessToken`。
2. 页面初始化调用 `GET /api/layer-tree` 渲染图层树。
3. 用户在数据中心上传本地 GeoJSON 或注册在线 GeoJSON，拿到 `datasetId`。
4. 调用 `POST /api/layer-tree/dataset-layers` 把 `datasetId` 加入图层树。
5. 使用返回节点的 `datasetId` 调用 `GET /api/datasets/{datasetId}/preview` 获取地图预览数据。
6. 节点重命名、显隐和透明度变化时调用 `PATCH /api/layer-tree/nodes/{nodeId}`。
7. 拖拽排序完成后调用 `POST /api/layer-tree/nodes/{nodeId}/move`。
8. 删除用户图层时调用 `DELETE /api/layer-tree/nodes/{nodeId}`。

## 前端字段约定

- `iconKey` 由前端映射到图标组件，例如 `map`、`satellite`、`layers`、`user-round`、`map-pinned`、`route`。
- `userManaged=false` 的节点应在前端禁用重命名、移动、删除操作。
- `datasetId` 有值的节点代表可加载数据中心数据；`datasetId=null` 的默认节点可能需要前端按内置图层能力处理。
- `position` 超过目标分组子节点数量时，后端会按追加到末尾处理。

## 存储配置

图层树默认存储根目录由配置 `layer_tree_storage_root` 控制，默认值：

```txt
data/layer-trees
```

每个用户会保存独立 JSON 文件。当前实现为轻量本地持久化，后续可切换到数据库存储并保持 API 契约不变。
