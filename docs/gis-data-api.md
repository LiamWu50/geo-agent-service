# GIS 数据接入接口文档

本文档描述 Geo Agent Service 第一阶段 GIS 数据接入接口，覆盖 GeoJSON 上传、数据集查询、数据预览和 `dataRef` 约定。

## 基础信息

- Base URL: `/api`
- 数据格式: JSON
- 上传格式: `multipart/form-data`
- 当前支持文件: `.geojson`、`.json`
- 当前支持在线数据: GeoJSON URL
- 当前不支持: Shapefile zip、PostGIS 图层、WMS/WFS/WMTS/XYZ 服务注册

## 1. 上传 GIS 数据集

上传 GeoJSON 文件，后端保存原始文件，生成规范化 GeoJSON，返回数据摘要。

```http
POST /api/datasets
Content-Type: multipart/form-data
```

### Form Data

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `file` | File | 是 | `.geojson` 或 `.json` 文件 |
| `name` | string | 否 | 数据集展示名称；不传时使用文件名 |

### curl 示例

```bash
curl -X POST "http://localhost:8000/api/datasets" \
  -F "name=schools" \
  -F "file=@./schools.geojson"
```

### 成功响应

```json
{
  "datasetId": "dataset_abc123def456",
  "name": "schools",
  "sourceType": "upload",
  "geometryType": "Point",
  "crs": "EPSG:4326",
  "featureCount": 2,
  "bbox": [116.1, 39.7, 116.2, 39.8],
  "fields": [
    {
      "name": "students",
      "type": "number",
      "sampleValues": ["120", "80"],
      "nullRatio": 0,
      "uniqueCount": 2
    }
  ],
  "warnings": [],
  "dataRef": "storage://normalized/dataset_abc123def456/data.geojson"
}
```

### 错误响应

不支持的文件扩展名：

```json
{
  "detail": "Only .geojson and .json uploads are supported."
}
```

不可读取的 GeoJSON：

```json
{
  "detail": "Uploaded file is not a readable GeoJSON dataset."
}
```

## 2. 注册在线 GeoJSON 数据集

提交在线 GeoJSON 地址，后端会下载一份快照到受控存储目录，解析并返回统一数据摘要。

```http
POST /api/datasets/from-url
Content-Type: application/json
```

### 请求体

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `url` | string | 是 | 在线 GeoJSON 地址，仅支持 `http` / `https` |
| `name` | string | 否 | 数据集展示名称；不传时使用 URL 文件名 |

### curl 示例

```bash
curl -X POST "http://localhost:8000/api/datasets/from-url" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "schools",
    "url": "https://example.com/schools.geojson"
  }'
```

### 成功响应

```json
{
  "datasetId": "dataset_abc123def456",
  "name": "schools",
  "sourceType": "url",
  "geometryType": "Point",
  "crs": "EPSG:4326",
  "featureCount": 2,
  "bbox": [116.1, 39.7, 116.2, 39.8],
  "fields": [],
  "warnings": [],
  "dataRef": "storage://normalized/dataset_abc123def456/data.geojson"
}
```

### 错误响应

远程地址不可访问：

```json
{
  "detail": "Unable to download GeoJSON from URL."
}
```

远程响应不是 JSON / GeoJSON：

```json
{
  "detail": "URL must return a GeoJSON or JSON response."
}
```

远程文件为空：

```json
{
  "detail": "Downloaded GeoJSON file is empty."
}
```

## 3. 查询数据集列表

返回当前本地元数据索引中的所有数据集摘要，按创建时间倒序排列。

```http
GET /api/datasets
```

### curl 示例

```bash
curl "http://localhost:8000/api/datasets"
```

### 成功响应

```json
{
  "datasets": [
    {
      "datasetId": "dataset_abc123def456",
      "name": "schools",
      "sourceType": "upload",
      "geometryType": "Point",
      "crs": "EPSG:4326",
      "featureCount": 2,
      "bbox": [116.1, 39.7, 116.2, 39.8],
      "fields": [],
      "warnings": [],
      "dataRef": "storage://normalized/dataset_abc123def456/data.geojson"
    }
  ]
}
```

