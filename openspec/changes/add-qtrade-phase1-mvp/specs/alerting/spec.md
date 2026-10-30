# Spec Delta

## Purpose

统一处理行情与策略、风险、数据质量、系统运行四类告警，确保重要异常被及时、准确、可追溯地通知和处置。

## ADDED Requirements

### Requirement: 告警规则管理
授权用户 MUST 能创建、测试、启用、禁用和修改告警规则，规则 MUST 定义作用域、数据来源、条件、严重级别、冷却时间、静默窗口和通知渠道。

#### Scenario: 创建价格阈值规则
- **WHEN** analyst 创建某 Instrument 价格上穿阈值的规则并启用
- **THEN** 后续满足条件时按规则严重级别和通知渠道产生告警

#### Scenario: 非授权用户修改规则
- **WHEN** viewer 尝试修改 Workspace 告警规则
- **THEN** API 拒绝操作并记录审计事件

### Requirement: 四类告警来源
告警系统 MUST 支持行情/策略信号、风险事件、数据质量和系统运行状态四类来源，并使用统一的事件模型呈现严重级别、时间、来源、作用域与状态。

#### Scenario: 风险限额触发
- **WHEN** 账户单日亏损达到风险告警阈值
- **THEN** 系统产生风险类告警并包含限额值、当前值与受影响策略

#### Scenario: 行情延迟
- **WHEN** 数据源延迟超过规则阈值
- **THEN** 系统产生数据类告警，并展示市场、延迟时间和连接状态

### Requirement: 去重与冷却
同一规则在同一主体和根因下重复触发时，系统 MUST 按配置去重或合并，并在冷却时间内不重复通知；新的更高严重级别 MUST NOT 被低级别冷却抑制。

#### Scenario: 重复条件
- **WHEN** 同一价格阈值在冷却时间内连续满足
- **THEN** 系统更新原告警计数或最后触发时间，不重复发送通知

#### Scenario: 严重级别升级
- **WHEN** 告警从 medium 升级为 critical
- **THEN** 系统立即发送升级通知并保留原事件历史

### Requirement: 通知渠道
系统 MUST 支持站内通知、Email 和 Webhook；每次通知尝试 MUST 记录目标、状态、响应摘要和重试结果。

#### Scenario: Webhook 成功
- **WHEN** Webhook 返回 2xx
- **THEN** 通知记录标记成功并保存响应摘要

#### Scenario: Webhook 失败
- **WHEN** Webhook 超时或返回非 2xx
- **THEN** 系统按策略重试，最终状态可查询，告警本体不被删除

### Requirement: 告警处置
授权用户 MUST 能认领、确认、静默、关闭或重新打开告警，并添加处置备注；处置历史 MUST 保留操作者与时间。

#### Scenario: 认领告警
- **WHEN** trader 认领一个交易相关告警
- **THEN** 告警显示负责人，后续状态变更进入同一处置历史

#### Scenario: 无权限关闭
- **WHEN** viewer 尝试关闭 critical 告警
- **THEN** 系统拒绝操作并说明权限不足

### Requirement: 触发上下文
告警产生时，系统 MUST 保存触发时关键上下文快照，包括规则版本、相关行情或风险摘要、策略/运行时状态和数据质量信息。

#### Scenario: 查看告警详情
- **WHEN** 用户打开一个已触发的告警
- **THEN** 系统展示触发条件、当时上下文、通知历史和处置记录

