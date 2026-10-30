# 服务端会话

身份上下文中的 `SessionService` 是调用接口：创建、认证、读取服务端 token、CSRF、撤销。
`SessionStorePort` 隔离 PostgreSQL，`TokenCipherPort` 隔离加密实现；业务领域不依赖 HTTP、
SQL 或 cryptography。BFF/OIDC 适配层必须先验签与校验 claims，再调用身份映射/会话创建。
本模块不接收未验证浏览器 claims，也没有对外会话创建后门。

## 数据与生命周期

- 每次登录生成独立的 256-bit 随机会话与 CSRF 值；数据库只存 SHA-256 摘要。
- access/refresh/ID token 与 CSRF 原值仅存在于 AES-256-GCM 密文中。随机 12-byte nonce
  每次重新生成，associated data 绑定会话摘要，复制密文到其他会话或篡改内容均不能解密。
- 服务端密钥版本存入记录；新会话使用 primary key，保留旧密钥可读取旧会话，移除旧钥
  使旧会话不可用。密钥不能进浏览器、日志、Git 或镜像；部署时通过 secret 环境注入。
- 会话默认为八小时绝对过期，可配置一分钟至十二小时；读取不延长期限。
  过期、撤销、用户禁用、工作区归档、成员失效时立即拒绝后续认证/token 读取。
- `GET /auth/session` 只返回身份/工作区 ID、有效期与 CSRF 值，不返回 OIDC token。
  `POST /auth/logout` 要求绑定会话的 CSRF，撤销后清除 Cookie。成功/失败均 no-store。
  全局 SSO logout 的验签与 replay 防护见 [Back-channel Logout](BACKCHANNEL-LOGOUT.md)。

## 服务端配置

- `DATABASE_URL`：已迁移的 PostgreSQL；不暴露给浏览器。
- `QTRADE_SESSION_KEYS_JSON`：JSON 映射，key ID 对应 base64 编码的 32-byte 随机密钥。
- `QTRADE_SESSION_PRIMARY_KEY`：映射中当前写入 key ID。
- `QTRADE_SESSION_LIFETIME_SECONDS`：默认 28800。
- `QTRADE_ENV`：local/test/staging/production；staging/production 缺少密钥时启动失败。
  local 无密钥时 session 路由返回 503，不创建默认密钥或接受任意身份。
- `QTRADE_INSECURE_LOCAL_COOKIES`：默认 false，仅 local/test 可显式 true。
  生产使用 `__Host-qtrade-session`，HttpOnly/Secure/SameSite=Lax、Path=/、无 Domain。
  显式本地 HTTP 使用 `qtrade-session`，保持 HttpOnly/SameSite=Lax。

OIDC issuer/client、回调校验与 BFF Cookie 转发已接入，见 [OIDC BFF](OIDC.md)；
真实 SSO 本地联调与全局登出仍待验收。所有业务变更还须在 3.6/3.7 实施对象权限和统一 CSRF 守卫。

## 验证

`bash scripts/check-python.sh` 覆盖密文、篡改、会话绑定、key rotation、过期、撤销、Cookie
配置与 HTTP token 隐私；`scripts/selftest-postgres.py` 验证实际持久化/进程重建/访问变更。
`apps/web` 的 `npm run test:e2e` 启动仅 loopback 的测试 fixture，真实 Chromium 验证
JavaScript 不可读 HttpOnly Cookie、浏览器响应/storage 无 SSO token、伪造 logout 被拒绝。
该 fixture 位于 tests/，不打入生产镜像；它验证会话模块，不冒充 SSO 端到端登录。