## 4. 查询单个数据集

根据 `datasetId` 查询数据集摘要。

```http
GET /api/datasets/{datasetId}
```

### Path 参数

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `datasetId` | string | 是 | 上传接口返回的 `datasetId` |

### curl 示例

```bash
curl "http://localhost:8000/api/datasets/dataset_abc123def456"
```

### 成功响应

返回 `InputDataSummary`。

### 错误响应

数据集不存在：

```json
{
  "detail": "Dataset not found."
}
```

HTTP 状态码为 `404`。

## 5. 查询数据预览

返回数据集的 GeoJSON 预览。该接口用于前端地图轻量展示，不应用于拉取大型完整数据。

```http
GET /api/datasets/{datasetId}/preview?limit=100
```

### Path 参数

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `datasetId` | string | 是 | 上传接口返回的 `datasetId` |

### Query 参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `limit` | number | 否 | `100` | 返回的最大 Feature 数量，范围 `1-1000` |

### curl 示例

```bash
curl "http://localhost:8000/api/datasets/dataset_abc123def456/preview?limit=50"
```

### 成功响应

```json
{
  "datasetId": "dataset_abc123def456",
  "bbox": [116.1, 39.7, 116.2, 39.8],
  "featureCount": 2,
  "returnedFeatureCount": 2,
  "data": {
    "type": "FeatureCollection",
    "features": [
      {
        "type": "Feature",
        "properties": {
          "name": "School A",
          "students": 120
        },
        "geometry": {
          "type": "Point",
          "coordinates": [116.1, 39.7]
        }
      }
    ]
  }
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

## 数据模型

### InputDataSummary

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `datasetId` | string | 数据集唯一 ID |
| `name` | string | 数据集名称 |
| `sourceType` | string | 当前支持 `upload`、`url` |
| `geometryType` | string/null | `Point`、`LineString`、`Polygon`、`MultiPoint`、`MultiLineString`、`MultiPolygon`、`Mixed`、`Raster` |
| `crs` | string/null | 坐标参考系，例如 `EPSG:4326` |
| `featureCount` | number/null | 要素总数 |
| `bbox` | number[4]/null | `[minX, minY, maxX, maxY]` |
| `fields` | FieldSummary[] | 属性字段摘要 |
| `warnings` | string[] | 解析或分析风险提示 |
| `dataRef` | string | 后端内部数据引用 |

### FieldSummary

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `name` | string | 字段名 |
| `type` | string | `string`、`number`、`boolean`、`date`、`unknown` |
| `sampleValues` | string[] | 最多 5 个非空样例值 |
| `nullRatio` | number/null | 空值比例，范围 `0-1` |
| `uniqueCount` | number/null | 非空唯一值数量 |

## 存储与 dataRef 约定

默认存储根目录由配置 `gis_storage_root` 控制，默认值：

```txt
data/gis
```

当前文件组织：

```txt
data/gis/uploads/{datasetId}/source.geojson
data/gis/normalized/{datasetId}/data.geojson
data/gis/metadata/datasets.json
```

第一阶段 `dataRef` 格式：

```txt
storage://normalized/{datasetId}/data.geojson
```

前端不要解析或拼接 `dataRef` 对应的真实文件路径。`dataRef` 只作为后端 GIS 工具和后续 Agent 工作流读取真实数据的引用。

## 前端对接建议

1. 上传前只做轻量校验：扩展名、文件大小、是否选择文件。
2. 在线 URL 注册时，前端只提交 URL；由后端下载和解析数据。
3. 上传或 URL 注册成功后保存返回的 `InputDataSummary` 到数据上下文面板。
4. 地图预览使用 `GET /api/datasets/{datasetId}/preview`，不要用响应里的 `dataRef` 直接取文件。
5. 如果 `warnings` 非空，在数据详情里展示给用户。
6. 后续 Agent 请求只传 `datasetId`，不要传完整 GeoJSON。
