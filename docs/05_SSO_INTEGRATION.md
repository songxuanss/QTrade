# QTrade 接入 `../sso` 方案

## 1. 基本结论

`../sso` 是 OIDC / OAuth 2.1 Provider，要求所有客户端使用 PKCE，并支持 Token Introspection 与 Back-channel Logout。QTrade 不实现密码登录、注册、找回密码、MFA、用户会话中枢，这些能力由 SSO 提供。

QTrade 仍需要实现：

- Authorization Code + PKCE 客户端流程。
- 本地会话或 BFF 会话。
- 用户首次登录后的 workspace / membership 初始化。
- 角色、权限与业务审计。
- Back-channel Logout。
- 退出登录与 SSO 会话联动。

## 2. 端点

根据 `../sso` 实现与示例，需使用：

| 用途 | 端点 |
| --- | --- |
| Discovery | `GET {SSO_ISSUER}/.well-known/openid-configuration` |
| Authorize | `GET {SSO_ISSUER}/auth/authorize` |
| Token | `POST {SSO_ISSUER}/oidc/token` |
| JWKS | `GET {SSO_ISSUER}/jwks.json` |
| UserInfo | `GET/POST {SSO_ISSUER}/oidc/userinfo` |
| Introspection | `POST {SSO_ISSUER}/oidc/introspect` |
| Revocation | `POST {SSO_ISSUER}/oidc/revoke` |
| Logout | `GET/POST {SSO_ISSUER}/oidc/logout` |

## 3. 推荐架构：BFF / Confidential Client

不要把 `client_secret` 和 access token 放在浏览器。建议由 Next.js 服务端或 QTrade API 充当 BFF：

```text
Browser
  -> QTrade BFF /auth/login
  -> SSO /auth/authorize?...PKCE...
  -> SSO login / consent
  -> QTrade /auth/callback?code&state
  -> QTrade server exchanges code + verifier
  -> verify id_token + nonce
  -> fetch userinfo
  -> create server-side local session
  -> set HttpOnly SameSite=Lax cookie
```

优点：

- secret 不进入浏览器。
- token 可由服务端持有并轮换。
- 易于实现 CSRF 和安全 cookie。
- API 权限在后端统一执行。

## 4. 授权参数

最小示例：

```http
GET /auth/authorize?
  client_id=QTRADE_CLIENT_ID
  &redirect_uri=https://qtrade.example.com/auth/callback
  &response_type=code
  &scope=openid%20email%20profile
  &state=...
  &nonce=...
  &code_challenge=...
  &code_challenge_method=S256
```

注意：

- `redirect_uri` 必须与 SSO 管理台注册值完全一致。
- `state` 与 `nonce` 必须随机且一次性使用。
- PKCE verifier 使用高熵随机值。
- ID token 校验 `iss`、`aud`、`exp`、`iat`、`nonce`，并使用 JWKS 验签。

## 5. QTrade 会话

建议存储：

| 字段 | 说明 |
| --- | --- |
| `session_id` | 随机 opaque ID，HttpOnly cookie |
| `sso_sub` | SSO 用户稳定 ID |
| `email` / `name` | 来自 UserInfo |
| `workspace_memberships` | 本地授权 |
| `access_token` / `refresh_token` | 服务端加密或仅保留必要期限 |
| `expires_at` | 会话过期时间 |
| `id_token` | 用于 RP-initiated logout |

QTrade 权限不应写入浏览器可解码的长期 token，敏感操作每次由后端重新判断。

## 6. Token Introspection

SSO 的 access token 是 opaque token。QTrade API 或服务间调用应使用 QTrade 自己的 client credentials 调用：

```http
POST /oidc/introspect
Authorization: Basic ...
Content-Type: application/x-www-form-urlencoded

token=...
```

要求：

- 只查询签发给 QTrade client 的 token。
- 校验 `active`、`scope`、`exp`、`sub`。
- 可按 `exp` 做短缓存，但不能长期缓存已撤销 token。
- Back-channel Logout 或 revocation 后立即失效本地会话。

## 7. Back-channel Logout

在 SSO 管理台配置 QTrade 的 HTTPS logout URI，例如：

```text
https://qtrade.example.com/auth/backchannel-logout
```

QTrade 收到 logout token 后必须：

1. 使用 SSO JWKS 验签。
2. 校验 `iss`、`aud`、`exp`、`jti`、`events`。
3. 记录 `jti` 防重放。
4. 根据 `sub` 撤销本地会话。
5. 返回 2xx；验签失败返回 401。

## 8. 登出

QTrade 登出流程：

1. 删除 QTrade 会话 cookie。
2. 如持有 `id_token`，调用：

```text
GET /oidc/logout
  ?id_token_hint=...
  &post_logout_redirect_uri=https://qtrade.example.com/logout-complete
```

3. `post_logout_redirect_uri` 必须已在 SSO 注册。

## 9. 用户映射与本地角色

首次登录：

```text
sso_sub + issuer
  -> 查找或创建 qtrade_user
  -> 初始化 personal workspace
  -> 写入 login audit
```

角色由 QTrade 管理，不从 SSO scope 推断：

| 角色 | 权限 |
| --- | --- |
| viewer | 查看授权工作区的数据与报告 |
| analyst | 创建策略、运行回测、管理告警规则 |
| trader | 下纸面单，提交实盘订单请求 |
| risk_manager | 配置限额、审批实盘、触发熔断 |
| admin | 管理成员、数据源、密钥、系统配置 |

`admin` 只代表 QTrade 业务管理员，不等同于 SSO owner。

## 10. 环境变量

```dotenv
SSO_ISSUER=http://localhost:8000
QTRADE_BASE_URL=http://localhost:3000
SSO_CLIENT_ID=
SSO_CLIENT_SECRET=
SSO_CALLBACK_URL=http://localhost:3000/auth/callback
SSO_POST_LOGOUT_URL=http://localhost:3000/logout-complete
SESSION_SECRET=
```

生产环境必须：

- `SSO_ISSUER` 与 `QTRADE_BASE_URL` 使用 HTTPS。
- cookie 设置 `Secure`。
- 配置 HTTPS Back-channel Logout URI。
- 定期轮换 `SESSION_SECRET` 时保持兼容或强制重登。

