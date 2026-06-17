# 用户权限接口文档

本文档描述 Geo Agent Service 轻量用户权限接口，覆盖登录、获取当前用户信息、更新用户基本信息和退出登录。

## 基础信息

- Base URL: `/api`
- 数据格式: JSON
- 认证方式: `Authorization: Bearer <accessToken>`
- 当前用户模式: 单默认用户
- 当前不支持: 注册、多用户、角色权限、刷新 token、找回密码

## 通用模型

### UserProfile

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | 用户 ID，默认用户固定为 `default` |
| `username` | string | 登录用户名，不允许通过资料接口修改 |
| `nickname` | string | 用户昵称 |
| `email` | string \| null | 邮箱 |
| `avatarUrl` | string \| null | 头像 URL |

### 通用未认证响应

未登录、token 缺失、token 无效、token 过期、已退出登录后继续使用旧 token，统一返回：

```http
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Bearer
```

```json
{
  "detail": "Unauthorized."
}
```

## 1. 登录

使用默认用户账号密码登录。登录成功后会返回新的访问令牌；由于当前只允许单个活跃会话，重新登录会使旧 token 失效。

```http
POST /api/auth/login
Content-Type: application/json
```

### 请求体

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `username` | string | 是 | 登录用户名，默认来自 `AUTH_USERNAME` |
| `password` | string | 是 | 登录密码，默认来自 `AUTH_PASSWORD` |

### curl 示例

```bash
curl -X POST "http://localhost:8000/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "admin",
    "password": "admin"
  }'
```

### 成功响应

```json
{
  "accessToken": "token-id.signature",
  "tokenType": "bearer",
  "expiresIn": 86400,
  "user": {
    "id": "default",
    "username": "admin",
    "nickname": "admin",
    "email": null,
    "avatarUrl": null
  }
}
```

### 响应字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `accessToken` | string | Bearer token，前端需保存并用于后续接口 |
| `tokenType` | string | 固定为 `bearer` |
| `expiresIn` | number | token 有效期，单位秒 |
| `user` | UserProfile | 当前用户信息 |

### 错误响应

用户名或密码错误：

```json
{
  "detail": "Unauthorized."
}
```

HTTP 状态码为 `401`。

## 2. 获取当前用户信息

根据 Bearer token 获取当前用户资料。

```http
GET /api/auth/me
Authorization: Bearer <accessToken>
```

### curl 示例

```bash
curl "http://localhost:8000/api/auth/me" \
  -H "Authorization: Bearer token-id.signature"
```

### 成功响应

```json
{
  "id": "default",
  "username": "admin",
  "nickname": "admin",
  "email": null,
  "avatarUrl": null
}
```

### 错误响应

未携带 token、token 无效或 token 已过期时返回通用 `401` 未认证响应。

## 3. 更新当前用户信息

更新当前用户基本信息。`username` 不允许修改；请求体中未传的字段保持原值。

```http
PUT /api/auth/me
Content-Type: application/json
Authorization: Bearer <accessToken>
```

### 请求体

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `nickname` | string | 否 | 用户昵称 |
| `email` | string \| null | 否 | 邮箱；传 `null` 可清空 |
| `avatarUrl` | string \| null | 否 | 头像 URL；传 `null` 可清空 |

### curl 示例

```bash
curl -X PUT "http://localhost:8000/api/auth/me" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer token-id.signature" \
  -d '{
    "nickname": "Geo Admin",
    "email": "admin@example.com",
    "avatarUrl": "https://example.com/avatar.png"
  }'
```

### 成功响应

```json
{
  "id": "default",
  "username": "admin",
  "nickname": "Geo Admin",
  "email": "admin@example.com",
  "avatarUrl": "https://example.com/avatar.png"
}
```

### 错误响应

未携带 token、token 无效或 token 已过期时返回通用 `401` 未认证响应。

## 4. 退出登录

使当前 Bearer token 失效。

```http
POST /api/auth/logout
Authorization: Bearer <accessToken>
```

### curl 示例

```bash
curl -X POST "http://localhost:8000/api/auth/logout" \
  -H "Authorization: Bearer token-id.signature"
```

### 成功响应

```http
HTTP/1.1 204 No Content
```

响应体为空。

### 错误响应

未携带 token、token 无效或 token 已过期时返回通用 `401` 未认证响应。

## 前端对接建议

- 登录成功后保存 `accessToken`，并在后续受保护接口中追加请求头：`Authorization: Bearer ${accessToken}`。
- 收到任意 auth 接口的 `401` 时，前端应清理本地 token，并跳转到登录页或展示登录弹窗。
- 由于重新登录会使旧 token 失效，多标签页或多人共用默认账号时，后登录的一方会挤掉旧会话。
- `expiresIn` 单位为秒，可用于前端设置本地过期时间；最终以服务端校验结果为准。
