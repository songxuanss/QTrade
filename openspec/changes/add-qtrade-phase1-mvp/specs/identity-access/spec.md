# Spec Delta

## Purpose

通过 ../sso 完成 QTrade 用户认证，并在 QTrade 内建立工作区、成员关系与对象级授权，确保认证与业务授权边界清晰。

## ADDED Requirements

### Requirement: OIDC 授权码登录
QTrade MUST 使用 OIDC Authorization Code + PKCE 完成登录，并对 ID Token 验签、校验 issuer、audience、expiry 与 nonce；QTrade MUST NOT 接受缺少 state 或 state 不匹配的回调。

#### Scenario: 首次登录
- **WHEN** 未登录用户访问 QTrade 受保护页面
- **THEN** 系统跳转到 SSO 授权端点，登录成功后回到 QTrade 并建立服务端会话

#### Scenario: 回调 state 无效
- **WHEN** QTrade 收到 state 缺失、过期或不匹配的 OIDC 回调
- **THEN** 系统拒绝该回调，不交换令牌，也不创建 QTrade 会话

### Requirement: 安全 BFF 会话
QTrade 访问令牌、刷新令牌与 client secret MUST NOT 暴露给浏览器；浏览器只持有 HttpOnly 会话 Cookie，生产环境该 Cookie MUST 设置 Secure 与适当的 SameSite 属性。

#### Scenario: 检查浏览器存储
- **WHEN** 用户完成登录并检查浏览器 localStorage、sessionStorage 与 Cookie
- **THEN** 浏览器中没有 QTrade SSO client secret、access token 或 refresh token

#### Scenario: 未认证 API 请求
- **WHEN** 无有效 QTrade 会话的请求访问需要登录的 API
- **THEN** API 返回未认证错误，不返回业务数据

### Requirement: 用户映射与个人工作区
用户首次登录时，QTrade MUST 使用 issuer 与 SSO subject 唯一映射 QTrade User，并创建或绑定其个人 Workspace；重复登录 MUST NOT 创建重复用户或重复个人 Workspace。

#### Scenario: 重复登录
- **WHEN** 同一 SSO 用户第二次登录 QTrade
- **THEN** 系统复用既有 QTrade User 和个人 Workspace，并保留其已有授权资源

### Requirement: SSO 登出联动
QTrade MUST 支持 SSO Back-channel Logout；验证通过的 Logout Token MUST 撤销对应 SSO subject 的所有 QTrade 本地会话，并防止同一 logout token 重放。

#### Scenario: SSO 全局登出
- **WHEN** SSO 向 QTrade 发送有效且未重放的 Back-channel Logout Token
- **THEN** QTrade 撤销该用户全部本地会话，后续请求要求重新认证

#### Scenario: Logout Token 验签失败
- **WHEN** QTrade 收到签名、issuer、audience、expiry、events 或 jti 无效的 Logout Token
- **THEN** QTrade 返回认证失败状态，不撤销任何会话

### Requirement: 对象级 RBAC
QTrade MUST 为 Workspace 资源实施对象级授权，至少区分 viewer、analyst、trader、risk_manager 与 admin；后端 MUST 在每个业务 API 上重新判断权限，不能只依赖前端隐藏控件。

#### Scenario: viewer 尝试修改策略
- **WHEN** viewer 角色调用策略版本创建或回测启动 API
- **THEN** API 返回权限拒绝，策略状态不变，并记录审计事件

#### Scenario: 跨工作区访问
- **WHEN** 用户访问自己不是成员的 Workspace 资源
- **THEN** API 返回不可见或无权限结果，不泄露资源存在性以外的业务数据

### Requirement: 状态变更 CSRF 防护
所有通过浏览器发起的状态变更请求 MUST 使用不可预测且绑定会话的 CSRF 保护；缺失或错误 CSRF 凭据的请求 MUST 被拒绝。

#### Scenario: 伪造状态变更请求
- **WHEN** 恶意站点携带用户 Cookie 提交缺少有效 CSRF token 的表单或 API 请求
- **THEN** QTrade 拒绝该请求且不发生业务状态变化

