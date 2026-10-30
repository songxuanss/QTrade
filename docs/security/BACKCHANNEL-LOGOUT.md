# SSO Back-channel Logout

机器接口 `POST /auth/backchannel-logout` 位于 Next.js BFF；无需浏览器 Cookie 或 CSRF，
授权凭据是 SSO 签名的 Logout Token。支持标准 form `logout_token` 与 sibling SSO 当前发送的
JSON `{ "logout_token": "..." }`；拒绝重复字段、额外字段与超大输入，不回显 token。

协议依据 [OpenID Connect Back-Channel Logout 1.0](https://openid.net/specs/openid-connect-backchannel-1_0.html)。
BFF 从配置 issuer 的 discovery/JWKS 验证 RS256 签名，要求精确 issuer、client audience、
有效 iat/exp/jti、logout events，以及 sub 或 sid。禁止 nonce、未来签发、五分钟前签发和
超过五分钟的 token 有效期；过期无宽限。配置仍使用 [OIDC BFF](OIDC.md) 的 HTTPS/密钥规则。

验签后 BFF 用独立 server secret 调用 API `/internal/auth/backchannel-logout`，提交最小
VerifiedLogout（无原始 token）。API 再次校验 issuer、shape、expiry，调用 LogoutService。
领域只依赖 LogoutStorePort；PostgresLogoutStore 用同一事务消费事件并撤销会话。
该实现与会话存储共用数据库事务/错误边界，不继承会话的业务操作。

## 一致性与作用域

- `logout_receipt` 以 `(issuer, jti)` 为主键；并发 INSERT ON CONFLICT 只有一个消费者。
  同事务 UPDATE 撤销会话；任一数据库失败同时回滚 receipt 和撤销，允许供应商安全重试。
- 有 sub 时按 issuer/sub 撤销全部 QTrade 会话，符合 Phase 1 的 subject 全局登出要求；
  即使 token 同时带 sid，也不将范围缩小到单个本地会话。
- sid-only token 按 issuer/sso_sid 撤销对应会话，不影响其他 issuer 的相同 sid。
- 未知身份仍消费有效 jti，返回成功但不披露用户是否存在。重放返回 401，不再次撤销，
  因而首次消费后创建的新登录不会被旧 token 撤销。
- 成功 200、无响应 body；验证/重放拒绝 401，持久化不可用 503；全部 no-store。
  Receipt 到 token 过期后才清理；应用与数据库需要同步 UTC 时间。

## 部署与验证

注册 client 的 `backchannel_logout_uri` 为 `<QTRADE_PUBLIC_ORIGIN>/auth/backchannel-logout`；
SSO 必须能访问此入口。API internal 路由限制到可信 BFF 网络，bridge secret 与 issuer 一致。
部署前执行 `docker compose exec -T api python -m qtrade_postgres.migrate`，应用 expand-only
0002 migration；已应用的 0001 checksum 不变，不执行破坏性 down migration。

`npm run check` 覆盖真实 RSA 签名验证及 issuer/audience/签名/过期/iat/events/nonce/jti/
subject/有效期负例。`npm run build && npm run test:auth` 使用生产 standalone BFF 验证
签名拒绝不撤销、有效 form 全局退出、多标签失效与新登录后的 JSON 重放拒绝。
`bash scripts/check-python.sh` 覆盖可信桥接与应用时钟；`scripts/selftest-postgres.py`
验证实际 PG 的进程重建防重放、issuer/sub/sid 隔离、八消费者并发与数据库失败原子回滚。
真实 ../sso client 的登录/全局登出联调仍由任务 3.9 验收。
