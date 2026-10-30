# Spec Delta

## Purpose

提供与实盘执行语义一致的纸面交易和小范围受控实盘能力，管理订单生命周期、恢复与对账，避免策略直接触达外部交易通道。

## ADDED Requirements

### Requirement: Paper Runtime
系统 MUST 支持使用与 Live Runtime 相同的策略版本、订单模型和风控入口创建 Paper Runtime，仅撮合与账户连接由模拟适配器替代。

#### Scenario: 启动纸面交易
- **WHEN** analyst 启动一个已验证 Strategy Version 的 Paper Runtime
- **THEN** 系统创建运行时、应用资源限制，并通过模拟撮合记录订单与成交

#### Scenario: 纸面交易风控拒绝
- **WHEN** Paper Runtime 产生违反限额的 Order Intent
- **THEN** RiskEngine 拒绝该意图，模拟账户不产生订单

### Requirement: 订单生命周期
系统 MUST 跟踪订单创建、风控通过、已提交、部分成交、全部成交、已撤销、拒绝、超时和失败等状态，并保留每次状态变化的时间、来源和上下文。

#### Scenario: 部分成交后撤销
- **WHEN** 订单部分成交后用户撤销剩余数量
- **THEN** 系统记录已成交数量、撤销结果和最终订单状态

#### Scenario: 券商拒绝
- **WHEN** Broker Adapter 返回拒绝原因
- **THEN** 订单进入拒绝状态，原因展示给授权用户并进入审计

### Requirement: 幂等下单
每个 Order Command MUST 携带全局唯一幂等键；网络重试或组件恢复 MUST NOT 导致同一命令重复提交。

#### Scenario: 提交后网络超时
- **WHEN** QTrade 提交订单后未收到确认并重试
- **THEN** Broker Adapter 依据幂等键返回同一结果，不创建重复订单

### Requirement: 实盘功能开关
Live Runtime MUST 受环境级 feature flag、Workspace 配置和账户授权共同控制；任一开关禁止时，即使用户具备角色权限也不能启动实盘。

#### Scenario: 全局关闭实盘
- **WHEN** 生产环境全局 Live Trading 开关关闭
- **THEN** 所有 Live Runtime 启动请求被拒绝，Paper Runtime 不受影响

### Requirement: 受控实盘启动
启动 Live Runtime 前，系统 MUST 验证 Strategy Version 已审批、Paper Runtime 达到配置观察期、风控限额已配置、Broker Account 连接正常、回滚方案已保存，并要求有权限用户二次确认。

#### Scenario: 缺少风控限额
- **WHEN** 用户尝试启动未配置账户限额的 Live Runtime
- **THEN** 系统拒绝启动并列出缺失配置

#### Scenario: 二次确认
- **WHEN** trader 启动已满足条件的 Live Runtime
- **THEN** 系统要求确认目标账户、策略版本和最大风险配置，确认后才开始运行

### Requirement: 断线恢复
Broker 或行情连接断开时，系统 MUST 保持运行时状态、停止依赖缺失数据的新订单，并在恢复后同步账户、订单与持仓状态。

#### Scenario: 券商连接断开
- **WHEN** Live Runtime 与 Broker Adapter 连接断开
- **THEN** 系统显示连接异常，按配置暂停新订单或进入安全状态，并产生告警

#### Scenario: 服务重启
- **WHEN** QTrade worker 或 API 在 Paper/Live Runtime 运行期间重启
- **THEN** 系统恢复运行时状态并继续跟踪订单生命周期，不重复执行已确认订单

### Requirement: 对账
系统 MUST 支持 QTrade 本地订单、成交、持仓、现金与 Broker Account 侧状态对账，并生成差异报告；未处理差异 MUST 阻止相关账户启动新实盘订单。

#### Scenario: 发现持仓差异
- **WHEN** 对账发现本地持仓与券商持仓不一致
- **THEN** 系统生成差异报告和告警，并按配置暂停该账户新订单

#### Scenario: 差异处理完成
- **WHEN** 授权用户确认差异原因并完成调整
- **THEN** 系统记录处理过程，并在下次对账一致后恢复可交易状态

### Requirement: 手动干预
授权用户 MUST 能对 Paper/Live Runtime 执行暂停、恢复、取消未完成订单、一键平仓和停止操作；不可逆操作 MUST 二次确认并审计。

#### Scenario: 一键平仓
- **WHEN** trader 对有持仓的 Live Runtime 执行一键平仓
- **THEN** 系统对每个持仓生成经过风控的平仓 Order Command，并展示执行结果

