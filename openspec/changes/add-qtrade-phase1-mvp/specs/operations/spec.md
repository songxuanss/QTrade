# Spec Delta

## Purpose

提供可重复部署、可观测、可备份、可恢复和可安全发布的运行基础，降低交易平台从开发到生产的操作风险。

## ADDED Requirements

### Requirement: 本地一键环境
开发与测试环境 MUST 能通过容器编排启动 Web、API、worker、realtime、数据库、缓存、对象存储和必要的 mock 依赖，并使用非生产默认凭据。

#### Scenario: 首次启动
- **WHEN** 开发者按 README 执行环境启动命令
- **THEN** 系统启动全部核心依赖，初始化数据库，并提供可访问的健康检查

### Requirement: 数据库迁移
所有 schema 变更 MUST 使用可版本化迁移；应用启动或发布过程 MUST 能应用迁移，并支持回滚或向前修复策略。

#### Scenario: 空库初始化
- **WHEN** 全新环境启动
- **THEN** 数据库迁移完成，应用健康检查通过

#### Scenario: 迁移失败
- **WHEN** 发布过程中迁移失败
- **THEN** 发布停止，服务不使用不一致 schema 继续处理交易请求

### Requirement: 结构化日志与追踪
API、worker、realtime、engine runner 和 adapter MUST 输出结构化日志，并尽量携带请求、任务或运行时追踪标识；日志 MUST NOT 记录明文密钥。

#### Scenario: 查询任务日志
- **WHEN** 用户查看失败回测日志
- **THEN** 日志按时间展示结构化错误、阶段与 trace 标识，并过滤敏感值

### Requirement: 指标与健康检查
系统 MUST 暴露服务健康、就绪、队列长度、任务耗时、错误率、行情延迟、broker 连接、数据库与缓存状态等指标，并在异常时产生系统告警。

#### Scenario: 队列积压
- **WHEN** 回测队列等待数超过阈值
- **THEN** 指标显示积压并触发系统告警

### Requirement: 备份与恢复
领域数据库、配置、密钥材料、不可变产物和必要索引 MUST 有备份策略；恢复流程 MUST 在文档中演练，恢复后的登录、策略、订单与审计一致性可验证。

#### Scenario: 恢复演练
- **WHEN** 运维人员按手册恢复 staging 数据
- **THEN** 用户可登录，历史策略与回测可查询，订单状态与审计记录一致

### Requirement: CI/CD 安全
代码库 MUST 自动执行 lint、类型检查、单元测试、集成测试、前端 E2E、安全扫描和镜像扫描；失败构建 MUST NOT 发布生产镜像。

#### Scenario: 测试失败阻止发布
- **WHEN** CI 中关键测试或安全扫描失败
- **THEN** 系统不生成可部署的生产发布产物

### Requirement: 环境配置
系统 MUST 区分 local、dev、staging、production 配置；生产外部地址必须使用 HTTPS，生产默认开启实盘安全开关，敏感配置 MUST 来自受控 secret 来源。

#### Scenario: 生产配置校验
- **WHEN** 生产启动检测到非 HTTPS SSO issuer 或缺失会话 secret
- **THEN** 服务拒绝启动并输出明确配置错误

### Requirement: 发布回滚
每个发布 MUST 记录镜像摘要、迁移版本和配置版本，并支持回滚到上一个安全版本；回滚 MUST 评估数据库迁移兼容性。

#### Scenario: 应用异常回滚
- **WHEN** 新版本导致 API 或实盘 worker 不可用
- **THEN** 运维人员能按发布记录回滚应用，并保持数据一致

