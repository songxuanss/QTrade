# OIDC BFF 登录

Next.js BFF 独占 Authorization Code + PKCE（S256）与 token 交换。openid-client
执行 state、nonce、issuer、audience、时间与 RS256 验签；显式开启
`enableNonRepudiationChecks`，确保 HTTPS token endpoint 的信任不能替代 ID token 验签。
发现文档 issuer 必须与部署配置完全一致。回调不接受重复 state/code、缺失 state、
provider error 或过期事务；失败统一进入登录错误页，不暴露 claims/token/内部错误。

登录事务包含 state、nonce、PKCE verifier、目标相对路径与五分钟过期时间，使用独立
AES-256-GCM 密钥加密，并绑定 issuer/client/public origin。事务 Cookie 保持
HttpOnly/Secure/SameSite=Lax、Path=/、无 Domain；成功/失败回调均清除事务。
returnTo 只接受站内路径，拒绝协议相对 URL、反斜杠、换行和身份路由循环。

## 边界

BFF 验证后用独立 Bearer secret 调用 API 的 `POST /internal/auth/session`。
API 检查服务身份、精确 issuer 和 payload 格式，然后调用 IdentityService/SessionService；
响应仅有 204 与 opaque session Cookie。部署须限制此接口到 BFF 网络，使用 HTTPS 或
隔离的可信服务网络。BFF 将 Cookie 转发给浏览器；SSO token 始终只在服务端交换/加密持久化。
API 不接受浏览器提交的未验证 claims。错误响应不回显敏感 payload。

浏览器通过 BFF `/auth/session` 获取允许字段（身份/工作区 ID、有效期、CSRF），不获取 token。
`POST /auth/logout` 要求精确 Origin 和会话绑定 CSRF，并调用服务端撤销。
登录状态驱动布局、工作台入口和访问守卫；当前各业务模块的空态明确显示尚未开放。

## 配置

全部为服务端环境变量，禁止 NEXT_PUBLIC 前缀、Git 或客户端打包：

- `QTRADE_OIDC_ISSUER`：缺失时身份功能不可用，不创建默认登录身份。
- `QTRADE_OIDC_CLIENT_ID`、`QTRADE_OIDC_CLIENT_SECRET`：SSO 中注册的 confidential client。
- `QTRADE_PUBLIC_ORIGIN`：对外 origin；注册回调为 `<origin>/auth/callback`。
- `QTRADE_API_ORIGIN`：BFF 可访问的 API origin，HTTP 仅用于可信隔离服务网络。
- `QTRADE_API_PUBLIC_ORIGIN`：浏览器可访问的 API origin，用于建立行情健康 WebSocket；其主机应与登录站点一致以便浏览器附带会话 Cookie，生产环境使用 HTTPS。
- `QTRADE_BFF_SECRET`：BFF/API 共用、至少 32 字符的独立随机 secret。
- `QTRADE_OIDC_TRANSACTION_KEY`：base64 编码的 32-byte 独立随机 AES 密钥。
- `QTRADE_ENV`、`QTRADE_INSECURE_LOCAL_COOKIES`：与会话部署配置保持一致。
- `QTRADE_OIDC_ALLOW_HTTP`：默认 false，仅 local/test 可显式启用；生产 issuer/外部 origin
  必须 HTTPS，禁止不安全 Cookie。密钥轮换使未完成事务失效，重新登录即可。

API 同样配置 issuer/BFF secret，以及 [会话密钥与 PostgreSQL](SESSIONS.md)。
未配置的 Compose 保持访客态与 identity 503；真实 SSO client 配置及全局登出联调属于任务 3.9。

## 验证

`npm run check` 使用真实 RSA JWT/HTTP discovery/JWKS/token endpoint 的单元 fixture，
覆盖成功、PKCE/client authentication、state/nonce/issuer/audience/签名/过期失败和事务篡改。
每个 JWT 失败用例确认已生成 token response，避免 token 交换失败产生假阳性。

`npm run build && npm run test:auth` 使用生产 Next.js 与仅 loopback 的 synthetic provider，
配合真实 API bridge/SessionService/TokenCipher 测试保护页登录、Cookie/浏览器 token 隐私、
签名拒绝不映射身份/不创建会话、缺失 state 不交换 token、伪造 logout 与正常撤销，
并运行登录成功/失败页面 axe 检查。fixture 位于 tests/，不进入生产镜像。
这是协议与模块集成测试；不声称已联调 ../sso，也不替代真实 PostgreSQL 持久化测试。

机器全局登出入口、验证与原子重放防护见 [Back-channel Logout](BACKCHANNEL-LOGOUT.md)。
