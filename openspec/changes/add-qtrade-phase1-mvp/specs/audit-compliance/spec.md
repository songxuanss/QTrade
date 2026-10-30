# Spec Delta

## Purpose

记录安全、策略、交易、风控与配置行为，形成可查询、可导出、不可抵赖的审计链条，满足专业交易平台的追溯要求。

## ADDED Requirements

### Requirement: 必审计事件
系统 MUST 审计登录与登出、认证失败、权限拒绝、Workspace 与成员变更、策略版本创建、状态转换、回测输入访问、实盘发布、订单与撤销、风控决策、限额变更、凭证变更、Kill Switch、数据源配置和对账处理。

#### Scenario: 实盘订单审计
- **WHEN** 一笔实盘订单提交到 Broker Adapter
- **THEN** 审计记录包含操作者、Workspace、Strategy Version、Order Command、Risk Decision、账户与提交时间

#### Scenario: 权限拒绝审计
- **WHEN** 用户尝试执行未授权敏感操作
- **THEN** 系统记录 actor、目标资源、操作、来源 IP 与拒绝原因

### Requirement: 审计内容
每条审计记录 MUST 至少包含 actor 身份、动作、目标对象、Workspace、发生时间、请求标识、来源 IP 或客户端上下文、结果以及必要的变更前后摘要；敏感 secret MUST NOT 写入明文。

#### Scenario: 配置变更
- **WHEN** admin 修改某个风险限额
- **THEN** 审计记录展示旧值、新值、作用范围和操作者

#### Scenario: 凭证变更脱敏
- **WHEN** 用户更新 Broker API key
- **THEN** 审计记录只包含凭证标识、掩码摘要和操作结果，不包含明文密钥

### Requirement: 审计查询与导出
授权用户 MUST 能按时间、actor、Workspace、动作、对象、结果和请求标识查询审计记录，并导出受限范围的报告。

#### Scenario: 查询实盘相关事件
- **WHEN** risk_manager 查询某账户某天的订单与风控审计
- **THEN** 系统返回按时间排序的事件，并支持追踪相关请求与订单

#### Scenario: 导出越权
- **WHEN** 用户尝试导出无权限 Workspace 的审计记录
- **THEN** 系统拒绝导出并记录失败尝试

### Requirement: 不可抵赖与保留
审计记录 MUST 不可由普通业务 API 修改或删除，并按配置保留；归档或清理 MUST 是受权限控制的运维操作并有独立记录。

#### Scenario: 尝试删除审计
- **WHEN** admin 通过业务 API 尝试删除或修改审计记录
- **THEN** 系统拒绝该操作

### Requirement: 关联追踪
关键业务对象 MUST 保存请求标识或追踪标识，使登录、策略审批、风控决策、订单、通知和对账能够被关联查询。

#### Scenario: 排查一笔异常订单
- **WHEN** risk_manager 从订单详情打开关联追踪
- **THEN** 系统展示同一链路下的策略事件、Risk Decision、broker 响应与审计记录

