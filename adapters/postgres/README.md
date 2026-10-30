# PostgreSQL 身份基础

`qtrade_postgres.migrate` 管理有序、校验和锁定的 SQL 迁移。业务模型在
`qtrade_domain.identity`，数据库字段/约束由 Adapter 负责，领域代码不导入 psycopg。

```bash
# 在部署环境提供 DATABASE_URL；默认写入 qtrade schema。
PYTHONPATH=adapters/postgres/src .venv/bin/python -m qtrade_postgres.migrate
# 已构建 Compose API 镜像包含 Adapter 入口与 DATABASE_URL：
docker compose run --rm --no-deps api python -m qtrade_postgres.migrate
# 对本地 Compose 数据库运行隔离 schema 集成验收：
.venv/bin/python scripts/selftest-postgres.py
```

## 迁移与回退策略

- 每次执行持有事务 advisory lock；SQL 与版本记录在同一事务内提交，失败整体回滚。
- 重复执行校验已执行版本的 SHA-256 后跳过；已执行文件禁止修改。旧应用遇到数据库
  中更高版本时拒绝执行，避免意外降级。新版本连续编号从 0001 开始。
- 0001 只添加表、索引和审计保护。发布采用 expand/contract：先添加兼容字段/表，再切换
  读写，最后单独迁移删除旧结构。保留期间可回退应用；不要 drop schema 来回退生产。
- 失败且未提交的迁移可修复后重跑；已提交迁移必须增加新的向前修复文件。
  删除用户、成员与审计历史会破坏安全/追溯语义，不提供自动 destructive down。
- 若向前修复不可行，停止写入并按备份恢复流程恢复经验证的一致数据快照；不能用
  旧 schema 覆盖现有数据。后续备份/恢复任务负责证明该部署流程。
- 集成测试故意失败初始迁移/升级，证明前者无残留 schema、后者保留原有用户/工作区，
  再以新正确迁移前进。测试使用随机独立 schema，finally 清理，不触及业务 schema。

## 数据不变量

issuer + subject 唯一映射用户；每个用户最多一个个人工作区；成员关系唯一且仅允许五个
内置角色。工作区实盘默认为关闭。会话只保存会话/CSRF 的 SHA-256 摘要及可选 token 密文，
密文必须有 key ID；活动工作区须有该用户成员关系，过期时间晚于创建时间。
会话加密、过期/撤销与权限服务在后续任务实现，这些表不代表认证链路已完成。
审计包含 actor、工作区、动作、结果、时间、来源/IP、trace 与结构化详情；触发器禁止
UPDATE/DELETE/TRUNCATE。数据库管理员仍可改变触发器，生产应使用受限应用角色与独立审计归档。

`PostgresIdentityStore.initialize` 在同一事务完成 issuer/sub 映射、个人工作区及首次 admin
成员创建，通过唯一约束与用户行锁串行化并发登录。重复登录只更新可信身份资料，保留
工作区及现有授权；用户禁用时拒绝，不自动复活撤销/删除的成员。调用者必须先验证 OIDC，
不能把任意浏览器 claims 直接送到该接口。SSO 密码与凭据不属于 QTrade 用户模型。
