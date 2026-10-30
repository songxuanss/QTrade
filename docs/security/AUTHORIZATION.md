# Workspace 对象授权

身份认证与 QTrade 角色分开。SSO claims、scope、个人工作区 admin 和客户端字段均不能授予
另一个 Workspace 的权限。API 通过 SessionService 认证，再由 WorkspaceService 对目标工作区
的当前用户/成员/归档状态裁决；跨工作区与不存在资源统一 404，缺少权限 403，未认证 401。

## 五角色矩阵

所有角色只可读取自己被授权工作区的数据。额外写权限是显式 grant，不是角色继承：

| 角色 | 额外授权 |
| --- | --- |
| viewer | 无写权限 |
| analyst | 策略编写、回测启动、paper 策略启动、告警规则与处置 |
| trader | 下单请求、live 启动请求、平仓、告警处置 |
| risk_manager | 风险限额、live 审批、Kill Switch、告警处置、审计查询/导出 |
| admin | 研究/paper/交易操作、告警管理与处置、成员、数据、凭证、系统管理、策略归档、风险配置/Kill Switch、审计查询/导出 |

admin 显式拥有日常研究与执行权限，使首次创建的个人工作区可以使用产品；live 审批仍仅由
risk_manager 执行。risk_manager 不自动拥有提交订单权限。
角色只是前置权限，不能替代 RiskEngine、运行时状态、审批资格和禁止自我审批等业务不变量。
领域 `Permission`/`ROLE_PERMISSIONS` 是唯一 Python 策略来源，grants 为不可变集合。

## Interface 与所有权

`WorkspaceService.authorize(actor, workspace, permission, object_workspace_id=...)` 对所属对象
校验工作区一致性；资源实现必须先从持久化记录取得实际归属，不能信任浏览器传来的归属。
返回 WorkspaceAccess 只在当前操作使用，不缓存为后续请求授权。
WorkspaceStorePort 的 PostgreSQL/Mock Adapter 返回即时成员状态；其他业务上下文只引用
用户/工作区 ID，不修改身份上下文的表。

浏览器 API 使用 `ApiSecurity.workspace(permission, mutate=True)` 依赖声明写权限和
绑定会话的 CSRF。只读权限不能注册为写守卫。`/api/v1` 路由检查拒绝漏用中央守卫的新增
APIRouter 路由；该静态检查是防遗漏工具，业务集成测试仍需验证对象归属与实际状态不变。
机器 OIDC/internal/logout 接口走独立验签/服务凭据，不接受浏览器 claims，不用浏览器 CSRF。

已提供 `/api/v1/workspaces` 授权列表、`/{id}` 当前角色/能力、`/{id}/members` 管理查询和
`PUT /{id}/members/{user_id}` 设置角色/启用状态。成员目标必须是已映射的有效 SSO 用户，
不创建密码或凭证；请求不接受额外字段。读取不返回 token、SSO subject 或 email。
所有会话/权限结果 no-store。前端仅收到验证后的工作区名称与本地角色用于显示，不能授权操作。

成员写入在 workspace 行锁保护的事务内重新检查 actor 的实时 admin 权限，并保持至少
一位非禁用的 active admin。并发撤销两位 admin 只能成功一次；禁止通过禁用用户补足管理员数。
写入保留初始 created_by/created_at，更新 updated_at；授权撤销在下一请求生效。

## 验证与后续集成

`bash scripts/check-python.sh` 验证五角色 allow/deny、对象归属、即时角色变化、用户禁用、
工作区归档、viewer 写拒绝、admin 成员管理、伪造 form/跨会话 CSRF 和新增路由漏守卫。
`scripts/selftest-postgres.py` 使用实际 PostgreSQL 验证成员变更、最后 admin 并发保护及
真实 SessionService/HTTP API：个人 workspace admin 在团队仅有 viewer 权限；新角色即时生效。
策略守卫 caller fixture 验证 viewer 不进入写处理器；实际策略/回测 API 在 7.1/7.6 实现时
必须复用相同裁决并验证真实对象状态不变。审计在 3.8 补齐，业务全链 CSRF 在 3.7 验收。
